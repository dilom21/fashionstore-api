-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA I (5BR) - CIERRE DE ACL
-- HERENCIA DE PRIVILEGIOS DE PLATAFORMA EN EL ESQUEMA ORIGINAL
--
-- Hallazgo que motiva esta migracion
-- ---------------------------------
-- El `pg_dump` de Supabase no solo concede privilegios a `anon`,
-- `authenticated` y `service_role` sobre los objetos existentes: ademas declara
-- `ALTER DEFAULT PRIVILEGES FOR ROLE postgres|supabase_admin IN SCHEMA public
-- GRANT ALL ON TABLES/SEQUENCES/FUNCTIONS TO ...`. Esa regla se aplica a TODO
-- objeto NUEVO creado por `postgres`, de modo que las tablas que crean
-- 20261030000600..01000 (recepcion_compra, detalle_recepcion_compra,
-- reembolso_devolucion, operacion_idempotente, intento_invitacion, ...) nacian
-- con privilegios plenos para esos tres roles de plataforma.
--
-- Que hace esta migracion (ultima del pipeline)
-- ---------------------------------------------
-- 1. Revoca los DEFAULT PRIVILEGES heredados, para que ningun objeto futuro los
--    reciba (defensa en profundidad, no solo limpieza del estado actual).
-- 2. Revoca los privilegios ya materializados sobre tablas, secuencias y
--    rutinas de `public`.
-- 3. Retira `EXECUTE` de PUBLIC en todas las rutinas de `public`. Las funciones
--    de trigger no lo necesitan para dispararse y las rutinas SaaS del runtime
--    tienen GRANT explicito por rol.
-- 4. Verifica fail-closed que no quede ninguna concesion a roles de plataforma.
--
-- No toca `postgres`, ni los propietarios NOLOGIN, ni los GRANT explicitos de
-- `fs_saas_runtime` / `saas_admin_runtime`. NO desactiva ninguna RLS.
BEGIN;

SET LOCAL lock_timeout = '5s';

-- ===========================================================================
-- 1. Default privileges heredados
-- ===========================================================================
DO $defaults_plataforma$
DECLARE
  v_duenio TEXT;
  v_rol    TEXT;
  v_tipo   TEXT;
BEGIN
  FOREACH v_duenio IN ARRAY ARRAY['postgres', 'supabase_admin'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_duenio);
    FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
      CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);
      FOREACH v_tipo IN ARRAY ARRAY['TABLES', 'SEQUENCES', 'FUNCTIONS'] LOOP
        BEGIN
          EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
            'REVOKE ALL ON %s FROM %I', v_duenio, v_tipo, v_rol);
        EXCEPTION WHEN insufficient_privilege THEN
          -- Si el rol no es miembro del duenio, el default ACL no le aplica.
          NULL;
        END;
      END LOOP;
    END LOOP;
  END LOOP;
END;
$defaults_plataforma$;

-- ===========================================================================
-- 2. Privilegios ya materializados
-- ===========================================================================
DO $acl_materializada$
DECLARE
  v_rol  TEXT;
  v_item RECORD;
BEGIN
  FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);

    FOR v_item IN
      SELECT c.oid::regclass AS objeto, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
       WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
         AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = v_rol)
    LOOP
      IF v_item.relkind = 'S' THEN
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', v_item.objeto, v_rol);
      ELSE
        EXECUTE format('REVOKE ALL ON TABLE %s FROM %I', v_item.objeto, v_rol);
      END IF;
    END LOOP;

    FOR v_item IN
      SELECT p.oid::regprocedure AS firma
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'public'
         AND has_function_privilege(v_rol, p.oid, 'EXECUTE')
    LOOP
      EXECUTE format('REVOKE ALL ON ROUTINE %s FROM %I', v_item.firma, v_rol);
    END LOOP;

    EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', v_rol);
  END LOOP;
END;
$acl_materializada$;

-- ===========================================================================
-- 3. EXECUTE implicito de PUBLIC en rutinas de public
-- ===========================================================================
-- `EXECUTE` se concede a PUBLIC por defecto al crear una funcion. Las funciones
-- de trigger no lo requieren para dispararse y las rutinas SaaS reciben GRANT
-- explicito al rol que las invoca (00400 revoca y vuelve a conceder lo minimo).
DO $execute_public$
DECLARE
  v_firma TEXT;
BEGIN
  FOR v_firma IN
    SELECT p.oid::regprocedure::TEXT
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND has_function_privilege('public', p.oid, 'EXECUTE')
  LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', v_firma);
  END LOOP;
END;
$execute_public$;

-- ===========================================================================
-- 4. Fail-closed final
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas.validar_acl_plataforma_5br()
RETURNS TABLE(violacion TEXT)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public, saas
AS $validar$
  SELECT format('privilegio de plataforma: %s sobre %s', r.rolname, c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN (VALUES ('anon'), ('authenticated'), ('service_role')) v(r)
    JOIN pg_roles r ON r.rolname = v.r
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
     AND a.grantee = r.oid
  UNION ALL
  SELECT format('default privilege de plataforma: %s %s',
                pg_get_userbyid(d.defaclrole), d.defaclobjtype)
    FROM pg_default_acl d
    JOIN pg_namespace n ON n.oid = d.defaclnamespace
    CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    JOIN pg_roles r ON r.oid = a.grantee
   WHERE n.nspname = 'public'
     AND r.rolname IN ('anon', 'authenticated', 'service_role')
  UNION ALL
  SELECT format('EXECUTE implicito de PUBLIC en %s', p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND has_function_privilege('public', p.oid, 'EXECUTE')
  UNION ALL
  SELECT format('DEFINER con propietario capaz de login: %s', p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    JOIN pg_roles r ON r.rolname = pg_get_userbyid(p.proowner)
   WHERE n.nspname = 'public' AND p.prosecdef AND r.rolcanlogin
  UNION ALL
  SELECT format('DEFINER sin search_path fijo: %s', p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public' AND p.prosecdef
     AND NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) c
                      WHERE c LIKE 'search_path=%');
$validar$;

DO $fail_closed_acl$
DECLARE
  v TEXT;
BEGIN
  SELECT string_agg(violacion, ' | ') INTO v FROM saas.validar_acl_plataforma_5br();
  IF v IS NOT NULL THEN
    RAISE EXCEPTION '5BR ACL BLOQUEADO: %', v;
  END IF;
END;
$fail_closed_acl$;

DO $acl_final$
BEGIN
  REVOKE ALL ON FUNCTION saas.validar_acl_plataforma_5br() FROM PUBLIC;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_rls_owner') THEN
    REVOKE ALL ON FUNCTION saas.validar_acl_plataforma_5br() FROM saas_rls_owner;
  END IF;
END;
$acl_final$;

COMMIT;
