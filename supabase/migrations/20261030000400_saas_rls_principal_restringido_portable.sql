-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA C (5BK)
-- PRINCIPAL SQL RESTRINGIDO + POLITICAS RLS MULTITENANT EFECTIVAS
--
-- Objetivo
-- --------
-- Instalar un aislamiento por empresa que NO dependa de un GUC escrito por
-- FastAPI. `set_config('app.empresa_id', ...)` es un dato de auditoria: cualquier
-- consulta SQL puede falsificarlo. Por eso las politicas usan
-- `saas.tenant_actual()`, que resuelve la empresa a partir de una ASERCION
-- FIRMADA (HMAC-SHA256) cuyo secreto vive en `saas.rls_secret` (tabla sin
-- privilegios para el rol de ejecucion y sin BYPASSRLS de por medio).
--
-- La asercion tiene formato texto:
--     <empresa_id>|<usuario_id>|<epoch_expiracion>|<nonce>|<hmac_hex>
-- y ademas de la firma se revalida en base: membresia activa, empresa activa,
-- rol activo, plan activo y suscripcion ACTIVA vigente.
--
-- Decisiones de privilegios
-- -------------------------
-- * El rol de ejecucion (por defecto `fs_saas_runtime`) NO es propietario de
--   ninguna tabla, NO es SUPERUSER, NO es BYPASSRLS y NO hereda roles.
-- * `saas_rls_owner` es NOLOGIN y solo actua como propietario de las funciones
--   SECURITY DEFINER. Sus unicas politicas son `owner_lectura` sobre el catalogo
--   de identidad necesario para validar la asercion.
-- * `FORCE ROW LEVEL SECURITY` se aplica a las 31 tablas empresariales y a las
--   tablas SaaS con `empresa_id`, para que ni el propietario (si algun dia
--   fuese un rol con login) quede exento.
-- * `plan_saas` es catalogo global de planes: solo lectura para el runtime.
--
-- Requisitos
-- ----------
-- * Migraciones 20261030000100..000300 ya aplicadas (empresa_id en 31 tablas).
-- * Rol de ejecucion ya creado en el cluster (createdb/createrole NO se hace
--   aqui: los roles son globales del cluster y se aprovisionan aparte).
-- * Ejecutar como propietario de las tablas / superusuario del laboratorio.
--
-- Parametros de sesion opcionales (mismo estilo que 20261030000200):
--   SET saas.runtime_role = 'fs_saas_runtime';   -- rol de ejecucion de la API
--   SET saas.rls_owner_role = 'saas_rls_owner';  -- propietario NOLOGIN
--
-- Idempotente: puede aplicarse dos veces sin duplicar politicas ni funciones.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
  v_rol     record;
BEGIN
  IF to_regclass('public.usuario_empresa') IS NULL
     OR to_regclass('public.empresa') IS NULL
     OR to_regclass('public.suscripcion') IS NULL THEN
    RAISE EXCEPTION 'Faltan las tablas SaaS: aplicar 20261030000100..000300 antes';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime) THEN
    RAISE EXCEPTION 'El rol de ejecucion % no existe en el cluster', v_runtime;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_owner) THEN
    RAISE EXCEPTION 'El propietario NOLOGIN % no existe en el cluster', v_owner;
  END IF;
  SELECT rolsuper, rolbypassrls, rolcanlogin INTO v_rol
    FROM pg_roles WHERE rolname = v_runtime;
  IF v_rol.rolsuper OR v_rol.rolbypassrls THEN
    RAISE EXCEPTION 'El rol % es SUPERUSER o BYPASSRLS: no sirve como principal restringido', v_runtime;
  END IF;
  IF NOT v_rol.rolcanlogin THEN
    RAISE EXCEPTION 'El rol % debe poder iniciar sesion', v_runtime;
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = v_owner) THEN
    RAISE EXCEPTION 'El propietario de funciones % no debe poder iniciar sesion', v_owner;
  END IF;
END;
$preflight$;

DO $roles$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', v_runtime);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', v_runtime);
END;
$roles$;

-- 1) Esquema de seguridad interno.
CREATE SCHEMA IF NOT EXISTS saas;

COMMENT ON SCHEMA saas IS
  'Objetos internos de seguridad SaaS: asercion firmada, resolucion de tenant y bootstrap de login. No exponer por HTTP.';

-- 2) Secreto de firma. Sin fila => tenant_actual() devuelve NULL (deny-all).
CREATE TABLE IF NOT EXISTS saas.rls_secret (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
  clave TEXT NOT NULL,
  actualizado TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_rls_secret_singleton CHECK (singleton IS TRUE),
  CONSTRAINT ck_rls_secret_longitud CHECK (LENGTH(clave) >= 32)
);

COMMENT ON TABLE saas.rls_secret IS
  'Clave HMAC de la asercion de tenant. Solo el propietario la lee; el rol de ejecucion no tiene privilegios.';

-- 3) Whitelist de principales. Cualquier otro session_user recibe NULL.
CREATE TABLE IF NOT EXISTS saas.principal_runtime (
  rol TEXT PRIMARY KEY,
  tipo TEXT NOT NULL,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT ck_principal_tipo CHECK (tipo IN ('runtime', 'owner'))
);

COMMENT ON TABLE saas.principal_runtime IS
  'Roles SQL reconocidos por las politicas. Alta explicita, nunca por prefijo.';

-- 4) Funciones. Todas con search_path fijo y sin SQL dinamico.
CREATE OR REPLACE FUNCTION saas.hmac_sha256(clave BYTEA, mensaje BYTEA)
RETURNS BYTEA
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, saas
AS $hmac$
DECLARE
  k BYTEA := clave;
  ipad BYTEA := ''::BYTEA;
  opad BYTEA := ''::BYTEA;
  i INTEGER;
BEGIN
  IF octet_length(k) > 64 THEN
    k := sha256(k);
  END IF;
  k := k || decode(repeat('00', 64 - octet_length(k)), 'hex');
  FOR i IN 0..63 LOOP
    ipad := ipad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 54);
    opad := opad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 92);
  END LOOP;
  RETURN sha256(opad || sha256(ipad || mensaje));
END;
$hmac$;

COMMENT ON FUNCTION saas.hmac_sha256(BYTEA, BYTEA) IS
  'HMAC-SHA256 en SQL puro (sin pgcrypto) para verificar la asercion de tenant.';

CREATE OR REPLACE FUNCTION saas.tenant_actual()
RETURNS BIGINT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $tenant$
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
  -- Solo el principal de ejecucion reconocido puede resolver un tenant.
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
  -- Ventana acotada: ni vencida ni absurdamente lejana.
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

  -- Segunda barrera, en base: firma valida pero identidad ya revocada.
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

  RETURN v_empresa;
END;
$tenant$;

COMMENT ON FUNCTION saas.tenant_actual() IS
  'Empresa autenticada de la transaccion. Verifica firma HMAC + membresia/plan/suscripcion vigentes. NULL = deny.';

-- 5) Bootstrap de login: dos funciones cerradas en lugar de SELECT sobre
--    usuario/password_hash. Ejecutadas con privilegios del propietario NOLOGIN.
CREATE OR REPLACE FUNCTION saas.login_credenciales(p_correo TEXT)
RETURNS TABLE(usuario_id BIGINT, password_hash TEXT)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $login1$
  SELECT u.id, u.password_hash
    FROM public.usuario u
   WHERE u.correo = lower(btrim(p_correo))
     AND u.estado IS TRUE
   LIMIT 2;
$login1$;

CREATE OR REPLACE FUNCTION saas.login_membresia(
  p_usuario_id BIGINT,
  p_empresa_id BIGINT,
  p_empresa_slug TEXT,
  p_contexto TEXT
)
RETURNS TABLE(
  usuario_id BIGINT,
  empresa_id BIGINT,
  empresa_slug TEXT,
  empresa_nombre TEXT,
  rol_id BIGINT,
  rol_nombre TEXT,
  plan_codigo TEXT,
  permite_reportes_ia BOOLEAN
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $login2$
BEGIN
  -- Exactamente una empresa por ID o por slug: nunca ambas ni ninguna.
  IF (p_empresa_id IS NULL) = (p_empresa_slug IS NULL) THEN
    RETURN;
  END IF;
  RETURN QUERY
  SELECT ue.usuario_id,
         e.id,
         e.slug::text,
         e.nombre_comercial::text,
         ue.rol_id,
         r.nombre::text,
         p.codigo::text,
         p.permite_reportes_ia
    FROM public.usuario_empresa AS ue
    JOIN public.usuario AS u ON u.id = ue.usuario_id
    JOIN public.empresa AS e ON e.id = ue.empresa_id
    JOIN public.rol AS r ON r.id = ue.rol_id
    JOIN public.suscripcion AS s ON s.empresa_id = e.id
    JOIN public.plan_saas AS p ON p.id = s.plan_saas_id
   WHERE ue.usuario_id = p_usuario_id
     AND (p_empresa_id IS NULL OR e.id = p_empresa_id)
     AND (p_empresa_slug IS NULL OR e.slug = p_empresa_slug)
     AND u.estado IS TRUE
     AND ue.estado IS TRUE
     AND e.estado IS TRUE
     AND r.estado IS TRUE
     AND p.estado IS TRUE
     AND s.estado = 'ACTIVA'
     AND (timezone('America/La_Paz', now()))::date
         BETWEEN s.fecha_inicio AND s.fecha_vencimiento
     AND (
       (p_contexto = 'cliente' AND EXISTS (
          SELECT 1 FROM public.cliente c
           WHERE c.usuario_id = u.id AND c.empresa_id = e.id
             AND c.estado IS TRUE))
       OR
       (p_contexto = 'personal' AND EXISTS (
          SELECT 1 FROM public.empleado em
            JOIN public.sucursal su ON su.id = em.sucursal_id
           WHERE em.usuario_id = u.id AND em.empresa_id = e.id
             AND su.empresa_id = e.id AND em.estado IS TRUE))
     )
   LIMIT 2;
END;
$login2$;

-- 6) Propiedad y privilegios de las funciones.
DO $owners$
DECLARE
  v_owner text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                           'saas_rls_owner');
BEGIN
  EXECUTE format('ALTER FUNCTION saas.hmac_sha256(BYTEA, BYTEA) OWNER TO %I', v_owner);
  EXECUTE format('ALTER FUNCTION saas.tenant_actual() OWNER TO %I', v_owner);
  EXECUTE format('ALTER FUNCTION saas.login_credenciales(TEXT) OWNER TO %I', v_owner);
  EXECUTE format(
    'ALTER FUNCTION saas.login_membresia(BIGINT, BIGINT, TEXT, TEXT) OWNER TO %I',
    v_owner
  );
END;
$owners$;

-- Nada de EXECUTE implicito para PUBLIC en el esquema de seguridad.
DO $acl$ 
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
BEGIN
  EXECUTE format('REVOKE ALL ON SCHEMA saas FROM PUBLIC');
  EXECUTE format('GRANT USAGE ON SCHEMA saas TO %I, %I', v_runtime, v_owner);
  EXECUTE format('REVOKE ALL ON TABLE saas.rls_secret FROM PUBLIC, %I', v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas.principal_runtime FROM PUBLIC, %I', v_runtime);
  EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA saas FROM PUBLIC');
  -- El runtime solo puede: resolver su tenant y ejecutar el bootstrap de login.
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.tenant_actual() TO %I', v_runtime);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.login_credenciales(TEXT) TO %I', v_runtime);
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION saas.login_membresia(BIGINT, BIGINT, TEXT, TEXT) TO %I',
    v_runtime
  );
  -- El propietario NOLOGIN necesita leer su catalogo interno y las tablas de
  -- identidad que validan la asercion. NO recibe DML.
  EXECUTE format('GRANT SELECT ON TABLE saas.rls_secret, saas.principal_runtime TO %I', v_owner);
  EXECUTE format('GRANT SELECT ON TABLE public.usuario, public.usuario_empresa, '
                 'public.empresa, public.suscripcion, public.plan_saas, public.rol, '
                 'public.cliente, public.empleado, public.sucursal TO %I', v_owner);
END;
$acl$;

-- 7) Whitelist de principales (runtime + propietario NOLOGIN).
DO $whitelist$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
BEGIN
  INSERT INTO saas.principal_runtime(rol, tipo, activo)
  VALUES (v_runtime, 'runtime', TRUE), (v_owner, 'owner', TRUE)
  ON CONFLICT (rol) DO UPDATE SET tipo = EXCLUDED.tipo, activo = TRUE;
END;
$whitelist$;

-- 8) Politicas RLS.
-- 8.1 Las 31 tablas empresariales: aislamiento puro por empresa.
DO $tenant_tables$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_tabla text;
  v_tablas text[] := ARRAY[
    'bitacora','sucursal','empleado','cliente','categoria','producto',
    'variante_producto','recurso_producto','temporada','coleccion',
    'producto_coleccion','promocion','promocion_producto','proveedor',
    'proveedor_producto','orden_compra','detalle_orden_compra','inventario',
    'movimiento_inventario','carrito','detalle_carrito','reserva',
    'detalle_reserva','venta','detalle_venta','pago','devolucion',
    'detalle_devolucion','configuracion_vestidor_ar','sesion_vestidor_ar',
    'prueba_vestidor_ar'
  ];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('DROP POLICY IF EXISTS tenant_aislamiento ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY tenant_aislamiento ON public.%I AS PERMISSIVE FOR ALL TO %I '
      'USING (empresa_id = saas.tenant_actual()) '
      'WITH CHECK (empresa_id = saas.tenant_actual())',
      v_tabla, v_runtime
    );
  END LOOP;
END;
$tenant_tables$;

-- 8.2 bitacora: los triggers historicos pueden insertar sin empresa_id. Se
--     acepta la fila NULL (invisible para todo tenant) pero nunca una fila de
--     otro tenant. Revisar en Etapa D junto con los 27 triggers reales.
DO $bitacora$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE format('DROP POLICY IF EXISTS tenant_aislamiento ON public.bitacora');
  EXECUTE format(
    'CREATE POLICY tenant_aislamiento ON public.bitacora AS PERMISSIVE FOR ALL TO %I '
    'USING (empresa_id = saas.tenant_actual()) '
    'WITH CHECK (empresa_id IS NULL OR empresa_id = saas.tenant_actual())',
    v_runtime
  );
END;
$bitacora$;

-- 8.3 Tablas SaaS con empresa_id (empresa, suscripcion, usuario_empresa,
--     empresa_configuracion). Se habilita FORCE; el propietario NOLOGIN recibe
--     una politica de lectura porque valida la asercion desde SECURITY DEFINER.
DO $saas_tables$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
BEGIN
  -- empresa: la PK ES el tenant.
  EXECUTE 'ALTER TABLE public.empresa ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.empresa FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_aislamiento ON public.empresa';
  EXECUTE format(
    'CREATE POLICY tenant_aislamiento ON public.empresa AS PERMISSIVE FOR ALL TO %I '
    'USING (id = saas.tenant_actual()) WITH CHECK (id = saas.tenant_actual())',
    v_runtime
  );
  EXECUTE 'DROP POLICY IF EXISTS owner_lectura ON public.empresa';
  EXECUTE format(
    'CREATE POLICY owner_lectura ON public.empresa AS PERMISSIVE FOR SELECT TO %I '
    'USING (true)', v_owner
  );

  -- El resto: filtro por empresa_id.
  EXECUTE 'ALTER TABLE public.suscripcion ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.suscripcion FORCE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.usuario_empresa ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.usuario_empresa FORCE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.empresa_configuracion ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.empresa_configuracion FORCE ROW LEVEL SECURITY';
END;
$saas_tables$;

DO $saas_policies$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
  v_tabla   text;
  v_tablas  text[] := ARRAY['suscripcion','usuario_empresa','empresa_configuracion'];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_aislamiento ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY tenant_aislamiento ON public.%I AS PERMISSIVE FOR ALL TO %I '
      'USING (empresa_id = saas.tenant_actual()) '
      'WITH CHECK (empresa_id = saas.tenant_actual())',
      v_tabla, v_runtime
    );
  END LOOP;
  -- Lectura del propietario para validar la asercion (suscripcion y membresia).
  FOREACH v_tabla IN ARRAY ARRAY['suscripcion','usuario_empresa'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS owner_lectura ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY owner_lectura ON public.%I AS PERMISSIVE FOR SELECT TO %I '
      'USING (true)', v_tabla, v_owner
    );
  END LOOP;
  -- cliente/empleado/sucursal: el bootstrap de login comprueba el contexto
  -- (cliente vs personal) con privilegios del propietario NOLOGIN. Solo SELECT.
  FOREACH v_tabla IN ARRAY ARRAY['cliente','empleado','sucursal'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS owner_lectura ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY owner_lectura ON public.%I AS PERMISSIVE FOR SELECT TO %I '
      'USING (true)', v_tabla, v_owner
    );
  END LOOP;
END;
$saas_policies$;

-- 8.4 usuario: identidad global. El runtime solo ve usuarios con membresia en
--     su empresa; nunca password_hash. El propietario lo lee para el login.
DO $usuario$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
BEGIN
  EXECUTE 'ALTER TABLE public.usuario ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.usuario FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_miembros ON public.usuario';
  EXECUTE format(
    'CREATE POLICY tenant_miembros ON public.usuario AS PERMISSIVE FOR SELECT TO %I '
    'USING (EXISTS (SELECT 1 FROM public.usuario_empresa ue '
    '  WHERE ue.usuario_id = usuario.id '
    '    AND ue.empresa_id = saas.tenant_actual()))',
    v_runtime
  );
  EXECUTE 'DROP POLICY IF EXISTS owner_lectura ON public.usuario';
  EXECUTE format(
    'CREATE POLICY owner_lectura ON public.usuario AS PERMISSIVE FOR SELECT TO %I '
    'USING (true)', v_owner
  );
END;
$usuario$;

-- 8.5 plan_saas: catalogo global de planes, solo lectura.
DO $plan$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', true), ''),
                             'saas_rls_owner');
BEGIN
  EXECUTE 'ALTER TABLE public.plan_saas ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.plan_saas FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS plan_lectura ON public.plan_saas';
  EXECUTE format(
    'CREATE POLICY plan_lectura ON public.plan_saas AS PERMISSIVE FOR SELECT TO %I, %I '
    'USING (true)', v_runtime, v_owner
  );
END;
$plan$;

-- 9) GRANT/REVOKE explicitos del principal de ejecucion.
DO $grants$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', true), ''),
                             'fs_saas_runtime');
  v_tabla   text;
  v_tablas  text[] := ARRAY[
    'bitacora','sucursal','empleado','cliente','categoria','producto',
    'variante_producto','recurso_producto','temporada','coleccion',
    'producto_coleccion','promocion','promocion_producto','proveedor',
    'proveedor_producto','orden_compra','detalle_orden_compra','inventario',
    'movimiento_inventario','carrito','detalle_carrito','reserva',
    'detalle_reserva','venta','detalle_venta','pago','devolucion',
    'detalle_devolucion','configuracion_vestidor_ar','sesion_vestidor_ar',
    'prueba_vestidor_ar','empresa','suscripcion','usuario_empresa',
    'empresa_configuracion'
  ];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', v_tabla);
    EXECUTE format(
      'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO %I',
      v_tabla, v_runtime
    );
  END LOOP;

  -- Diccionarios globales sin empresa_id (no contienen datos de negocio).
  EXECUTE format(
    'GRANT SELECT ON TABLE public.rol, public.rol_funcion, public.modulo, '
    'public.accion, public.funcion, public.ciudad, public.color, public.talla TO %I',
    v_runtime
  );
  -- usuario: proyeccion sin hash. La RLS limita a miembros de la empresa.
  EXECUTE format('REVOKE ALL ON public.usuario FROM %I', v_runtime);
  EXECUTE format(
    'GRANT SELECT (id, correo, estado, fecha_creacion, rol_id) ON public.usuario TO %I',
    v_runtime
  );
  -- plan_saas: solo lectura.
  EXECUTE format('GRANT SELECT ON TABLE public.plan_saas TO %I', v_runtime);

  -- Secuencias: necesarias para INSERT en columnas SERIAL del esquema heredado.
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', v_runtime);
END;
$grants$;

COMMIT;
