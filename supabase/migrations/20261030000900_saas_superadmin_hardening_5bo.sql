-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA F (5BO)
-- CU01/CU02: ENDURECIMIENTO DEL SUPERADMINISTRADOR (RATE LIMIT COMPARTIDO + AUDITORIA GLOBAL)
--
-- Por que existe esta migracion
-- -----------------------------
-- 5BM dejo el plano global aislado (saas_admin_runtime sin privilegios de tabla
-- y con EXECUTE solo sobre funciones cerradas). Faltaba el endurecimiento que
-- el master prompt exige para el superadministrador:
--
--   * proteccion contra fuerza bruta COMPARTIDA ENTRE PROCESOS (no un
--     diccionario en memoria): contadores en PostgreSQL por huella de clave y
--     por IP hasheada, con ventana deslizante;
--   * auditoria global de operaciones y de intentos, con metadatos minimos y
--     SIN secretos (nunca la clave, ni su hash completo, ni el DSN);
--   * superficie de lectura para el propio superadministrador autorizado.
--
-- El principal `saas_admin_runtime` sigue SIN privilegios de tabla: solo
-- EXECUTE sobre estas funciones `SECURITY DEFINER` del propietario NOLOGIN
-- `saas_admin_owner`. El principal tenant no puede ver ni ejecutar nada aqui.
--
-- No se modifica la autenticacion, el onboarding ni las funciones 5BM; esta
-- migracion solo agrega objetos nuevos.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
BEGIN
  IF to_regclass('saas.bitacora_global') IS NULL
     OR to_regprocedure('saas_admin.listar_empresas()') IS NULL THEN
    RAISE EXCEPTION '5BO: faltan objetos de 5BM; aplique 00100..00700 antes';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_admin_runtime')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_admin_owner') THEN
    RAISE EXCEPTION '5BO: faltan los roles del plano global';
  END IF;
END;
$preflight$;

CREATE SCHEMA IF NOT EXISTS saas_admin;

-- ===========================================================================
-- 1. Contadores de intentos de autenticacion global
-- ===========================================================================
CREATE TABLE IF NOT EXISTS saas_admin.intento_auth (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  ip_hash     TEXT NOT NULL,
  resultado   TEXT NOT NULL,
  creado      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_intento_resultado CHECK (resultado IN ('OK', 'FALLA')),
  CONSTRAINT ck_intento_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{8,64}$'),
  CONSTRAINT ck_intento_ip CHECK (ip_hash ~ '^[0-9a-f]{8,64}$')
);

COMMENT ON TABLE saas_admin.intento_auth IS
  'Intentos de autenticacion superadmin. Guarda solo huella parcial SHA-256 e IP hasheada; nunca la clave ni la IP en claro.';

CREATE INDEX IF NOT EXISTS ix_intento_auth_ip
  ON saas_admin.intento_auth (ip_hash, creado DESC);
CREATE INDEX IF NOT EXISTS ix_intento_auth_fingerprint
  ON saas_admin.intento_auth (fingerprint, creado DESC);
CREATE INDEX IF NOT EXISTS ix_intento_auth_creado
  ON saas_admin.intento_auth (creado DESC);

-- ===========================================================================
-- 2. Auditoria global de operaciones superadmin
-- ===========================================================================
CREATE TABLE IF NOT EXISTS saas_admin.auditoria_operacion (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fingerprint TEXT,
  ip_hash     TEXT,
  metodo      TEXT NOT NULL,
  ruta        TEXT NOT NULL,
  codigo      INTEGER,
  resultado   TEXT NOT NULL,
  empresa_id  BIGINT,
  detalle     TEXT,
  creado      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_auditoria_metodo CHECK (metodo ~ '^[A-Z]{3,10}$'),
  CONSTRAINT ck_auditoria_ruta CHECK (length(ruta) BETWEEN 1 AND 200),
  CONSTRAINT ck_auditoria_resultado CHECK (resultado IN ('OK', 'DENEGADO', 'ERROR', 'LIMITADO'))
);

COMMENT ON TABLE saas_admin.auditoria_operacion IS
  'Auditoria global del plano superadmin. Sin cuerpos de request, sin claves, sin tokens.';

CREATE INDEX IF NOT EXISTS ix_auditoria_creado
  ON saas_admin.auditoria_operacion (creado DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_empresa
  ON saas_admin.auditoria_operacion (empresa_id, creado DESC);

-- ===========================================================================
-- 3. Funciones cerradas (SECURITY DEFINER, propietario NOLOGIN)
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas_admin.estado_acceso(
  p_fingerprint TEXT,
  p_ip_hash     TEXT,
  p_max_ip      INTEGER,
  p_max_clave   INTEGER,
  p_ventana_seg INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_ip    BIGINT;
  v_clave BIGINT;
  v_max_ip    INTEGER := GREATEST(COALESCE(p_max_ip, 10), 1);
  v_max_clave INTEGER := GREATEST(COALESCE(p_max_clave, 30), 1);
  v_ventana   INTEGER := GREATEST(COALESCE(p_ventana_seg, 300), 30);
BEGIN
  SELECT count(*) INTO v_ip
    FROM saas_admin.intento_auth i
   WHERE i.ip_hash = p_ip_hash
     AND i.resultado = 'FALLA'
     AND i.creado > now() - make_interval(secs => v_ventana);
  SELECT count(*) INTO v_clave
    FROM saas_admin.intento_auth i
   WHERE i.fingerprint = p_fingerprint
     AND i.resultado = 'FALLA'
     AND i.creado > now() - make_interval(secs => v_ventana);
  RETURN jsonb_build_object(
    'fallas_ip', v_ip,
    'fallas_clave', v_clave,
    'bloqueado', (v_ip >= v_max_ip OR v_clave >= v_max_clave),
    'ventana_seg', v_ventana);
END;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.registrar_intento(
  p_fingerprint TEXT,
  p_ip_hash     TEXT,
  p_resultado   TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
  IF p_resultado NOT IN ('OK', 'FALLA') THEN
    RETURN jsonb_build_object('registrado', FALSE);
  END IF;
  IF p_fingerprint !~ '^[0-9a-f]{8,64}$' OR p_ip_hash !~ '^[0-9a-f]{8,64}$' THEN
    RETURN jsonb_build_object('registrado', FALSE);
  END IF;
  INSERT INTO saas_admin.intento_auth (fingerprint, ip_hash, resultado)
  VALUES (p_fingerprint, p_ip_hash, p_resultado);
  RETURN jsonb_build_object('registrado', TRUE);
END;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.auditar_operacion(
  p_fingerprint TEXT,
  p_ip_hash     TEXT,
  p_metodo      TEXT,
  p_ruta        TEXT,
  p_codigo      INTEGER,
  p_resultado   TEXT,
  p_empresa_id  BIGINT,
  p_detalle     TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
  IF p_resultado NOT IN ('OK', 'DENEGADO', 'ERROR', 'LIMITADO') THEN
    p_resultado := 'ERROR';
  END IF;
  INSERT INTO saas_admin.auditoria_operacion
    (fingerprint, ip_hash, metodo, ruta, codigo, resultado, empresa_id, detalle)
  VALUES (
    CASE WHEN p_fingerprint ~ '^[0-9a-f]{8,64}$' THEN p_fingerprint END,
    CASE WHEN p_ip_hash ~ '^[0-9a-f]{8,64}$' THEN p_ip_hash END,
    upper(coalesce(p_metodo, 'GET')),
    left(coalesce(p_ruta, '/'), 200),
    p_codigo,
    p_resultado,
    CASE WHEN p_empresa_id IS NOT NULL AND p_empresa_id > 0 THEN p_empresa_id END,
    left(p_detalle, 300));
  RETURN jsonb_build_object('registrado', TRUE);
END;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.listar_auditoria(p_limite INTEGER)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
  SELECT COALESCE(jsonb_agg(fila ORDER BY fila->>'creado' DESC), '[]'::JSONB)
    FROM (
      SELECT to_jsonb(a) AS fila
        FROM saas_admin.auditoria_operacion a
       ORDER BY a.creado DESC
       LIMIT GREATEST(COALESCE(p_limite, 50), 1)
    ) t;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.limpiar_intentos(p_dias INTEGER)
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
    DELETE FROM saas_admin.intento_auth
     WHERE creado < now() - make_interval(days => v_dias)
     RETURNING 1
  )
  SELECT count(*) INTO v_borradas FROM borradas;
  RETURN v_borradas;
END;
$fn$;

-- ===========================================================================
-- 4. ACL: EXECUTE solo al principal global
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
  EXECUTE format('REVOKE ALL ON SCHEMA saas_admin FROM %I', v_admin);
  EXECUTE format('GRANT USAGE ON SCHEMA saas_admin TO %I', v_admin);

  EXECUTE format('REVOKE ALL ON TABLE saas_admin.intento_auth FROM PUBLIC, %I, %I',
                 v_admin, v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas_admin.auditoria_operacion FROM PUBLIC, %I, %I',
                 v_admin, v_runtime);
  EXECUTE format('GRANT SELECT, INSERT, DELETE ON TABLE saas_admin.intento_auth TO %I', v_aowner);
  EXECUTE format('GRANT SELECT, INSERT ON TABLE saas_admin.auditoria_operacion TO %I', v_aowner);
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA saas_admin TO %I', v_aowner);

  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'saas_admin'
       AND p.proname IN ('estado_acceso', 'registrar_intento', 'auditar_operacion',
                         'listar_auditoria', 'limpiar_intentos')
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', r.firma, v_aowner);
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC, %I, %I',
                   r.firma, v_admin, v_runtime);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_admin);
  END LOOP;

  -- El principal global sigue sin privilegios de tabla.
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA saas_admin FROM %I', v_admin);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA saas_admin FROM %I', v_admin);
END;
$acl$;

COMMIT;
