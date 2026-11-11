/* CU15 - migracion revisada, no ejecutar contra Supabase sin autorizacion.
   Una sola transaccion. No llama procedimientos legacy ni modifica venta/pago.
   Dependencias verificadas: public.venta, detalle_venta, pago, inventario,
   movimiento_inventario, producto, variante_producto, cliente, empleado,
   usuario_empresa y el contexto JWT de fashionstore_private. */
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL search_path = pg_catalog, public, pg_temp;

DO $$ DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['empresa','venta','detalle_venta','pago','cliente','sucursal','empleado',
    'usuario_empresa','rol','funcion','accion','rol_funcion','inventario','movimiento_inventario',
    'variante_producto','producto'] LOOP
    IF to_regclass('public.'||t) IS NULL THEN
      RAISE EXCEPTION 'CU15 preflight: falta public.%', t USING ERRCODE='undefined_table';
    END IF;
  END LOOP;
  IF to_regprocedure('fashionstore_private.actor_empresa_id()') IS NULL
     OR to_regprocedure('fashionstore_private.actor_usuario_id()') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api_login')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_sp_owner') THEN
    RAISE EXCEPTION 'CU15 preflight: identidad o roles SaaS ausentes' USING ERRCODE='undefined_object';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_venta_tenant ON public.venta(empresa_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_detalle_tenant ON public.detalle_venta(empresa_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_cliente_tenant ON public.cliente(empresa_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_sucursal_tenant ON public.sucursal(empresa_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_inventario_tenant ON public.inventario(empresa_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cu15_variante_tenant ON public.variante_producto(empresa_id,id);

CREATE TABLE IF NOT EXISTS public.cu15_politica_empresa (
  empresa_id bigint PRIMARY KEY REFERENCES public.empresa(id),
  plazo_dias integer NOT NULL CHECK (plazo_dias=7),
  reembolso_sin_cambio numeric(5,4) NOT NULL CHECK (reembolso_sin_cambio=.5000),
  diferencia_negativa_configurada boolean NOT NULL DEFAULT false,
  actualizada_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.cu15_operacion (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL,
  venta_id bigint NOT NULL, cliente_id bigint, sucursal_recepcion_id bigint NOT NULL,
  tipo text NOT NULL CHECK (tipo IN ('CAMBIO','SIN_CAMBIO')),
  estado text NOT NULL DEFAULT 'SOLICITADA' CHECK (estado IN ('SOLICITADA','APROBADA','INSPECCIONADA','RECHAZADA','COMPLETADA')),
  motivo text NOT NULL, observacion text, idempotency_key uuid NOT NULL,
  actor_solicita_id bigint NOT NULL, actor_aprueba_id bigint, actor_inspecciona_id bigint,
  actor_completa_id bigint, fecha_hora timestamptz NOT NULL DEFAULT now(),
  fecha_inspeccion timestamptz, fecha_completada timestamptz,
  UNIQUE(empresa_id,id), UNIQUE(empresa_id,idempotency_key),
  FOREIGN KEY(empresa_id,venta_id) REFERENCES public.venta(empresa_id,id),
  FOREIGN KEY(empresa_id,cliente_id) REFERENCES public.cliente(empresa_id,id),
  FOREIGN KEY(empresa_id,sucursal_recepcion_id) REFERENCES public.sucursal(empresa_id,id)
);
CREATE TABLE IF NOT EXISTS public.cu15_item (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL,
  operacion_id bigint NOT NULL, detalle_venta_id bigint NOT NULL, variante_original_id bigint NOT NULL,
  variante_nueva_id bigint, cantidad integer NOT NULL CHECK(cantidad>0),
  precio_original numeric(14,2) NOT NULL CHECK(precio_original>=0), precio_nuevo numeric(14,2) CHECK(precio_nuevo>=0),
  condicion text CHECK(condicion IN ('APTO','NO_APTO','CUARENTENA')),
  observacion_inspeccion text, restock_autorizado boolean NOT NULL DEFAULT false,
  restock_ejecutado boolean NOT NULL DEFAULT false, movimiento_entrada_id bigint, movimiento_salida_id bigint,
  UNIQUE(empresa_id,operacion_id,detalle_venta_id),
  FOREIGN KEY(empresa_id,operacion_id) REFERENCES public.cu15_operacion(empresa_id,id),
  FOREIGN KEY(empresa_id,detalle_venta_id) REFERENCES public.detalle_venta(empresa_id,id),
  FOREIGN KEY(empresa_id,variante_original_id) REFERENCES public.variante_producto(empresa_id,id),
  FOREIGN KEY(empresa_id,variante_nueva_id) REFERENCES public.variante_producto(empresa_id,id)
);
CREATE TABLE IF NOT EXISTS public.cu15_diferencia (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL, operacion_id bigint NOT NULL,
  importe numeric(14,2) NOT NULL CHECK(importe>0), importe_cotizado_en timestamptz NOT NULL DEFAULT now(),
  estado text NOT NULL DEFAULT 'PENDIENTE' CHECK(estado IN ('PENDIENTE','PAGADO')),
  medio text, comprobante text, actor_id bigint, pagado_en timestamptz,
  UNIQUE(empresa_id,operacion_id), FOREIGN KEY(empresa_id,operacion_id) REFERENCES public.cu15_operacion(empresa_id,id)
);
CREATE TABLE IF NOT EXISTS public.cu15_reembolso (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL, operacion_id bigint NOT NULL,
  importe_elegible numeric(14,2) NOT NULL CHECK(importe_elegible>=0), importe_reembolso numeric(14,2) NOT NULL CHECK(importe_reembolso>=0),
  importe_retenido numeric(14,2) NOT NULL CHECK(importe_retenido>=0), estado text NOT NULL DEFAULT 'PENDIENTE' CHECK(estado IN ('PENDIENTE','ENTREGADO')),
  metodo text, comprobante text, actor_entrega_id bigint, entregado_en timestamptz,
  UNIQUE(empresa_id,operacion_id), FOREIGN KEY(empresa_id,operacion_id) REFERENCES public.cu15_operacion(empresa_id,id)
);
CREATE TABLE IF NOT EXISTS public.cu15_nota (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL, operacion_id bigint NOT NULL,
  numero bigint NOT NULL, tipo text NOT NULL CHECK(tipo IN ('CAMBIO','SIN_CAMBIO')),
  importe_origen numeric(14,2) NOT NULL, importe_sustituto numeric(14,2) NOT NULL, diferencia numeric(14,2) NOT NULL,
  importe_reembolsable numeric(14,2), importe_retenido numeric(14,2), creado_en timestamptz NOT NULL DEFAULT now(),
  UNIQUE(empresa_id,operacion_id), UNIQUE(empresa_id,numero),
  FOREIGN KEY(empresa_id,operacion_id) REFERENCES public.cu15_operacion(empresa_id,id)
);
CREATE SEQUENCE IF NOT EXISTS public.cu15_nota_numero_seq;
ALTER TABLE public.cu15_nota ALTER COLUMN numero SET DEFAULT nextval('public.cu15_nota_numero_seq');
CREATE TABLE IF NOT EXISTS public.cu15_outbox (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, empresa_id bigint NOT NULL, operacion_id bigint NOT NULL, cliente_id bigint,
  tipo text NOT NULL CHECK(tipo IN ('CU15_PRENDA_RECIBIDA','CU15_CAMBIO_COMPLETADO','CU15_DEVOLUCION_COMPLETADA')),
  estado text NOT NULL DEFAULT 'PENDIENTE' CHECK(estado IN ('PENDIENTE','PUBLICADO','FALLIDO')),
  creado_en timestamptz NOT NULL DEFAULT now(), publicado_en timestamptz,
  UNIQUE(empresa_id,operacion_id,tipo), FOREIGN KEY(empresa_id,operacion_id) REFERENCES public.cu15_operacion(empresa_id,id)
);
CREATE INDEX IF NOT EXISTS ix_cu15_operacion_empresa_estado ON public.cu15_operacion(empresa_id,estado,fecha_hora DESC);
CREATE INDEX IF NOT EXISTS ix_cu15_operacion_empresa_venta ON public.cu15_operacion(empresa_id,venta_id);
CREATE INDEX IF NOT EXISTS ix_cu15_outbox_pendiente ON public.cu15_outbox(empresa_id,estado,creado_en);

DO $$ DECLARE t text;
BEGIN FOREACH t IN ARRAY ARRAY['cu15_politica_empresa','cu15_operacion','cu15_item','cu15_diferencia','cu15_reembolso','cu15_nota','cu15_outbox'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS cu15_tenant ON public.%I',t);
  EXECUTE format('CREATE POLICY cu15_tenant ON public.%I FOR ALL TO fashionstore_api_login,fashionstore_sp_owner USING (empresa_id=fashionstore_private.actor_empresa_id()) WITH CHECK (empresa_id=fashionstore_private.actor_empresa_id())',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated',t);
END LOOP; END $$;

CREATE OR REPLACE FUNCTION public.cu15_actor_ok(p_empresa bigint,p_actor bigint,p_venta bigint,p_sucursal bigint,p_accion text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE cliente_ok boolean; personal_ok boolean;
BEGIN
  IF p_empresa IS NULL OR p_empresa IS DISTINCT FROM fashionstore_private.actor_empresa_id()
     OR p_actor IS NULL OR p_actor IS DISTINCT FROM fashionstore_private.actor_usuario_id() THEN
    RAISE EXCEPTION 'actor o tenant no autorizado' USING ERRCODE='42501';
  END IF;
  SELECT EXISTS(SELECT 1 FROM venta v JOIN cliente c ON c.empresa_id=v.empresa_id AND c.id=v.cliente_id
    WHERE v.empresa_id=p_empresa AND v.id=p_venta AND c.usuario_id=p_actor),
    EXISTS(SELECT 1 FROM empleado e JOIN usuario_empresa ue ON ue.usuario_id=e.usuario_id AND ue.empresa_id=p_empresa AND ue.estado
      JOIN rol_funcion rf ON rf.rol_id=ue.rol_id JOIN funcion f ON f.id=rf.funcion_id AND f.nombre='GESTIONAR_DEVOLUCIONES' AND f.estado
      JOIN accion a ON a.id=rf.accion_id AND a.nombre=p_accion AND a.estado
      WHERE e.usuario_id=p_actor AND e.estado AND (p_sucursal IS NULL OR e.sucursal_id=p_sucursal)) INTO cliente_ok,personal_ok;
  IF NOT (cliente_ok AND p_accion IN ('CREAR','CONSULTAR')) AND NOT personal_ok THEN RAISE EXCEPTION 'permiso o alcance de sucursal insuficiente' USING ERRCODE='42501'; END IF;
END $$;

CREATE OR REPLACE FUNCTION public.cu15_lineas_elegibles(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE v record; lim timestamptz; ok boolean;
BEGIN
  PERFORM cu15_actor_ok(p_empresa,p_actor,(p->>'venta_id')::bigint,NULL,'CONSULTAR');
  SELECT v0.* INTO v FROM venta v0 WHERE v0.empresa_id=p_empresa AND v0.id=(p->>'venta_id')::bigint;
  IF NOT FOUND THEN RETURN jsonb_build_object('venta_id',(p->>'venta_id')::bigint,'es_elegible',false,'motivo','Venta no encontrada'); END IF;
  SELECT plazo_dias=7 INTO ok FROM cu15_politica_empresa WHERE empresa_id=p_empresa;
  IF NOT COALESCE(ok,false) THEN RETURN jsonb_build_object('venta_id',v.id,'es_elegible',false,'motivo','Política CU15 no configurada'); END IF;
  lim:=v.fecha_hora+interval '7 days';
  RETURN jsonb_build_object('venta_id',v.id,'fecha_compra',v.fecha_hora,'fecha_limite_reclamo',lim,
    'es_elegible',v.estado='COMPLETADA' AND now() BETWEEN v.fecha_hora AND lim,
    'motivo',CASE WHEN v.estado<>'COMPLETADA' THEN 'Venta no completada' WHEN now()<v.fecha_hora THEN 'Venta futura' WHEN now()>lim THEN 'Plazo de 7 días vencido' END,
    'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('detalle_venta_id',d.id,'cantidad',d.cantidad,'precio_unitario',d.precio_unitario,'variante_original_id',i.variante_producto_id)) FROM detalle_venta d JOIN inventario i ON i.empresa_id=p_empresa AND i.id=d.inventario_id WHERE d.empresa_id=p_empresa AND d.venta_id=v.id),'[]'::jsonb));
END $$;

CREATE OR REPLACE FUNCTION public.cu15_crear(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE o bigint; k uuid; v record; x jsonb; d record; usado int; pagado numeric; total numeric; unit numeric; pol boolean;
BEGIN
  k:=(p->>'idempotency_key')::uuid; SELECT id INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND idempotency_key=k FOR UPDATE; IF FOUND THEN RETURN jsonb_build_object('ok',true,'operacion_id',o,'idempotente',true); END IF;
  SELECT * INTO v FROM venta WHERE empresa_id=p_empresa AND id=(p->>'venta_id')::bigint AND estado='COMPLETADA' AND now() BETWEEN fecha_hora AND fecha_hora+interval '7 days' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'reclamo fuera de 7 días o venta no elegible' USING ERRCODE='23514'; END IF;
  SELECT plazo_dias=7 INTO pol FROM cu15_politica_empresa WHERE empresa_id=p_empresa; IF NOT COALESCE(pol,false) THEN RAISE EXCEPTION 'política CU15 no configurada' USING ERRCODE='23514'; END IF;
  PERFORM cu15_actor_ok(p_empresa,p_actor,v.id,(p->>'sucursal_recepcion_id')::bigint,'CREAR');
  INSERT INTO cu15_operacion(empresa_id,venta_id,cliente_id,sucursal_recepcion_id,tipo,motivo,observacion,idempotency_key,actor_solicita_id) VALUES(p_empresa,v.id,v.cliente_id,(p->>'sucursal_recepcion_id')::bigint,p->>'tipo',p->>'motivo',p->>'observacion',k,p_actor) RETURNING id INTO o;
  SELECT COALESCE(SUM(monto),0) INTO pagado FROM pago WHERE empresa_id=p_empresa AND venta_id=v.id AND estado='APROBADO'; total:=NULLIF(v.total,0);
  FOR x IN SELECT * FROM jsonb_array_elements(p->'items') LOOP
    SELECT d0.*,i.variante_producto_id INTO d FROM detalle_venta d0 JOIN inventario i ON i.empresa_id=p_empresa AND i.id=d0.inventario_id WHERE d0.empresa_id=p_empresa AND d0.id=(x->>'detalle_venta_id')::bigint AND d0.venta_id=v.id FOR UPDATE;
    IF NOT FOUND OR (x->>'cantidad')::int<1 OR (x->>'cantidad')::int>d.cantidad THEN RAISE EXCEPTION 'línea no elegible' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(SUM(ci.cantidad),0) INTO usado FROM cu15_item ci JOIN cu15_operacion co ON co.empresa_id=p_empresa AND co.id=ci.operacion_id WHERE ci.empresa_id=p_empresa AND ci.detalle_venta_id=d.id AND co.estado<>'RECHAZADA';
    IF (x->>'cantidad')::int+usado>d.cantidad THEN RAISE EXCEPTION 'cantidad ya reclamada' USING ERRCODE='23514'; END IF;
    unit:=ROUND(d.precio_unitario*COALESCE(pagado/total,1),2);
    INSERT INTO cu15_item(empresa_id,operacion_id,detalle_venta_id,variante_original_id,variante_nueva_id,cantidad,precio_original) VALUES(p_empresa,o,d.id,d.variante_producto_id,NULLIF(x->>'variante_nueva_id','')::bigint,(x->>'cantidad')::int,unit);
  END LOOP;
  RETURN jsonb_build_object('ok',true,'operacion_id',o,'estado','SOLICITADA','idempotente',false);
END $$;

CREATE OR REPLACE FUNCTION public.cu15_cotizar_diferencia(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE o record;i record;precio numeric;ori numeric:=0;sus numeric:=0;dif numeric:=0;
BEGIN
 SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF;
 PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'EDITAR'); IF o.tipo<>'CAMBIO' OR o.estado NOT IN ('APROBADA','INSPECCIONADA') THEN RAISE EXCEPTION 'cotización no permitida' USING ERRCODE='23514'; END IF;
 FOR i IN SELECT * FROM cu15_item WHERE empresa_id=p_empresa AND operacion_id=o.id ORDER BY variante_nueva_id,id FOR UPDATE LOOP
  IF i.condicion IS NOT NULL AND i.condicion<>'APTO' THEN CONTINUE; END IF; ori:=ori+i.cantidad*i.precio_original;
  IF i.variante_nueva_id IS NULL THEN RAISE EXCEPTION 'falta variante sustituta' USING ERRCODE='23514'; END IF;
  SELECT pr.precio INTO precio FROM variante_producto vp JOIN producto pr ON pr.id=vp.producto_id WHERE vp.empresa_id=p_empresa AND vp.id=i.variante_nueva_id AND pr.estado;
  IF NOT FOUND THEN RAISE EXCEPTION 'modelo sustituto no encontrado' USING ERRCODE='23514'; END IF;
  UPDATE cu15_item SET precio_nuevo=ROUND(precio,2) WHERE id=i.id; sus:=sus+i.cantidad*precio;
 END LOOP;
 dif:=GREATEST(ROUND(sus-ori,2),0); IF sus<ori THEN RAISE EXCEPTION 'cambio más barato bloqueado: política de devolución no definida' USING ERRCODE='23514'; END IF;
 IF dif>0 THEN INSERT INTO cu15_diferencia(empresa_id,operacion_id,importe) VALUES(p_empresa,o.id,dif) ON CONFLICT(empresa_id,operacion_id) DO UPDATE SET importe=EXCLUDED.importe,importe_cotizado_en=now(),estado=CASE WHEN cu15_diferencia.importe<>EXCLUDED.importe THEN 'PENDIENTE' ELSE cu15_diferencia.estado END; END IF;
 RETURN jsonb_build_object('ok',true,'operacion_id',o.id,'importe_origen',ROUND(ori,2),'importe_sustituto',ROUND(sus,2),'diferencia',dif,'cotizado_en',now());
END $$;

CREATE OR REPLACE FUNCTION public.cu15_confirmar_diferencia(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE d record;o record;ref text:=NULLIF(btrim(p->>'comprobante'),'');
BEGIN SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'EJECUTAR'); SELECT * INTO d FROM cu15_diferencia WHERE empresa_id=p_empresa AND operacion_id=o.id FOR UPDATE; IF NOT FOUND OR d.estado<>'PENDIENTE' THEN RAISE EXCEPTION 'diferencia inexistente o ya pagada' USING ERRCODE='23514'; END IF;
 IF ref IS NULL OR NOT EXISTS(SELECT 1 FROM pago WHERE empresa_id=p_empresa AND venta_id=o.venta_id AND estado='APROBADO' AND referencia_transaccion=ref AND monto>=d.importe) THEN RAISE EXCEPTION 'cobro no verificado por CU13/CU14' USING ERRCODE='55000'; END IF;
 UPDATE cu15_diferencia SET estado='PAGADO',medio=NULLIF(p->>'medio',''),comprobante=ref,actor_id=p_actor,pagado_en=now() WHERE id=d.id; RETURN jsonb_build_object('ok',true,'estado','PAGADO','importe',d.importe);
END $$;

CREATE OR REPLACE FUNCTION public.cu15_inspeccionar(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE o record;x jsonb;c text;
BEGIN SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'EDITAR'); IF o.estado<>'APROBADA' THEN RAISE EXCEPTION 'inspección no permitida' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(p->'items')='array' THEN FOR x IN SELECT * FROM jsonb_array_elements(p->'items') LOOP c:=x->>'condicion'; IF c NOT IN ('APTO','NO_APTO','CUARENTENA') THEN RAISE EXCEPTION 'condición inválida' USING ERRCODE='22023'; END IF; UPDATE cu15_item SET condicion=c,restock_autorizado=(c='APTO' AND COALESCE((x->>'restock_autorizado')::boolean,false)),observacion_inspeccion=x->>'observacion' WHERE empresa_id=p_empresa AND operacion_id=o.id AND id=(x->>'item_id')::bigint; IF NOT FOUND THEN RAISE EXCEPTION 'ítem de inspección inexistente' USING ERRCODE='23514'; END IF; END LOOP;
 ELSE c:=p->>'condicion'; IF c NOT IN ('APTO','NO_APTO','CUARENTENA') THEN RAISE EXCEPTION 'condición inválida' USING ERRCODE='22023'; END IF; UPDATE cu15_item SET condicion=c,restock_autorizado=(c='APTO' AND COALESCE((p->>'restock_autorizado')::boolean,false)),observacion_inspeccion=p->>'observacion' WHERE empresa_id=p_empresa AND operacion_id=o.id; END IF;
 IF EXISTS(SELECT 1 FROM cu15_item WHERE empresa_id=p_empresa AND operacion_id=o.id AND condicion IS NULL) THEN RAISE EXCEPTION 'falta inspeccionar un ítem' USING ERRCODE='23514'; END IF;
 UPDATE cu15_operacion SET estado='INSPECCIONADA',actor_inspecciona_id=p_actor,fecha_inspeccion=now() WHERE id=o.id; INSERT INTO cu15_outbox(empresa_id,operacion_id,cliente_id,tipo) VALUES(p_empresa,o.id,o.cliente_id,'CU15_PRENDA_RECIBIDA') ON CONFLICT DO NOTHING; RETURN jsonb_build_object('ok',true,'operacion_id',o.id,'estado','INSPECCIONADA');
END $$;

CREATE OR REPLACE FUNCTION public.cu15_transicion(p_empresa bigint,p_actor bigint,p_id bigint,p_siguiente text,p_motivo text DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE o record;
BEGIN SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=p_id FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,CASE WHEN p_siguiente='APROBADA' OR p_siguiente='RECHAZADA' THEN 'EDITAR' ELSE 'EJECUTAR' END); IF (o.estado,p_siguiente) NOT IN (('SOLICITADA','APROBADA'),('SOLICITADA','RECHAZADA')) THEN RAISE EXCEPTION 'transición no permitida' USING ERRCODE='23514'; END IF; UPDATE cu15_operacion SET estado=p_siguiente,motivo=CASE WHEN p_siguiente='RECHAZADA' THEN COALESCE(p_motivo,motivo) ELSE motivo END,actor_aprueba_id=CASE WHEN p_siguiente='APROBADA' THEN p_actor ELSE actor_aprueba_id END WHERE id=p_id; RETURN jsonb_build_object('ok',true,'operacion_id',p_id,'estado',p_siguiente); END $$;
CREATE OR REPLACE FUNCTION public.cu15_aprobar(bigint,bigint,jsonb) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ SELECT public.cu15_transicion($1,$2,($3->>'operacion_id')::bigint,'APROBADA',NULL) $$;
CREATE OR REPLACE FUNCTION public.cu15_rechazar(bigint,bigint,jsonb) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ SELECT public.cu15_transicion($1,$2,($3->>'operacion_id')::bigint,'RECHAZADA',$3->>'motivo') $$;

/* Cierre atómico: venta, líneas, inventario y pagos se bloquean; el orden de
   variantes evita deadlocks. Si falla una sola operación, PostgreSQL revierte todo. */
CREATE OR REPLACE FUNCTION public.cu15_completar(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE o record;i record;io record;inw record;d record;ori numeric:=0;sus numeric:=0;dif numeric:=0;qty int;mov bigint;
BEGIN
 SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint FOR UPDATE; IF NOT FOUND OR o.estado<>'INSPECCIONADA' THEN RAISE EXCEPTION 'operación no inspeccionada' USING ERRCODE='23514'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'EJECUTAR');
 PERFORM 1 FROM venta WHERE empresa_id=p_empresa AND id=o.venta_id FOR UPDATE;
 FOR i IN SELECT * FROM cu15_item WHERE empresa_id=p_empresa AND operacion_id=o.id ORDER BY LEAST(variante_original_id,COALESCE(variante_nueva_id,variante_original_id)),GREATEST(variante_original_id,COALESCE(variante_nueva_id,variante_original_id)),id FOR UPDATE LOOP
  ori:=ori+i.cantidad*i.precio_original; IF i.condicion<>'APTO' THEN CONTINUE; END IF;
  IF o.tipo='CAMBIO' THEN IF i.variante_nueva_id IS NULL OR i.precio_nuevo IS NULL THEN RAISE EXCEPTION 'cambio sin cotización vigente' USING ERRCODE='23514'; END IF; sus:=sus+i.cantidad*i.precio_nuevo; END IF;
 END LOOP;
 dif:=GREATEST(ROUND(sus-ori,2),0); IF dif>0 THEN SELECT * INTO d FROM cu15_diferencia WHERE empresa_id=p_empresa AND operacion_id=o.id FOR UPDATE; IF NOT FOUND OR d.importe<>dif OR d.estado<>'PAGADO' THEN RAISE EXCEPTION 'cobro de diferencia pendiente o no coincide' USING ERRCODE='55000'; END IF; END IF;
 FOR i IN SELECT * FROM cu15_item WHERE empresa_id=p_empresa AND operacion_id=o.id AND condicion='APTO' ORDER BY LEAST(variante_original_id,COALESCE(variante_nueva_id,variante_original_id)),GREATEST(variante_original_id,COALESCE(variante_nueva_id,variante_original_id)),id LOOP
  IF i.restock_autorizado THEN SELECT * INTO io FROM inventario WHERE empresa_id=p_empresa AND sucursal_id=o.sucursal_recepcion_id AND variante_producto_id=i.variante_original_id ORDER BY id LIMIT 1 FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'inventario de recepción inexistente' USING ERRCODE='23514'; END IF; UPDATE inventario SET stock_actual=stock_actual+i.cantidad,fecha_actualizacion=now() WHERE id=io.id; INSERT INTO movimiento_inventario(inventario_id,usuario_id,tipo,cantidad,observaciones,referencia_tipo,referencia_id) VALUES(io.id,p_actor,'DEVOLUCION',i.cantidad,'CU15 restock APTO','CU15',o.id) RETURNING id INTO mov; UPDATE cu15_item SET movimiento_entrada_id=mov,restock_ejecutado=true WHERE id=i.id; END IF;
  IF o.tipo='CAMBIO' THEN SELECT * INTO inw FROM inventario WHERE empresa_id=p_empresa AND sucursal_id=o.sucursal_recepcion_id AND variante_producto_id=i.variante_nueva_id ORDER BY id LIMIT 1 FOR UPDATE; IF NOT FOUND OR inw.stock_actual-inw.stock_reservado<i.cantidad THEN RAISE EXCEPTION 'stock sustituto insuficiente' USING ERRCODE='23514'; END IF; UPDATE inventario SET stock_actual=stock_actual-i.cantidad,fecha_actualizacion=now() WHERE id=inw.id; INSERT INTO movimiento_inventario(inventario_id,usuario_id,tipo,cantidad,observaciones,referencia_tipo,referencia_id) VALUES(inw.id,p_actor,'AJUSTE_SALIDA',i.cantidad,'CU15 sustitución','CU15',o.id) RETURNING id INTO mov; UPDATE cu15_item SET movimiento_salida_id=mov WHERE id=i.id; END IF;
 END LOOP;
 IF o.tipo='SIN_CAMBIO' THEN INSERT INTO cu15_reembolso(empresa_id,operacion_id,importe_elegible,importe_reembolso,importe_retenido) VALUES(p_empresa,o.id,ROUND(ori,2),ROUND(ori*.50,2),ROUND(ori-ROUND(ori*.50,2),2)) ON CONFLICT DO NOTHING; END IF;
 UPDATE cu15_operacion SET estado='COMPLETADA',actor_completa_id=p_actor,fecha_completada=now() WHERE id=o.id;
 INSERT INTO cu15_nota(empresa_id,operacion_id,numero,tipo,importe_origen,importe_sustituto,diferencia,importe_reembolsable,importe_retenido) VALUES(p_empresa,o.id,nextval('public.cu15_nota_numero_seq'),o.tipo,ROUND(ori,2),ROUND(sus,2),dif,CASE WHEN o.tipo='SIN_CAMBIO' THEN ROUND(ori*.50,2) END,CASE WHEN o.tipo='SIN_CAMBIO' THEN ROUND(ori-ROUND(ori*.50,2),2) END);
 INSERT INTO cu15_outbox(empresa_id,operacion_id,cliente_id,tipo) VALUES(p_empresa,o.id,o.cliente_id,CASE WHEN o.tipo='CAMBIO' THEN 'CU15_CAMBIO_COMPLETADO' ELSE 'CU15_DEVOLUCION_COMPLETADA' END) ON CONFLICT DO NOTHING;
 RETURN jsonb_build_object('ok',true,'operacion_id',o.id,'estado','COMPLETADA','importe_origen',ROUND(ori,2),'importe_sustituto',ROUND(sus,2),'diferencia',dif);
END $$;

CREATE OR REPLACE FUNCTION public.cu15_confirmar_reembolso(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE o record;r record;ref text:=NULLIF(btrim(p->>'comprobante'),'');
BEGIN SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'EJECUTAR'); SELECT * INTO r FROM cu15_reembolso WHERE empresa_id=p_empresa AND operacion_id=o.id FOR UPDATE; IF NOT FOUND OR r.estado<>'PENDIENTE' OR ref IS NULL THEN RAISE EXCEPTION 'reembolso no verificable' USING ERRCODE='55000'; END IF; IF NOT EXISTS(SELECT 1 FROM pago WHERE empresa_id=p_empresa AND venta_id=o.venta_id AND estado='APROBADO' AND referencia_transaccion=ref) THEN RAISE EXCEPTION 'comprobante no validado por autoridad de pagos' USING ERRCODE='55000'; END IF; UPDATE cu15_reembolso SET estado='ENTREGADO',comprobante=ref,metodo=NULLIF(p->>'metodo',''),actor_entrega_id=p_actor,entregado_en=now() WHERE id=r.id; RETURN jsonb_build_object('ok',true,'estado','ENTREGADO'); END $$;

CREATE OR REPLACE FUNCTION public.cu15_detalle(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE o record;r jsonb;
BEGIN SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=(p->>'operacion_id')::bigint; IF NOT FOUND THEN RAISE EXCEPTION 'operación no encontrada' USING ERRCODE='42501'; END IF; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'CONSULTAR'); SELECT jsonb_build_object('operacion_id',o.id,'venta_id',o.venta_id,'tipo',o.tipo,'estado',o.estado,'fecha_hora',o.fecha_hora,'items',COALESCE((SELECT jsonb_agg(to_jsonb(i)) FROM cu15_item i WHERE i.empresa_id=o.empresa_id AND i.operacion_id=o.id),'[]'::jsonb)) INTO r; RETURN r; END $$;
CREATE OR REPLACE FUNCTION public.cu15_listar(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE r jsonb;es_cliente boolean;lim int:=LEAST(COALESCE((p->>'limite')::int,20),100);pag int:=GREATEST(COALESCE((p->>'pagina')::int,1)-1,0);
BEGIN IF p_empresa IS DISTINCT FROM fashionstore_private.actor_empresa_id() OR p_actor IS DISTINCT FROM fashionstore_private.actor_usuario_id() THEN RAISE EXCEPTION 'actor o tenant no autorizado' USING ERRCODE='42501'; END IF; SELECT EXISTS(SELECT 1 FROM cliente WHERE empresa_id=p_empresa AND usuario_id=p_actor) INTO es_cliente; IF NOT es_cliente THEN PERFORM cu15_actor_ok(p_empresa,p_actor,NULL,NULL,'CONSULTAR'); END IF; SELECT COALESCE(jsonb_agg(jsonb_build_object('operacion_id',q.id,'venta_id',q.venta_id,'tipo',q.tipo,'estado',q.estado,'fecha_hora',q.fecha_hora)),'[]'::jsonb) INTO r FROM (SELECT o.* FROM cu15_operacion o JOIN venta v ON v.empresa_id=o.empresa_id AND v.id=o.venta_id WHERE o.empresa_id=p_empresa AND (NOT es_cliente OR v.cliente_id=(SELECT id FROM cliente WHERE empresa_id=p_empresa AND usuario_id=p_actor)) ORDER BY o.id DESC LIMIT lim OFFSET pag*lim) q; RETURN r; END $$;
CREATE OR REPLACE FUNCTION public.cu15_nota(p_empresa bigint,p_actor bigint,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ DECLARE n record;o record;r jsonb;
BEGIN SELECT * INTO n FROM cu15_nota WHERE empresa_id=p_empresa AND operacion_id=(p->>'operacion_id')::bigint; IF NOT FOUND THEN RAISE EXCEPTION 'nota no encontrada' USING ERRCODE='42501'; END IF; SELECT * INTO o FROM cu15_operacion WHERE empresa_id=p_empresa AND id=n.operacion_id; PERFORM cu15_actor_ok(p_empresa,p_actor,o.venta_id,o.sucursal_recepcion_id,'CONSULTAR'); SELECT jsonb_build_object('nota_id',n.id,'numero',n.numero::text,'tipo',n.tipo,'estado',o.estado,'venta_id',o.venta_id,'fecha_hora',o.fecha_hora,'importe_origen',n.importe_origen,'importe_sustituto',n.importe_sustituto,'diferencia',n.diferencia,'importe_reembolsable',r0.importe_reembolso,'importe_retenido',r0.importe_retenido,'reembolso_estado',r0.estado,'items',COALESCE((SELECT jsonb_agg(to_jsonb(i)) FROM cu15_item i WHERE i.empresa_id=n.empresa_id AND i.operacion_id=n.operacion_id),'[]'::jsonb),'outbox_pendiente',EXISTS(SELECT 1 FROM cu15_outbox x WHERE x.empresa_id=n.empresa_id AND x.operacion_id=n.operacion_id AND x.estado='PENDIENTE')) INTO r FROM cu15_reembolso r0 WHERE r0.empresa_id=n.empresa_id AND r0.operacion_id=n.operacion_id OR r0.id IS NULL; RETURN COALESCE(r,jsonb_build_object('nota_id',n.id,'numero',n.numero::text,'tipo',n.tipo,'estado',o.estado,'venta_id',o.venta_id,'fecha_hora',o.fecha_hora,'importe_origen',n.importe_origen,'importe_sustituto',n.importe_sustituto,'diferencia',n.diferencia,'items',COALESCE((SELECT jsonb_agg(to_jsonb(i)) FROM cu15_item i WHERE i.empresa_id=n.empresa_id AND i.operacion_id=n.operacion_id),'[]'::jsonb))); END $$;

DO $$ DECLARE f record; BEGIN FOR f IN SELECT p.oid::regprocedure AS r FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'cu15_%' LOOP EXECUTE 'REVOKE ALL ON FUNCTION '||f.r||' FROM PUBLIC,anon,authenticated'; END LOOP; END $$;
ALTER FUNCTION public.cu15_actor_ok(bigint,bigint,bigint,bigint,text) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_lineas_elegibles(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_crear(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_cotizar_diferencia(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_confirmar_diferencia(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_detalle(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_listar(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_aprobar(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_rechazar(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_inspeccionar(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_completar(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_nota(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION public.cu15_confirmar_reembolso(bigint,bigint,jsonb) OWNER TO fashionstore_sp_owner;
GRANT USAGE,SELECT ON SEQUENCE public.cu15_nota_numero_seq TO fashionstore_sp_owner;
GRANT SELECT,INSERT,UPDATE ON public.cu15_politica_empresa,public.cu15_operacion,public.cu15_item,public.cu15_diferencia,public.cu15_reembolso,public.cu15_nota,public.cu15_outbox TO fashionstore_sp_owner;
GRANT SELECT ON public.venta,public.detalle_venta,public.pago,public.cliente,public.empleado,public.usuario_empresa,public.rol,public.funcion,public.accion,public.rol_funcion,public.sucursal,public.variante_producto,public.producto,public.inventario,public.movimiento_inventario TO fashionstore_sp_owner;
GRANT EXECUTE ON FUNCTION public.cu15_lineas_elegibles(bigint,bigint,jsonb),public.cu15_crear(bigint,bigint,jsonb),public.cu15_cotizar_diferencia(bigint,bigint,jsonb),public.cu15_confirmar_diferencia(bigint,bigint,jsonb),public.cu15_detalle(bigint,bigint,jsonb),public.cu15_listar(bigint,bigint,jsonb),public.cu15_aprobar(bigint,bigint,jsonb),public.cu15_rechazar(bigint,bigint,jsonb),public.cu15_inspeccionar(bigint,bigint,jsonb),public.cu15_completar(bigint,bigint,jsonb),public.cu15_nota(bigint,bigint,jsonb),public.cu15_confirmar_reembolso(bigint,bigint,jsonb) TO fashionstore_api_login;
COMMIT;
