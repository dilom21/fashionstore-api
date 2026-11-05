-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA F (5BO)
-- CU03/CU04: ALTA DE PERSONAL EMPRESARIAL, INVITACION DE UN SOLO USO Y RBAC NOMINAL POR TENANT
--
-- Por que existe esta migracion
-- -----------------------------
-- 5BN dejo demostrado que el principal tenant `fs_saas_runtime` NO puede
-- insertar en `public.usuario` (42501 + FORCE RLS) y que la autorizacion de
-- CU04 solo permitia listar/rol/estado de membresias YA existentes. Faltaba el
-- minimo funcional para que un ADMINISTRADOR de empresa incorpore personal
-- nuevo, le asigne sucursal y rol empresariales y administre permisos sin
-- adquirir privilegios globales:
--
--   * `public.sp_alta_personal_tenant` - SECURITY DEFINER, propietario NOLOGIN
--     dedicado `saas_personal_owner`, EXECUTE SOLO para `fs_saas_runtime`.
--     Revalida EN POSTGRESQL la asercion HMAC (`saas.tenant_actual()` y
--     `saas.actor_actual()`), la membresia/rol del actor, la sucursal, el rol
--     empresarial, la empresa y la suscripcion vigente ANTES de tocar tablas
--     globales. `public.usuario.rol_id` se fuerza SIEMPRE a CLIENTE: el rol
--     empresarial vive unicamente en `usuario_empresa`. No hay escalada global.
--   * `saas.invitacion_personal` - contrasena inicial por invitacion de un solo
--     uso con expiracion. La base nunca recibe ni devuelve la contrasena en
--     claro: recibe el hash Argon2 ya calculado y guarda solo SHA-256 del token.
--   * `saas.permiso_default` + `saas.permiso_tenant` + `saas.tiene_permiso_tenant`
--     - matriz RBAC nominal. Los defaults son por NOMBRE DE ROL y la tabla
--     empresarial permite excepciones por tenant. No se reescribe
--     `public.rol_funcion` (matriz global del primer parcial).
--
-- No se modifica ninguna migracion previa (00100..00700), ni el onboarding
-- global 5BM, ni `.env`, ni flags productivos.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
BEGIN
  IF to_regprocedure('saas.tenant_actual()') IS NULL
     OR to_regclass('public.usuario') IS NULL
     OR to_regclass('public.usuario_empresa') IS NULL
     OR to_regclass('public.empleado') IS NULL
     OR to_regclass('public.sucursal') IS NULL
     OR to_regclass('public.rol') IS NULL
     OR to_regclass('public.suscripcion') IS NULL
     OR to_regclass('public.plan_saas') IS NULL THEN
    RAISE EXCEPTION '5BO: faltan objetos base de 00100..00500';
  END IF;
  IF to_regclass('saas.operacion_idempotente') IS NULL THEN
    RAISE EXCEPTION '5BO: falta saas.operacion_idempotente (5BM); aplique migraciones previas';
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Propietario NOLOGIN dedicado al bootstrap de personal
-- ===========================================================================
DO $rol_owner$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saas_personal_owner') THEN
    CREATE ROLE saas_personal_owner NOLOGIN NOSUPERUSER NOBYPASSRLS
      NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'saas_personal_owner') THEN
    RAISE EXCEPTION '5BO: saas_personal_owner debe ser NOLOGIN';
  END IF;
  IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'saas_personal_owner') THEN
    RAISE EXCEPTION '5BO: saas_personal_owner no puede ser superusuario ni BYPASSRLS';
  END IF;
END;
$rol_owner$;

-- ===========================================================================
-- 2. Actor real desde la asercion FIRMADA (nunca desde un GUC falseable)
-- ===========================================================================
-- `app.usuario_id` es un dato de auditoria: cualquier SQL de la sesion puede
-- reescribirlo. `saas.actor_actual()` recomputa el HMAC de la asercion de la
-- transaccion y devuelve el usuario firmado, solo para el principal runtime
-- reconocido. Se usa como autoridad del actor en el alta de personal.
CREATE OR REPLACE FUNCTION saas.actor_actual()
RETURNS BIGINT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $actor$
DECLARE
  v_bruto TEXT;
  v_partes TEXT[];
  v_empresa BIGINT;
  v_usuario BIGINT;
  v_expira BIGINT;
  v_clave TEXT;
  v_esperado TEXT;
  v_ahora BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM saas.principal_runtime p
     WHERE p.rol = session_user AND p.tipo = 'runtime' AND p.activo
  ) THEN
    RETURN NULL;
  END IF;

  v_bruto := current_setting('app.tenant_assertion', true);
  IF v_bruto IS NULL OR length(v_bruto) < 32 THEN
    RETURN NULL;
  END IF;
  v_partes := string_to_array(v_bruto, '|');
  IF array_length(v_partes, 1) <> 5 THEN
    RETURN NULL;
  END IF;
  BEGIN
    v_empresa := v_partes[1]::BIGINT;
    v_usuario := v_partes[2]::BIGINT;
    v_expira  := v_partes[3]::BIGINT;
  EXCEPTION WHEN others THEN
    RETURN NULL;
  END;
  IF v_empresa IS NULL OR v_empresa <= 0
     OR v_usuario IS NULL OR v_usuario <= 0 THEN
    RETURN NULL;
  END IF;
  IF v_expira < v_ahora OR v_expira > v_ahora + 3600 THEN
    RETURN NULL;
  END IF;

  SELECT s.clave INTO v_clave FROM saas.rls_secret s WHERE s.singleton;
  IF v_clave IS NULL THEN
    RETURN NULL;
  END IF;
  v_esperado := encode(
    saas.hmac_sha256(
      convert_to(v_clave, 'UTF8'),
      convert_to(v_partes[1] || '|' || v_partes[2] || '|' || v_partes[3]
                 || '|' || v_partes[4], 'UTF8')
    ),
    'hex'
  );
  IF v_partes[5] IS DISTINCT FROM v_esperado THEN
    RETURN NULL;
  END IF;

  -- La firma no basta: la membresia debe seguir vigente AHORA.
  IF NOT EXISTS (
    SELECT 1
      FROM public.usuario_empresa ue
      JOIN public.usuario u ON u.id = ue.usuario_id
      JOIN public.empresa e ON e.id = ue.empresa_id
      JOIN public.rol r ON r.id = ue.rol_id
      JOIN public.suscripcion s ON s.empresa_id = e.id
      JOIN public.plan_saas p ON p.id = s.plan_saas_id
     WHERE ue.usuario_id = v_usuario
       AND ue.empresa_id = v_empresa
       AND ue.estado IS TRUE
       AND u.estado IS TRUE
       AND e.estado IS TRUE
       AND r.estado IS TRUE
       AND p.estado IS TRUE
       AND s.estado = 'ACTIVA'
       AND (timezone('America/La_Paz', now()))::date
           BETWEEN s.fecha_inicio AND s.fecha_vencimiento
  ) THEN
    RETURN NULL;
  END IF;

  RETURN v_usuario;
END;
$actor$;

COMMENT ON FUNCTION saas.actor_actual() IS
  'Usuario firmado en la asercion HMAC de la transaccion. NULL = deny. Nunca usa app.usuario_id.';

-- ===========================================================================
-- 3. Matriz RBAC nominal
-- ===========================================================================
-- 3.1 Defaults por NOMBRE DE ROL (catalogo global de referencia, sin datos de
--     tenant). Una fila ausente significa DENEGADO. `ADMINISTRADOR` no puede
--     perder PERSONAL/ADMINISTRAR: su matriz es de solo lectura.
CREATE TABLE IF NOT EXISTS saas.permiso_default (
  rol_nombre TEXT NOT NULL,
  funcion    TEXT NOT NULL,
  accion     TEXT NOT NULL,
  permitido  BOOLEAN NOT NULL,
  CONSTRAINT pk_permiso_default PRIMARY KEY (rol_nombre, funcion, accion),
  CONSTRAINT ck_permiso_default_funcion CHECK (funcion ~ '^[A-Z_]{3,40}$'),
  CONSTRAINT ck_permiso_default_accion  CHECK (accion  ~ '^[A-Z_]{3,40}$')
);

COMMENT ON TABLE saas.permiso_default IS
  'Matriz RBAC nominal por nombre de rol base. Fila ausente = denegado. No reemplaza ni modifica public.rol_funcion.';

-- 3.2 Excepciones por empresa/rol. Tiene prioridad sobre el default.
CREATE TABLE IF NOT EXISTS saas.permiso_tenant (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id     BIGINT NOT NULL REFERENCES public.empresa(id)
                   ON UPDATE CASCADE ON DELETE CASCADE,
  rol_id         BIGINT NOT NULL REFERENCES public.rol(id)
                   ON UPDATE CASCADE ON DELETE RESTRICT,
  funcion        TEXT NOT NULL,
  accion         TEXT NOT NULL,
  permitido      BOOLEAN NOT NULL,
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_permiso_tenant UNIQUE (empresa_id, rol_id, funcion, accion),
  CONSTRAINT ck_permiso_tenant_funcion CHECK (funcion ~ '^[A-Z_]{3,40}$'),
  CONSTRAINT ck_permiso_tenant_accion  CHECK (accion  ~ '^[A-Z_]{3,40}$')
);

COMMENT ON TABLE saas.permiso_tenant IS
  'Excepciones RBAC por empresa y rol. Tiene prioridad sobre saas.permiso_default. Aislada por RLS.';

-- 3.3 Invitaciones de un solo uso.
CREATE TABLE IF NOT EXISTS saas.invitacion_personal (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id  BIGINT NOT NULL REFERENCES public.empresa(id)
                ON UPDATE CASCADE ON DELETE CASCADE,
  usuario_id  BIGINT NOT NULL REFERENCES public.usuario(id)
                ON UPDATE CASCADE ON DELETE CASCADE,
  empleado_id BIGINT,
  token_hash  TEXT NOT NULL,
  creada_en   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expira_en   TIMESTAMPTZ NOT NULL,
  usada_en    TIMESTAMPTZ,
  revocada_en TIMESTAMPTZ,
  CONSTRAINT uq_invitacion_token UNIQUE (token_hash),
  CONSTRAINT ck_invitacion_token CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_invitacion_expira CHECK (expira_en > creada_en)
);

COMMENT ON TABLE saas.invitacion_personal IS
  'Token de aceptacion de un solo uso (se guarda solo SHA-256). Permite la contrasena inicial sin almacenar ni devolver contrasenas en claro.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_invitacion_activa
  ON saas.invitacion_personal (usuario_id)
  WHERE usada_en IS NULL AND revocada_en IS NULL;

-- ===========================================================================
-- 4. Politicas RLS y privilegios de los objetos nuevos
-- ===========================================================================
DO $rls_nuevas$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  -- permiso_tenant: aislamiento puro por empresa.
  EXECUTE 'ALTER TABLE saas.permiso_tenant ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE saas.permiso_tenant FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_aislamiento ON saas.permiso_tenant';
  EXECUTE format(
    'CREATE POLICY tenant_aislamiento ON saas.permiso_tenant AS PERMISSIVE '
    'FOR ALL TO %I USING (empresa_id = saas.tenant_actual()) '
    'WITH CHECK (empresa_id = saas.tenant_actual())', v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas.permiso_tenant FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE saas.permiso_tenant TO %I',
                 v_runtime);

  -- invitacion_personal: el tenant la lee/administra solo para su empresa; la
  -- aceptacion (sin tenant) la ejecuta el propietario NOLOGIN via funcion.
  EXECUTE 'ALTER TABLE saas.invitacion_personal ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE saas.invitacion_personal FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_aislamiento ON saas.invitacion_personal';
  EXECUTE format(
    'CREATE POLICY tenant_aislamiento ON saas.invitacion_personal AS PERMISSIVE '
    'FOR ALL TO %I USING (empresa_id = saas.tenant_actual()) '
    'WITH CHECK (empresa_id = saas.tenant_actual())', v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas.invitacion_personal FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE saas.invitacion_personal TO %I',
                 v_runtime);

  -- permiso_default: catalogo global de referencia, solo lectura.
  EXECUTE format('REVOKE ALL ON TABLE saas.permiso_default FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT SELECT ON TABLE saas.permiso_default TO %I', v_runtime);
END;
$rls_nuevas$;

-- Los privilegios de tabla del runtime se reafirman aqui: el alta la ejecuta
-- una funcion DEFINER, el runtime NO recibe INSERT en public.usuario.
DO $reafirmar_runtime$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON TABLE public.usuario FROM %I', v_runtime);
  EXECUTE format(
    'REVOKE ALL ON FUNCTION saas.actor_actual() FROM PUBLIC, %I, saas_admin_runtime',
    v_runtime);
END;
$reafirmar_runtime$;

-- ===========================================================================
-- 5. Semilla de la matriz nominal (idempotente)
-- ===========================================================================
INSERT INTO saas.permiso_default (rol_nombre, funcion, accion, permitido) VALUES
  ('ADMINISTRADOR','PERSONAL','CONSULTAR',TRUE),
  ('ADMINISTRADOR','PERSONAL','ADMINISTRAR',TRUE),
  ('ADMINISTRADOR','SUCURSALES','CONSULTAR',TRUE),
  ('ADMINISTRADOR','SUCURSALES','ADMINISTRAR',TRUE),
  ('ADMINISTRADOR','CATALOGO','CONSULTAR',TRUE),
  ('ADMINISTRADOR','CATALOGO','ADMINISTRAR',TRUE),
  ('ADMINISTRADOR','COMPRAS','CONSULTAR',TRUE),
  ('ADMINISTRADOR','COMPRAS','GESTIONAR',TRUE),
  ('ADMINISTRADOR','DEVOLUCIONES','CONSULTAR',TRUE),
  ('ADMINISTRADOR','DEVOLUCIONES','GESTIONAR',TRUE),
  ('ADMINISTRADOR','RESERVAS','CONSULTAR',TRUE),
  ('ADMINISTRADOR','RESERVAS','GESTIONAR',TRUE),
  ('ADMINISTRADOR','VENTAS','CONSULTAR',TRUE),
  ('ADMINISTRADOR','VENTAS','REGISTRAR',TRUE),
  ('ADMINISTRADOR','REPORTES','CONSULTAR',TRUE),

  ('ENCARGADO_SUCURSAL','PERSONAL','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','SUCURSALES','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','CATALOGO','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','CATALOGO','ADMINISTRAR',TRUE),
  ('ENCARGADO_SUCURSAL','COMPRAS','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','COMPRAS','GESTIONAR',TRUE),
  ('ENCARGADO_SUCURSAL','DEVOLUCIONES','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','DEVOLUCIONES','GESTIONAR',TRUE),
  ('ENCARGADO_SUCURSAL','RESERVAS','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','RESERVAS','GESTIONAR',TRUE),
  ('ENCARGADO_SUCURSAL','VENTAS','CONSULTAR',TRUE),
  ('ENCARGADO_SUCURSAL','VENTAS','REGISTRAR',TRUE),
  ('ENCARGADO_SUCURSAL','REPORTES','CONSULTAR',TRUE),

  ('CAJERO','SUCURSALES','CONSULTAR',TRUE),
  ('CAJERO','CATALOGO','CONSULTAR',TRUE),
  ('CAJERO','DEVOLUCIONES','CONSULTAR',TRUE),
  ('CAJERO','DEVOLUCIONES','REGISTRAR',TRUE),
  ('CAJERO','RESERVAS','CONSULTAR',TRUE),
  ('CAJERO','RESERVAS','GESTIONAR',TRUE),
  ('CAJERO','VENTAS','CONSULTAR',TRUE),
  ('CAJERO','VENTAS','REGISTRAR',TRUE),

  ('CLIENTE','CATALOGO','CONSULTAR',TRUE),
  ('CLIENTE','RESERVAS','CONSULTAR',TRUE),
  ('CLIENTE','RESERVAS','REGISTRAR',TRUE)
ON CONFLICT (rol_nombre, funcion, accion)
DO UPDATE SET permitido = EXCLUDED.permitido;

-- ===========================================================================
-- 6. Permiso efectivo por tenant
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas.tiene_permiso_tenant(
  p_empresa_id BIGINT,
  p_rol_id     BIGINT,
  p_funcion    TEXT,
  p_accion     TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $permiso$
DECLARE
  v_funcion TEXT := upper(btrim(COALESCE(p_funcion, '')));
  v_accion  TEXT := upper(btrim(COALESCE(p_accion, '')));
  v_tenant  BIGINT := saas.tenant_actual();
  v_valor   BOOLEAN;
BEGIN
  IF p_empresa_id IS NULL OR p_rol_id IS NULL
     OR p_empresa_id <= 0 OR p_rol_id <= 0
     OR v_funcion = '' OR v_accion = '' THEN
    RETURN FALSE;
  END IF;
  -- Nunca resolver permisos de una empresa distinta a la autenticada.
  IF v_tenant IS NULL OR v_tenant <> p_empresa_id THEN
    RETURN FALSE;
  END IF;

  -- 1) Excepcion explicita del tenant (RLS garantiza que solo se ve su fila).
  SELECT pt.permitido INTO v_valor
    FROM saas.permiso_tenant pt
   WHERE pt.empresa_id = p_empresa_id
     AND pt.rol_id = p_rol_id
     AND pt.funcion = v_funcion
     AND pt.accion = v_accion;
  IF FOUND THEN
    RETURN v_valor;
  END IF;

  -- 2) Default por NOMBRE del rol asignado EN LA EMPRESA. Los roles del
  --    esquema historico son globales; el nombre determina la matriz base y
  --    la excepcion por empresa (paso 1) siempre tiene prioridad. No se exige
  --    que exista ya una membresia con ese rol: la empresa puede configurar
  --    la matriz antes de contratar a la primera persona con ese perfil.
  SELECT pd.permitido INTO v_valor
    FROM public.rol r
    JOIN saas.permiso_default pd
      ON pd.rol_nombre = upper(r.nombre)
     AND pd.funcion = v_funcion
     AND pd.accion = v_accion
   WHERE r.id = p_rol_id
     AND r.estado IS TRUE
   LIMIT 1;
  IF FOUND THEN
    RETURN v_valor;
  END IF;

  -- 3) Sin default: denegado (los roles personalizados exigen excepcion).
  RETURN FALSE;
END;
$permiso$;

COMMENT ON FUNCTION saas.tiene_permiso_tenant(BIGINT, BIGINT, TEXT, TEXT) IS
  'RBAC nominal: excepcion por empresa > default por nombre de rol > DENEGADO. Nunca cruza empresas.';

-- ===========================================================================
-- 7. Alta de personal (SECURITY DEFINER, propietario NOLOGIN)
-- ===========================================================================
CREATE OR REPLACE FUNCTION public.sp_alta_personal_tenant(
  p_empresa_id       BIGINT,
  p_actor_usuario_id BIGINT,
  p_correo           TEXT,
  p_nombres          TEXT,
  p_apellidos        TEXT,
  p_ci               TEXT,
  p_telefono         TEXT,
  p_sucursal_id      BIGINT,
  p_rol_id           BIGINT,
  p_password_hash    TEXT,
  p_token_hash       TEXT,
  p_expira_en        TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, saas
AS $alta$
DECLARE
  v_correo      TEXT := lower(btrim(COALESCE(p_correo, '')));
  v_documento   TEXT := btrim(COALESCE(p_ci, ''));
  v_tenant      BIGINT;
  v_actor       BIGINT;
  v_actor_rol   BIGINT;
  v_rol_nombre  TEXT;
  v_cliente_rol BIGINT;
  v_sucursal    BIGINT;
  v_usuario     BIGINT;
  v_empleado    BIGINT;
  v_membresia   BIGINT;
BEGIN
  -- 7.1 Doble principal: el runtime tenant y nadie mas.
  IF NOT saas.es_principal_runtime() THEN
    RAISE EXCEPTION 'Alta de personal fuera del principal tenant'
      USING ERRCODE = '42501';
  END IF;

  -- 7.2 Identidad: asercion HMAC valida y actor = usuario firmado.
  v_tenant := saas.tenant_actual();
  v_actor  := saas.actor_actual();
  IF v_tenant IS NULL OR v_tenant <> p_empresa_id
     OR v_actor IS NULL OR v_actor <> p_actor_usuario_id THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 403,
      'codigo_error', 'CONTEXTO_NO_AUTORIZADO',
      'mensaje', 'Operacion no autorizada');
  END IF;

  -- 7.3 Datos obligatorios y formato del hash.
  IF v_correo = '' OR v_documento = ''
     OR btrim(COALESCE(p_nombres, '')) = ''
     OR btrim(COALESCE(p_apellidos, '')) = '' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'DATOS_INCOMPLETOS', 'mensaje', 'Datos del personal incompletos');
  END IF;
  IF v_correo !~ '^[^\s@]+@[^\s@]+\.[^\s@]+$' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'CORREO_INVALIDO', 'mensaje', 'Datos del personal incompletos');
  END IF;
  IF p_password_hash IS NULL OR p_password_hash !~ '^\$argon2' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'HASH_INVALIDO', 'mensaje', 'Datos del personal incompletos');
  END IF;
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'TOKEN_INVALIDO', 'mensaje', 'Datos del personal incompletos');
  END IF;
  IF p_expira_en IS NULL
     OR p_expira_en <= now()
     OR p_expira_en > now() + interval '7 days' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'EXPIRACION_INVALIDA', 'mensaje', 'Datos del personal incompletos');
  END IF;

  -- 7.4 Empresa, suscripcion vigente y rol ADMINISTRADOR del actor.
  --     Sin `FOR UPDATE`: el propietario NOLOGIN solo tiene SELECT sobre
  --     `empresa`. La serializacion se hace con un advisory lock por empresa,
  --     que no exige privilegios de tabla ni eleva al runtime.
  PERFORM pg_advisory_xact_lock(hashtextextended(
    '5bo:alta-personal:' || p_empresa_id::text, 0));

  PERFORM 1 FROM public.empresa e
   WHERE e.id = p_empresa_id AND e.estado IS TRUE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404,
      'codigo_error', 'EMPRESA_NO_DISPONIBLE', 'mensaje', 'Empresa no disponible');
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM public.suscripcion s
      JOIN public.plan_saas p ON p.id = s.plan_saas_id
     WHERE s.empresa_id = p_empresa_id
       AND s.estado = 'ACTIVA'
       AND p.estado IS TRUE
       AND (timezone('America/La_Paz', now()))::date
           BETWEEN s.fecha_inicio AND s.fecha_vencimiento
  ) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 403,
      'codigo_error', 'SUSCRIPCION_NO_VIGENTE',
      'mensaje', 'Suscripcion empresarial no vigente');
  END IF;

  SELECT ue.rol_id INTO v_actor_rol
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id = ue.usuario_id
    JOIN public.empleado em
      ON em.usuario_id = ue.usuario_id AND em.empresa_id = ue.empresa_id
     AND em.estado IS TRUE
   WHERE ue.empresa_id = p_empresa_id
     AND ue.usuario_id = v_actor
     AND ue.estado IS TRUE
     AND u.estado IS TRUE
   LIMIT 1;
  IF v_actor_rol IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 403,
      'codigo_error', 'ACTOR_NO_AUTORIZADO', 'mensaje', 'Operacion no autorizada');
  END IF;
  IF NOT saas.tiene_permiso_tenant(p_empresa_id, v_actor_rol,
                                   'PERSONAL', 'ADMINISTRAR') THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 403,
      'codigo_error', 'PERMISO_INSUFICIENTE', 'mensaje', 'Permiso empresarial insuficiente');
  END IF;

  -- 7.5 Sucursal de la MISMA empresa y activa.
  SELECT s.id INTO v_sucursal
    FROM public.sucursal s
   WHERE s.id = p_sucursal_id
     AND s.empresa_id = p_empresa_id
     AND s.estado IS TRUE;
  IF v_sucursal IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 404,
      'codigo_error', 'SUCURSAL_NO_DISPONIBLE', 'mensaje', 'Sucursal no disponible');
  END IF;

  -- 7.6 Rol asignable: base interno usado por la empresa o rol propio de ella.
  --     CLIENTE nunca es rol de personal. Un rol exclusivo de otra empresa no
  --     se puede asignar (no revela cual empresa lo usa).
  SELECT r.nombre INTO v_rol_nombre
    FROM public.rol r
   WHERE r.id = p_rol_id
     AND r.estado IS TRUE
     AND upper(r.nombre) <> 'CLIENTE'
     AND (
       upper(r.nombre) IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL', 'CAJERO')
       OR EXISTS (
         SELECT 1 FROM public.usuario_empresa ue2
          WHERE ue2.empresa_id = p_empresa_id AND ue2.rol_id = r.id
       )
     );
  IF v_rol_nombre IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 422,
      'codigo_error', 'ROL_NO_ASIGNABLE', 'mensaje', 'Rol no asignable al personal de esta empresa');
  END IF;

  -- 7.7 Conflicto de correo: NUNCA se vincula una cuenta existente y la
  --     respuesta es identica exista en esta empresa o en otra.
  IF EXISTS (SELECT 1 FROM public.usuario u WHERE lower(u.correo) = v_correo) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'codigo_error', 'CORREO_NO_DISPONIBLE',
      'mensaje', 'No fue posible registrar el personal con los datos indicados');
  END IF;
  IF EXISTS (SELECT 1 FROM public.empleado em
              WHERE em.empresa_id = p_empresa_id
                AND lower(em.ci) = lower(v_documento)) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'codigo_error', 'DOCUMENTO_DUPLICADO',
      'mensaje', 'Documento de empleado duplicado en esta empresa');
  END IF;

  -- 7.8 Rol GLOBAL CLIENTE: el runtime jamas escribe roles globales altos.
  SELECT r.id INTO v_cliente_rol
    FROM public.rol r WHERE r.nombre = 'CLIENTE' AND r.estado IS TRUE;
  IF v_cliente_rol IS NULL THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'codigo_error', 'ROL_BASE_AUSENTE', 'mensaje', 'Configuracion de roles incompleta');
  END IF;

  BEGIN
    -- 7.8.b La tabla global `public.usuario` tiene el trigger historico de
    -- bitacora, que rechaza escrituras del principal tenant. El propietario
    -- NOLOGIN de esta funcion es la UNICA excepcion autorizada y deja el
    -- evento en la bitacora EMPRESARIAL de esta empresa (no en la global).
    PERFORM set_config('app.alta_personal_empresa', p_empresa_id::text, true);

    INSERT INTO public.usuario (rol_id, correo, password_hash, fecha_creacion, estado)
    VALUES (v_cliente_rol, v_correo, p_password_hash, NOW(), TRUE)
    RETURNING id INTO v_usuario;

    INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado)
    VALUES (v_usuario, p_empresa_id, p_rol_id, TRUE)
    RETURNING id INTO v_membresia;

    INSERT INTO public.empleado (empresa_id, usuario_id, sucursal_id, nombres,
                                apellidos, ci, telefono, fecha_contratacion, estado)
    VALUES (p_empresa_id, v_usuario, v_sucursal, btrim(p_nombres),
            btrim(p_apellidos), v_documento, p_telefono,
            (timezone('America/La_Paz', now()))::date, TRUE)
    RETURNING id INTO v_empleado;

    INSERT INTO saas.invitacion_personal
      (empresa_id, usuario_id, empleado_id, token_hash, expira_en)
    VALUES (p_empresa_id, v_usuario, v_empleado, p_token_hash, p_expira_en);
  EXCEPTION WHEN unique_violation THEN
    -- Carrera de dos altas con el mismo correo/documento: mismo contrato.
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 409,
      'codigo_error', 'CORREO_NO_DISPONIBLE',
      'mensaje', 'No fue posible registrar el personal con los datos indicados');
  END;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, v_actor, 'PERSONAL_ALTA', 'usuario_empresa',
    format('Alta de personal usuario %s con rol %s en sucursal %s',
           v_usuario, v_rol_nombre, v_sucursal),
    'usuario_empresa');

  RETURN jsonb_build_object(
    'ok', TRUE,
    'empresa_id', p_empresa_id,
    'usuario_id', v_usuario,
    'empleado_id', v_empleado,
    'membresia_id', v_membresia,
    'sucursal_id', v_sucursal,
    'correo', v_correo,
    'rol', v_rol_nombre,
    'estado_membresia', TRUE,
    'expira_en', p_expira_en);
END;
$alta$;

COMMENT ON FUNCTION public.sp_alta_personal_tenant(
  BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT,
  TEXT, TEXT, TIMESTAMPTZ) IS
  'Alta de personal nuevo: asercion HMAC + actor firmado + admin + sucursal + rol + suscripcion. Rol global siempre CLIENTE.';

-- ===========================================================================
-- 8. Aceptacion de invitacion (un solo uso, sin contrasena en claro)
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas.aceptar_invitacion_personal(
  p_token_hash    TEXT,
  p_password_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, saas
AS $aceptar$
DECLARE
  v_invitacion RECORD;
BEGIN
  IF NOT saas.es_principal_runtime() THEN
    RAISE EXCEPTION 'Aceptacion de invitacion fuera del principal tenant'
      USING ERRCODE = '42501';
  END IF;
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 400,
      'mensaje', 'Invitacion invalida o expirada');
  END IF;
  IF p_password_hash IS NULL OR p_password_hash !~ '^\$argon2' THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 400,
      'mensaje', 'Invitacion invalida o expirada');
  END IF;

  SELECT i.id, i.empresa_id, i.usuario_id, i.empleado_id
    INTO v_invitacion
    FROM saas.invitacion_personal i
   WHERE i.token_hash = p_token_hash
     AND i.usada_en IS NULL
     AND i.revocada_en IS NULL
     AND i.expira_en > now()
   FOR UPDATE;

  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 400,
      'mensaje', 'Invitacion invalida o expirada');
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM public.usuario u
      JOIN public.usuario_empresa ue ON ue.usuario_id = u.id
      JOIN public.empresa e ON e.id = ue.empresa_id
     WHERE u.id = v_invitacion.usuario_id
       AND ue.empresa_id = v_invitacion.empresa_id
       AND u.estado IS TRUE
       AND ue.estado IS TRUE
       AND e.estado IS TRUE
  ) THEN
    RETURN jsonb_build_object('ok', FALSE, 'codigo', 400,
      'mensaje', 'Invitacion invalida o expirada');
  END IF;

  -- El trigger de bitacora de `public.usuario` necesita la empresa destino y
  -- el autor; se fijan LOCAL a esta transaccion, no se filtran al pool.
  PERFORM set_config('app.alta_personal_empresa',
                     v_invitacion.empresa_id::text, true);
  PERFORM set_config('app.usuario_id',
                     v_invitacion.usuario_id::text, true);

  UPDATE public.usuario u
     SET password_hash = p_password_hash
   WHERE u.id = v_invitacion.usuario_id;
  UPDATE saas.invitacion_personal i
     SET usada_en = now()
   WHERE i.id = v_invitacion.id;

  INSERT INTO public.bitacora
    (usuario_id, empresa_id, fecha_hora, accion, entidad_afectada, descripcion)
  VALUES (v_invitacion.usuario_id, v_invitacion.empresa_id, CURRENT_TIMESTAMP,
          'INVITACION_ACEPTADA', 'usuario',
          format('Invitacion aceptada por el usuario %s', v_invitacion.usuario_id));

  RETURN jsonb_build_object('ok', TRUE, 'empresa_id', v_invitacion.empresa_id,
                            'usuario_id', v_invitacion.usuario_id);
END;
$aceptar$;

COMMENT ON FUNCTION saas.aceptar_invitacion_personal(TEXT, TEXT) IS
  'Consume una invitacion vigente y fija el hash Argon2. Token de un solo uso.';

-- ===========================================================================
-- 9. Politicas del propietario NOLOGIN + grants acotados
-- ===========================================================================
DO $politicas_owner$
DECLARE
  v_owner text := 'saas_personal_owner';
BEGIN
  -- Escrituras del bootstrap sobre tablas con FORCE RLS. El propietario no es
  -- alcanzable por el tenant: es NOLOGIN, NOINHERIT y solo recibe EXECUTE a
  -- traves de las dos funciones de esta migracion.
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_alta ON public.usuario');
  EXECUTE format('CREATE POLICY personal_owner_alta ON public.usuario AS PERMISSIVE '
                 'FOR ALL TO %I USING (true) WITH CHECK (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_alta ON public.usuario_empresa');
  EXECUTE format('CREATE POLICY personal_owner_alta ON public.usuario_empresa AS PERMISSIVE '
                 'FOR ALL TO %I USING (true) WITH CHECK (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_alta ON public.empleado');
  EXECUTE format('CREATE POLICY personal_owner_alta ON public.empleado AS PERMISSIVE '
                 'FOR ALL TO %I USING (true) WITH CHECK (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_lectura ON public.empresa');
  EXECUTE format('CREATE POLICY personal_owner_lectura ON public.empresa AS PERMISSIVE '
                 'FOR SELECT TO %I USING (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_lectura ON public.suscripcion');
  EXECUTE format('CREATE POLICY personal_owner_lectura ON public.suscripcion AS PERMISSIVE '
                 'FOR SELECT TO %I USING (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_lectura ON public.plan_saas');
  EXECUTE format('CREATE POLICY personal_owner_lectura ON public.plan_saas AS PERMISSIVE '
                 'FOR SELECT TO %I USING (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_lectura ON public.sucursal');
  EXECUTE format('CREATE POLICY personal_owner_lectura ON public.sucursal AS PERMISSIVE '
                 'FOR SELECT TO %I USING (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_alta ON saas.permiso_tenant');
  EXECUTE format('CREATE POLICY personal_owner_alta ON saas.permiso_tenant AS PERMISSIVE '
                 'FOR ALL TO %I USING (true) WITH CHECK (true)', v_owner);
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_alta ON saas.invitacion_personal');
  EXECUTE format('CREATE POLICY personal_owner_alta ON saas.invitacion_personal AS PERMISSIVE '
                 'FOR ALL TO %I USING (true) WITH CHECK (true)', v_owner);
  -- Bitacora del alta y de la aceptacion. El propietario no es alcanzable por
  -- HTTP: solo inserta desde las funciones DEFINER de esta migracion.
  EXECUTE format('DROP POLICY IF EXISTS personal_owner_bitacora ON public.bitacora');
  EXECUTE format('CREATE POLICY personal_owner_bitacora ON public.bitacora AS PERMISSIVE '
                 'FOR INSERT TO %I WITH CHECK (empresa_id IS NOT NULL)', v_owner);

  EXECUTE format('GRANT USAGE ON SCHEMA public, saas TO %I', v_owner);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE public.usuario, '
                 'public.usuario_empresa, public.empleado TO %I', v_owner);
  EXECUTE format('GRANT SELECT ON TABLE public.empresa, public.suscripcion, '
                 'public.plan_saas, public.sucursal, public.rol, public.ciudad TO %I', v_owner);
  EXECUTE format('GRANT INSERT ON TABLE public.bitacora TO %I', v_owner);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE saas.invitacion_personal TO %I', v_owner);
  EXECUTE format('GRANT SELECT ON TABLE saas.permiso_default TO %I', v_owner);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE saas.permiso_tenant TO %I', v_owner);
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', v_owner);
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA saas TO %I', v_owner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.fn_bitacora_evento(BIGINT,BIGINT,TEXT,TEXT,TEXT,TEXT) TO %I', v_owner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.tiene_permiso_tenant(BIGINT,BIGINT,TEXT,TEXT) TO %I', v_owner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.es_principal_runtime() TO %I', v_owner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.tenant_actual() TO %I', v_owner);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.actor_actual() TO %I', v_owner);
  -- El propietario NO puede administrar el plano global.
  EXECUTE 'REVOKE ALL ON SCHEMA saas_admin FROM PUBLIC, saas_personal_owner';
END;
$politicas_owner$;

-- 9.1 Propiedad de las funciones de bootstrap: NOLOGIN, no el owner de tablas.
DO $ownership$
BEGIN
  ALTER FUNCTION public.sp_alta_personal_tenant(
    BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT,
    TEXT, TEXT, TIMESTAMPTZ) OWNER TO saas_personal_owner;
  ALTER FUNCTION saas.aceptar_invitacion_personal(TEXT, TEXT) OWNER TO saas_personal_owner;
  ALTER FUNCTION saas.actor_actual() OWNER TO saas_rls_owner;

  REVOKE ALL ON FUNCTION public.sp_alta_personal_tenant(
    BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT,
    TEXT, TEXT, TIMESTAMPTZ) FROM PUBLIC, fs_saas_runtime, saas_admin_runtime;
  REVOKE ALL ON FUNCTION saas.aceptar_invitacion_personal(TEXT, TEXT)
    FROM PUBLIC, fs_saas_runtime, saas_admin_runtime;
  REVOKE ALL ON FUNCTION saas.actor_actual()
    FROM PUBLIC, fs_saas_runtime, saas_admin_runtime;
  REVOKE ALL ON FUNCTION saas.tiene_permiso_tenant(BIGINT,BIGINT,TEXT,TEXT)
    FROM PUBLIC, fs_saas_runtime, saas_admin_runtime;

  GRANT EXECUTE ON FUNCTION public.sp_alta_personal_tenant(
    BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT,
    TEXT, TEXT, TIMESTAMPTZ) TO fs_saas_runtime;
  GRANT EXECUTE ON FUNCTION saas.aceptar_invitacion_personal(TEXT, TEXT) TO fs_saas_runtime;
  GRANT EXECUTE ON FUNCTION saas.actor_actual() TO fs_saas_runtime;
  GRANT EXECUTE ON FUNCTION saas.tiene_permiso_tenant(BIGINT,BIGINT,TEXT,TEXT)
    TO fs_saas_runtime;
END;
$ownership$;

-- La funcion actor_actual es SECURITY DEFINER y lee saas.rls_secret: su
-- propietario `saas_rls_owner` ya posee esa lectura desde 00400.
GRANT SELECT ON TABLE saas.rls_secret, saas.principal_runtime TO saas_rls_owner;

-- ===========================================================================
-- 10. Excepcion auditada del trigger de bitacora para la identidad global
-- ===========================================================================
-- `public.usuario` es una tabla GLOBAL y tiene `trg_bitacora_usuario`
-- (`public.fn_registrar_bitacora`, SECURITY INVOKER). Ese trigger rechaza con
-- 42501 cualquier escritura del principal tenant para impedir que fabrique
-- eventos globales. El alta de personal SI necesita crear la identidad global
-- desde una funcion SECURITY DEFINER.
--
-- La unica excepcion es `current_user = 'saas_personal_owner'`: un rol NOLOGIN,
-- NOINHERIT, sin SUPERUSER/BYPASSRLS, sin membership para el runtime y sin
-- acceso HTTP. En ese caso el evento NO va a `saas.bitacora_global` (sigue
-- prohibido para el tenant) sino a `public.bitacora` con la empresa destino,
-- que se toma del GUC `app.alta_personal_empresa` fijado DENTRO de
-- `sp_alta_personal_tenant` / `saas.aceptar_invitacion_personal`.
--
-- Todo lo demas del trigger se conserva textualmente (misma semantica 5BL).
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_usuario_text TEXT := current_setting('app.usuario_id', TRUE);
  v_usuario_id   BIGINT;
  v_empresa_text TEXT;
  v_empresa_id   BIGINT;
  v_registro_id  BIGINT;
  v_accion       TEXT;
  v_fila         JSONB;
  v_tenant       BIGINT;
  v_ip           TEXT := NULLIF(btrim(COALESCE(current_setting('app.ip', TRUE), '')), '');
BEGIN
  IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN
    v_usuario_id := v_usuario_text::BIGINT;
  END IF;

  IF TG_OP = 'DELETE' THEN v_fila := to_jsonb(OLD); ELSE v_fila := to_jsonb(NEW); END IF;

  v_empresa_text := v_fila->>'empresa_id';
  IF v_empresa_text IS NOT NULL AND v_empresa_text ~ '^[0-9]+$' THEN
    v_empresa_id := v_empresa_text::BIGINT;
  END IF;

  CASE TG_OP
    WHEN 'INSERT' THEN v_accion := 'CREAR';
    WHEN 'UPDATE' THEN v_accion := 'MODIFICAR';
    WHEN 'DELETE' THEN v_accion := 'ELIMINAR';
    ELSE v_accion := TG_OP;
  END CASE;

  IF TG_OP = 'DELETE' THEN
    v_registro_id := NULLIF(to_jsonb(OLD)->>'id', '')::BIGINT;
  ELSE
    v_registro_id := NULLIF(to_jsonb(NEW)->>'id', '')::BIGINT;
  END IF;

  IF v_empresa_id IS NULL THEN
    -- Tabla global (usuario) o fila sin tenant: NUNCA se escribe en la bitacora
    -- empresarial. Un principal tenant no puede fabricar eventos globales.
    IF saas.es_principal_runtime() THEN
      -- 5BO: excepcion acotada al propietario NOLOGIN del alta de personal.
      IF current_user = 'saas_personal_owner' THEN
        v_empresa_text := NULLIF(btrim(COALESCE(
          current_setting('app.alta_personal_empresa', TRUE), '')), '');
        IF v_empresa_text IS NOT NULL AND v_empresa_text ~ '^[0-9]+$' THEN
          v_empresa_id := v_empresa_text::BIGINT;
        END IF;
      END IF;
      IF v_empresa_id IS NULL THEN
        RAISE EXCEPTION
          'Auditoria global rechazada para el principal tenant (tabla %)', TG_TABLE_NAME
          USING ERRCODE = '42501';
      END IF;
    END IF;

    IF v_empresa_id IS NULL THEN
      INSERT INTO saas.bitacora_global
        (actor_sql, accion, entidad_afectada, entidad_id, descripcion, usuario_id, ip)
      VALUES
        (session_user, v_accion, TG_TABLE_NAME, v_registro_id,
         format('Operacion %s sobre registro ID %s', v_accion, COALESCE(v_registro_id::TEXT, '?')),
         v_usuario_id, v_ip);
    ELSE
      INSERT INTO public.bitacora
        (usuario_id, empresa_id, fecha_hora, accion, entidad_afectada, descripcion)
      VALUES
        (v_usuario_id, v_empresa_id, CURRENT_TIMESTAMP, v_accion, TG_TABLE_NAME,
         format('Operacion %s sobre registro ID %s', v_accion, COALESCE(v_registro_id::TEXT, '?')));
    END IF;
  ELSE
    v_tenant := saas.tenant_actual();
    IF v_tenant IS NOT NULL AND v_tenant <> v_empresa_id THEN
      RAISE EXCEPTION
        'Auditoria cruzada: fila de empresa % con contexto %', v_empresa_id, v_tenant
        USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.bitacora
      (usuario_id, empresa_id, fecha_hora, accion, entidad_afectada, descripcion)
    VALUES
      (v_usuario_id, v_empresa_id, CURRENT_TIMESTAMP, v_accion, TG_TABLE_NAME,
       format('Operacion %s sobre registro ID %s', v_accion, COALESCE(v_registro_id::TEXT, '?')));
  END IF;

  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$function$;

COMMIT;
