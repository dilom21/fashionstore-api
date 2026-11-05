-- 5BO / Etapa F - Metadatos de catalogo SOLO LECTURA
--
-- Se ejecuta con psql contra el entorno AUTORIZADO (nunca loopback, nunca
-- produccion sin permiso escrito) junto a `pg_dump --schema-only`.
-- Todas las consultas son SELECT sobre catalogos del sistema.
--
-- Objetivo: comparar el esquema real con la reconstruccion 5BL y auditar
-- propietarios, SECURITY DEFINER, ACL, triggers, search_path y politicas RLS
-- ANTES de cualquier migracion. No modifica nada.

\pset pager off
\echo '=== VERSION DEL SERVIDOR ==='
SELECT version(), current_database(), current_user;

\echo ''
\echo '=== TABLAS (schema|tabla|rls|force_rls|filas_estimadas) ==='
SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
       c.reltuples::bigint
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r','p') AND n.nspname IN ('public','saas','saas_admin')
 ORDER BY 1, 2;

\echo ''
\echo '=== COLUMNAS CON empresa_id (aislamiento logico) ==='
SELECT table_schema, table_name
  FROM information_schema.columns
 WHERE column_name = 'empresa_id'
   AND table_schema IN ('public','saas')
 ORDER BY 1, 2;

\echo ''
\echo '=== FUNCIONES Y RUTINAS (schema|nombre|tipo|security_definer|propietario|search_path) ==='
SELECT n.nspname,
       p.proname,
       CASE p.prokind WHEN 'f' THEN 'funcion' WHEN 'p' THEN 'procedimiento'
                      ELSE p.prokind::text END AS tipo,
       p.prosecdef,
       pg_get_userbyid(p.proowner) AS propietario,
       COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>') AS configuracion
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('public','saas','saas_admin')
 ORDER BY 1, 2;

\echo ''
\echo '=== PRIVILEGIOS EFECTIVOS SOBRE RUTINAS (rol|rutina|EXECUTE) ==='
SELECT r.rolname, n.nspname || '.' || p.proname AS rutina,
       has_function_privilege(r.oid, p.oid, 'EXECUTE') AS puede_ejecutar
  FROM pg_roles r
  CROSS JOIN pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('saas','saas_admin')
   AND r.rolname IN ('fs_saas_runtime','saas_admin_runtime','saas_admin_owner',
                     'saas_personal_owner','saas_rls_owner','saas_actor_sql_owner')
 ORDER BY 1, 2;

\echo ''
\echo '=== TRIGGERS (tabla|trigger|funcion|definer|habilitado) ==='
SELECT c.relname AS tabla,
       t.tgname AS trigger,
       p.proname AS funcion,
       p.prosecdef AS definer,
       t.tgenabled AS habilitado
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE NOT t.tgisinternal
   AND n.nspname IN ('public','saas')
 ORDER BY 1, 2;

\echo ''
\echo '=== POLITICAS RLS (schema|tabla|politica|comando|roles|using|with_check) ==='
SELECT schemaname, tablename, policyname, cmd,
       array_to_string(roles, ',') AS roles,
       COALESCE(qual, '<ninguno>') AS using_expr,
       COALESCE(with_check, '<ninguno>') AS with_check_expr
  FROM pg_policies
 WHERE schemaname IN ('public','saas')
 ORDER BY 1, 2, 3;

\echo ''
\echo '=== PROPIETARIOS DE TABLAS (schema|tabla|propietario) ==='
SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r','p','v','m')
   AND n.nspname IN ('public','saas','saas_admin')
 ORDER BY 1, 2;

\echo ''
\echo '=== ROLES DEL CLUSTER (rol|login|super|bypassrls|inherit|createdb|createrole) ==='
SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit,
       rolcreatedb, rolcreaterole
  FROM pg_roles
 WHERE rolname NOT LIKE 'pg\_%'
 ORDER BY rolname;

\echo ''
\echo '=== EXTENSIONES INSTALADAS ==='
SELECT extname, extversion, n.nspname AS schema
  FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
 ORDER BY extname;

\echo ''
\echo '=== VISTAS Y SU security_invoker ==='
SELECT n.nspname, c.relname,
       COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                  WHERE option_name = 'security_invoker'), '<no definido>') AS security_invoker
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'v' AND n.nspname IN ('public','saas')
 ORDER BY 1, 2;

\echo ''
\echo '=== CONTEO DE OBJETOS (comparacion con la reconstruccion 5BL) ==='
SELECT
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.relkind='r' AND n.nspname='public') AS tablas_public,
  (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE NOT t.tgisinternal AND n.nspname='public') AS triggers_public,
  (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public') AS rutinas_public,
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.relkind='v' AND n.nspname='public') AS vistas_public;
