-- CU14/CU23/CU24 SaaS. PROPUESTA LOCAL: NO EJECUTAR SIN AUTORIZACION.
\echo 'NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA'
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';

DO $pre$
BEGIN
 IF current_setting('server_version_num')::int < 170000 THEN
  RAISE EXCEPTION 'Requiere PostgreSQL 17'; END IF;
 IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
  RAISE EXCEPTION 'Instalador sin CREATEROLE'; END IF;
 IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NULL
 OR to_regprocedure('public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)') IS NULL THEN
  RAISE EXCEPTION 'Faltan prerrequisitos CU11/CU13'; END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_sp_owner','fashionstore_api') AND
   (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb)) THEN
  RAISE EXCEPTION 'Roles base inseguros'; END IF;
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
   ('carrito','detalle_carrito','venta','detalle_venta','pago','inventario','movimiento_inventario')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  RAISE EXCEPTION 'RLS/FORCE RLS incompleto'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(ARRAY[
   'p_sp_tenant_carrito','p_sp_tenant_detalle_carrito','p_sp_tenant_venta',
   'p_sp_tenant_detalle_venta','p_sp_tenant_pago','p_sp_tenant_inventario',
   'p_sp_tenant_movimiento_inventario']) p(nombre)
   WHERE NOT EXISTS(SELECT 1 FROM pg_policies x WHERE x.schemaname='public'
    AND x.policyname=p.nombre)) THEN
  RAISE EXCEPTION 'Policies SP empresariales ausentes'; END IF;
 IF NOT has_table_privilege('fashionstore_sp_owner','public.venta','SELECT,INSERT,UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.detalle_venta','SELECT,INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.carrito','SELECT,UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.inventario','SELECT') THEN
  RAISE EXCEPTION 'ACL minima del owner SP ausente'; END IF;
 IF to_regprocedure('public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)') IS NOT NULL
 OR to_regprocedure('fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)') IS NOT NULL THEN
  RAISE EXCEPTION 'Objetos CU14 ya existen'; END IF;
 IF EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND policyname IN
   ('p_api_select_client_venta','p_api_select_client_detalle_venta',
    'p_api_select_client_pago','p_webhook_pago','p_webhook_venta',
    'p_webhook_detalle_venta','p_webhook_inventario','p_webhook_movimiento')) THEN
  RAISE EXCEPTION 'Policies CU14 ya existen; detener y auditar'; END IF;
 IF NOT has_schema_privilege(current_user,'public','CREATE')
 OR NOT has_schema_privilege(current_user,'fashionstore_private','CREATE') THEN
  RAISE EXCEPTION 'Instalador sin CREATE en schemas CU14'; END IF;
END $pre$;

DO $roles$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_webhook') THEN
  CREATE ROLE fashionstore_webhook NOLOGIN NOSUPERUSER NOBYPASSRLS
   NOCREATEDB NOCREATEROLE NOINHERIT;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_webhook_owner') THEN
  CREATE ROLE fashionstore_webhook_owner NOLOGIN NOSUPERUSER NOBYPASSRLS
   NOCREATEDB NOCREATEROLE NOINHERIT;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_webhook','fashionstore_webhook_owner') AND
   (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolinherit)) THEN
  RAISE EXCEPTION 'Roles webhook inseguros'; END IF;
 -- El instalador debe poder transferir ownership. En Supabase postgres no es
 -- superusuario: recibe ADMIN OPTION de supabase_admin con SET FALSE.
 IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members
      WHERE member=CURRENT_USER::regrole
        AND roleid='fashionstore_sp_owner'::regrole AND admin_option)
 THEN RAISE EXCEPTION 'Instalador sin ADMIN OPTION sobre sp_owner'; END IF;
 IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members
      WHERE member=CURRENT_USER::regrole
        AND roleid='fashionstore_webhook_owner'::regrole AND admin_option)
 THEN RAISE EXCEPTION 'Instalador sin ADMIN OPTION sobre webhook_owner'; END IF;
END $roles$;

CREATE OR REPLACE PROCEDURE public.sp_crear_venta_digital_tenant(
 IN p_empresa_id bigint, IN p_usuario_id bigint, IN p_cliente_id bigint,
 IN p_carrito_id bigint, IN p_canal text, INOUT p_venta_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $p$
DECLARE
 v_carrito record; v_linea record; v_total numeric(12,2):=0; v_canal text;
 v_total_lineas integer; v_validas integer; v_insertadas integer:=0;
BEGIN
 IF public.fs_verificar_actor_reserva(p_empresa_id,p_usuario_id) IS NOT TRUE THEN
  RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501'; END IF;
 IF p_empresa_id IS NULL OR p_empresa_id<=0
    OR p_usuario_id IS NULL OR p_usuario_id<=0
    OR p_cliente_id IS NULL OR p_cliente_id<=0
    OR p_carrito_id IS NULL OR p_carrito_id<=0 THEN
  RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS (SELECT 1 FROM public.cliente c WHERE c.id=p_cliente_id
   AND c.empresa_id=p_empresa_id AND c.usuario_id=p_usuario_id AND c.estado IS TRUE)
 OR NOT EXISTS (SELECT 1 FROM public.usuario_empresa ue JOIN public.rol r ON r.id=ue.rol_id
   JOIN public.suscripcion s ON s.empresa_id=ue.empresa_id AND s.estado='ACTIVA'
   JOIN public.plan_saas pl ON pl.id=s.plan_saas_id AND pl.estado IS TRUE
   WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
    AND ue.estado IS TRUE AND r.nombre='CLIENTE' AND pl.permite_ventas IS TRUE
    AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
        BETWEEN s.fecha_inicio AND s.fecha_vencimiento) THEN
  RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501'; END IF;
 v_canal:=upper(btrim(coalesce(p_canal,'')));
 IF v_canal NOT IN ('WEB','MOVIL') THEN
  RAISE EXCEPTION 'Canal invalido' USING ERRCODE='22023'; END IF;
 SELECT ca.id,ca.sucursal_id,ca.estado INTO v_carrito
 FROM public.carrito ca JOIN public.sucursal su ON su.id=ca.sucursal_id
  AND su.empresa_id=ca.empresa_id AND su.estado IS TRUE
 WHERE ca.id=p_carrito_id AND ca.empresa_id=p_empresa_id
   AND ca.cliente_id=p_cliente_id
 FOR UPDATE OF ca;
 IF NOT FOUND OR v_carrito.estado<>'ACTIVO' THEN
  RAISE EXCEPTION 'Carrito no disponible' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.venta v
   WHERE v.empresa_id=p_empresa_id AND v.carrito_id=p_carrito_id) THEN
  RAISE EXCEPTION 'Carrito ya convertido' USING ERRCODE='23505'; END IF;

 -- Contar TODAS las lineas del carrito, sin filtrar por validez. Si una linea
 -- no aparece en el JOIN de validacion, la venta se rechaza completa.
 SELECT count(*) INTO v_total_lineas
 FROM public.detalle_carrito dc WHERE dc.carrito_id=p_carrito_id;
 IF v_total_lineas=0 THEN
  RAISE EXCEPTION 'Carrito vacio' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO v_validas
 FROM public.detalle_carrito dc
 JOIN public.inventario i ON i.id=dc.inventario_id
  AND i.empresa_id=p_empresa_id AND i.sucursal_id=v_carrito.sucursal_id
 JOIN public.variante_producto vp ON vp.id=i.variante_producto_id
  AND vp.empresa_id=p_empresa_id AND vp.estado IS TRUE
 JOIN public.producto pr ON pr.id=vp.producto_id
  AND pr.empresa_id=p_empresa_id AND pr.estado IS TRUE
 JOIN public.categoria c ON c.id=pr.categoria_id
  AND c.empresa_id=p_empresa_id AND c.estado IS TRUE
 JOIN public.temporada t ON t.id=i.temporada_id
  AND t.empresa_id=p_empresa_id AND t.estado IS TRUE
 WHERE dc.empresa_id=p_empresa_id AND dc.carrito_id=p_carrito_id
   AND dc.cantidad>0 AND pr.precio>=0
   AND i.stock_actual-i.stock_reservado>=dc.cantidad;
 IF v_validas<>v_total_lineas THEN
  RAISE EXCEPTION 'Carrito con lineas invalidas' USING ERRCODE='23514'; END IF;

 INSERT INTO public.venta(
  empresa_id,cliente_id,sucursal_id,fecha_hora,canal,estado,total,carrito_id)
 VALUES(p_empresa_id,p_cliente_id,v_carrito.sucursal_id,clock_timestamp(),
  v_canal,'PENDIENTE',0,p_carrito_id)
 RETURNING id INTO p_venta_id;

 FOR v_linea IN
  SELECT dc.inventario_id,dc.cantidad,pr.precio,i.stock_actual,i.stock_reservado
  FROM public.detalle_carrito dc
  JOIN public.inventario i ON i.id=dc.inventario_id
   AND i.empresa_id=p_empresa_id AND i.sucursal_id=v_carrito.sucursal_id
  JOIN public.variante_producto vp ON vp.id=i.variante_producto_id
   AND vp.empresa_id=p_empresa_id AND vp.estado IS TRUE
  JOIN public.producto pr ON pr.id=vp.producto_id
   AND pr.empresa_id=p_empresa_id AND pr.estado IS TRUE
  JOIN public.categoria c ON c.id=pr.categoria_id
   AND c.empresa_id=p_empresa_id AND c.estado IS TRUE
  JOIN public.temporada t ON t.id=i.temporada_id
   AND t.empresa_id=p_empresa_id AND t.estado IS TRUE
  WHERE dc.empresa_id=p_empresa_id AND dc.carrito_id=p_carrito_id
  ORDER BY dc.inventario_id
 LOOP
  IF v_linea.cantidad<=0 OR v_linea.precio<0
     OR v_linea.stock_actual-v_linea.stock_reservado<v_linea.cantidad THEN
   RAISE EXCEPTION 'Stock o precio invalido' USING ERRCODE='23514'; END IF;
  INSERT INTO public.detalle_venta(
   empresa_id,venta_id,inventario_id,cantidad,precio_unitario)
  VALUES(p_empresa_id,p_venta_id,v_linea.inventario_id,
   v_linea.cantidad,v_linea.precio);
  v_total:=v_total+(v_linea.precio*v_linea.cantidad);
  v_insertadas:=v_insertadas+1;
 END LOOP;
 IF v_insertadas<>v_total_lineas OR v_total<=0 THEN
  RAISE EXCEPTION 'Venta digital incompleta' USING ERRCODE='23514'; END IF;
 UPDATE public.venta SET total=v_total
 WHERE id=p_venta_id AND empresa_id=p_empresa_id;
 UPDATE public.carrito SET estado='CONVERTIDO',fecha_actualizacion=clock_timestamp()
 WHERE id=p_carrito_id AND empresa_id=p_empresa_id AND estado='ACTIVO';
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Carrito cambiado concurrentemente' USING ERRCODE='23514';
 END IF;
END $p$;

CREATE OR REPLACE PROCEDURE public.sp_registrar_intencion_stripe_tenant(
 IN p_empresa_id bigint, IN p_usuario_id bigint, IN p_venta_id bigint,
 IN p_payment_intent_id text, IN p_moneda text, INOUT p_pago_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $p$
DECLARE v_venta record; v_pago record;
BEGIN
 IF public.fs_verificar_actor_reserva(p_empresa_id,p_usuario_id) IS NOT TRUE THEN
  RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501'; END IF;
 IF p_empresa_id IS NULL OR p_empresa_id<=0
    OR p_usuario_id IS NULL OR p_usuario_id<=0
    OR p_venta_id IS NULL OR p_venta_id<=0 THEN
  RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501'; END IF;
 IF p_payment_intent_id IS NULL OR btrim(p_payment_intent_id)=''
    OR p_payment_intent_id !~ '^pi_[A-Za-z0-9_]+$'
    OR p_moneda IS NULL OR lower(btrim(p_moneda))<>'bob' THEN
  RAISE EXCEPTION 'Referencia Stripe invalida' USING ERRCODE='22023'; END IF;

 SELECT ve.id,ve.total,ve.estado,ve.canal INTO v_venta
 FROM public.venta ve
 JOIN public.cliente c ON c.id=ve.cliente_id
  AND c.empresa_id=ve.empresa_id AND c.estado IS TRUE
 WHERE ve.id=p_venta_id AND ve.empresa_id=p_empresa_id
   AND c.usuario_id=p_usuario_id
 FOR UPDATE OF ve;
 IF NOT FOUND OR v_venta.estado<>'PENDIENTE'
    OR v_venta.canal NOT IN ('WEB','MOVIL') OR v_venta.total<=0 THEN
  RAISE EXCEPTION 'Venta digital invalida' USING ERRCODE='23514'; END IF;

 -- La referencia es unica global. Se consulta SIN filtrar empresa para poder
 -- rechazar cruces en lugar de convertirlos en un 23505 opaco.
 SELECT pa.id,pa.empresa_id,pa.venta_id,pa.monto,pa.estado,pa.pasarela
 INTO v_pago
 FROM public.pago pa
 WHERE pa.referencia_transaccion=p_payment_intent_id
 FOR UPDATE OF pa;
 IF FOUND THEN
  IF v_pago.empresa_id<>p_empresa_id OR v_pago.venta_id<>p_venta_id
     OR v_pago.pasarela<>'STRIPE' OR v_pago.monto<>v_venta.total THEN
   RAISE EXCEPTION 'Referencia Stripe inconsistente' USING ERRCODE='23514';
  END IF;
  IF v_pago.estado='PENDIENTE' THEN
   p_pago_id:=v_pago.id; RETURN;
  END IF;
  -- La venta ya se exige PENDIENTE arriba; una venta COMPLETADA nunca llega
  -- aqui (el servicio la rechaza antes de crear una nueva intencion).
  RAISE EXCEPTION 'Referencia Stripe no reutilizable' USING ERRCODE='23514';
 END IF;

 BEGIN
  INSERT INTO public.pago(
   empresa_id,venta_id,fecha_hora,monto,metodo,estado,
   referencia_transaccion,pasarela)
  VALUES(p_empresa_id,p_venta_id,clock_timestamp(),v_venta.total,
   'TARJETA','PENDIENTE',p_payment_intent_id,'STRIPE')
  RETURNING id INTO p_pago_id;
 EXCEPTION WHEN unique_violation THEN
  -- RLS puede ocultar la fila de otra empresa; el UNIQUE global sigue siendo
  -- la autoridad final. Se traduce a un rechazo de dominio estable.
  RAISE EXCEPTION 'Referencia Stripe inconsistente' USING ERRCODE='23514';
 END;
END $p$;

CREATE OR REPLACE PROCEDURE fashionstore_private.sp_evento_stripe_tenant(
 IN p_payment_intent_id text, IN p_evento text, IN p_monto_minimo bigint,
 IN p_moneda text, INOUT p_procesado bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $p$
DECLARE v_pago record; v_venta record; d record; i record; v_evento text;
BEGIN
 p_procesado:=0;
 IF p_payment_intent_id IS NULL OR btrim(p_payment_intent_id)=''
    OR p_payment_intent_id !~ '^pi_[A-Za-z0-9_]+$' THEN
  RAISE EXCEPTION 'Referencia Stripe invalida' USING ERRCODE='22023'; END IF;
 v_evento:=lower(btrim(coalesce(p_evento,'')));
 IF v_evento='' THEN
  RAISE EXCEPTION 'Evento Stripe invalido' USING ERRCODE='22023'; END IF;

 SELECT pa.id,pa.empresa_id,pa.venta_id,pa.monto,pa.estado,pa.pasarela
 INTO v_pago
 FROM public.pago pa
 WHERE pa.referencia_transaccion=p_payment_intent_id
 FOR UPDATE OF pa;
 IF NOT FOUND THEN RETURN; END IF;
 IF v_pago.pasarela<>'STRIPE' THEN
  RAISE EXCEPTION 'Pago no Stripe' USING ERRCODE='23514'; END IF;

 SELECT ve.* INTO v_venta FROM public.venta ve
 WHERE ve.id=v_pago.venta_id AND ve.empresa_id=v_pago.empresa_id
 FOR UPDATE OF ve;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Venta no encontrada' USING ERRCODE='P0002'; END IF;
 IF v_pago.monto<>v_venta.total THEN
  RAISE EXCEPTION 'Monto inconsistente con la venta' USING ERRCODE='23514';
 END IF;

 IF v_evento='payment_intent.processing' THEN
  p_procesado:=1; RETURN; END IF;
 IF v_evento='payment_intent.payment_failed' THEN
  IF v_pago.estado='PENDIENTE' THEN
   UPDATE public.pago SET estado='RECHAZADO' WHERE id=v_pago.id; END IF;
  p_procesado:=1; RETURN;
 END IF;
 IF v_evento='payment_intent.canceled' THEN
  IF v_pago.estado='PENDIENTE' THEN
   UPDATE public.pago SET estado='ANULADO' WHERE id=v_pago.id; END IF;
  p_procesado:=1; RETURN;
 END IF;
 IF v_evento='payment_intent.refunded' THEN
  -- Solo un reembolso total y en la moneda de la venta puede marcar
  -- REEMBOLSADO. Un importe parcial, nulo o en otra moneda se rechaza antes
  -- de tocar el estado comercial.
  IF p_moneda IS NULL OR p_monto_minimo IS NULL
     OR lower(btrim(p_moneda))<>'bob'
     OR p_monto_minimo<>round(v_pago.monto*100)::bigint THEN
   RAISE EXCEPTION 'Reembolso no total o moneda invalida' USING ERRCODE='23514';
  END IF;
  IF v_pago.estado='REEMBOLSADO' AND v_venta.estado='CANCELADA' THEN
   p_procesado:=1; RETURN; END IF;
  IF v_venta.estado='COMPLETADA' THEN
   -- Un refund fuera de orden no retrocede una venta finalizada.
   p_procesado:=1; RETURN; END IF;
  IF v_venta.estado IN ('PENDIENTE','CANCELADA') AND v_pago.estado IN
     ('PENDIENTE','APROBADO','RECHAZADO','ANULADO','REEMBOLSADO') THEN
   UPDATE public.pago SET estado='REEMBOLSADO' WHERE id=v_pago.id;
   UPDATE public.venta SET estado='CANCELADA'
   WHERE id=v_venta.id AND estado IN ('PENDIENTE','CANCELADA');
   p_procesado:=1; RETURN;
  END IF;
  RAISE EXCEPTION 'Estado de reembolso invalido' USING ERRCODE='23514';
 END IF;
 IF v_evento<>'payment_intent.succeeded' THEN RETURN; END IF;

 -- Importe/moneda solo se exigen para eventos que representan dinero.
 IF p_moneda IS NULL OR p_monto_minimo IS NULL
    OR lower(btrim(p_moneda))<>'bob'
    OR p_monto_minimo<>round(v_pago.monto*100)::bigint THEN
  RAISE EXCEPTION 'Monto o moneda Stripe no coincide' USING ERRCODE='23514';
 END IF;

 IF v_pago.estado='APROBADO' AND v_venta.estado='COMPLETADA' THEN
  p_procesado:=1; RETURN; END IF;
 IF v_pago.estado='REEMBOLSADO' AND v_venta.estado='CANCELADA' THEN
  p_procesado:=1; RETURN; END IF;
 IF v_pago.estado<>'PENDIENTE' OR v_venta.estado<>'PENDIENTE'
    OR v_venta.canal NOT IN ('WEB','MOVIL') THEN
  RAISE EXCEPTION 'Estado comercial invalido' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.detalle_venta dv
   WHERE dv.empresa_id=v_pago.empresa_id AND dv.venta_id=v_venta.id) THEN
  RAISE EXCEPTION 'Venta sin detalles' USING ERRCODE='23514'; END IF;

 FOR d IN
  SELECT dv.inventario_id,dv.cantidad FROM public.detalle_venta dv
  WHERE dv.empresa_id=v_pago.empresa_id AND dv.venta_id=v_venta.id
  ORDER BY dv.inventario_id
 LOOP
  SELECT i2.stock_actual,i2.stock_reservado,i2.sucursal_id INTO i
  FROM public.inventario i2
  WHERE i2.id=d.inventario_id AND i2.empresa_id=v_pago.empresa_id
  FOR UPDATE OF i2;
  IF NOT FOUND OR i.sucursal_id<>v_venta.sucursal_id
     OR i.stock_actual-i.stock_reservado<d.cantidad THEN
   RAISE EXCEPTION 'Stock insuficiente' USING ERRCODE='23514'; END IF;
  UPDATE public.inventario i2
  SET stock_actual=i2.stock_actual-d.cantidad,
      fecha_actualizacion=clock_timestamp()
  WHERE i2.id=d.inventario_id AND i2.empresa_id=v_pago.empresa_id;
  INSERT INTO public.movimiento_inventario(
   empresa_id,inventario_id,usuario_id,tipo,cantidad,fecha_hora,
   observaciones,referencia_tipo,referencia_id)
  VALUES(v_pago.empresa_id,d.inventario_id,NULL,'SALIDA_VENTA',d.cantidad,
   clock_timestamp(),'Venta digital confirmada por webhook Stripe',
   'VENTA',v_venta.id);
 END LOOP;

 UPDATE public.pago SET estado='APROBADO' WHERE id=v_pago.id;
 UPDATE public.venta SET estado='COMPLETADA' WHERE id=v_venta.id;
 p_procesado:=1;
END $p$;

-- Lectura directa solo del cliente propietario; se combina por OR con las
-- policies administrativas preexistentes, sin ampliarlas.
CREATE POLICY p_api_select_client_venta ON public.venta FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  (SELECT fashionstore_private.actor_posee_cliente(cliente_id)));
CREATE POLICY p_api_select_client_detalle_venta ON public.detalle_venta FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND EXISTS(
  SELECT 1 FROM public.venta v WHERE v.id=venta_id AND v.empresa_id=empresa_id
   AND (SELECT fashionstore_private.actor_posee_cliente(v.cliente_id))));
CREATE POLICY p_api_select_client_pago ON public.pago FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND EXISTS(
  SELECT 1 FROM public.venta v WHERE v.id=venta_id AND v.empresa_id=empresa_id
   AND (SELECT fashionstore_private.actor_posee_cliente(v.cliente_id))));

-- Autoridad interna del webhook: owner NOLOGIN, sin BYPASSRLS. Solo el
-- procedimiento privado tiene EXECUTE para el rol funcional webhook.
CREATE POLICY p_webhook_pago ON public.pago FOR ALL TO fashionstore_webhook_owner USING(true) WITH CHECK(true);
CREATE POLICY p_webhook_venta ON public.venta FOR ALL TO fashionstore_webhook_owner USING(true) WITH CHECK(true);
CREATE POLICY p_webhook_detalle_venta ON public.detalle_venta FOR SELECT TO fashionstore_webhook_owner USING(true);
CREATE POLICY p_webhook_inventario ON public.inventario FOR ALL TO fashionstore_webhook_owner USING(true) WITH CHECK(true);
CREATE POLICY p_webhook_movimiento ON public.movimiento_inventario FOR INSERT TO fashionstore_webhook_owner WITH CHECK(true);

GRANT USAGE ON SCHEMA public,fashionstore_private TO fashionstore_webhook,fashionstore_webhook_owner;
GRANT SELECT,UPDATE ON public.pago,public.venta,public.inventario TO fashionstore_webhook_owner;
GRANT SELECT ON public.detalle_venta TO fashionstore_webhook_owner;
GRANT INSERT ON public.movimiento_inventario TO fashionstore_webhook_owner;
GRANT USAGE,SELECT ON SEQUENCE public.movimiento_inventario_id_seq TO fashionstore_webhook_owner;
GRANT SELECT,INSERT,UPDATE ON public.venta,public.detalle_venta,public.pago,public.carrito TO fashionstore_sp_owner;
GRANT USAGE,SELECT ON SEQUENCE public.venta_id_seq,public.detalle_venta_id_seq,public.pago_id_seq TO fashionstore_sp_owner;

REVOKE ALL ON PROCEDURE public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint) FROM PUBLIC;
REVOKE ALL ON PROCEDURE public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint) FROM PUBLIC;
REVOKE ALL ON PROCEDURE fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint) TO fashionstore_api;
GRANT EXECUTE ON PROCEDURE public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint) TO fashionstore_api;
GRANT EXECUTE ON PROCEDURE fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint) TO fashionstore_webhook;

-- Marcador verificable antes de transferir ownership.
COMMENT ON PROCEDURE public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)
 IS 'fashionstore-cu14-v2:venta-digital';
COMMENT ON PROCEDURE public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)
 IS 'fashionstore-cu14-v2:intencion-stripe';
COMMENT ON PROCEDURE fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)
 IS 'fashionstore-cu14-v2:webhook-stripe';

-- PostgreSQL 17: membresias y CREATE estrictamente temporales. Se preserva
-- cualquier concesion preexistente de CURRENT_USER (SET FALSE) y las filas de
-- otros grantors. Nunca se revoca una membresia preexistente.
CREATE TEMP TABLE cu14_membresias_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid IN ('fashionstore_sp_owner'::regrole,
                 'fashionstore_webhook_owner'::regrole);
CREATE TEMP TABLE cu14_set_temporal (
 rol name PRIMARY KEY, existente boolean NOT NULL,
 admin_previo boolean, inherit_previo boolean, set_previo boolean
) ON COMMIT DROP;
CREATE TEMP TABLE cu14_creates_temporales (
 rol name, esquema name, PRIMARY KEY (rol,esquema)
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu14_preparar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v_admin boolean; v_inherit boolean; v_set boolean;
BEGIN
 SELECT m.admin_option,m.inherit_option,m.set_option
 INTO v_admin,v_inherit,v_set
 FROM pg_auth_members m
 WHERE m.member=CURRENT_USER::regrole
   AND m.roleid=p_rol::regrole
   AND m.grantor=CURRENT_USER::regrole;
 IF FOUND THEN
  IF v_set IS NOT TRUE THEN
   EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE',
    p_rol,
    CASE WHEN v_admin THEN 'ADMIN OPTION,' ELSE '' END,
    CASE WHEN v_inherit THEN 'TRUE' ELSE 'FALSE' END);
  END IF;
  INSERT INTO pg_temp.cu14_set_temporal(
   rol,existente,admin_previo,inherit_previo,set_previo)
  VALUES(p_rol,true,v_admin,v_inherit,v_set)
  ON CONFLICT (rol) DO UPDATE SET
   existente=EXCLUDED.existente,admin_previo=EXCLUDED.admin_previo,
   inherit_previo=EXCLUDED.inherit_previo,set_previo=EXCLUDED.set_previo;
 ELSE
  EXECUTE format('GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE',p_rol);
  INSERT INTO pg_temp.cu14_set_temporal(
   rol,existente,admin_previo,inherit_previo,set_previo)
  VALUES(p_rol,false,NULL,NULL,NULL)
  ON CONFLICT (rol) DO UPDATE SET
   existente=EXCLUDED.existente,admin_previo=EXCLUDED.admin_previo,
   inherit_previo=EXCLUDED.inherit_previo,set_previo=EXCLUDED.set_previo;
 END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu14_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v record;
BEGIN
 SELECT * INTO v FROM pg_temp.cu14_set_temporal WHERE rol=p_rol;
 IF NOT FOUND THEN RETURN; END IF;
 IF v.existente THEN
  EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s',
   p_rol,
   CASE WHEN v.admin_previo THEN 'ADMIN OPTION,' ELSE '' END,
   CASE WHEN v.inherit_previo THEN 'TRUE' ELSE 'FALSE' END,
   CASE WHEN v.set_previo THEN 'TRUE' ELSE 'FALSE' END);
 ELSE
  EXECUTE format('REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER',p_rol);
 END IF;
END $fn$;

DO $grant_create$
BEGIN
 IF NOT has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
  GRANT CREATE ON SCHEMA public TO fashionstore_sp_owner;
  INSERT INTO cu14_creates_temporales VALUES('fashionstore_sp_owner','public');
 END IF;
 IF NOT has_schema_privilege('fashionstore_webhook_owner','fashionstore_private','CREATE') THEN
  GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_webhook_owner;
  INSERT INTO cu14_creates_temporales VALUES('fashionstore_webhook_owner','fashionstore_private');
 END IF;
END $grant_create$;

DO $temp_set$
BEGIN
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
  PERFORM pg_temp.cu14_preparar_set('fashionstore_sp_owner');
 END IF;
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_webhook_owner','SET') THEN
  PERFORM pg_temp.cu14_preparar_set('fashionstore_webhook_owner');
 END IF;
END $temp_set$;

ALTER PROCEDURE public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint) OWNER TO fashionstore_sp_owner;
ALTER PROCEDURE public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint) OWNER TO fashionstore_sp_owner;
ALTER PROCEDURE fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint) OWNER TO fashionstore_webhook_owner;

DO $restore_set$
BEGIN
 PERFORM pg_temp.cu14_restaurar_set('fashionstore_sp_owner');
 PERFORM pg_temp.cu14_restaurar_set('fashionstore_webhook_owner');
END $restore_set$;

DO $revoke_create$
BEGIN
 IF EXISTS(SELECT 1 FROM cu14_creates_temporales
   WHERE rol='fashionstore_sp_owner' AND esquema='public') THEN
  REVOKE CREATE ON SCHEMA public FROM fashionstore_sp_owner; END IF;
 IF EXISTS(SELECT 1 FROM cu14_creates_temporales
   WHERE rol='fashionstore_webhook_owner' AND esquema='fashionstore_private') THEN
  REVOKE CREATE ON SCHEMA fashionstore_private FROM fashionstore_webhook_owner; END IF;
END $revoke_create$;

DO $verify_ownership$
BEGIN
 IF EXISTS (
  SELECT 1 FROM cu14_membresias_previas p
  WHERE NOT EXISTS (
   SELECT 1 FROM pg_auth_members m
   WHERE m.roleid=p.roleid AND m.member=p.member AND m.grantor=p.grantor
     AND m.admin_option=p.admin_option AND m.inherit_option=p.inherit_option
     AND m.set_option=p.set_option)) THEN
  RAISE EXCEPTION 'Membresia preexistente alterada'; END IF;
 IF EXISTS (
  SELECT 1 FROM pg_auth_members m
  WHERE m.member=CURRENT_USER::regrole
    AND m.roleid IN ('fashionstore_sp_owner'::regrole,
                     'fashionstore_webhook_owner'::regrole)
    AND NOT EXISTS (SELECT 1 FROM cu14_membresias_previas p
     WHERE p.roleid=m.roleid AND p.member=m.member AND p.grantor=m.grantor)) THEN
  RAISE EXCEPTION 'Membresia temporal no revocada'; END IF;
 IF has_schema_privilege('fashionstore_sp_owner','public','CREATE')
 OR has_schema_privilege('fashionstore_webhook_owner','fashionstore_private','CREATE') THEN
  RAISE EXCEPTION 'CREATE residual en owners CU14'; END IF;
END $verify_ownership$;

COMMIT;
