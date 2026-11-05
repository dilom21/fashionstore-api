-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA 5C0
-- ALTA TENANT DE PROVEEDORES Y ASOCIACION DE PRODUCTOS
--
-- Por que existe esta migracion
-- -----------------------------
-- 5BN cerro alta de ordenes de compra, pero `sp_crear_orden_compra_tenant`
-- exige (a) un proveedor activo de la empresa y (b) una fila activa en
-- `proveedor_producto` para el producto de la variante. El catalogo tenant solo
-- listaba proveedores: una empresa NUEVA no podia originar compras sin una
-- carga manual previa de datos.
--
-- Esta migracion agrega DOS rutinas aditivas, sin reutilizar el router legacy:
--   * public.sp_crear_proveedor_tenant(...)               -> alta + asociaciones
--   * public.sp_reemplazar_productos_proveedor_tenant(...) -> correccion posterior
--
-- Decisiones de seguridad (no se relaja ninguna)
-- ----------------------------------------------
--   * SECURITY INVOKER: RLS/FORCE y la asercion HMAC de 5BK siguen aplicando.
--   * El actor se revalida en SQL (empleado activo + empresa + rol) y ademas
--     la API exige COMPRAS/GESTIONAR antes de llamar.
--   * `empresa_id` NUNCA viaja desde el cliente: se deriva del JWT/membresia.
--   * No se concede DML nuevo: `proveedor` y `proveedor_producto` ya tenian
--     SELECT/INSERT/UPDATE/DELETE para fs_saas_runtime con politica
--     `tenant_aislamiento` desde 00400.
--   * Duplicado de NIT dentro de la empresa -> SQLSTATE 23505 (la API lo
--     traduce a 409 sin reenviar el texto SQL).
--   * Producto inexistente, inactivo o de otra empresa -> 42501 (403).
--   * Bitacora: `saas.fn_bitacora_evento` solo inserta si la tabla no tiene ya
--     trigger historico de bitacora (comprobacion en pg_trigger).
--
-- Requisitos: 20261030000700 (SPs de compras) y 20261030001300 aplicadas.
-- Idempotente: CREATE OR REPLACE + GRANT.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  IF to_regprocedure('saas.tenant_actual()') IS NULL
     OR to_regprocedure('saas.fs_actor_empresa(bigint,bigint)') IS NULL
     OR to_regprocedure('saas.fs_plan_vigente(bigint)') IS NULL
     OR to_regprocedure('saas.fn_bitacora_evento(bigint,bigint,text,text,text,text)') IS NULL THEN
    RAISE EXCEPTION '5C0: faltan helpers de 5BN/00600; aplique las migraciones previas';
  END IF;
  IF to_regclass('public.proveedor') IS NULL
     OR to_regclass('public.proveedor_producto') IS NULL
     OR to_regclass('public.producto') IS NULL THEN
    RAISE EXCEPTION '5C0: faltan tablas de proveedor/producto';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime) THEN
    RAISE EXCEPTION '5C0: falta el rol %', v_runtime;
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Alta de proveedor tenant (con asociacion opcional de productos)
-- ===========================================================================
CREATE OR REPLACE FUNCTION public.sp_crear_proveedor_tenant(
  p_empresa_id   BIGINT,
  p_usuario_id   BIGINT,
  p_razon_social TEXT,
  p_nit          TEXT,
  p_correo       TEXT,
  p_telefono     TEXT,
  p_direccion    TEXT,
  p_producto_ids JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant    BIGINT := saas.tenant_actual();
  v_empleado  BIGINT;
  v_rol       TEXT;
  v_plan      RECORD;
  v_razon     TEXT := btrim(COALESCE(p_razon_social, ''));
  v_nit       TEXT := NULLIF(btrim(COALESCE(p_nit, '')), '');
  v_correo    TEXT := lower(NULLIF(btrim(COALESCE(p_correo, '')), ''));
  v_telefono  TEXT := NULLIF(btrim(COALESCE(p_telefono, '')), '');
  v_direccion TEXT := NULLIF(btrim(COALESCE(p_direccion, '')), '');
  v_proveedor BIGINT;
  v_productos INTEGER := 0;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_usuario_id IS NULL OR p_usuario_id <= 0 THEN
    RAISE EXCEPTION 'Identificador de usuario invalido' USING ERRCODE = '22023';
  END IF;

  -- Validacion de contrato (la API valida antes; SQL no confia en el borde).
  IF v_razon = '' OR char_length(v_razon) > 150 THEN
    RAISE EXCEPTION 'Razon social invalida' USING ERRCODE = '22023';
  END IF;
  IF v_nit IS NOT NULL AND char_length(v_nit) > 30 THEN
    RAISE EXCEPTION 'NIT invalido' USING ERRCODE = '22023';
  END IF;
  IF v_correo IS NOT NULL
     AND (char_length(v_correo) > 150
          OR v_correo !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$') THEN
    RAISE EXCEPTION 'Correo invalido' USING ERRCODE = '22023';
  END IF;
  IF v_telefono IS NOT NULL AND char_length(v_telefono) > 30 THEN
    RAISE EXCEPTION 'Telefono invalido' USING ERRCODE = '22023';
  END IF;
  IF v_direccion IS NOT NULL AND char_length(v_direccion) > 255 THEN
    RAISE EXCEPTION 'Direccion invalida' USING ERRCODE = '22023';
  END IF;

  -- Actor: personal activo de la empresa con rol que opera compras.
  SELECT a.empleado_id, a.rol
    INTO v_empleado, v_rol
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede gestionar proveedores', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_productos IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite gestion de productos'
      USING ERRCODE = '42501';
  END IF;

  IF p_producto_ids IS NULL THEN
    p_producto_ids := '[]'::JSONB;
  END IF;
  IF jsonb_typeof(p_producto_ids) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_producto_ids) > 100 THEN
    RAISE EXCEPTION 'Lista de productos invalida' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)
     WHERE t.valor !~ '^[1-9][0-9]{0,17}$'
  ) THEN
    RAISE EXCEPTION 'Lista de productos invalida' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM (SELECT DISTINCT t.valor::BIGINT AS producto_id
              FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)) ids
     WHERE NOT EXISTS (
       SELECT 1 FROM public.producto pr
        WHERE pr.id = ids.producto_id
          AND pr.empresa_id = p_empresa_id
          AND pr.estado IS TRUE)
  ) THEN
    RAISE EXCEPTION 'Producto no disponible para la empresa' USING ERRCODE = '42501';
  END IF;

  IF v_nit IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.proveedor pr
     WHERE pr.empresa_id = p_empresa_id AND pr.nit = v_nit
  ) THEN
    RAISE EXCEPTION 'NIT duplicado en la empresa' USING ERRCODE = '23505';
  END IF;

  INSERT INTO public.proveedor
    (empresa_id, razon_social, nit, correo, telefono, direccion, estado)
  VALUES
    (p_empresa_id, v_razon, v_nit, v_correo, v_telefono, v_direccion, TRUE)
  RETURNING id INTO v_proveedor;

  INSERT INTO public.proveedor_producto
    (empresa_id, proveedor_id, producto_id, costo_referencia, estado)
  SELECT p_empresa_id, v_proveedor, ids.producto_id, 0, TRUE
    FROM (SELECT DISTINCT t.valor::BIGINT AS producto_id
            FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)) ids;
  GET DIAGNOSTICS v_productos = ROW_COUNT;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'PROVEEDOR_CREADO', 'proveedor',
    format('Proveedor %s creado (id %s) con %s producto(s)',
           v_razon, v_proveedor, v_productos),
    'proveedor');

  RETURN jsonb_build_object(
    'ok', TRUE,
    'proveedor_id', v_proveedor,
    'razon_social', v_razon,
    'nit', v_nit,
    'correo', v_correo,
    'telefono', v_telefono,
    'direccion', v_direccion,
    'estado', TRUE,
    'total_productos', v_productos);
END;
$sp$;

-- ===========================================================================
-- 2. Reemplazo atomico de productos asociados a un proveedor existente
-- ===========================================================================
CREATE OR REPLACE FUNCTION public.sp_reemplazar_productos_proveedor_tenant(
  p_empresa_id   BIGINT,
  p_proveedor_id BIGINT,
  p_usuario_id   BIGINT,
  p_producto_ids JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant    BIGINT := saas.tenant_actual();
  v_empleado  BIGINT;
  v_rol       TEXT;
  v_plan      RECORD;
  v_razon     TEXT;
  v_productos INTEGER := 0;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_proveedor_id IS NULL OR p_proveedor_id <= 0
     OR p_usuario_id IS NULL OR p_usuario_id <= 0 THEN
    RAISE EXCEPTION 'Identificadores invalidos' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol
    INTO v_empleado, v_rol
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede gestionar proveedores', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_productos IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite gestion de productos'
      USING ERRCODE = '42501';
  END IF;

  SELECT pr.razon_social INTO v_razon
    FROM public.proveedor pr
   WHERE pr.id = p_proveedor_id AND pr.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Proveedor no disponible' USING ERRCODE = '42501';
  END IF;

  IF p_producto_ids IS NULL THEN
    p_producto_ids := '[]'::JSONB;
  END IF;
  IF jsonb_typeof(p_producto_ids) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_producto_ids) > 100 THEN
    RAISE EXCEPTION 'Lista de productos invalida' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)
     WHERE t.valor !~ '^[1-9][0-9]{0,17}$'
  ) THEN
    RAISE EXCEPTION 'Lista de productos invalida' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM (SELECT DISTINCT t.valor::BIGINT AS producto_id
              FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)) ids
     WHERE NOT EXISTS (
       SELECT 1 FROM public.producto pr
        WHERE pr.id = ids.producto_id
          AND pr.empresa_id = p_empresa_id
          AND pr.estado IS TRUE)
  ) THEN
    RAISE EXCEPTION 'Producto no disponible para la empresa' USING ERRCODE = '42501';
  END IF;

  DELETE FROM public.proveedor_producto pp
   WHERE pp.proveedor_id = p_proveedor_id
     AND pp.empresa_id = p_empresa_id;

  INSERT INTO public.proveedor_producto
    (empresa_id, proveedor_id, producto_id, costo_referencia, estado)
  SELECT p_empresa_id, p_proveedor_id, ids.producto_id, 0, TRUE
    FROM (SELECT DISTINCT t.valor::BIGINT AS producto_id
            FROM jsonb_array_elements_text(p_producto_ids) AS t(valor)) ids;
  GET DIAGNOSTICS v_productos = ROW_COUNT;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'PROVEEDOR_PRODUCTOS', 'proveedor_producto',
    format('Productos del proveedor %s (%s) reemplazados: %s',
           p_proveedor_id, v_razon, v_productos),
    'proveedor_producto');

  RETURN jsonb_build_object(
    'ok', TRUE,
    'proveedor_id', p_proveedor_id,
    'total_productos', v_productos);
END;
$sp$;

-- ===========================================================================
-- 3. ACL de las rutinas nuevas
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
       AND p.proname IN ('sp_crear_proveedor_tenant',
                         'sp_reemplazar_productos_proveedor_tenant')
  LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', r.firma);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_runtime);
  END LOOP;
END;
$acl_sp$;

COMMIT;
