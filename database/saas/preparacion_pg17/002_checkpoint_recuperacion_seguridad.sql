-- SOLO LECTURA. Clasifica una instalacion de seguridad completa o parcial.
-- Ejecutar despues de cualquier error/perdida de conexion y archivar la salida.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='30s';

WITH m AS (
 SELECT
  (SELECT count(*) FROM pg_roles WHERE rolname=ANY(ARRAY[
   'fashionstore_api','fashionstore_bootstrap','fashionstore_platform_admin',
   'fashionstore_context_owner','fashionstore_bootstrap_owner',
   'fashionstore_sp_owner','fashionstore_audit_owner'])) AS roles_fs,
  to_regnamespace('fashionstore_private') IS NOT NULL AS private_schema,
  to_regprocedure('fashionstore_private.actor_claims()') IS NOT NULL AS actor_claims,
  to_regprocedure('fashionstore_private.bootstrap_credencial(text)') IS NOT NULL AS bootstrap,
  (SELECT count(*) FROM pg_policies WHERE schemaname='public') AS policies,
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity) AS force_rls,
  (SELECT count(*) FROM pg_trigger
   WHERE tgname LIKE 'trg_%_asignar_vanter_legacy' AND NOT tgisinternal) AS triggers_045,
  EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.empresa'::regclass
   AND tgname='trg_empresa_guard_legacy_vanter_monotenant' AND NOT tgisinternal) AS guard_045,
  to_regprocedure('public.fn_guard_legacy_vanter_monotenant()') IS NOT NULL AS function_045,
  CASE WHEN EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api') THEN
   has_table_privilege('fashionstore_api','public.producto','SELECT')
  ELSE false END AS api_acl,
  CASE WHEN EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_platform_admin') THEN
   has_table_privilege('fashionstore_platform_admin','public.empresa','INSERT')
  ELSE false END AS platform_acl,
  coalesce((SELECT r.rolname='fashionstore_audit_owner'
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   JOIN pg_roles r ON r.oid=p.proowner
   WHERE n.nspname='public' AND p.proname='fn_registrar_bitacora'
     AND p.prokind='f'),false) AS audit_owner
), classified AS (
 SELECT m.*,
  CASE
   WHEN roles_fs=0 AND NOT private_schema AND policies=0 AND force_rls=0
        AND triggers_045=31 AND guard_045 AND function_045 THEN 'S0_PRE_050A'
   WHEN roles_fs=7 AND private_schema AND actor_claims AND bootstrap
        AND policies=0 AND force_rls=0 AND triggers_045=31
        AND guard_045 AND NOT api_acl AND NOT platform_acl THEN 'S1_050A_COMMIT'
   WHEN roles_fs=7 AND private_schema AND actor_claims AND bootstrap
        AND policies=111 AND force_rls=46 AND triggers_045=31
        AND guard_045 AND NOT api_acl AND NOT platform_acl THEN 'S2_050B_COMMIT'
   WHEN roles_fs=7 AND private_schema AND actor_claims AND bootstrap
        AND policies=111 AND force_rls=46 AND triggers_045=31
        AND guard_045 AND api_acl AND platform_acl AND audit_owner THEN 'S3_050C_COMMIT'
   WHEN roles_fs=7 AND private_schema AND actor_claims AND bootstrap
        AND policies=111 AND force_rls=46 AND triggers_045=0
        AND NOT guard_045 AND NOT function_045
        AND api_acl AND platform_acl AND audit_owner THEN 'S4_055_COMMIT'
   ELSE 'INCONSISTENTE_DETENER'
  END AS checkpoint
 FROM m
)
SELECT checkpoint,roles_fs,private_schema,actor_claims,bootstrap,policies,
 force_rls,triggers_045,guard_045,function_045,api_acl,platform_acl,audit_owner
FROM classified;

ROLLBACK;
