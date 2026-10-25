-- FashionStore 5AJ - ENSAYO DE RESERVA SOLO EN CLUSTER AISLADO PG18: 127.0.0.1:55432.
-- JAMAS desplegar en las cinco empresas de 5432 ni Supabase.
-- PROCEDURE sp_lab_* NO ES la de produccion; actor SQL ficticio 910001/910002,
-- no sustituye JWT, usuario_empresa, planes ni contrato CU16 definitivo.
-- Crea dos carritos ficticios ADICIONALES para competir por stock 5 de empresa-lab-a.
-- Script NO IDEMPOTENTE: unicamente una instalacion.
BEGIN;
SET LOCAL statement_timeout='60s';
SET LOCAL lock_timeout='5s';

DO $pre$
DECLARE v RECORD; n bigint;
BEGIN
  IF current_database()<>'fashionstore_rls_lab_5u'
     OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'
     OR inet_server_port()<>55432
     OR session_user<>'postgres' OR current_user<>'postgres'
     OR current_setting('server_version_num')::int/10000<>18
     OR lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/'))
        <> 'c:/si2_parcial1/fashionstore-rls-lab-5v-data'
  THEN RAISE EXCEPTION '5AJ: laboratorio equivocado'; END IF;

  IF (SELECT count(*) FROM pg_class c JOIN pg_namespace ns ON c.relnamespace=ns.oid
      WHERE ns.nspname='public' AND c.relkind='r') <> 46
    OR (SELECT count(*) FROM pg_constraint co
        JOIN pg_class c ON co.conrelid=c.oid JOIN pg_namespace ns ON c.relnamespace=ns.oid
        WHERE ns.nspname='public' AND co.contype='f'
          AND co.convalidated AND co.conname LIKE 'fk_tenant_%') <> 48
    OR (SELECT count(*) FROM pg_policies WHERE schemaname='public') <> 35
    OR (SELECT count(*) FROM public.empresa) <> 2
    OR (SELECT count(*) FROM public.empresa WHERE slug IN ('empresa-lab-a','empresa-lab-b')) <> 2
    OR (SELECT count(*) FROM public.carrito) <> 2
    OR (SELECT count(*) FROM public.detalle_carrito) <> 2
    OR (SELECT count(*) FROM public.inventario) <> 2
    OR (SELECT count(*) FROM public.reserva) <> 2
    OR (SELECT count(*) FROM public.detalle_reserva) <> 2
    OR (SELECT count(*) FROM public.inventario WHERE stock_actual=5 AND stock_reservado=0) <> 2
    OR (SELECT count(*) FROM pg_roles WHERE rolname IN ('fashionstore_5ab_a','fashionstore_5ab_b')
        AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolinherit) <> 2
  THEN RAISE EXCEPTION '5AJ: estado anterior distinto, no instalar'; END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON p.pronamespace=ns.oid
             WHERE ns.nspname='public' AND p.proname='sp_lab_reservar_5aj')
  THEN RAISE EXCEPTION '5AJ: procedimiento ya existente, no repetir'; END IF;
  IF EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
             AND policyname LIKE 'fs_lab_5aj_%')
  THEN RAISE EXCEPTION '5AJ: policies ya existentes'; END IF;
END $pre$;

DO $fixtures$
DECLARE
  va bigint;
  vi bigint;
  vs bigint;
  vcliente bigint;
  vcarrito bigint;
  j integer;
BEGIN
  SELECT id INTO STRICT va FROM public.empresa WHERE slug='empresa-lab-a';
  SELECT id INTO STRICT vi FROM public.inventario WHERE empresa_id=va;
  SELECT id INTO STRICT vs FROM public.sucursal WHERE empresa_id=va;

  FOR j IN 2..3 LOOP
     INSERT INTO public.cliente(empresa_id,nombre,apellido)
     VALUES(va,'Cliente-5AJ-A'||j,'Unicamente Laboratorio')
     RETURNING id INTO vcliente;
     INSERT INTO public.carrito(empresa_id,cliente_id,sucursal_id,estado)
     VALUES(va,vcliente,vs,'ACTIVO')
     RETURNING id INTO vcarrito;
     INSERT INTO public.detalle_carrito(empresa_id,carrito_id,inventario_id,cantidad)
     VALUES(va,vcarrito,vi,4);
  END LOOP;
  IF (SELECT count(*) FROM public.carrito WHERE empresa_id=va)<>3
     OR (SELECT count(*) FROM public.detalle_carrito WHERE empresa_id=va)<>3
  THEN RAISE EXCEPTION '5AJ: carritos de competencia incompletos'; END IF;
END $fixtures$;

-- Policies RLS DML para las sesiones reales de A/B, vinculadas a session_user
-- a traves del SELECT protegido en empresa; nunca dependen de GUC editable.
CREATE POLICY fs_lab_5aj_reserva_insert ON public.reserva
  FOR INSERT TO fashionstore_5ab_a, fashionstore_5ab_b
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

CREATE POLICY fs_lab_5aj_detalle_reserva_insert ON public.detalle_reserva
  FOR INSERT TO fashionstore_5ab_a, fashionstore_5ab_b
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

CREATE POLICY fs_lab_5aj_movimiento_insert ON public.movimiento_inventario
  FOR INSERT TO fashionstore_5ab_a, fashionstore_5ab_b
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

CREATE POLICY fs_lab_5aj_bitacora_insert ON public.bitacora
  FOR INSERT TO fashionstore_5ab_a, fashionstore_5ab_b
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

CREATE POLICY fs_lab_5aj_inventario_update ON public.inventario
  FOR UPDATE TO fashionstore_5ab_a, fashionstore_5ab_b
  USING (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END))
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

CREATE POLICY fs_lab_5aj_carrito_update ON public.carrito
  FOR UPDATE TO fashionstore_5ab_a, fashionstore_5ab_b
  USING (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END))
  WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
      CASE session_user WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                        WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b' END));

GRANT SELECT ON public.detalle_carrito
  TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT INSERT (empresa_id,cliente_id,sucursal_id,carrito_id,fecha_atencion,estado)
  ON public.reserva TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT INSERT (empresa_id,reserva_id,inventario_id,cantidad)
  ON public.detalle_reserva TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT INSERT (empresa_id,inventario_id,tipo,cantidad)
  ON public.movimiento_inventario TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT INSERT ON public.bitacora TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT UPDATE (stock_reservado) ON public.inventario
  TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT UPDATE (estado,fecha_actualizacion) ON public.carrito
  TO fashionstore_5ab_a, fashionstore_5ab_b;

DO $seqgrant$
DECLARE t text; s text;
BEGIN
  FOREACH t IN ARRAY ARRAY['reserva','detalle_reserva','movimiento_inventario','bitacora']
  LOOP
     s := pg_get_serial_sequence('public.'||t,'id');
     IF s IS NULL THEN
        RAISE EXCEPTION '5AJ: secuencia de ID ausente: %',t;
     END IF;
     EXECUTE format('GRANT USAGE ON SEQUENCE %s TO fashionstore_5ab_a, fashionstore_5ab_b',s);
  END LOOP;
END $seqgrant$;

-- Este procedimiento es un ARNES SQL de ensayo. No sustituye CU16 real.
-- Una sesion A solo puede ser actor ficticio 910001 y empresa-lab-a;
-- una sesion B solo puede ser 910002 y empresa-lab-b.
CREATE PROCEDURE public.sp_lab_reservar_5aj(
    IN p_empresa_id bigint,
    IN p_carrito_id bigint,
    IN p_actor_id bigint,
    IN p_fecha_atencion timestamptz,
    IN p_simular_fallo boolean,
    INOUT p_reserva_id bigint
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=public,pg_temp
AS $lab$
DECLARE
  v_slug text;
  v_actor bigint;
  v_empresa bigint;
  v_cliente bigint;
  v_sucursal bigint;
  v_estado text;
  v_det record;
  v_actual integer;
  v_reservado integer;
  v_qty integer;
  v_rows integer;
BEGIN
  v_slug:=CASE session_user
    WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
    WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b'
    ELSE NULL END;
  v_actor:=CASE session_user
    WHEN 'fashionstore_5ab_a' THEN 910001::bigint
    WHEN 'fashionstore_5ab_b' THEN 910002::bigint
    ELSE NULL END;
  IF v_slug IS NULL OR p_actor_id IS DISTINCT FROM v_actor
     OR p_empresa_id IS NULL OR p_empresa_id<=0
     OR p_carrito_id IS NULL OR p_carrito_id<=0
     OR p_fecha_atencion IS NULL OR p_fecha_atencion<=clock_timestamp()
     OR NOT row_security_active('public.reserva'::regclass)
     OR NOT row_security_active('public.inventario'::regclass)
  THEN RAISE EXCEPTION '5AJ: actor/parametros no autorizados' USING ERRCODE='42501'; END IF;
  SELECT id INTO v_empresa FROM public.empresa WHERE slug=v_slug;
  IF v_empresa IS NULL OR p_empresa_id<>v_empresa THEN
     RAISE EXCEPTION '5AJ: empresa no autorizada' USING ERRCODE='42501';
  END IF;

  SELECT ca.cliente_id,ca.sucursal_id,ca.estado
    INTO v_cliente,v_sucursal,v_estado
    FROM public.carrito ca
    WHERE ca.id=p_carrito_id AND ca.empresa_id=v_empresa
    FOR UPDATE;
  IF NOT FOUND OR v_estado<>'ACTIVO' THEN
    RAISE EXCEPTION '5AJ: carrito no disponible' USING ERRCODE='P0002';
  END IF;
  IF EXISTS(SELECT 1 FROM public.reserva WHERE carrito_id=p_carrito_id) THEN
    RAISE EXCEPTION '5AJ: carrito ya reservado' USING ERRCODE='23505';
  END IF;
  SELECT count(*) INTO v_qty FROM public.detalle_carrito
    WHERE carrito_id=p_carrito_id AND empresa_id=v_empresa;
  IF v_qty=0 THEN
    RAISE EXCEPTION '5AJ: carrito vacio' USING ERRCODE='23514';
  END IF;
  INSERT INTO public.reserva(empresa_id,cliente_id,sucursal_id,carrito_id,fecha_atencion,estado)
    VALUES(v_empresa,v_cliente,v_sucursal,p_carrito_id,p_fecha_atencion,'PENDIENTE')
    RETURNING id INTO p_reserva_id;

  FOR v_det IN
    SELECT dc.inventario_id,dc.cantidad
      FROM public.detalle_carrito dc
      WHERE dc.carrito_id=p_carrito_id AND dc.empresa_id=v_empresa
      ORDER BY dc.inventario_id
  LOOP
    SELECT i.stock_actual,i.stock_reservado
      INTO v_actual,v_reservado
      FROM public.inventario i
      WHERE i.id=v_det.inventario_id
        AND i.empresa_id=v_empresa AND i.sucursal_id=v_sucursal
      FOR UPDATE;
    IF NOT FOUND OR v_det.cantidad<=0 OR
       v_det.cantidad > v_actual-v_reservado THEN
      RAISE EXCEPTION '5AJ: stock insuficiente' USING ERRCODE='23514';
    END IF;
    UPDATE public.inventario
      SET stock_reservado=stock_reservado+v_det.cantidad
      WHERE id=v_det.inventario_id AND empresa_id=v_empresa
        AND stock_actual-stock_reservado>=v_det.cantidad;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows<>1 THEN
      RAISE EXCEPTION '5AJ: stock modificado concurrentemente' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.detalle_reserva(empresa_id,reserva_id,inventario_id,cantidad)
      VALUES(v_empresa,p_reserva_id,v_det.inventario_id,v_det.cantidad);
    INSERT INTO public.movimiento_inventario(empresa_id,inventario_id,tipo,cantidad)
      VALUES(v_empresa,v_det.inventario_id,'RESERVA',v_det.cantidad);
  END LOOP;
  UPDATE public.carrito SET estado='CONVERTIDO',fecha_actualizacion=clock_timestamp()
    WHERE id=p_carrito_id AND empresa_id=v_empresa AND estado='ACTIVO';
  GET DIAGNOSTICS v_rows=ROW_COUNT;
  IF v_rows<>1 THEN
     RAISE EXCEPTION '5AJ: carrito modificado concurrentemente' USING ERRCODE='23514';
  END IF;
  IF COALESCE(p_simular_fallo,false) THEN
     RAISE EXCEPTION '5AJ: fallo intencional despues de escribir' USING ERRCODE='23514';
  END IF;
END
$lab$;

REVOKE ALL ON PROCEDURE public.sp_lab_reservar_5aj(
   bigint,bigint,bigint,timestamptz,boolean,bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_lab_reservar_5aj(
   bigint,bigint,bigint,timestamptz,boolean,bigint)
   TO fashionstore_5ab_a, fashionstore_5ab_b;

DO $final$
BEGIN
 IF (SELECT count(*) FROM pg_policies WHERE schemaname='public')<>41
    OR (SELECT count(*) FROM public.carrito)<>4
    OR (SELECT count(*) FROM public.detalle_carrito)<>4
    OR (SELECT count(*) FROM public.reserva)<>2
    OR (SELECT count(*) FROM public.inventario WHERE stock_actual=5 AND stock_reservado=0)<>2
 THEN
    RAISE EXCEPTION '5AJ: instalacion incompleta, rollback';
 END IF;
 RAISE NOTICE '5AJ PRECOMMIT OK: 41 policies, 4 carritos, 2 reservas iniciales';
END $final$;
COMMIT;
SELECT '5AJ INSTALADO LAB: SP DE ENSAYO, NUNCA PARA PRODUCCION' AS resultado;
