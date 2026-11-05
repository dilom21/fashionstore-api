-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA 5BX
-- CU03/CU04: DICCIONARIOS LEGIBLES POR EL PROPIETARIO DEL SP DE PERSONAL
--
-- Por que existe esta migracion
-- -----------------------------
-- La Etapa I (00550) instalo la politica `catalogo_lectura_saas` sobre los
-- diccionarios del esquema historico (`rol`, `rol_funcion`, `modulo`, `accion`,
-- `funcion`, `ciudad`, `color`, `talla`) para que el principal restringido
-- pudiera leerlos bajo FORCE RLS.
--
-- Esa migracion ya contemplaba a `saas_personal_owner`, pero el rol se crea en
-- la Etapa F (00800), que se aplica DESPUES. El guard
-- `CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles ...)` omitio el rol y la
-- politica quedo con cuatro destinatarios.
--
-- Consecuencia real medida en staging 5BX sobre el esquema original:
--   `sp_alta_personal_tenant` (SECURITY DEFINER, propietario
--   `saas_personal_owner`) evalua `saas.tiene_permiso_tenant(...)`, que lee
--   `public.rol`. Sin politica para el propietario, esa lectura devuelve 0
--   filas, el default RBAC no se resuelve y la respuesta es
--   403 PERMISO_INSUFICIENTE para un ADMINISTRADOR legitimo.
--
-- Alcance
-- -------
--   * Recrea SOLO la politica `catalogo_lectura_saas` de esos ocho
--     diccionarios, ahora con los cinco principales del SaaS.
--   * No concede nada a `anon`, `authenticated` ni `service_role`.
--   * No toca datos, ni privilegios de escritura, ni el resto de politicas.
--   * Idempotente: puede reaplicarse sin efectos adicionales.
--   * Fail-closed: si falta un objeto o un rol esperado, aborta sin cambios.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_faltantes TEXT := '';
  v_rol TEXT;
BEGIN
  FOREACH v_rol IN ARRAY ARRAY[
    'fs_saas_runtime', 'saas_rls_owner', 'saas_admin_owner',
    'saas_actor_sql_owner', 'saas_personal_owner'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol) THEN
      v_faltantes := v_faltantes || ' rol:' || v_rol;
    END IF;
  END LOOP;

  IF NOT EXISTS (SELECT 1 FROM pg_roles
                  WHERE rolname = 'saas_personal_owner' AND NOT rolcanlogin) THEN
    RAISE EXCEPTION '5BX: saas_personal_owner debe existir y ser NOLOGIN';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles
                  WHERE rolname = 'saas_personal_owner'
                    AND NOT rolsuper AND NOT rolbypassrls) THEN
    RAISE EXCEPTION '5BX: saas_personal_owner no puede ser superusuario ni BYPASSRLS';
  END IF;

  IF v_faltantes <> '' THEN
    RAISE EXCEPTION '5BX: faltan roles requeridos:%', v_faltantes;
  END IF;

  IF to_regclass('public.rol') IS NULL
     OR to_regclass('public.rol_funcion') IS NULL
     OR to_regclass('public.modulo') IS NULL
     OR to_regclass('public.accion') IS NULL
     OR to_regclass('public.funcion') IS NULL
     OR to_regclass('public.ciudad') IS NULL
     OR to_regclass('public.color') IS NULL
     OR to_regclass('public.talla') IS NULL THEN
    RAISE EXCEPTION '5BX: faltan diccionarios del esquema historico';
  END IF;

  IF to_regprocedure('saas.tiene_permiso_tenant(bigint,bigint,text,text)') IS NULL THEN
    RAISE EXCEPTION '5BX: falta saas.tiene_permiso_tenant (00800)';
  END IF;
END
$preflight$;

DO $catalogos$
DECLARE
  v_roles TEXT[] := ARRAY[
    COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
             'fs_saas_runtime'),
    COALESCE(NULLIF(current_setting('saas.rls_owner_role', TRUE), ''),
             'saas_rls_owner'),
    'saas_admin_owner', 'saas_actor_sql_owner', 'saas_personal_owner'
  ];
  v_tablas CONSTANT TEXT[] := ARRAY[
    'rol', 'rol_funcion', 'modulo', 'accion', 'funcion', 'ciudad', 'color', 'talla'
  ];
  v_tabla TEXT;
  v_rol TEXT;
  v_destinos TEXT;
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('DROP POLICY IF EXISTS catalogo_lectura_saas ON public.%I', v_tabla);

    v_destinos := '';
    FOREACH v_rol IN ARRAY v_roles LOOP
      CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);
      v_destinos := v_destinos
        || CASE WHEN v_destinos = '' THEN '' ELSE ', ' END
        || quote_ident(v_rol);
    END LOOP;

    IF v_destinos = '' THEN
      RAISE EXCEPTION '5BX: sin destinatarios para la politica de %', v_tabla;
    END IF;

    EXECUTE format(
      'CREATE POLICY catalogo_lectura_saas ON public.%I AS PERMISSIVE '
      'FOR SELECT TO %s USING (true)', v_tabla, v_destinos);
  END LOOP;
END
$catalogos$;

-- Verificacion fail-closed: cada diccionario debe admitir a los cinco roles.
DO $verificacion$
DECLARE
  v_tablas CONSTANT TEXT[] := ARRAY[
    'rol', 'rol_funcion', 'modulo', 'accion', 'funcion', 'ciudad', 'color', 'talla'
  ];
  v_tabla TEXT;
  v_roles_en_politica TEXT;
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    SELECT string_agg(r.rolname, ',' ORDER BY r.rolname)
      INTO v_roles_en_politica
      FROM pg_policies p
      CROSS JOIN LATERAL unnest(p.roles) AS r(rolname)
     WHERE p.schemaname = 'public' AND p.tablename = v_tabla
       AND p.policyname = 'catalogo_lectura_saas';

    IF v_roles_en_politica IS NULL
       OR position('saas_personal_owner' IN v_roles_en_politica) = 0 THEN
      RAISE EXCEPTION
        '5BX: la politica de % no incluye a saas_personal_owner (%)',
        v_tabla, COALESCE(v_roles_en_politica, 'sin politica');
    END IF;
  END LOOP;
END
$verificacion$;

COMMIT;
