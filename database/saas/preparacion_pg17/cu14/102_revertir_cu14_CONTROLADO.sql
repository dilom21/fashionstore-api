-- Reversión CU14 controlada. Solo si no hay operaciones CU14 pendientes.
-- No elimina datos, ni objetos CU11/CU12/CU13, ni membresías preexistentes.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';

DO $pre$
BEGIN
 IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
  RAISE EXCEPTION 'Instalador sin CREATEROLE'; END IF;
 -- Actividad CU14 pendiente, sin depender de la fecha actual.
 IF EXISTS (SELECT 1 FROM public.venta v
   WHERE v.canal IN ('WEB','MOVIL') AND v.estado='PENDIENTE') THEN
  RAISE EXCEPTION 'Actividad CU14 pendiente: venta digital PENDIENTE'; END IF;
 IF EXISTS (SELECT 1 FROM public.pago p
   WHERE p.pasarela='STRIPE' AND p.estado='PENDIENTE') THEN
  RAISE EXCEPTION 'Actividad CU14 pendiente: pago Stripe PENDIENTE'; END IF;
 IF EXISTS (SELECT 1 FROM public.venta v
   JOIN public.pago p ON p.venta_id=v.id AND p.empresa_id=v.empresa_id
   WHERE v.canal IN ('WEB','MOVIL') AND v.estado='CANCELADA'
     AND p.pasarela='STRIPE' AND p.estado='APROBADO') THEN
  RAISE EXCEPTION 'Compensacion CU14 pendiente'; END IF;
 IF EXISTS (SELECT 1 FROM public.venta v
   JOIN public.pago p ON p.venta_id=v.id AND p.empresa_id=v.empresa_id
   WHERE v.canal IN ('WEB','MOVIL') AND v.estado='COMPLETADA'
     AND p.pasarela='STRIPE' AND p.estado='PENDIENTE') THEN
  RAISE EXCEPTION 'Inconsistencia CU14: venta completada con pago pendiente'; END IF;

 IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE (n.nspname,p.proname) IN (
   ('public','sp_crear_venta_digital_tenant'),
   ('public','sp_registrar_intencion_stripe_tenant'),
   ('fashionstore_private','sp_evento_stripe_tenant')))<>3 THEN
  RAISE EXCEPTION 'Procedencia o integridad CU14 no verificable'; END IF;
 IF obj_description(to_regprocedure('public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)'),'pg_proc')
      <>'fashionstore-cu14-v2:venta-digital'
 OR obj_description(to_regprocedure('public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)'),'pg_proc')
      <>'fashionstore-cu14-v2:intencion-stripe'
 OR obj_description(to_regprocedure('fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)'),'pg_proc')
      <>'fashionstore-cu14-v2:webhook-stripe' THEN
  RAISE EXCEPTION 'Procedencia o integridad CU14 no verificable'; END IF;
 IF (SELECT count(*) FROM pg_policies WHERE schemaname='public' AND policyname IN
   ('p_api_select_client_venta','p_api_select_client_detalle_venta',
    'p_api_select_client_pago','p_webhook_pago','p_webhook_venta',
    'p_webhook_detalle_venta','p_webhook_inventario','p_webhook_movimiento'))<>8 THEN
  RAISE EXCEPTION 'Policies CU14 incompletas; detener'; END IF;
 IF (SELECT count(*) FROM pg_roles WHERE rolname IN
   ('fashionstore_webhook','fashionstore_webhook_owner'))<>2 THEN
  RAISE EXCEPTION 'Roles CU14 incompletos; detener'; END IF;
END $pre$;

-- Snapshot de la membresía del instalador sobre el owner que sobrevive.
CREATE TEMP TABLE cu14_revert_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid='fashionstore_sp_owner'::regrole;
CREATE TEMP TABLE cu14_revert_set (
 rol name PRIMARY KEY, existente boolean NOT NULL,
 admin_previo boolean, inherit_previo boolean, set_previo boolean
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu14_revert_preparar_set(p_rol name)
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
  INSERT INTO pg_temp.cu14_revert_set(
   rol,existente,admin_previo,inherit_previo,set_previo)
  VALUES(p_rol,true,v_admin,v_inherit,v_set)
  ON CONFLICT (rol) DO UPDATE SET
   existente=EXCLUDED.existente,admin_previo=EXCLUDED.admin_previo,
   inherit_previo=EXCLUDED.inherit_previo,set_previo=EXCLUDED.set_previo;
 ELSE
  EXECUTE format('GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE',p_rol);
  INSERT INTO pg_temp.cu14_revert_set(
   rol,existente,admin_previo,inherit_previo,set_previo)
  VALUES(p_rol,false,NULL,NULL,NULL)
  ON CONFLICT (rol) DO UPDATE SET
   existente=EXCLUDED.existente,admin_previo=EXCLUDED.admin_previo,
   inherit_previo=EXCLUDED.inherit_previo,set_previo=EXCLUDED.set_previo;
 END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu14_revert_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v record;
BEGIN
 SELECT * INTO v FROM pg_temp.cu14_revert_set WHERE rol=p_rol;
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

DO $drop_sp_owner$
BEGIN
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
  PERFORM pg_temp.cu14_revert_preparar_set('fashionstore_sp_owner'); END IF;
END $drop_sp_owner$;
SET ROLE fashionstore_sp_owner;
DROP PROCEDURE public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint);
DROP PROCEDURE public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint);
RESET ROLE;
DO $restore_sp_owner$
BEGIN PERFORM pg_temp.cu14_revert_restaurar_set('fashionstore_sp_owner'); END
$restore_sp_owner$;

DO $drop_webhook_owner$
BEGIN
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_webhook_owner','SET') THEN
  PERFORM pg_temp.cu14_revert_preparar_set('fashionstore_webhook_owner'); END IF;
END $drop_webhook_owner$;
SET ROLE fashionstore_webhook_owner;
DROP PROCEDURE fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint);
RESET ROLE;
DO $restore_webhook_owner$
BEGIN PERFORM pg_temp.cu14_revert_restaurar_set('fashionstore_webhook_owner'); END
$restore_webhook_owner$;

DROP POLICY p_api_select_client_venta ON public.venta;
DROP POLICY p_api_select_client_detalle_venta ON public.detalle_venta;
DROP POLICY p_api_select_client_pago ON public.pago;
DROP POLICY p_webhook_pago ON public.pago;
DROP POLICY p_webhook_venta ON public.venta;
DROP POLICY p_webhook_detalle_venta ON public.detalle_venta;
DROP POLICY p_webhook_inventario ON public.inventario;
DROP POLICY p_webhook_movimiento ON public.movimiento_inventario;

REVOKE ALL ON SCHEMA public,fashionstore_private FROM fashionstore_webhook;
REVOKE ALL ON SCHEMA public,fashionstore_private FROM fashionstore_webhook_owner;
REVOKE ALL ON public.pago,public.venta,public.inventario,public.detalle_venta,
 public.movimiento_inventario FROM fashionstore_webhook_owner;
REVOKE ALL ON SEQUENCE public.movimiento_inventario_id_seq FROM fashionstore_webhook_owner;

DO $guard$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
   WHERE r.rolname IN ('fashionstore_webhook','fashionstore_webhook_owner'))
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
   WHERE r.rolname IN ('fashionstore_webhook','fashionstore_webhook_owner'))
 OR EXISTS (SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
   WHERE r.rolname IN ('fashionstore_webhook','fashionstore_webhook_owner'))
 OR EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
   WHERE r.rolname IN ('fashionstore_webhook','fashionstore_webhook_owner')) THEN
  RAISE EXCEPTION 'Roles webhook poseen objetos ajenos; detener'; END IF;
END $guard$;

DROP ROLE fashionstore_webhook;
DROP ROLE fashionstore_webhook_owner;

DO $post$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_auth_members m
   WHERE m.roleid='fashionstore_sp_owner'::regrole
     AND m.member=CURRENT_USER::regrole
     AND m.set_option) THEN
  RAISE EXCEPTION 'SET temporal residual en sp_owner'; END IF;
 IF EXISTS (
  SELECT 1 FROM cu14_revert_previas p
  WHERE NOT EXISTS (
   SELECT 1 FROM pg_auth_members m
   WHERE m.roleid=p.roleid AND m.member=p.member AND m.grantor=p.grantor
     AND m.admin_option=p.admin_option AND m.inherit_option=p.inherit_option
     AND m.set_option=p.set_option)) THEN
  RAISE EXCEPTION 'Membresia preexistente de sp_owner alterada'; END IF;
 IF has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
  RAISE EXCEPTION 'CREATE residual en sp_owner'; END IF;

 IF to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)') IS NULL
  OR to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)') IS NULL
  OR to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)') IS NULL
  OR to_regprocedure('public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)') IS NULL
  OR to_regprocedure('public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)') IS NULL
  OR to_regprocedure('public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)') IS NULL THEN
  RAISE EXCEPTION 'CU11/CU12/CU13 alterados por la reversion'; END IF;
 RAISE NOTICE 'CU14_REVERT=PASS';
END $post$;

COMMIT;
