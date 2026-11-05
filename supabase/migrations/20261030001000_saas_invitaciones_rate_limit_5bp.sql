-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA G (5BP)
-- B9: RATE LIMIT COMPARTIDO DEL ENDPOINT PUBLICO DE ACEPTACION DE INVITACIONES
--
-- Por que existe esta migracion
-- -----------------------------
-- `POST /auth/tenant/invitaciones/aceptar` es publico por diseno: el invitado
-- aun no tiene sesion y solo dispone de un token de un solo uso de 256 bits.
-- La entropia hace inviable la fuerza bruta, pero sin limite por origen un
-- atacante puede inundar el endpoint (coste de Argon2 por peticion, presion de
-- conexiones, ruido en bitacora) y probar tokens filtrados de terceros.
--
-- Alcance deliberadamente PROPORCIONADO
-- -------------------------------------
--   * No se limita la aceptacion legitima del invitado correcto: solo cuentan
--     como fallo los intentos con token invalido/expirado/usado.
--   * Se limita por IP y por huella PARCIAL del token (los primeros 16 hex del
--     SHA-256 que ya calcula la API para buscar la invitacion). Nunca se guarda
--     el token, ni su hash completo, ni el correo, ni la IP en claro.
--   * Los contadores viven en PostgreSQL (`saas_admin.intento_invitacion`), por
--     lo que el estado es COMPARTIDO entre procesos y replicas.
--   * Toda escritura ocurre en funciones SECURITY DEFINER del propietario
--     NOLOGIN `saas_admin_owner`; el principal `saas_admin_runtime` solo recibe
--     EXECUTE y sigue sin privilegios de tabla.
--
-- Requisitos: 20261030000900 aplicada (esquema y propietario saas_admin).
-- Idempotente: puede reaplicarse sin duplicar tablas, indices ni funciones.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
BEGIN
  IF to_regclass('saas_admin.intento_auth') IS NULL THEN
    RAISE EXCEPTION
      '5BP: falta 20261030000900 (saas_admin.intento_auth). Aplicar en orden.';
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Contadores de intentos de aceptacion de invitacion
-- ===========================================================================
CREATE TABLE IF NOT EXISTS saas_admin.intento_invitacion (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ip_hash       TEXT NOT NULL,
  token_huella  TEXT NOT NULL,
  resultado     TEXT NOT NULL,
  creado        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_intento_invitacion_resultado CHECK (resultado IN ('OK', 'FALLA')),
  CONSTRAINT ck_intento_invitacion_ip CHECK (ip_hash ~ '^[0-9a-f]{16,64}$'),
  CONSTRAINT ck_intento_invitacion_token CHECK (token_huella ~ '^[0-9a-f]{8,64}$')
);

COMMENT ON TABLE saas_admin.intento_invitacion IS
  '5BP/B9: intentos de aceptacion de invitacion. Solo IP hasheada y huella parcial del token; nunca token, hash completo, correo ni IP en claro.';

COMMENT ON COLUMN saas_admin.intento_invitacion.token_huella IS
  'Primeros 16 hex del SHA-256 del token. Identificador no reversible y suficiente para agrupar intentos del mismo token.';

CREATE INDEX IF NOT EXISTS ix_intento_invitacion_ip
  ON saas_admin.intento_invitacion (ip_hash, creado DESC);
CREATE INDEX IF NOT EXISTS ix_intento_invitacion_token
  ON saas_admin.intento_invitacion (token_huella, creado DESC);
CREATE INDEX IF NOT EXISTS ix_intento_invitacion_creado
  ON saas_admin.intento_invitacion (creado DESC);

-- ===========================================================================
-- 2. Consulta de estado (solo lectura, sin efectos)
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas_admin.estado_invitacion(
  p_ip_hash      TEXT,
  p_token_huella TEXT,
  p_max_ip       INTEGER,
  p_max_token    INTEGER,
  p_ventana_seg  INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_ip          BIGINT;
  v_token       BIGINT;
  v_max_ip      INTEGER := GREATEST(COALESCE(p_max_ip, 30), 1);
  v_max_token   INTEGER := GREATEST(COALESCE(p_max_token, 10), 1);
  v_ventana     INTEGER := GREATEST(COALESCE(p_ventana_seg, 300), 30);
  v_reintentar  INTEGER := v_ventana;
BEGIN
  IF p_ip_hash IS NULL OR p_ip_hash !~ '^[0-9a-f]{16,64}$'
     OR p_token_huella IS NULL OR p_token_huella !~ '^[0-9a-f]{8,64}$' THEN
    -- Entrada no conforme: se informa no bloqueado; el llamador registra el
    -- fallo con su propio identificador saneado.
    RETURN jsonb_build_object(
      'permitido', TRUE, 'bloqueado', FALSE,
      'fallas_ip', 0, 'fallas_token', 0,
      'ventana_seg', v_ventana, 'reintentar_en', 0);
  END IF;

  SELECT count(*) INTO v_ip
    FROM saas_admin.intento_invitacion i
   WHERE i.ip_hash = p_ip_hash
     AND i.resultado = 'FALLA'
     AND i.creado > now() - make_interval(secs => v_ventana);

  SELECT count(*) INTO v_token
    FROM saas_admin.intento_invitacion i
   WHERE i.token_huella = p_token_huella
     AND i.resultado = 'FALLA'
     AND i.creado > now() - make_interval(secs => v_ventana);

  -- Segundos hasta que la falla mas antigua salga de la ventana.
  SELECT COALESCE(
           CEIL(EXTRACT(EPOCH FROM (
             min(i.creado) + make_interval(secs => v_ventana) - now())))::INTEGER,
           0)
    INTO v_reintentar
    FROM saas_admin.intento_invitacion i
   WHERE i.resultado = 'FALLA'
     AND (i.ip_hash = p_ip_hash OR i.token_huella = p_token_huella)
     AND i.creado > now() - make_interval(secs => v_ventana)
     AND (i.ip_hash = p_ip_hash AND v_ip >= v_max_ip
          OR i.token_huella = p_token_huella AND v_token >= v_max_token);

  RETURN jsonb_build_object(
    'permitido', (v_ip < v_max_ip AND v_token < v_max_token),
    'bloqueado', (v_ip >= v_max_ip OR v_token >= v_max_token),
    'fallas_ip', v_ip,
    'fallas_token', v_token,
    'ventana_seg', v_ventana,
    'reintentar_en', GREATEST(COALESCE(v_reintentar, 0), 1));
END;
$fn$;

-- ===========================================================================
-- 3. Registro de intentos (unico punto de escritura)
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas_admin.registrar_intento_invitacion(
  p_ip_hash      TEXT,
  p_token_huella TEXT,
  p_resultado    TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
  IF p_resultado NOT IN ('OK', 'FALLA') THEN
    RETURN jsonb_build_object('registrado', FALSE, 'motivo', 'resultado_invalido');
  END IF;
  IF p_ip_hash IS NULL OR p_ip_hash !~ '^[0-9a-f]{16,64}$'
     OR p_token_huella IS NULL OR p_token_huella !~ '^[0-9a-f]{8,64}$' THEN
    RETURN jsonb_build_object('registrado', FALSE, 'motivo', 'identificador_invalido');
  END IF;
  INSERT INTO saas_admin.intento_invitacion (ip_hash, token_huella, resultado)
  VALUES (p_ip_hash, p_token_huella, p_resultado);
  RETURN jsonb_build_object('registrado', TRUE);
END;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.limpiar_intentos_invitacion(p_dias INTEGER)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_dias INTEGER := GREATEST(COALESCE(p_dias, 7), 1);
  v_borradas BIGINT;
BEGIN
  WITH borradas AS (
    DELETE FROM saas_admin.intento_invitacion
     WHERE creado < now() - make_interval(days => v_dias)
     RETURNING 1
  )
  SELECT count(*) INTO v_borradas FROM borradas;
  RETURN v_borradas;
END;
$fn$;

-- ===========================================================================
-- 4. ACL: EXECUTE solo al principal global; cero privilegios de tabla
-- ===========================================================================
DO $acl$
DECLARE
  v_admin  text := COALESCE(NULLIF(current_setting('saas.admin_runtime_role', TRUE), ''),
                            'saas_admin_runtime');
  v_aowner text := COALESCE(NULLIF(current_setting('saas.admin_owner_role', TRUE), ''),
                            'saas_admin_owner');
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  r RECORD;
BEGIN
  EXECUTE format('GRANT USAGE ON SCHEMA saas_admin TO %I', v_aowner);
  EXECUTE 'REVOKE ALL ON SCHEMA saas_admin FROM PUBLIC';
  EXECUTE format('REVOKE ALL ON SCHEMA saas_admin FROM %I', v_runtime);
  EXECUTE format('GRANT USAGE ON SCHEMA saas_admin TO %I', v_admin);

  EXECUTE format('REVOKE ALL ON TABLE saas_admin.intento_invitacion '
                 'FROM PUBLIC, %I, %I', v_admin, v_runtime);
  EXECUTE format('GRANT SELECT, INSERT, DELETE ON TABLE '
                 'saas_admin.intento_invitacion TO %I', v_aowner);
  EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE '
                 'saas_admin.intento_invitacion_id_seq TO %I', v_aowner);

  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'saas_admin'
       AND p.proname IN ('estado_invitacion', 'registrar_intento_invitacion',
                         'limpiar_intentos_invitacion')
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', r.firma, v_aowner);
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC, %I, %I',
                   r.firma, v_admin, v_runtime);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_admin);
  END LOOP;

  -- El principal global no gana ningun privilegio de tabla con esta migracion.
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA saas_admin FROM %I', v_admin);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA saas_admin FROM %I', v_admin);
END;
$acl$;

COMMIT;
