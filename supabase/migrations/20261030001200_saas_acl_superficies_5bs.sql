-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA J (5BS) - ACL DE SUPERFICIES NO PUBLICAS
-- CIERRE DEL HALLAZGO R1-J1 (EXECUTE PUBLIC residual fuera de `public`)
--
-- Hallazgo que motiva esta migracion
-- ---------------------------------
-- 20261030001100 cerro los privilegios de `anon`, `authenticated` y `service_role`
-- y el `EXECUTE` implicito de `PUBLIC` **solo** para objetos de `public`. La
-- auditoria de la Etapa J encontro que `saas.es_principal_runtime()` (SECURITY
-- DEFINER, propietario NOLOGIN `saas_rls_owner`, `search_path` fijo) conservaba
-- `EXECUTE` para `PUBLIC` (`proacl` iniciaba con `=X/saas_rls_owner`), de modo que
-- `anon`, `authenticated` y `service_role` lo resolvian por herencia de PUBLIC.
-- El esquema `saas` no es superficie de PostgREST por defecto, por lo que no habia
-- explotacion directa, pero la concesion implicita contradecia el contrato de
-- minimo privilegio y el validador 5BR no la cubria.
--
-- Que hace esta migracion (ultima del pipeline)
-- --------------------------------------------
-- 1. Revoca `USAGE` de `saas`, `saas_admin` y `saas_migracion` a los roles de
--    plataforma (si existen en el destino).
-- 2. Revoca privilegios materializados de esos roles sobre tablas, secuencias,
--    vistas y rutinas de esos esquemas.
-- 3. Revoca `EXECUTE` de `PUBLIC` en **todas** las rutinas de esos esquemas.
--    Los roles legitimados conservan sus GRANT explicitos (verificado: ninguno
--    depende de PUBLIC).
-- 4. Revoca DEFAULT PRIVILEGES de `postgres`/`supabase_admin` en esos esquemas
--    para que un objeto futuro no nazca expuesto.
-- 5. Verifica fail-closed con `saas.validar_acl_superficies_5bs()`.
--
-- No toca `auth`, `storage`, `extensions`, `graphql*` ni `vault`: son esquemas de
-- plataforma Supabase, fuera del contrato de esta migracion.
-- No desactiva RLS, no borra objetos y no reabre ninguna superficie de `public`.
BEGIN;

SET LOCAL lock_timeout = '5s';

-- ===========================================================================
-- 1. USAGE de esquema
-- ===========================================================================
DO $usage_esquemas$
DECLARE
  v_rol    TEXT;
  v_esquema TEXT;
BEGIN
  FOREACH v_esquema IN ARRAY ARRAY['saas', 'saas_admin', 'saas_migracion'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = v_esquema);
    FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
      CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);
      BEGIN
        EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', v_esquema, v_rol);
      EXCEPTION WHEN insufficient_privilege THEN
        NULL;  -- la verificacion final exige el resultado, no el intento
      END;
    END LOOP;
  END LOOP;
END;
$usage_esquemas$;

-- ===========================================================================
-- 2. Privilegios ya materializados en objetos de esos esquemas
-- ===========================================================================
DO $acl_objetos$
DECLARE
  v_rol  TEXT;
  v_item RECORD;
BEGIN
  FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);

    FOR v_item IN
      SELECT c.oid::regclass AS objeto, c.relkind,
             pg_get_userbyid(c.relowner) AS duenio
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
       WHERE n.nspname IN ('saas', 'saas_admin', 'saas_migracion')
         AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
         AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = v_rol)
    LOOP
      BEGIN
        IF v_item.relkind = 'S' THEN
          EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', v_item.objeto, v_rol);
        ELSE
          EXECUTE format('REVOKE ALL ON TABLE %s FROM %I', v_item.objeto, v_rol);
        END IF;
      EXCEPTION WHEN insufficient_privilege THEN
        -- El propietario del objeto puede revocar aunque el ejecutor no lo sea.
        BEGIN
          EXECUTE format('SET LOCAL ROLE %I', v_item.duenio);
          IF v_item.relkind = 'S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', v_item.objeto, v_rol);
          ELSE
            EXECUTE format('REVOKE ALL ON TABLE %s FROM %I', v_item.objeto, v_rol);
          END IF;
          RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
          RESET ROLE;
        END;
      END;
    END LOOP;

    FOR v_item IN
      SELECT p.oid::regprocedure AS firma,
             pg_get_userbyid(p.proowner) AS duenio
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname IN ('saas', 'saas_admin', 'saas_migracion')
         AND has_function_privilege(v_rol, p.oid, 'EXECUTE')
    LOOP
      BEGIN
        EXECUTE format('REVOKE ALL ON ROUTINE %s FROM %I', v_item.firma, v_rol);
      EXCEPTION WHEN insufficient_privilege THEN
        BEGIN
          EXECUTE format('SET LOCAL ROLE %I', v_item.duenio);
          EXECUTE format('REVOKE ALL ON ROUTINE %s FROM %I', v_item.firma, v_rol);
          RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
          RESET ROLE;
        END;
      END;
    END LOOP;
  END LOOP;
END;
$acl_objetos$;

-- ===========================================================================
-- 3. EXECUTE implicito de PUBLIC en esos esquemas
-- ===========================================================================
DO $execute_public_no_publico$
DECLARE
  v_firma TEXT;
  v_duenio TEXT;
BEGIN
  FOR v_firma, v_duenio IN
    SELECT p.oid::regprocedure::TEXT, pg_get_userbyid(p.proowner)
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname IN ('saas', 'saas_admin', 'saas_migracion')
       AND has_function_privilege('public', p.oid, 'EXECUTE')
  LOOP
    BEGIN
      EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', v_firma);
    EXCEPTION WHEN insufficient_privilege THEN
      BEGIN
        EXECUTE format('SET LOCAL ROLE %I', v_duenio);
        EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', v_firma);
        RESET ROLE;
      EXCEPTION WHEN OTHERS THEN
        RESET ROLE;
      END;
    END;
  END LOOP;
END;
$execute_public_no_publico$;

-- ===========================================================================
-- 4. Default privileges de esos esquemas (defensa en profundidad)
-- ===========================================================================
DO $defaults_no_publicos$
DECLARE
  v_duenio TEXT;
  v_rol    TEXT;
  v_tipo   TEXT;
  v_esquema TEXT;
BEGIN
  FOREACH v_esquema IN ARRAY ARRAY['saas', 'saas_admin', 'saas_migracion'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = v_esquema);
    FOREACH v_duenio IN ARRAY ARRAY['postgres', 'supabase_admin'] LOOP
      CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_duenio);
      FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);
        FOREACH v_tipo IN ARRAY ARRAY['TABLES', 'SEQUENCES', 'FUNCTIONS'] LOOP
          BEGIN
            EXECUTE format(
              'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
              'REVOKE ALL ON %s FROM %I', v_duenio, v_esquema, v_tipo, v_rol);
          EXCEPTION WHEN insufficient_privilege THEN
            NULL;
          END;
        END LOOP;
      END LOOP;
    END LOOP;
  END LOOP;
END;
$defaults_no_publicos$;

-- ===========================================================================
-- 5. Verificacion fail-closed
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas.validar_acl_superficies_5bs()
RETURNS TABLE(violacion TEXT)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, saas, public
AS $validar$
  WITH superficies(nspname) AS (
    VALUES ('saas'), ('saas_admin'), ('saas_migracion')
  ),
  plataforma(rolname) AS (
    VALUES ('anon'), ('authenticated'), ('service_role')
  )
  SELECT format('privilegio de plataforma: %s sobre %s.%s',
                p.rolname, n.nspname, c.relname)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN superficies s ON s.nspname = n.nspname
    CROSS JOIN plataforma p
    JOIN pg_roles r ON r.rolname = p.rolname
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE a.grantee = r.oid
  UNION ALL
  SELECT format('EXECUTE de plataforma: %s en %s.%s',
                p.rolname, n.nspname, pr.proname)
    FROM pg_proc pr
    JOIN pg_namespace n ON n.oid = pr.pronamespace
    JOIN superficies s ON s.nspname = n.nspname
    CROSS JOIN plataforma p
    JOIN pg_roles r ON r.rolname = p.rolname
   WHERE has_function_privilege(r.oid, pr.oid, 'EXECUTE')
  UNION ALL
  SELECT format('EXECUTE implicito de PUBLIC en %s.%s', n.nspname, pr.proname)
    FROM pg_proc pr
    JOIN pg_namespace n ON n.oid = pr.pronamespace
    JOIN superficies s ON s.nspname = n.nspname
   WHERE has_function_privilege('public', pr.oid, 'EXECUTE')
  UNION ALL
  SELECT format('USAGE de esquema para plataforma: %s en %s', p.rolname, n.nspname)
    FROM pg_namespace n
    JOIN superficies s ON s.nspname = n.nspname
    CROSS JOIN plataforma p
    JOIN pg_roles r ON r.rolname = p.rolname
   WHERE has_schema_privilege(r.oid, n.oid, 'USAGE')
  UNION ALL
  SELECT format('DEFINER con propietario capaz de login: %s.%s', n.nspname, pr.proname)
    FROM pg_proc pr
    JOIN pg_namespace n ON n.oid = pr.pronamespace
    JOIN superficies s ON s.nspname = n.nspname
    JOIN pg_roles r ON r.rolname = pg_get_userbyid(pr.proowner)
   WHERE pr.prosecdef AND r.rolcanlogin
  UNION ALL
  SELECT format('DEFINER sin search_path fijo: %s.%s', n.nspname, pr.proname)
    FROM pg_proc pr
    JOIN pg_namespace n ON n.oid = pr.pronamespace
    JOIN superficies s ON s.nspname = n.nspname
   WHERE pr.prosecdef
     AND NOT EXISTS (SELECT 1 FROM unnest(pr.proconfig) c
                      WHERE c LIKE 'search_path=%');
$validar$;

-- El validador nace con EXECUTE para PUBLIC por defecto; se cierra ANTES de
-- verificarlo para que no se detecte a si mismo como violacion.
REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM PUBLIC;
DO $acl_validador_5bs$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_rls_owner') THEN
    REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM saas_rls_owner;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_admin_owner') THEN
    REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM saas_admin_owner;
  END IF;
END;
$acl_validador_5bs$;

DO $fail_closed_superficies$
DECLARE
  v TEXT;
BEGIN
  SELECT string_agg(violacion, ' | ') INTO v FROM saas.validar_acl_superficies_5bs();
  IF v IS NOT NULL THEN
    RAISE EXCEPTION '5BS ACL DE SUPERFICIES BLOQUEADO: %', v;
  END IF;
END;
$fail_closed_superficies$;

DO $acl_final_5bs$
BEGIN
  REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM PUBLIC;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_rls_owner') THEN
    REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM saas_rls_owner;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_admin_owner') THEN
    REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM saas_admin_owner;
  END IF;
END;
$acl_final_5bs$;

COMMIT;
