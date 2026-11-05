-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA E1 (5BM)
-- PLANO GLOBAL SEPARADO + RESERVAS TENANT + VENTA DE CONTADO IDEMPOTENTE
--
-- Por que existe esta migracion
-- -----------------------------
-- 5BL dejo demostrado que el principal restringido `fs_saas_runtime` NO puede
-- insertar en `public.usuario` (42501) y que las tablas SaaS estan bajo FORCE
-- RLS. Ese bloqueo es correcto y se conserva. CU01/CU02 necesitan un plano
-- GLOBAL separado:
--
--   * rol LOGIN `saas_admin_runtime` con EXECUTE sobre funciones explicitas del
--     esquema `saas_admin` y SIN privilegios de tabla;
--   * duenos NOLOGIN `saas_admin_owner` y `saas_actor_sql_owner`, sin
--     SUPERUSER/BYPASSRLS/DDL, con `search_path` fijo y PUBLIC revocado;
--   * auditoria global en `saas.bitacora_global` (jamas visible al tenant).
--
-- Ademas habilita las operaciones comerciales minimas, todas invocadas por el
-- principal tenant con la asercion HMAC ya instalada en 5BK:
--
--   * `public.sp_crear_reserva_tenant` / `sp_cancelar_reserva_tenant` /
--     `sp_finalizar_reserva_sin_compra_tenant` (SECURITY INVOKER, RLS activa);
--   * `public.sp_registrar_venta_contado_tenant` con idempotencia por
--     `(empresa_id, tipo, operacion_id)` y huella de carga normalizada;
--   * `public.fs_verificar_actor_reserva` (SECURITY DEFINER, dueno NOLOGIN)
--     que ata el `session_user` de la asercion al usuario del JWT.
--
-- Requisitos: 20261030000100..00500 aplicadas y los roles globales del cluster
-- aprovisionados (el laboratorio los crea antes de aplicar este archivo).
-- Idempotente: puede reaplicarse sin duplicar objetos ni politicas.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_admin text := COALESCE(NULLIF(current_setting('saas.admin_runtime_role', TRUE), ''),
                           'saas_admin_runtime');
  v_aowner text := COALESCE(NULLIF(current_setting('saas.admin_owner_role', TRUE), ''),
                            'saas_admin_owner');
  v_actor text := COALESCE(NULLIF(current_setting('saas.actor_owner_role', TRUE), ''),
                           'saas_actor_sql_owner');
BEGIN
  IF to_regprocedure('saas.tenant_actual()') IS NULL
     OR to_regclass('saas.bitacora_global') IS NULL THEN
    RAISE EXCEPTION '5BM: faltan objetos de 00400/00500; aplique las migraciones previas';
  END IF;
  IF to_regclass('public.venta') IS NULL OR to_regclass('public.pago') IS NULL
     OR to_regclass('public.reserva') IS NULL OR to_regclass('public.inventario') IS NULL THEN
    RAISE EXCEPTION '5BM: faltan tablas comerciales historicas';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_admin)
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_aowner)
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_actor) THEN
    RAISE EXCEPTION '5BM: faltan los roles globales % / % / %', v_admin, v_aowner, v_actor;
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = v_aowner)
     OR (SELECT rolcanlogin FROM pg_roles WHERE rolname = v_actor) THEN
    RAISE EXCEPTION '5BM: los duenos de funciones deben ser NOLOGIN';
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Esquema administrativo y tabla de idempotencia
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS saas_admin;

COMMENT ON SCHEMA saas_admin IS
  'Plano global CU01/CU02: funciones SECURITY DEFINER con dueno NOLOGIN. Solo EXECUTE para saas_admin_runtime.';

CREATE TABLE IF NOT EXISTS saas.operacion_idempotente (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id    BIGINT NOT NULL REFERENCES public.empresa(id) ON UPDATE CASCADE ON DELETE RESTRICT,
  tipo          TEXT NOT NULL,
  operacion_id  UUID NOT NULL,
  huella        TEXT NOT NULL,
  estado        TEXT NOT NULL DEFAULT 'EN_PROCESO',
  resultado     JSONB,
  creado        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  actualizado   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_operacion_idempotente_tipo CHECK (tipo IN ('VENTA_CONTADO')),
  CONSTRAINT ck_operacion_idempotente_estado CHECK (estado IN ('EN_PROCESO', 'PROCESADA')),
  CONSTRAINT ck_operacion_idempotente_huella CHECK (huella ~ '^[0-9a-f]{64}$'),
  CONSTRAINT uq_operacion_idempotente UNIQUE (empresa_id, tipo, operacion_id)
);

COMMENT ON TABLE saas.operacion_idempotente IS
  'Idempotencia de reenvios offline: un UUID por empresa y tipo. La huella detecta payload distinto.';

-- Sin columnas de otro tenant: RLS por empresa y solo el principal runtime.
DO $idem_rls$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE 'ALTER TABLE saas.operacion_idempotente ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE saas.operacion_idempotente FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_idempotencia ON saas.operacion_idempotente';
  EXECUTE format(
    'CREATE POLICY tenant_idempotencia ON saas.operacion_idempotente AS PERMISSIVE '
    'FOR ALL TO %I USING (empresa_id = saas.tenant_actual()) '
    'WITH CHECK (empresa_id = saas.tenant_actual())', v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas.operacion_idempotente FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE saas.operacion_idempotente TO %I', v_runtime);
END;
$idem_rls$;

-- ===========================================================================
-- 2. Funciones globales CU01/CU02 (SECURITY DEFINER, dueno NOLOGIN)
-- ===========================================================================

-- 2.1 Lecturas globales.
CREATE OR REPLACE FUNCTION saas_admin.listar_empresas()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
  SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.id), '[]'::JSONB)
    FROM public.empresa e;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.listar_planes()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
  SELECT COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]'::JSONB)
    FROM public.plan_saas p;
$fn$;

CREATE OR REPLACE FUNCTION saas_admin.listar_suscripciones(p_empresa_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
  IF p_empresa_id IS NULL OR p_empresa_id <= 0 THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.empresa e WHERE e.id = p_empresa_id) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  RETURN jsonb_build_object(
    'ok', TRUE,
    'suscripciones', COALESCE((
      SELECT jsonb_agg(to_jsonb(s) ORDER BY s.id)
        FROM public.suscripcion s
       WHERE s.empresa_id = p_empresa_id), '[]'::JSONB));
END;
$fn$;

-- 2.2 Alta de empresa + configuracion + suscripcion vigente (atomica).
CREATE OR REPLACE FUNCTION saas_admin.crear_empresa(
  p_razon_social      TEXT,
  p_nombre_comercial  TEXT,
  p_slug              TEXT,
  p_nit               TEXT,
  p_correo_contacto   TEXT,
  p_telefono          TEXT,
  p_plan_id           BIGINT,
  p_fecha_inicio      DATE,
  p_fecha_vencimiento DATE
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_id BIGINT;
  v_sub BIGINT;
BEGIN
  IF btrim(COALESCE(p_razon_social, '')) = ''
     OR btrim(COALESCE(p_nombre_comercial, '')) = ''
     OR btrim(COALESCE(p_slug, '')) = '' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Datos de empresa incompletos');
  END IF;
  IF p_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Slug empresarial invalido');
  END IF;
  IF p_fecha_inicio IS NULL OR p_fecha_vencimiento IS NULL
     OR p_fecha_vencimiento < p_fecha_inicio THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Fecha de fin anterior al inicio');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.plan_saas p WHERE p.id = p_plan_id AND p.estado IS TRUE) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Plan activo inexistente');
  END IF;
  IF EXISTS (SELECT 1 FROM public.empresa e WHERE e.slug = p_slug) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409, 'mensaje', 'Slug empresarial duplicado');
  END IF;

  INSERT INTO public.empresa (razon_social, nombre_comercial, slug, nit,
                              correo_contacto, telefono, estado)
  VALUES (btrim(p_razon_social), btrim(p_nombre_comercial), p_slug, p_nit,
          p_correo_contacto, p_telefono, TRUE)
  RETURNING id INTO v_id;

  INSERT INTO public.empresa_configuracion (empresa_id) VALUES (v_id);

  INSERT INTO public.suscripcion (empresa_id, plan_saas_id, fecha_inicio,
                                  fecha_vencimiento, estado)
  VALUES (v_id, p_plan_id, p_fecha_inicio, p_fecha_vencimiento, 'ACTIVA')
  RETURNING id INTO v_sub;

  PERFORM saas.registrar_evento_global(
    'SAAS_EMPRESA_CREADA', 'empresa', v_id,
    format('Empresa %s creada con plan %s y suscripcion %s', p_slug, p_plan_id, v_sub),
    NULL, NULL);

  RETURN jsonb_build_object(
    'ok', TRUE,
    'empresa', (SELECT to_jsonb(e) FROM public.empresa e WHERE e.id = v_id),
    'suscripcion', (SELECT to_jsonb(s) FROM public.suscripcion s WHERE s.id = v_sub));
END;
$fn$;

-- 2.3 Estado de empresa.
CREATE OR REPLACE FUNCTION saas_admin.cambiar_estado_empresa(
  p_empresa_id BIGINT,
  p_estado     BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_actual BOOLEAN;
BEGIN
  IF p_empresa_id IS NULL OR p_empresa_id <= 0 OR p_estado IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  SELECT e.estado INTO v_actual FROM public.empresa e
   WHERE e.id = p_empresa_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  IF v_actual IS DISTINCT FROM p_estado THEN
    UPDATE public.empresa e SET estado = p_estado, fecha_actualizacion = NOW()
     WHERE e.id = p_empresa_id;
    PERFORM saas.registrar_evento_global(
      'SAAS_EMPRESA_ESTADO', 'empresa', p_empresa_id,
      format('Estado de empresa %s -> %s', p_empresa_id, p_estado), NULL, NULL);
  END IF;
  RETURN jsonb_build_object('ok', TRUE,
    'empresa', (SELECT to_jsonb(e) FROM public.empresa e WHERE e.id = p_empresa_id));
END;
$fn$;

-- 2.4 Alta de plan.
CREATE OR REPLACE FUNCTION saas_admin.crear_plan(
  p_codigo                        TEXT,
  p_nombre                        TEXT,
  p_descripcion                   TEXT,
  p_max_sucursales_activas        INTEGER,
  p_permite_productos             BOOLEAN,
  p_permite_ventas                BOOLEAN,
  p_permite_reservas              BOOLEAN,
  p_permite_offline               BOOLEAN,
  p_permite_reportes_convencionales BOOLEAN,
  p_permite_reportes_ia           BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_id BIGINT;
BEGIN
  IF btrim(COALESCE(p_codigo, '')) !~ '^[A-Z][A-Z0-9_]*$'
     OR btrim(COALESCE(p_nombre, '')) = '' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Datos del plan invalidos');
  END IF;
  IF p_max_sucursales_activas IS NULL
     OR p_max_sucursales_activas NOT BETWEEN 1 AND 1000 THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Limite de sucursales invalido');
  END IF;
  IF EXISTS (SELECT 1 FROM public.plan_saas p WHERE p.codigo = p_codigo) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409, 'mensaje', 'Codigo de plan duplicado');
  END IF;
  INSERT INTO public.plan_saas (codigo, nombre, descripcion, max_sucursales_activas,
      permite_productos, permite_ventas, permite_reservas, permite_offline,
      permite_reportes_convencionales, permite_reportes_ia, estado)
  VALUES (p_codigo, btrim(p_nombre), p_descripcion, p_max_sucursales_activas,
      COALESCE(p_permite_productos, TRUE), COALESCE(p_permite_ventas, TRUE),
      COALESCE(p_permite_reservas, TRUE), COALESCE(p_permite_offline, TRUE),
      COALESCE(p_permite_reportes_convencionales, TRUE),
      COALESCE(p_permite_reportes_ia, FALSE), TRUE)
  RETURNING id INTO v_id;
  PERFORM saas.registrar_evento_global(
    'SAAS_PLAN_CREADO', 'plan_saas', v_id,
    format('Plan %s creado', p_codigo), NULL, NULL);
  RETURN jsonb_build_object('ok', TRUE,
    'plan', (SELECT to_jsonb(p) FROM public.plan_saas p WHERE p.id = v_id));
END;
$fn$;

-- 2.5 Estado de plan: no desactivar con suscripciones activas.
CREATE OR REPLACE FUNCTION saas_admin.cambiar_estado_plan(
  p_plan_id BIGINT,
  p_estado  BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_actual BOOLEAN;
  v_activas BIGINT;
BEGIN
  IF p_plan_id IS NULL OR p_plan_id <= 0 OR p_estado IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Plan inexistente');
  END IF;
  SELECT p.estado INTO v_actual FROM public.plan_saas p
   WHERE p.id = p_plan_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Plan inexistente');
  END IF;
  IF v_actual IS DISTINCT FROM p_estado THEN
    IF p_estado IS FALSE THEN
      SELECT count(*) INTO v_activas FROM public.suscripcion s
       WHERE s.plan_saas_id = p_plan_id AND s.estado = 'ACTIVA';
      IF v_activas > 0 THEN
        RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
          'mensaje', 'No se puede desactivar un plan con suscripciones activas');
      END IF;
    END IF;
    UPDATE public.plan_saas p SET estado = p_estado WHERE p.id = p_plan_id;
    PERFORM saas.registrar_evento_global(
      'SAAS_PLAN_ESTADO', 'plan_saas', p_plan_id,
      format('Estado de plan %s -> %s', p_plan_id, p_estado), NULL, NULL);
  END IF;
  RETURN jsonb_build_object('ok', TRUE,
    'plan', (SELECT to_jsonb(p) FROM public.plan_saas p WHERE p.id = p_plan_id));
END;
$fn$;

-- 2.6 Asignacion de suscripcion con bloqueo de downgrade.
CREATE OR REPLACE FUNCTION saas_admin.asignar_suscripcion(
  p_empresa_id       BIGINT,
  p_plan_id          BIGINT,
  p_fecha_inicio     DATE,
  p_fecha_vencimiento DATE,
  p_observacion      TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_max INTEGER;
  v_activas BIGINT;
  v_sub BIGINT;
BEGIN
  IF p_fecha_inicio IS NULL OR p_fecha_vencimiento IS NULL
     OR p_fecha_vencimiento < p_fecha_inicio THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Fecha de fin anterior al inicio');
  END IF;
  PERFORM 1 FROM public.empresa e WHERE e.id = p_empresa_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  SELECT p.max_sucursales_activas INTO v_max FROM public.plan_saas p
   WHERE p.id = p_plan_id AND p.estado IS TRUE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Plan activo inexistente');
  END IF;
  SELECT count(*) INTO v_activas FROM public.sucursal s
   WHERE s.empresa_id = p_empresa_id AND s.estado IS TRUE;
  IF v_activas > v_max THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'El plan elegido permite menos sucursales activas que las existentes');
  END IF;
  UPDATE public.suscripcion s SET estado = 'CANCELADA', fecha_actualizacion = NOW()
   WHERE s.empresa_id = p_empresa_id AND s.estado = 'ACTIVA';
  INSERT INTO public.suscripcion (empresa_id, plan_saas_id, fecha_inicio,
                                  fecha_vencimiento, estado, observacion)
  VALUES (p_empresa_id, p_plan_id, p_fecha_inicio, p_fecha_vencimiento,
          'ACTIVA', p_observacion)
  RETURNING id INTO v_sub;
  PERFORM saas.registrar_evento_global(
    'SAAS_SUSCRIPCION_ASIGNADA', 'suscripcion', v_sub,
    format('Empresa %s -> plan %s', p_empresa_id, p_plan_id), NULL, NULL);
  RETURN jsonb_build_object('ok', TRUE,
    'suscripcion', (SELECT to_jsonb(s) FROM public.suscripcion s WHERE s.id = v_sub));
END;
$fn$;

-- 2.7 Estado de suscripcion (sin reactivacion automatica).
CREATE OR REPLACE FUNCTION saas_admin.cambiar_estado_suscripcion(
  p_empresa_id     BIGINT,
  p_suscripcion_id BIGINT,
  p_estado         TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_actual TEXT;
BEGIN
  IF p_estado NOT IN ('PENDIENTE', 'SUSPENDIDA', 'VENCIDA', 'CANCELADA') THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Estado de suscripcion invalido');
  END IF;
  PERFORM 1 FROM public.empresa e WHERE e.id = p_empresa_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa inexistente');
  END IF;
  SELECT s.estado INTO v_actual FROM public.suscripcion s
   WHERE s.id = p_suscripcion_id AND s.empresa_id = p_empresa_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Suscripcion ajena o inexistente');
  END IF;
  IF v_actual = p_estado THEN
    RETURN jsonb_build_object('ok', TRUE,
      'suscripcion', (SELECT to_jsonb(s) FROM public.suscripcion s WHERE s.id = p_suscripcion_id));
  END IF;
  IF v_actual <> 'ACTIVA' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Solo puede modificarse una suscripcion activa');
  END IF;
  UPDATE public.suscripcion s SET estado = p_estado, fecha_actualizacion = NOW()
   WHERE s.id = p_suscripcion_id AND s.empresa_id = p_empresa_id;
  PERFORM saas.registrar_evento_global(
    'SAAS_SUSCRIPCION_ESTADO', 'suscripcion', p_suscripcion_id,
    format('Suscripcion %s -> %s', p_suscripcion_id, p_estado), NULL, NULL);
  RETURN jsonb_build_object('ok', TRUE,
    'suscripcion', (SELECT to_jsonb(s) FROM public.suscripcion s WHERE s.id = p_suscripcion_id));
END;
$fn$;

-- 2.8 Alta ATOMICA del primer administrador (CU01).
--     El hash Argon2 llega ya calculado por FastAPI: la base nunca ve la
--     contrasena en claro y no se guarda ningun secreto en la auditoria.
CREATE OR REPLACE FUNCTION saas_admin.registrar_primer_administrador(
  p_empresa_id          BIGINT,
  p_correo              TEXT,
  p_password_hash       TEXT,
  p_nombres             TEXT,
  p_apellidos           TEXT,
  p_ci                  TEXT,
  p_ciudad_id           BIGINT,
  p_nombre_sucursal     TEXT,
  p_direccion_sucursal  TEXT,
  p_telefono            TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_correo TEXT := lower(btrim(COALESCE(p_correo, '')));
  v_documento TEXT := btrim(COALESCE(p_ci, ''));
  v_cliente_rol BIGINT;
  v_admin_rol BIGINT;
  v_sucursal BIGINT;
  v_usuario BIGINT;
  v_empleado BIGINT;
  v_membresia BIGINT;
  v_roles BIGINT;
BEGIN
  IF v_correo = '' OR v_documento = ''
     OR btrim(COALESCE(p_nombres, '')) = ''
     OR btrim(COALESCE(p_apellidos, '')) = ''
     OR btrim(COALESCE(p_nombre_sucursal, '')) = ''
     OR btrim(COALESCE(p_direccion_sucursal, '')) = '' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Datos del administrador incompletos');
  END IF;
  IF p_password_hash IS NULL OR p_password_hash !~ '^\$argon2' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422, 'mensaje', 'Hash de contrasena invalido');
  END IF;

  PERFORM 1 FROM public.empresa e
    WHERE e.id = p_empresa_id AND e.estado IS TRUE FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Empresa no disponible');
  END IF;
  IF EXISTS (SELECT 1 FROM public.usuario_empresa ue WHERE ue.empresa_id = p_empresa_id) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Empresa con membresias; utilizar administracion propia CU04');
  END IF;
  IF EXISTS (SELECT 1 FROM public.sucursal s WHERE s.empresa_id = p_empresa_id) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Empresa con sucursales; registro inicial no aplicable');
  END IF;

  SELECT count(*) INTO v_roles
    FROM public.suscripcion s
    JOIN public.plan_saas p ON p.id = s.plan_saas_id
   WHERE s.empresa_id = p_empresa_id
     AND s.estado = 'ACTIVA'
     AND p.estado IS TRUE
     AND p.max_sucursales_activas >= 1
     AND (timezone('America/La_Paz', now()))::date
         BETWEEN s.fecha_inicio AND s.fecha_vencimiento;
  IF v_roles <> 1 THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Empresa sin una suscripcion vigente que admita sucursales');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM public.ciudad c
                  WHERE c.id = p_ciudad_id AND c.estado IS TRUE) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404, 'mensaje', 'Ciudad activa inexistente');
  END IF;

  SELECT count(*) INTO v_roles FROM public.rol r
   WHERE r.nombre = 'CLIENTE' AND r.estado IS TRUE;
  IF v_roles <> 1 THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Rol base del sistema no configurado: CLIENTE');
  END IF;
  SELECT r.id INTO v_cliente_rol FROM public.rol r
   WHERE r.nombre = 'CLIENTE' AND r.estado IS TRUE;

  SELECT count(*) INTO v_roles FROM public.rol r
   WHERE r.nombre = 'ADMINISTRADOR' AND r.estado IS TRUE;
  IF v_roles <> 1 THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'mensaje', 'Rol base del sistema no configurado: ADMINISTRADOR');
  END IF;
  SELECT r.id INTO v_admin_rol FROM public.rol r
   WHERE r.nombre = 'ADMINISTRADOR' AND r.estado IS TRUE;

  IF EXISTS (SELECT 1 FROM public.usuario u WHERE lower(u.correo) = v_correo) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409, 'mensaje', 'Correo ya registrado');
  END IF;
  IF EXISTS (SELECT 1 FROM public.empleado em
              WHERE em.empresa_id = p_empresa_id
                AND lower(em.ci) = lower(v_documento)) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409, 'mensaje', 'Documento de empleado duplicado');
  END IF;

  INSERT INTO public.sucursal (empresa_id, ciudad_id, nombre, direccion, telefono, estado)
  VALUES (p_empresa_id, p_ciudad_id, btrim(p_nombre_sucursal),
          btrim(p_direccion_sucursal), p_telefono, TRUE)
  RETURNING id INTO v_sucursal;

  INSERT INTO public.usuario (rol_id, correo, password_hash, fecha_creacion, estado)
  VALUES (v_cliente_rol, v_correo, p_password_hash, NOW(), TRUE)
  RETURNING id INTO v_usuario;

  -- La membresia debe existir ANTES del perfil: la FK compuesta
  -- fk_tenant_empleado_usuario referencia usuario_empresa(empresa_id, usuario_id).
  INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado)
  VALUES (v_usuario, p_empresa_id, v_admin_rol, TRUE)
  RETURNING id INTO v_membresia;

  INSERT INTO public.empleado (empresa_id, usuario_id, sucursal_id, nombres,
                               apellidos, ci, telefono, fecha_contratacion, estado)
  VALUES (p_empresa_id, v_usuario, v_sucursal, btrim(p_nombres),
          btrim(p_apellidos), v_documento, p_telefono,
          (timezone('America/La_Paz', now()))::date, TRUE)
  RETURNING id INTO v_empleado;

  PERFORM saas.registrar_evento_global(
    'SAAS_PRIMER_ADMINISTRADOR', 'usuario_empresa', v_membresia,
    format('Primer administrador de empresa %s creado (usuario %s)', p_empresa_id, v_usuario),
    v_usuario, NULL);

  RETURN jsonb_build_object(
    'ok', TRUE,
    'empresa_id', p_empresa_id,
    'usuario_id', v_usuario,
    'empleado_id', v_empleado,
    'membresia_id', v_membresia,
    'sucursal_id', v_sucursal,
    'correo', v_correo,
    'rol_empresa', 'ADMINISTRADOR');
END;
$fn$;

-- 2.9 Propiedad y ACL del plano administrativo.
DO $acl_admin$
DECLARE
  v_admin  text := COALESCE(NULLIF(current_setting('saas.admin_runtime_role', TRUE), ''),
                            'saas_admin_runtime');
  v_aowner text := COALESCE(NULLIF(current_setting('saas.admin_owner_role', TRUE), ''),
                            'saas_admin_owner');
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  r RECORD;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'saas_admin'
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', r.firma, v_aowner);
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC, %I, %I',
                   r.firma, v_admin, v_runtime);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_admin);
  END LOOP;

  EXECUTE format('REVOKE ALL ON SCHEMA saas_admin FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT USAGE ON SCHEMA saas_admin TO %I', v_admin);

  -- El plano administrativo no puede alcanzar tablas empresariales por si
  -- mismo: solo el dueno NOLOGIN dentro de las funciones.
  EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', v_admin);
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', v_admin);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', v_admin);
END;
$acl_admin$;

-- 2.10 Politicas del dueno NOLOGIN para escribir el plano global.
DO $politicas_admin$
DECLARE
  v_aowner text := COALESCE(NULLIF(current_setting('saas.admin_owner_role', TRUE), ''),
                            'saas_admin_owner');
  v_tabla text;
  v_tablas text[] := ARRAY['empresa', 'empresa_configuracion', 'suscripcion',
                           'plan_saas', 'sucursal', 'usuario', 'empleado',
                           'usuario_empresa'];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('DROP POLICY IF EXISTS admin_global_acceso ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY admin_global_acceso ON public.%I AS PERMISSIVE FOR ALL TO %I '
      'USING (true) WITH CHECK (true)', v_tabla, v_aowner);
  END LOOP;
  EXECUTE 'DROP POLICY IF EXISTS bitacora_admin_insercion ON public.bitacora';
  EXECUTE format(
    'CREATE POLICY bitacora_admin_insercion ON public.bitacora AS PERMISSIVE '
    'FOR INSERT TO %I WITH CHECK (empresa_id IS NOT NULL)', v_aowner);
END;
$politicas_admin$;

DO $grants_admin$
DECLARE
  v_aowner text := COALESCE(NULLIF(current_setting('saas.admin_owner_role', TRUE), ''),
                            'saas_admin_owner');
BEGIN
  EXECUTE format(
    'GRANT SELECT, INSERT, UPDATE ON TABLE public.empresa, public.empresa_configuracion, '
    'public.suscripcion, public.plan_saas, public.sucursal, public.usuario, '
    'public.empleado, public.usuario_empresa TO %I', v_aowner);
  EXECUTE format('GRANT SELECT ON TABLE public.rol, public.ciudad TO %I', v_aowner);
  EXECUTE format('GRANT INSERT ON TABLE public.bitacora TO %I', v_aowner);
  EXECUTE format('GRANT INSERT ON TABLE saas.bitacora_global TO %I', v_aowner);
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', v_aowner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.registrar_evento_global(TEXT, TEXT, BIGINT, TEXT, BIGINT, TEXT) TO %I', v_aowner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.tenant_actual() TO %I', v_aowner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.es_principal_runtime() TO %I', v_aowner);
  EXECUTE format('GRANT USAGE ON SCHEMA saas TO %I', v_aowner);
  EXECUTE format('GRANT SELECT ON TABLE saas.rls_secret TO %I', v_aowner);
END;
$grants_admin$;

-- ===========================================================================
-- 3. Verificador de actor para reservas (SECURITY DEFINER, dueno NOLOGIN)
-- ===========================================================================

CREATE OR REPLACE FUNCTION public.fs_verificar_actor_reserva(
  p_empresa_id BIGINT,
  p_usuario_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_bruto TEXT;
  v_partes TEXT[];
  v_empresa BIGINT;
  v_usuario BIGINT;
  v_expira BIGINT;
  v_clave TEXT;
  v_esperado TEXT;
  v_ahora BIGINT;
BEGIN
  IF p_empresa_id IS NULL OR p_usuario_id IS NULL
     OR p_empresa_id <= 0 OR p_usuario_id <= 0 THEN
    RETURN FALSE;
  END IF;
  v_bruto := current_setting('app.tenant_assertion', TRUE);
  IF v_bruto IS NULL OR length(v_bruto) < 32 THEN
    RETURN FALSE;
  END IF;
  v_partes := string_to_array(v_bruto, '|');
  IF array_length(v_partes, 1) <> 5 THEN
    RETURN FALSE;
  END IF;
  BEGIN
    v_empresa := v_partes[1]::BIGINT;
    v_usuario := v_partes[2]::BIGINT;
    v_expira  := v_partes[3]::BIGINT;
  EXCEPTION WHEN others THEN
    RETURN FALSE;
  END;
  v_ahora := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
  IF v_expira < v_ahora OR v_expira > v_ahora + 3600 THEN
    RETURN FALSE;
  END IF;
  IF v_empresa IS DISTINCT FROM p_empresa_id
     OR v_usuario IS DISTINCT FROM p_usuario_id THEN
    RETURN FALSE;
  END IF;
  SELECT s.clave INTO v_clave FROM saas.rls_secret s WHERE s.singleton;
  IF v_clave IS NULL THEN
    RETURN FALSE;
  END IF;
  v_esperado := encode(
    saas.hmac_sha256(
      convert_to(v_clave, 'UTF8'),
      convert_to(v_partes[1] || '|' || v_partes[2] || '|' || v_partes[3]
                 || '|' || v_partes[4], 'UTF8')),
    'hex');
  IF v_partes[5] IS DISTINCT FROM v_esperado THEN
    RETURN FALSE;
  END IF;
  -- Barrera en base: membresia, empresa, rol, plan y suscripcion vigentes.
  IF saas.tenant_actual() IS DISTINCT FROM p_empresa_id THEN
    RETURN FALSE;
  END IF;
  RETURN TRUE;
END;
$fn$;

DO $acl_actor$
DECLARE
  v_actor text := COALESCE(NULLIF(current_setting('saas.actor_owner_role', TRUE), ''),
                           'saas_actor_sql_owner');
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE format('ALTER FUNCTION public.fs_verificar_actor_reserva(BIGINT, BIGINT) OWNER TO %I', v_actor);
  EXECUTE format('REVOKE ALL ON FUNCTION public.fs_verificar_actor_reserva(BIGINT, BIGINT) FROM PUBLIC');
  EXECUTE format('GRANT EXECUTE ON FUNCTION public.fs_verificar_actor_reserva(BIGINT, BIGINT) TO %I', v_runtime);
  EXECUTE format('GRANT SELECT ON TABLE saas.rls_secret TO %I', v_actor);
  EXECUTE format('GRANT USAGE ON SCHEMA saas TO %I', v_actor);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.tenant_actual() TO %I', v_actor);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.hmac_sha256(BYTEA, BYTEA) TO %I', v_actor);
END;
$acl_actor$;

-- ===========================================================================
-- 4. SP tenant de reservas (SECURITY INVOKER, RLS y asercion de 5BK)
-- ===========================================================================

CREATE OR REPLACE PROCEDURE public.sp_crear_reserva_tenant(
  IN p_empresa_id BIGINT,
  IN p_carrito_id BIGINT,
  IN p_usuario_id BIGINT,
  IN p_fecha_atencion TIMESTAMP WITH TIME ZONE,
  IN p_observacion TEXT,
  INOUT p_reserva_id BIGINT
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant BIGINT := saas.tenant_actual();
  v_permite BOOLEAN;
  v_carrito RECORD;
  v_detalle RECORD;
  v_inv RECORD;
  v_disponible INTEGER;
  v_existente BIGINT;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_carrito_id IS NULL OR p_carrito_id <= 0
     OR p_usuario_id IS NULL OR p_usuario_id <= 0 THEN
    RAISE EXCEPTION 'Identificadores invalidos' USING ERRCODE = '22023';
  END IF;
  IF p_fecha_atencion IS NULL OR p_fecha_atencion <= CURRENT_TIMESTAMP THEN
    RAISE EXCEPTION 'La fecha de atencion debe ser futura' USING ERRCODE = '22023';
  END IF;

  SELECT p.permite_reservas INTO v_permite
    FROM public.suscripcion s
    JOIN public.plan_saas p ON p.id = s.plan_saas_id
   WHERE s.empresa_id = p_empresa_id
     AND s.estado = 'ACTIVA'
     AND p.estado IS TRUE
     AND (timezone('America/La_Paz', now()))::date
         BETWEEN s.fecha_inicio AND s.fecha_vencimiento;
  IF v_permite IS NOT TRUE THEN
    RAISE EXCEPTION 'El plan no permite reservas' USING ERRCODE = '42501';
  END IF;

  SELECT c.id, c.cliente_id, c.sucursal_id, c.estado
    INTO v_carrito
    FROM public.carrito c
   WHERE c.id = p_carrito_id AND c.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Carrito no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_carrito.estado <> 'ACTIVO' THEN
    RAISE EXCEPTION 'El carrito ya no esta activo' USING ERRCODE = '23514';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.cliente cl
     WHERE cl.id = v_carrito.cliente_id
       AND cl.empresa_id = p_empresa_id
       AND cl.usuario_id = p_usuario_id
       AND cl.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'El carrito no pertenece al cliente autenticado' USING ERRCODE = '42501';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.sucursal su
     WHERE su.id = v_carrito.sucursal_id
       AND su.empresa_id = p_empresa_id
       AND su.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'Sucursal del carrito no disponible' USING ERRCODE = '42501';
  END IF;

  SELECT r.id INTO v_existente FROM public.reserva r
   WHERE r.carrito_id = p_carrito_id AND r.empresa_id = p_empresa_id
   FOR UPDATE;
  IF FOUND THEN
    RAISE EXCEPTION 'El carrito ya origino una reserva' USING ERRCODE = '23514';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.detalle_carrito dc WHERE dc.carrito_id = p_carrito_id
  ) THEN
    RAISE EXCEPTION 'El carrito no contiene prendas para reservar' USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.reserva (empresa_id, cliente_id, sucursal_id, carrito_id,
                              fecha_reserva, fecha_atencion, estado, observacion)
  VALUES (p_empresa_id, v_carrito.cliente_id, v_carrito.sucursal_id, p_carrito_id,
          CURRENT_TIMESTAMP, p_fecha_atencion, 'PENDIENTE', p_observacion)
  RETURNING id INTO p_reserva_id;

  FOR v_detalle IN
    SELECT dc.inventario_id, dc.cantidad
      FROM public.detalle_carrito dc
     WHERE dc.carrito_id = p_carrito_id
     ORDER BY dc.inventario_id
  LOOP
    IF v_detalle.cantidad IS NULL OR v_detalle.cantidad <= 0 THEN
      RAISE EXCEPTION 'Cantidad invalida en el carrito' USING ERRCODE = '23514';
    END IF;
    SELECT i.id, i.sucursal_id, i.stock_actual, i.stock_reservado
      INTO v_inv
      FROM public.inventario i
     WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id
     FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Inventario no disponible' USING ERRCODE = '42501';
    END IF;
    IF v_inv.sucursal_id <> v_carrito.sucursal_id THEN
      RAISE EXCEPTION 'El inventario no pertenece a la sucursal del carrito'
        USING ERRCODE = '23514';
    END IF;
    v_disponible := v_inv.stock_actual - v_inv.stock_reservado;
    IF v_disponible < v_detalle.cantidad THEN
      RAISE EXCEPTION 'Stock insuficiente para inventario %', v_detalle.inventario_id
        USING ERRCODE = '23514';
    END IF;

    UPDATE public.inventario i
       SET stock_reservado = i.stock_reservado + v_detalle.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id;

    INSERT INTO public.detalle_reserva (empresa_id, reserva_id, inventario_id, cantidad)
    VALUES (p_empresa_id, p_reserva_id, v_detalle.inventario_id, v_detalle.cantidad);

    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_detalle.inventario_id, p_usuario_id, 'RESERVA',
       v_detalle.cantidad, CURRENT_TIMESTAMP, 'Stock reservado para cliente',
       'RESERVA', p_reserva_id);
  END LOOP;

  UPDATE public.carrito c
     SET estado = 'CONVERTIDO', fecha_actualizacion = CURRENT_TIMESTAMP
   WHERE c.id = p_carrito_id AND c.empresa_id = p_empresa_id;
END;
$sp$;

CREATE OR REPLACE PROCEDURE public.sp_cancelar_reserva_tenant(
  IN p_empresa_id BIGINT,
  IN p_reserva_id BIGINT,
  IN p_usuario_id BIGINT,
  IN p_observacion TEXT
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant BIGINT := saas.tenant_actual();
  v_reserva RECORD;
  v_autorizado BOOLEAN := FALSE;
  v_detalle RECORD;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  SELECT r.id, r.cliente_id, r.estado, r.observacion
    INTO v_reserva
    FROM public.reserva r
   WHERE r.id = p_reserva_id AND r.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Reserva no disponible' USING ERRCODE = '42501';
  END IF;

  SELECT EXISTS (
    SELECT 1 FROM public.cliente cl
     WHERE cl.id = v_reserva.cliente_id AND cl.empresa_id = p_empresa_id
       AND cl.usuario_id = p_usuario_id AND cl.estado IS TRUE)
    OR EXISTS (
      SELECT 1 FROM public.empleado em
        JOIN public.sucursal su ON su.id = em.sucursal_id
       WHERE em.usuario_id = p_usuario_id AND em.empresa_id = p_empresa_id
         AND em.estado IS TRUE AND su.empresa_id = p_empresa_id)
    INTO v_autorizado;
  IF v_autorizado IS NOT TRUE THEN
    RAISE EXCEPTION 'Reserva fuera del cliente autenticado' USING ERRCODE = '42501';
  END IF;

  -- Idempotencia por transicion de estado: repetir no libera dos veces.
  IF v_reserva.estado = 'CANCELADA' THEN
    RETURN;
  END IF;
  IF v_reserva.estado NOT IN ('PENDIENTE', 'CONFIRMADA') THEN
    RAISE EXCEPTION 'La reserva no puede cancelarse en estado %', v_reserva.estado
      USING ERRCODE = '23514';
  END IF;

  FOR v_detalle IN
    SELECT dr.inventario_id, dr.cantidad
      FROM public.detalle_reserva dr
     WHERE dr.reserva_id = p_reserva_id AND dr.empresa_id = p_empresa_id
     ORDER BY dr.inventario_id
  LOOP
    PERFORM 1 FROM public.inventario i
      WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id
      FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Inventario de la reserva no disponible' USING ERRCODE = '42501';
    END IF;
    IF (SELECT i.stock_reservado FROM public.inventario i
         WHERE i.id = v_detalle.inventario_id) < v_detalle.cantidad THEN
      RAISE EXCEPTION 'Inconsistencia en stock reservado del inventario %',
        v_detalle.inventario_id USING ERRCODE = '23514';
    END IF;
    UPDATE public.inventario i
       SET stock_reservado = i.stock_reservado - v_detalle.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id;
    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_detalle.inventario_id, p_usuario_id, 'LIBERACION_RESERVA',
       v_detalle.cantidad, CURRENT_TIMESTAMP,
       COALESCE(p_observacion, 'Reserva cancelada'), 'RESERVA', p_reserva_id);
  END LOOP;

  UPDATE public.reserva r
     SET estado = 'CANCELADA',
         observacion = CASE
           WHEN p_observacion IS NULL THEN r.observacion
           WHEN r.observacion IS NULL THEN p_observacion
           ELSE r.observacion || ' | ' || p_observacion
         END
   WHERE r.id = p_reserva_id AND r.empresa_id = p_empresa_id;
END;
$sp$;

CREATE OR REPLACE PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(
  IN p_empresa_id BIGINT,
  IN p_reserva_id BIGINT,
  IN p_usuario_id BIGINT,
  IN p_observacion TEXT
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant BIGINT := saas.tenant_actual();
  v_reserva RECORD;
  v_detalle RECORD;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  SELECT r.id, r.estado INTO v_reserva
    FROM public.reserva r
   WHERE r.id = p_reserva_id AND r.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Reserva no disponible' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.empleado em
      JOIN public.sucursal su ON su.id = em.sucursal_id
     WHERE em.usuario_id = p_usuario_id AND em.empresa_id = p_empresa_id
       AND em.estado IS TRUE AND su.empresa_id = p_empresa_id
  ) THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;

  IF v_reserva.estado = 'ATENDIDA' THEN
    RETURN;
  END IF;
  IF v_reserva.estado NOT IN ('PENDIENTE', 'CONFIRMADA') THEN
    RAISE EXCEPTION 'La reserva no puede finalizarse en estado %', v_reserva.estado
      USING ERRCODE = '23514';
  END IF;

  FOR v_detalle IN
    SELECT dr.inventario_id, dr.cantidad
      FROM public.detalle_reserva dr
     WHERE dr.reserva_id = p_reserva_id AND dr.empresa_id = p_empresa_id
     ORDER BY dr.inventario_id
  LOOP
    PERFORM 1 FROM public.inventario i
      WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id
      FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Inventario de la reserva no disponible' USING ERRCODE = '42501';
    END IF;
    IF (SELECT i.stock_reservado FROM public.inventario i
         WHERE i.id = v_detalle.inventario_id) < v_detalle.cantidad THEN
      RAISE EXCEPTION 'Inconsistencia en stock reservado del inventario %',
        v_detalle.inventario_id USING ERRCODE = '23514';
    END IF;
    UPDATE public.inventario i
       SET stock_reservado = i.stock_reservado - v_detalle.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id;
    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_detalle.inventario_id, p_usuario_id, 'LIBERACION_RESERVA',
       v_detalle.cantidad, CURRENT_TIMESTAMP,
       COALESCE(p_observacion, 'Reserva finalizada sin compra'), 'RESERVA', p_reserva_id);
  END LOOP;

  UPDATE public.reserva r
     SET estado = 'ATENDIDA',
         observacion = CASE
           WHEN p_observacion IS NULL THEN r.observacion
           WHEN r.observacion IS NULL THEN p_observacion
           ELSE r.observacion || ' | ' || p_observacion
         END
   WHERE r.id = p_reserva_id AND r.empresa_id = p_empresa_id;
END;
$sp$;

-- ===========================================================================
-- 5. Venta presencial de contado con idempotencia
-- ===========================================================================

CREATE OR REPLACE FUNCTION public.sp_registrar_venta_contado_tenant(
  p_empresa_id   BIGINT,
  p_usuario_id   BIGINT,
  p_sucursal_id  BIGINT,
  p_operacion_id UUID,
  p_huella       TEXT,
  p_lineas       JSONB,
  p_metodo_pago  TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_permite BOOLEAN;
  v_linea RECORD;
  v_inv RECORD;
  v_precio NUMERIC(12,2);
  v_cantidad INTEGER;
  v_total NUMERIC(12,2) := 0;
  v_venta BIGINT;
  v_pago BIGINT;
  v_existente RECORD;
  v_resultado JSONB;
  v_stock JSONB := '[]'::JSONB;
  v_lineas_resultado JSONB := '[]'::JSONB;
  v_disponible INTEGER;
  v_metodo TEXT := upper(btrim(COALESCE(p_metodo_pago, '')));
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_operacion_id IS NULL THEN
    RAISE EXCEPTION 'operacion_id obligatorio' USING ERRCODE = '22023';
  END IF;
  IF p_huella IS NULL OR p_huella !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Huella de operacion invalida' USING ERRCODE = '22023';
  END IF;
  IF v_metodo NOT IN ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'QR', 'OTRO') THEN
    RAISE EXCEPTION 'Metodo de pago no admitido' USING ERRCODE = '22023';
  END IF;

  -- Empleado activo de la empresa y de la sucursal indicada + rol suficiente.
  SELECT em.id INTO v_empleado
    FROM public.empleado em
    JOIN public.sucursal su ON su.id = em.sucursal_id
    JOIN public.usuario_empresa ue ON ue.usuario_id = em.usuario_id
      AND ue.empresa_id = em.empresa_id AND ue.estado IS TRUE
    JOIN public.rol r ON r.id = ue.rol_id
   WHERE em.usuario_id = p_usuario_id AND em.empresa_id = p_empresa_id
     AND em.sucursal_id = p_sucursal_id AND em.estado IS TRUE
     AND su.empresa_id = p_empresa_id AND su.estado IS TRUE
     AND r.estado IS TRUE
     AND r.nombre IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL', 'CAJERO');
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'El usuario no puede vender en esta sucursal' USING ERRCODE = '42501';
  END IF;

  SELECT p.permite_ventas INTO v_permite
    FROM public.suscripcion s
    JOIN public.plan_saas p ON p.id = s.plan_saas_id
   WHERE s.empresa_id = p_empresa_id
     AND s.estado = 'ACTIVA'
     AND p.estado IS TRUE
     AND (timezone('America/La_Paz', now()))::date
         BETWEEN s.fecha_inicio AND s.fecha_vencimiento;
  IF v_permite IS NOT TRUE THEN
    RAISE EXCEPTION 'El plan no permite ventas' USING ERRCODE = '42501';
  END IF;

  IF p_lineas IS NULL OR jsonb_typeof(p_lineas) <> 'array'
     OR jsonb_array_length(p_lineas) < 1 OR jsonb_array_length(p_lineas) > 50 THEN
    RAISE EXCEPTION 'La venta requiere entre 1 y 50 lineas' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_lineas) x
     WHERE jsonb_typeof(x) <> 'object'
        OR (x->>'inventario_id') IS NULL
        OR (x->>'cantidad') IS NULL
        OR (x->>'inventario_id') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad')::INTEGER > 1000
  ) THEN
    RAISE EXCEPTION 'Linea de venta invalida' USING ERRCODE = '22023';
  END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(p_lineas) x)
     <> (SELECT count(DISTINCT (x->>'inventario_id'))
           FROM jsonb_array_elements(p_lineas) x) THEN
    RAISE EXCEPTION 'Inventario repetido en la venta' USING ERRCODE = '22023';
  END IF;

  -- Idempotencia: mismo UUID + misma huella => mismo resultado sin mutar stock.
  INSERT INTO saas.operacion_idempotente
    (empresa_id, tipo, operacion_id, huella, estado)
  VALUES (p_empresa_id, 'VENTA_CONTADO', p_operacion_id, p_huella, 'EN_PROCESO')
  ON CONFLICT (empresa_id, tipo, operacion_id) DO NOTHING;

  IF NOT FOUND THEN
    SELECT o.id, o.huella, o.estado, o.resultado
      INTO v_existente
      FROM saas.operacion_idempotente o
     WHERE o.empresa_id = p_empresa_id
       AND o.tipo = 'VENTA_CONTADO'
       AND o.operacion_id = p_operacion_id
     FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Operacion idempotente ilegible' USING ERRCODE = '40001';
    END IF;
    IF v_existente.huella IS DISTINCT FROM p_huella THEN
      RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
        'mensaje', 'operacion_id reutilizado con una carga distinta');
    END IF;
    IF v_existente.estado = 'PROCESADA' THEN
      RETURN jsonb_build_object('ok', TRUE, 'idempotente', TRUE,
        'venta', v_existente.resultado);
    END IF;
    RAISE EXCEPTION 'Operacion identica en curso; reintente' USING ERRCODE = '40001';
  END IF;

  -- Precios y stock SIEMPRE del servidor; nunca del cuerpo HTTP.
  FOR v_linea IN
    SELECT (x->>'inventario_id')::BIGINT AS inventario_id,
           (x->>'cantidad')::INTEGER AS cantidad
      FROM jsonb_array_elements(p_lineas) x
     ORDER BY 1
  LOOP
    v_cantidad := v_linea.cantidad;
    SELECT i.id, i.stock_actual, i.stock_reservado, pr.precio
      INTO v_inv
      FROM public.inventario i
      JOIN public.variante_producto vp ON vp.id = i.variante_producto_id
        AND vp.empresa_id = p_empresa_id AND vp.estado IS TRUE
      JOIN public.producto pr ON pr.id = vp.producto_id
        AND pr.empresa_id = p_empresa_id AND pr.estado IS TRUE
     WHERE i.id = v_linea.inventario_id
       AND i.empresa_id = p_empresa_id
       AND i.sucursal_id = p_sucursal_id
     FOR UPDATE OF i;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Inventario % no disponible en la sucursal', v_linea.inventario_id
        USING ERRCODE = '42501';
    END IF;
    v_disponible := v_inv.stock_actual - v_inv.stock_reservado;
    IF v_disponible < v_cantidad THEN
      RAISE EXCEPTION 'Stock insuficiente para inventario %', v_linea.inventario_id
        USING ERRCODE = '23514';
    END IF;
    v_precio := v_inv.precio;
    v_total := v_total + (v_precio * v_cantidad);
    v_lineas_resultado := v_lineas_resultado || jsonb_build_object(
      'inventario_id', v_linea.inventario_id,
      'cantidad', v_cantidad,
      'precio_unitario', v_precio,
      'subtotal', (v_precio * v_cantidad));
  END LOOP;

  INSERT INTO public.venta (empresa_id, empleado_id, sucursal_id, fecha_hora,
                            canal, estado, total)
  VALUES (p_empresa_id, v_empleado, p_sucursal_id, CURRENT_TIMESTAMP,
          'PRESENCIAL', 'PENDIENTE', v_total)
  RETURNING id INTO v_venta;

  FOR v_linea IN
    SELECT (x->>'inventario_id')::BIGINT AS inventario_id,
           (x->>'cantidad')::INTEGER AS cantidad
      FROM jsonb_array_elements(p_lineas) x
     ORDER BY 1
  LOOP
    SELECT pr.precio INTO v_precio
      FROM public.inventario i
      JOIN public.variante_producto vp ON vp.id = i.variante_producto_id
      JOIN public.producto pr ON pr.id = vp.producto_id
     WHERE i.id = v_linea.inventario_id AND i.empresa_id = p_empresa_id;

    INSERT INTO public.detalle_venta (empresa_id, venta_id, inventario_id,
                                      cantidad, precio_unitario)
    VALUES (p_empresa_id, v_venta, v_linea.inventario_id, v_linea.cantidad, v_precio);

    UPDATE public.inventario i
       SET stock_actual = i.stock_actual - v_linea.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_linea.inventario_id AND i.empresa_id = p_empresa_id;

    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_linea.inventario_id, p_usuario_id, 'SALIDA_VENTA',
       v_linea.cantidad, CURRENT_TIMESTAMP, 'Venta presencial de contado',
       'VENTA', v_venta);

    v_stock := v_stock || jsonb_build_object(
      'inventario_id', v_linea.inventario_id,
      'stock_actual', (SELECT i.stock_actual FROM public.inventario i
                        WHERE i.id = v_linea.inventario_id),
      'stock_reservado', (SELECT i.stock_reservado FROM public.inventario i
                           WHERE i.id = v_linea.inventario_id));
  END LOOP;

  INSERT INTO public.pago (empresa_id, venta_id, fecha_hora, monto, metodo, estado)
  VALUES (p_empresa_id, v_venta, CURRENT_TIMESTAMP, v_total, v_metodo, 'APROBADO')
  RETURNING id INTO v_pago;

  UPDATE public.venta v SET estado = 'COMPLETADA'
   WHERE v.id = v_venta AND v.empresa_id = p_empresa_id;

  -- Auditoria empresarial explicita (el esquema reconstruido solo tiene
  -- triggers de bitacora para inventario/reserva/etc., no para venta/pago).
  INSERT INTO public.bitacora (usuario_id, empresa_id, fecha_hora, accion,
                               entidad_afectada, descripcion)
  VALUES (p_usuario_id, p_empresa_id, CURRENT_TIMESTAMP, 'VENTA_CONTADO', 'venta',
          format('Venta %s por %s con total %s', v_venta, v_total, v_metodo));
  INSERT INTO public.bitacora (usuario_id, empresa_id, fecha_hora, accion,
                               entidad_afectada, descripcion)
  VALUES (p_usuario_id, p_empresa_id, CURRENT_TIMESTAMP, 'PAGO_CONTADO', 'pago',
          format('Pago %s de venta %s por %s', v_pago, v_venta, v_total));

  v_resultado := jsonb_build_object(
    'venta_id', v_venta,
    'pago_id', v_pago,
    'estado', 'COMPLETADA',
    'canal', 'PRESENCIAL',
    'metodo_pago', v_metodo,
    'total', v_total,
    'moneda', 'BOB',
    'lineas', v_lineas_resultado,
    'stock', v_stock,
    'operacion_id', p_operacion_id);

  UPDATE saas.operacion_idempotente o
     SET estado = 'PROCESADA', resultado = v_resultado, actualizado = NOW()
   WHERE o.empresa_id = p_empresa_id AND o.tipo = 'VENTA_CONTADO'
     AND o.operacion_id = p_operacion_id;

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE, 'venta', v_resultado);
END;
$sp$;

-- ===========================================================================
-- 6. ACL final de las rutinas tenant nuevas
-- ===========================================================================

DO $acl_sp$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  r RECORD;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN ('sp_crear_reserva_tenant', 'sp_cancelar_reserva_tenant',
                         'sp_finalizar_reserva_sin_compra_tenant',
                         'sp_registrar_venta_contado_tenant')
  LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', r.firma);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_runtime);
  END LOOP;
END;
$acl_sp$;

COMMIT;
