-- REGISTRO DE CAMBIO YA APLICADO en Supabase fashionstore (2026-11-11).
-- Migration ID: 20261111215246_restrict_public_execution_tenant_routines_transactional_20261111
-- No reejecutar en el proyecto que ya contiene esa migracion. No usar como script
-- general sin revisar roles y privilegios. No modifica cuerpo/owner de rutinas.
-- Motivo: 7 rutinas SECURITY DEFINER public eran ejecutables por anon y authenticated.
-- PostgreSQL 17 requiere tratar como ROUTINE tanto FUNCTION como PROCEDURE.
-- Los GRANT se hacen via owners NOLOGIN temporalmente y se retiran en la misma tx.
BEGIN;
DO $preflight$
DECLARE roles_good integer;routine_good integer;
BEGIN
 IF current_user <> 'postgres' THEN RAISE EXCEPTION 'Ejecutor inesperado'; END IF;
 SELECT count(DISTINCT r.rolname) INTO roles_good
 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member JOIN pg_roles g ON g.oid=m.grantor
 WHERE r.rolname IN ('fashionstore_context_owner','fashionstore_sp_owner')
 AND u.rolname='postgres' AND g.rolname='supabase_admin'
 AND m.admin_option AND NOT m.set_option AND NOT m.inherit_option;
 IF roles_good<>2 THEN RAISE EXCEPTION 'Membresias originales inesperadas'; END IF;
 SELECT count(*) INTO routine_good FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.prosecdef AND p.proname IN ('fs_verificar_actor_reserva','sp_cancelar_reserva_tenant','sp_confirmar_reserva_tenant','sp_crear_reserva_tenant','sp_crear_venta_presencial_tenant','sp_finalizar_reserva_sin_compra_tenant','sp_registrar_pago_presencial_tenant')
 AND ((p.proname='fs_verificar_actor_reserva'
       AND pg_get_userbyid(p.proowner)='fashionstore_context_owner')
    OR(p.proname<>'fs_verificar_actor_reserva'
       AND pg_get_userbyid(p.proowner)='fashionstore_sp_owner'))
 AND has_function_privilege('anon',p.oid,'EXECUTE')
 AND has_function_privilege('authenticated',p.oid,'EXECUTE')
 AND has_function_privilege('fashionstore_api',p.oid,'EXECUTE');
 IF routine_good<>7 THEN RAISE EXCEPTION 'ACL inicial inesperada: %',routine_good; END IF;
END $preflight$;

GRANT fashionstore_context_owner TO postgres WITH SET TRUE,INHERIT FALSE;
SET LOCAL ROLE fashionstore_context_owner;
REVOKE EXECUTE ON ROUTINE public.fs_verificar_actor_reserva(bigint,bigint)
 FROM PUBLIC,anon,authenticated;
SET LOCAL ROLE NONE;
GRANT fashionstore_sp_owner TO postgres WITH SET TRUE,INHERIT FALSE;
SET LOCAL ROLE fashionstore_sp_owner;
REVOKE EXECUTE ON ROUTINE public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text) FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON ROUTINE public.sp_confirmar_reserva_tenant(bigint,bigint,bigint) FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON ROUTINE public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint) FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON ROUTINE public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint) FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON ROUTINE public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text) FROM PUBLIC,anon,authenticated;
REVOKE EXECUTE ON ROUTINE public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint) FROM PUBLIC,anon,authenticated;
SET LOCAL ROLE NONE;
REVOKE fashionstore_context_owner FROM postgres GRANTED BY postgres;
REVOKE fashionstore_sp_owner FROM postgres GRANTED BY postgres;

DO $verify$
DECLARE bad integer;good integer;
BEGIN
 SELECT count(*) INTO bad FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.prosecdef AND p.proname IN ('fs_verificar_actor_reserva','sp_cancelar_reserva_tenant','sp_confirmar_reserva_tenant','sp_crear_reserva_tenant','sp_crear_venta_presencial_tenant','sp_finalizar_reserva_sin_compra_tenant','sp_registrar_pago_presencial_tenant')
 AND (has_function_privilege('anon',p.oid,'EXECUTE')
 OR has_function_privilege('authenticated',p.oid,'EXECUTE')
 OR NOT has_function_privilege('fashionstore_api',p.oid,'EXECUTE'));
 IF bad<>0 THEN RAISE EXCEPTION 'Postflight de rutina fallo: %',bad; END IF;
 SELECT count(DISTINCT r.rolname) INTO good
 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member JOIN pg_roles g ON g.oid=m.grantor
 WHERE r.rolname IN ('fashionstore_context_owner','fashionstore_sp_owner')
 AND u.rolname='postgres' AND g.rolname='supabase_admin'
 AND m.admin_option AND NOT m.set_option AND NOT m.inherit_option;
 IF good<>2 THEN RAISE EXCEPTION 'Membresias restringidas ausentes'; END IF;
 IF EXISTS(SELECT 1 FROM pg_auth_members m
 JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member
 WHERE r.rolname IN ('fashionstore_context_owner','fashionstore_sp_owner')
 AND u.rolname='postgres' AND (m.set_option OR m.inherit_option))
 THEN RAISE EXCEPTION 'SET/INHERIT temporal persistente'; END IF;
END $verify$;
COMMIT;

-- Postflight confirmado en Supabase:
-- 7/7 anon=false, authenticated=false, fashionstore_api=true;
-- 0 concesiones SET TRUE de owners en postgres.
-- Supabase Security Advisor ya no reporta warnings 0028 ni 0029.
