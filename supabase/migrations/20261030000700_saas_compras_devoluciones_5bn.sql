-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA E2 (5BN)
-- COMPRAS CON RECEPCION DE INVENTARIO + DEVOLUCIONES TENANT
--
-- Por que existe esta migracion
-- -----------------------------
-- E1 (5BM) cerro aprovisionamiento global, reservas y venta de contado. Faltaban
-- dos flujos comerciales que el esquema legacy resuelve con rutinas NO tenant-aware
-- (`sp_recibir_orden_compra`, `sp_registrar_devolucion`), ambas con DML global y
-- sin empresa_id. Esta migracion agrega rutinas NUEVAS, con nombre `*_tenant`,
-- SECURITY INVOKER para que RLS/FORCE y la asercion HMAC de 5BK sigan aplicando.
--
-- Decisiones de seguridad (no se relaja ninguna)
-- ----------------------------------------------
--   * No se concede UPDATE sobre `talla`/`color`: el bug de variantes se corrige
--     en Python usando bloqueo compartido (`FOR SHARE`), que solo exige SELECT.
--   * No se amplian privilegios del runtime: las tablas nuevas reciben la MISMA
--     politica `tenant_aislamiento` y los mismos grants SELECT/INSERT/UPDATE/DELETE.
--   * No se usa SECURITY DEFINER para el DML comercial: el actor se revalida en
--     SQL (empleado activo + sucursal de la empresa + rol + suscripcion vigente).
--   * Idempotencia: se reutiliza `saas.operacion_idempotente` (empresa, tipo, UUID)
--     con huella SHA-256 canonica; no se crea una tabla paralela.
--   * Kardex: `ENTRADA_COMPRA` y `DEVOLUCION` (tipos ya admitidos por 5BL).
--   * La bitacora empresarial NO se duplica: el helper `saas.fn_bitacora_evento`
--     solo inserta el evento de negocio si la tabla no tiene ya un trigger de
--     bitacora (comprobacion en pg_trigger, no una suposicion).
--   * Reembolso financiero: NO se simula. Se registra `PENDIENTE` con
--     `ejecutado = false`; el CHECK impide marcarlo ejecutado sin fecha real.
--
-- Requisitos: 20261030000100..00600 aplicadas. Idempotente: puede reaplicarse.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  IF to_regprocedure('saas.tenant_actual()') IS NULL
     OR to_regprocedure('public.sp_registrar_venta_contado_tenant(bigint,bigint,bigint,uuid,text,jsonb,text)') IS NULL
     OR to_regclass('saas.operacion_idempotente') IS NULL THEN
    RAISE EXCEPTION '5BN: faltan objetos de 5BM/00600; aplique las migraciones previas';
  END IF;
  IF to_regclass('public.orden_compra') IS NULL
     OR to_regclass('public.detalle_orden_compra') IS NULL
     OR to_regclass('public.devolucion') IS NULL
     OR to_regclass('public.detalle_devolucion') IS NULL
     OR to_regclass('public.movimiento_inventario') IS NULL THEN
    RAISE EXCEPTION '5BN: faltan tablas comerciales historicas';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime) THEN
    RAISE EXCEPTION '5BN: falta el rol % ', v_runtime;
  END IF;
  IF current_database() !~ '(lab|staging)' THEN
    RAISE EXCEPTION '5BN: la base % no parece descartable (nombre con lab/staging)',
      current_database();
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Idempotencia: tipos admitidos
-- ===========================================================================
DO $idem_tipos$
DECLARE
  v_def TEXT;
BEGIN
  SELECT pg_get_constraintdef(c.oid) INTO v_def
    FROM pg_constraint c
   WHERE c.conrelid = 'saas.operacion_idempotente'::regclass
     AND c.conname = 'ck_operacion_idempotente_tipo';
  IF v_def IS NULL OR v_def NOT LIKE '%RECEPCION_COMPRA%' THEN
    ALTER TABLE saas.operacion_idempotente
      DROP CONSTRAINT IF EXISTS ck_operacion_idempotente_tipo;
    ALTER TABLE saas.operacion_idempotente
      ADD CONSTRAINT ck_operacion_idempotente_tipo
      CHECK (tipo IN ('VENTA_CONTADO', 'RECEPCION_COMPRA',
                      'DEVOLUCION_VENTA', 'DEVOLUCION_PROCESO'));
  END IF;
END;
$idem_tipos$;

-- ===========================================================================
-- 2. Trazabilidad nueva: recepciones de compra y reembolso declarado
-- ===========================================================================

CREATE TABLE IF NOT EXISTS public.recepcion_compra (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id      BIGINT NOT NULL,
  orden_compra_id BIGINT NOT NULL,
  usuario_id      BIGINT NOT NULL,
  fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  estado          TEXT NOT NULL DEFAULT 'APLICADA',
  observacion     TEXT,
  CONSTRAINT ck_recepcion_compra_estado CHECK (estado IN ('APLICADA')),
  CONSTRAINT uq_recepcion_compra_empresa_id UNIQUE (empresa_id, id),
  CONSTRAINT fk_recepcion_compra_orden FOREIGN KEY (empresa_id, orden_compra_id)
    REFERENCES public.orden_compra (empresa_id, id) ON UPDATE NO ACTION ON DELETE NO ACTION,
  CONSTRAINT fk_recepcion_compra_usuario FOREIGN KEY (usuario_id)
    REFERENCES public.usuario (id) ON UPDATE NO ACTION ON DELETE NO ACTION
);

COMMENT ON TABLE public.recepcion_compra IS
  '5BN: evento fisico de recepcion de mercaderia; una fila por confirmacion con operacion_id idempotente.';

CREATE TABLE IF NOT EXISTS public.detalle_recepcion_compra (
  id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id               BIGINT NOT NULL,
  recepcion_id             BIGINT NOT NULL,
  detalle_orden_compra_id  BIGINT NOT NULL,
  cantidad                 INTEGER NOT NULL,
  CONSTRAINT ck_detalle_recepcion_cantidad CHECK (cantidad > 0),
  CONSTRAINT uq_detalle_recepcion_empresa_id UNIQUE (empresa_id, id),
  CONSTRAINT uq_detalle_recepcion_linea UNIQUE (empresa_id, recepcion_id, detalle_orden_compra_id),
  CONSTRAINT fk_detalle_recepcion_cabecera FOREIGN KEY (empresa_id, recepcion_id)
    REFERENCES public.recepcion_compra (empresa_id, id) ON UPDATE NO ACTION ON DELETE NO ACTION,
  CONSTRAINT fk_detalle_recepcion_orden_linea FOREIGN KEY (empresa_id, detalle_orden_compra_id)
    REFERENCES public.detalle_orden_compra (empresa_id, id) ON UPDATE NO ACTION ON DELETE NO ACTION
);

COMMENT ON TABLE public.detalle_recepcion_compra IS
  '5BN: cantidades efectivamente recibidas por linea; el saldo pendiente se deriva de aqui.';

-- Una devolucion puede resolverse sin reposicion (prenda no restockable). El
-- default TRUE conserva la semantica legacy de CU25.
ALTER TABLE public.detalle_devolucion
  ADD COLUMN IF NOT EXISTS reponer BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS public.reembolso_devolucion (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id       BIGINT NOT NULL,
  devolucion_id    BIGINT NOT NULL,
  estado           TEXT NOT NULL DEFAULT 'PENDIENTE',
  monto            NUMERIC(12, 2) NOT NULL,
  moneda           TEXT NOT NULL DEFAULT 'BOB',
  medio            TEXT NOT NULL DEFAULT 'NO_DISPONIBLE',
  ejecutado        BOOLEAN NOT NULL DEFAULT FALSE,
  referencia       TEXT,
  fecha_creacion   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  fecha_ejecucion  TIMESTAMPTZ,
  CONSTRAINT ck_reembolso_estado
    CHECK (estado IN ('PENDIENTE', 'NO_DISPONIBLE', 'EJECUTADO', 'ANULADO')),
  CONSTRAINT ck_reembolso_monto CHECK (monto >= 0),
  CONSTRAINT ck_reembolso_no_ficticio
    CHECK (ejecutado IS FALSE OR fecha_ejecucion IS NOT NULL),
  CONSTRAINT uq_reembolso_empresa_id UNIQUE (empresa_id, id),
  CONSTRAINT uq_reembolso_devolucion UNIQUE (empresa_id, devolucion_id),
  CONSTRAINT fk_reembolso_devolucion FOREIGN KEY (empresa_id, devolucion_id)
    REFERENCES public.devolucion (empresa_id, id) ON UPDATE NO ACTION ON DELETE NO ACTION
);

COMMENT ON TABLE public.reembolso_devolucion IS
  '5BN: intencion de reembolso declarada. Sin pasarela integrada queda PENDIENTE y ejecutado=false; no se simula dinero devuelto.';

-- RLS identica al resto de tablas empresariales (FORCE, misma politica).
DO $rls_nuevas$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  v_tabla   text;
  v_tablas  text[] := ARRAY['recepcion_compra', 'detalle_recepcion_compra',
                            'reembolso_devolucion'];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('DROP POLICY IF EXISTS tenant_aislamiento ON public.%I', v_tabla);
    EXECUTE format(
      'CREATE POLICY tenant_aislamiento ON public.%I AS PERMISSIVE FOR ALL TO %I '
      'USING (empresa_id = saas.tenant_actual()) '
      'WITH CHECK (empresa_id = saas.tenant_actual())', v_tabla, v_runtime);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', v_tabla);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO %I',
                   v_tabla, v_runtime);
  END LOOP;
END;
$rls_nuevas$;

-- ===========================================================================
-- 2.5 Invariante de catalogo global (correccion P0)
-- ===========================================================================
-- El runtime no puede bloquear filas de `talla`/`color` (PostgreSQL exige
-- UPDATE para cualquier FOR UPDATE/SHARE/KEY SHARE). En vez de conceder ese
-- privilegio, el invariante se valida en la propia escritura de la variante:
-- asi cubre a cualquier escritor y no solo al repositorio Python.
CREATE OR REPLACE FUNCTION public.fn_5bn_validar_catalogos_globales()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
BEGIN
  IF NEW.talla_id IS NULL OR NEW.color_id IS NULL THEN
    RAISE EXCEPTION 'La variante requiere talla y color' USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.talla t
     WHERE t.id = NEW.talla_id AND t.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'Talla % inactiva o inexistente', NEW.talla_id
      USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.color c
     WHERE c.id = NEW.color_id AND c.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'Color % inactivo o inexistente', NEW.color_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_5bn_validar_catalogos_globales ON public.variante_producto;
CREATE TRIGGER trg_5bn_validar_catalogos_globales
BEFORE INSERT OR UPDATE OF talla_id, color_id ON public.variante_producto
FOR EACH ROW EXECUTE FUNCTION public.fn_5bn_validar_catalogos_globales();

-- ===========================================================================
-- 3. Helpers de alcance (SECURITY INVOKER: RLS sigue aplicando)
-- ===========================================================================

-- Empleado activo del tenant con su rol y sucursal efectiva. Devuelve 0 filas
-- si el usuario no es personal activo de la empresa.
CREATE OR REPLACE FUNCTION saas.fs_actor_empresa(
  p_empresa_id BIGINT,
  p_usuario_id BIGINT
)
RETURNS TABLE(empleado_id BIGINT, rol TEXT, sucursal_id BIGINT)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $fn$
  SELECT em.id, r.nombre::TEXT, em.sucursal_id
    FROM public.empleado em
    JOIN public.sucursal su
      ON su.id = em.sucursal_id
     AND su.empresa_id = p_empresa_id
     AND su.estado IS TRUE
    JOIN public.usuario_empresa ue
      ON ue.usuario_id = em.usuario_id
     AND ue.empresa_id = p_empresa_id
     AND ue.estado IS TRUE
    JOIN public.rol r ON r.id = ue.rol_id AND r.estado IS TRUE
   WHERE em.usuario_id = p_usuario_id
     AND em.empresa_id = p_empresa_id
     AND em.estado IS TRUE
   LIMIT 1;
$fn$;

-- Suscripcion vigente del tenant. Devuelve el plan solo si esta activo y dentro
-- de fechas; 0 filas = escrituras comerciales bloqueadas.
CREATE OR REPLACE FUNCTION saas.fs_plan_vigente(p_empresa_id BIGINT)
RETURNS TABLE(plan_id BIGINT, permite_productos BOOLEAN, permite_ventas BOOLEAN,
              permite_reservas BOOLEAN)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $fn$
  SELECT p.id, p.permite_productos, p.permite_ventas, p.permite_reservas
    FROM public.suscripcion s
    JOIN public.plan_saas p ON p.id = s.plan_saas_id AND p.estado IS TRUE
   WHERE s.empresa_id = p_empresa_id
     AND s.estado = 'ACTIVA'
     AND (timezone('America/La_Paz', now()))::date
         BETWEEN s.fecha_inicio AND s.fecha_vencimiento
   ORDER BY s.id DESC
   LIMIT 1;
$fn$;

-- Evento de bitacora empresarial sin duplicar el trigger historico: si la tabla
-- ya tiene un trigger de bitacora, el trigger es la unica fuente del registro.
CREATE OR REPLACE FUNCTION saas.fn_bitacora_evento(
  p_empresa_id BIGINT,
  p_usuario_id BIGINT,
  p_accion TEXT,
  p_entidad TEXT,
  p_descripcion TEXT,
  p_tabla TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $fn$
BEGIN
  IF p_empresa_id IS NULL OR p_empresa_id <= 0 THEN
    RAISE EXCEPTION 'Bitacora sin tenant' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_trigger t
     WHERE t.tgrelid = format('public.%I', p_tabla)::regclass
       AND NOT t.tgisinternal
       AND (t.tgname ILIKE '%bitacora%'
            OR t.tgfoid = 'public.fn_registrar_bitacora()'::regprocedure)
  ) THEN
    INSERT INTO public.bitacora
      (usuario_id, empresa_id, fecha_hora, accion, entidad_afectada, descripcion)
    VALUES
      (p_usuario_id, p_empresa_id, CURRENT_TIMESTAMP, p_accion, p_entidad, p_descripcion);
  END IF;
END;
$fn$;

-- ===========================================================================
-- 4. Compras: crear / enviar / cancelar / recibir
-- ===========================================================================

CREATE OR REPLACE FUNCTION public.sp_crear_orden_compra_tenant(
  p_empresa_id   BIGINT,
  p_usuario_id   BIGINT,
  p_proveedor_id BIGINT,
  p_sucursal_id  BIGINT,
  p_fecha_estimada TIMESTAMPTZ,
  p_observacion  TEXT,
  p_lineas       JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_plan     RECORD;
  v_orden    BIGINT;
  v_linea    RECORD;
  v_variante RECORD;
  v_total_lineas INTEGER := 0;
  v_detalles JSONB := '[]'::JSONB;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_usuario_id IS NULL OR p_usuario_id <= 0
     OR p_sucursal_id IS NULL OR p_sucursal_id <= 0
     OR p_proveedor_id IS NULL OR p_proveedor_id <= 0 THEN
    RAISE EXCEPTION 'Identificadores invalidos' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede gestionar ordenes de compra', v_rol
      USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_sucursal_emp IS DISTINCT FROM p_sucursal_id THEN
    RAISE EXCEPTION 'Sucursal fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_productos IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite gestion de productos y compras'
      USING ERRCODE = '42501';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.proveedor pr
     WHERE pr.id = p_proveedor_id AND pr.empresa_id = p_empresa_id
       AND pr.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'Proveedor no disponible' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.sucursal su
     WHERE su.id = p_sucursal_id AND su.empresa_id = p_empresa_id
       AND su.estado IS TRUE
  ) THEN
    RAISE EXCEPTION 'Sucursal no disponible' USING ERRCODE = '42501';
  END IF;

  IF p_lineas IS NULL OR jsonb_typeof(p_lineas) <> 'array'
     OR jsonb_array_length(p_lineas) < 1 OR jsonb_array_length(p_lineas) > 50 THEN
    RAISE EXCEPTION 'La orden requiere entre 1 y 50 lineas' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_lineas) x
     WHERE jsonb_typeof(x) <> 'object'
        OR (x->>'variante_producto_id') IS NULL
        OR (x->>'temporada_id') IS NULL
        OR (x->>'cantidad') IS NULL
        OR (x->>'costo_unitario') IS NULL
        OR (x->>'variante_producto_id') !~ '^[1-9][0-9]*$'
        OR (x->>'temporada_id') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad')::INTEGER > 100000
        OR (x->>'costo_unitario') !~ '^[0-9]+(\.[0-9]{1,2})?$'
  ) THEN
    RAISE EXCEPTION 'Linea de compra invalida' USING ERRCODE = '22023';
  END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(p_lineas) x)
     <> (SELECT count(DISTINCT ((x->>'variante_producto_id') || ':' || (x->>'temporada_id')))
           FROM jsonb_array_elements(p_lineas) x) THEN
    RAISE EXCEPTION 'Variante y temporada repetidas en la orden' USING ERRCODE = '22023';
  END IF;
  IF p_fecha_estimada IS NOT NULL AND p_fecha_estimada < CURRENT_TIMESTAMP THEN
    RAISE EXCEPTION 'La fecha estimada no puede ser pasada' USING ERRCODE = '22023';
  END IF;

  -- Todas las lineas se validan ANTES de escribir: el cuerpo no puede fijar
  -- empresa, empleado ni estado, y el costo solo se acepta como dato de compra.
  FOR v_linea IN
    SELECT (x->>'variante_producto_id')::BIGINT AS variante_id,
           (x->>'temporada_id')::BIGINT AS temporada_id,
           (x->>'cantidad')::INTEGER AS cantidad,
           (x->>'costo_unitario')::NUMERIC(12,2) AS costo
      FROM jsonb_array_elements(p_lineas) x
     ORDER BY 1, 2
  LOOP
    SELECT vp.id, vp.producto_id
      INTO v_variante
      FROM public.variante_producto vp
     WHERE vp.id = v_linea.variante_id AND vp.empresa_id = p_empresa_id
       AND vp.estado IS TRUE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Variante % no disponible', v_linea.variante_id
        USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.producto pr
       WHERE pr.id = v_variante.producto_id AND pr.empresa_id = p_empresa_id
         AND pr.estado IS TRUE
    ) THEN
      RAISE EXCEPTION 'Producto de la variante % no disponible', v_linea.variante_id
        USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.temporada tm
       WHERE tm.id = v_linea.temporada_id AND tm.empresa_id = p_empresa_id
         AND tm.estado IS TRUE
    ) THEN
      RAISE EXCEPTION 'Temporada % no disponible', v_linea.temporada_id
        USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.proveedor_producto pp
       WHERE pp.proveedor_id = p_proveedor_id AND pp.empresa_id = p_empresa_id
         AND pp.producto_id = v_variante.producto_id AND pp.estado IS TRUE
    ) THEN
      RAISE EXCEPTION 'El producto de la variante % no esta asociado al proveedor',
        v_linea.variante_id USING ERRCODE = '42501';
    END IF;
    v_total_lineas := v_total_lineas + 1;
    v_detalles := v_detalles || jsonb_build_array(jsonb_build_object(
      'variante_producto_id', v_linea.variante_id,
      'temporada_id', v_linea.temporada_id,
      'cantidad', v_linea.cantidad,
      'costo_unitario', v_linea.costo));
  END LOOP;

  INSERT INTO public.orden_compra
    (empresa_id, proveedor_id, sucursal_id, empleado_id, fecha_orden,
     fecha_estimada, estado, observacion)
  VALUES
    (p_empresa_id, p_proveedor_id, p_sucursal_id, v_empleado, CURRENT_TIMESTAMP,
     p_fecha_estimada, 'BORRADOR', NULLIF(btrim(COALESCE(p_observacion, '')), ''))
  RETURNING id INTO v_orden;

  FOR v_linea IN
    SELECT (x->>'variante_producto_id')::BIGINT AS variante_id,
           (x->>'temporada_id')::BIGINT AS temporada_id,
           (x->>'cantidad')::INTEGER AS cantidad,
           (x->>'costo_unitario')::NUMERIC(12,2) AS costo
      FROM jsonb_array_elements(p_lineas) x
     ORDER BY 1, 2
  LOOP
    INSERT INTO public.detalle_orden_compra
      (empresa_id, orden_compra_id, variante_producto_id, temporada_id,
       cantidad, costo_unitario)
    VALUES
      (p_empresa_id, v_orden, v_linea.variante_id, v_linea.temporada_id,
       v_linea.cantidad, v_linea.costo);
  END LOOP;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'ORDEN_COMPRA_CREADA', 'orden_compra',
    format('Orden de compra %s creada con %s linea(s)', v_orden, v_total_lineas),
    'orden_compra');

  RETURN jsonb_build_object(
    'ok', TRUE,
    'orden_compra_id', v_orden,
    'estado', 'BORRADOR',
    'proveedor_id', p_proveedor_id,
    'sucursal_id', p_sucursal_id,
    'total_lineas', v_total_lineas,
    'lineas', v_detalles,
    'stock_modificado', FALSE);
END;
$sp$;

CREATE OR REPLACE FUNCTION public.sp_enviar_orden_compra_tenant(
  p_empresa_id BIGINT,
  p_orden_id   BIGINT,
  p_usuario_id BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_orden    RECORD;
  v_lineas   BIGINT;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_orden_id IS NULL OR p_orden_id <= 0 THEN
    RAISE EXCEPTION 'Orden invalida' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede enviar ordenes de compra', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT o.id, o.estado, o.sucursal_id INTO v_orden
    FROM public.orden_compra o
   WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Orden de compra no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_orden.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Orden fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;

  IF v_orden.estado = 'ENVIADA' THEN
    RETURN jsonb_build_object('ok', TRUE, 'idempotente', TRUE,
      'orden_compra_id', v_orden.id, 'estado', v_orden.estado);
  END IF;
  IF v_orden.estado <> 'BORRADOR' THEN
    RAISE EXCEPTION 'La orden no puede enviarse en estado %', v_orden.estado
      USING ERRCODE = '23514';
  END IF;
  SELECT count(*) INTO v_lineas FROM public.detalle_orden_compra d
   WHERE d.orden_compra_id = p_orden_id AND d.empresa_id = p_empresa_id;
  IF v_lineas = 0 THEN
    RAISE EXCEPTION 'La orden no tiene lineas' USING ERRCODE = '23514';
  END IF;

  UPDATE public.orden_compra o SET estado = 'ENVIADA'
   WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'ORDEN_COMPRA_ENVIADA', 'orden_compra',
    format('Orden de compra %s enviada al proveedor', p_orden_id), 'orden_compra');

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE,
    'orden_compra_id', p_orden_id, 'estado', 'ENVIADA');
END;
$sp$;

CREATE OR REPLACE FUNCTION public.sp_cancelar_orden_compra_tenant(
  p_empresa_id BIGINT,
  p_orden_id   BIGINT,
  p_usuario_id BIGINT,
  p_observacion TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_orden    RECORD;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_orden_id IS NULL OR p_orden_id <= 0 THEN
    RAISE EXCEPTION 'Orden invalida' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede cancelar ordenes de compra', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT o.id, o.estado, o.sucursal_id, o.observacion INTO v_orden
    FROM public.orden_compra o
   WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Orden de compra no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_orden.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Orden fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;

  IF v_orden.estado = 'CANCELADA' THEN
    RETURN jsonb_build_object('ok', TRUE, 'idempotente', TRUE,
      'orden_compra_id', v_orden.id, 'estado', v_orden.estado);
  END IF;
  -- Fidelidad legacy: PARCIAL/RECIBIDA ya afectaron inventario y no se cancelan.
  IF v_orden.estado NOT IN ('BORRADOR', 'ENVIADA') THEN
    RAISE EXCEPTION 'La orden no puede cancelarse en estado %', v_orden.estado
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.orden_compra o
     SET estado = 'CANCELADA',
         observacion = CASE
           WHEN p_observacion IS NULL THEN o.observacion
           WHEN o.observacion IS NULL THEN p_observacion
           ELSE o.observacion || ' | ' || p_observacion
         END
   WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'ORDEN_COMPRA_CANCELADA', 'orden_compra',
    format('Orden de compra %s cancelada', p_orden_id), 'orden_compra');

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE,
    'orden_compra_id', p_orden_id, 'estado', 'CANCELADA');
END;
$sp$;

CREATE OR REPLACE FUNCTION public.sp_recibir_orden_compra_tenant(
  p_empresa_id  BIGINT,
  p_orden_id    BIGINT,
  p_usuario_id  BIGINT,
  p_operacion_id UUID,
  p_huella      TEXT,
  p_lineas      JSONB,
  p_observacion TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_plan     RECORD;
  v_orden    RECORD;
  v_linea    RECORD;
  v_detalle  RECORD;
  v_pendiente INTEGER;
  v_previas  INTEGER;
  v_existente RECORD;
  v_recepcion BIGINT;
  v_plan_lineas JSONB := '[]'::JSONB;
  v_inv_id   BIGINT;
  v_stock    JSONB := '[]'::JSONB;
  v_resultado JSONB;
  v_restantes BIGINT;
  v_estado   TEXT;
  v_total    INTEGER := 0;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_orden_id IS NULL OR p_orden_id <= 0 THEN
    RAISE EXCEPTION 'Orden invalida' USING ERRCODE = '22023';
  END IF;
  IF p_operacion_id IS NULL THEN
    RAISE EXCEPTION 'operacion_id obligatorio' USING ERRCODE = '22023';
  END IF;
  IF p_huella IS NULL OR p_huella !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Huella de operacion invalida' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede recibir mercaderia', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_productos IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite recepcion de mercaderia'
      USING ERRCODE = '42501';
  END IF;

  -- Idempotencia ANTES de cualquier mutacion: replay devuelve el resultado previo.
  INSERT INTO saas.operacion_idempotente
    (empresa_id, tipo, operacion_id, huella, estado)
  VALUES (p_empresa_id, 'RECEPCION_COMPRA', p_operacion_id, p_huella, 'EN_PROCESO')
  ON CONFLICT (empresa_id, tipo, operacion_id) DO NOTHING;

  IF NOT FOUND THEN
    SELECT o.id, o.huella, o.estado, o.resultado INTO v_existente
      FROM saas.operacion_idempotente o
     WHERE o.empresa_id = p_empresa_id AND o.tipo = 'RECEPCION_COMPRA'
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
        'recepcion', v_existente.resultado);
    END IF;
    RAISE EXCEPTION 'Operacion identica en curso; reintente' USING ERRCODE = '40001';
  END IF;

  SELECT o.id, o.estado, o.sucursal_id INTO v_orden
    FROM public.orden_compra o
   WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Orden de compra no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_orden.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Orden fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;
  IF v_orden.estado = 'RECIBIDA' THEN
    RAISE EXCEPTION 'La orden ya fue recibida por completo' USING ERRCODE = '23514';
  END IF;
  IF v_orden.estado NOT IN ('ENVIADA', 'PARCIAL') THEN
    RAISE EXCEPTION 'La orden no admite recepcion en estado %', v_orden.estado
      USING ERRCODE = '23514';
  END IF;

  IF p_lineas IS NULL OR jsonb_typeof(p_lineas) <> 'array'
     OR jsonb_array_length(p_lineas) < 1 OR jsonb_array_length(p_lineas) > 50 THEN
    RAISE EXCEPTION 'La recepcion requiere entre 1 y 50 lineas' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_lineas) x
     WHERE jsonb_typeof(x) <> 'object'
        OR (x->>'detalle_orden_compra_id') IS NULL
        OR (x->>'cantidad') IS NULL
        OR (x->>'detalle_orden_compra_id') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad')::INTEGER > 100000
  ) THEN
    RAISE EXCEPTION 'Linea de recepcion invalida' USING ERRCODE = '22023';
  END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(p_lineas) x)
     <> (SELECT count(DISTINCT (x->>'detalle_orden_compra_id'))
           FROM jsonb_array_elements(p_lineas) x) THEN
    RAISE EXCEPTION 'Detalle de orden repetido en la recepcion' USING ERRCODE = '22023';
  END IF;

  -- Pass A: validar saldo pendiente por linea (detalles bloqueados por la orden).
  FOR v_linea IN
    SELECT (x->>'detalle_orden_compra_id')::BIGINT AS detalle_id,
           (x->>'cantidad')::INTEGER AS cantidad
      FROM jsonb_array_elements(p_lineas) x
     ORDER BY 1
  LOOP
    SELECT d.id, d.cantidad, d.variante_producto_id, d.temporada_id
      INTO v_detalle
      FROM public.detalle_orden_compra d
     WHERE d.id = v_linea.detalle_id AND d.orden_compra_id = p_orden_id
       AND d.empresa_id = p_empresa_id
     FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Detalle % no pertenece a la orden', v_linea.detalle_id
        USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(sum(r.cantidad), 0) INTO v_previas
      FROM public.detalle_recepcion_compra r
     WHERE r.detalle_orden_compra_id = v_linea.detalle_id
       AND r.empresa_id = p_empresa_id;
    v_pendiente := v_detalle.cantidad - v_previas;
    IF v_pendiente <= 0 THEN
      RAISE EXCEPTION 'La linea % ya fue recibida por completo', v_linea.detalle_id
        USING ERRCODE = '23514';
    END IF;
    IF v_linea.cantidad > v_pendiente THEN
      RAISE EXCEPTION 'La linea % excede el saldo pendiente (% )', v_linea.detalle_id,
        v_pendiente USING ERRCODE = '23514';
    END IF;
    v_total := v_total + v_linea.cantidad;
    v_plan_lineas := v_plan_lineas || jsonb_build_array(jsonb_build_object(
      'detalle_orden_compra_id', v_linea.detalle_id,
      'variante_producto_id', v_detalle.variante_producto_id,
      'temporada_id', v_detalle.temporada_id,
      'cantidad', v_linea.cantidad));
  END LOOP;

  INSERT INTO public.recepcion_compra
    (empresa_id, orden_compra_id, usuario_id, observacion)
  VALUES
    (p_empresa_id, p_orden_id, p_usuario_id,
     NULLIF(btrim(COALESCE(p_observacion, '')), ''))
  RETURNING id INTO v_recepcion;

  -- Pass B: un unico orden de bloqueo (variante, temporada) para evitar carreras
  -- entre ordenes distintas que apuntan al mismo inventario fisico.
  FOR v_linea IN
    SELECT (x->>'detalle_orden_compra_id')::BIGINT AS detalle_id,
           (x->>'variante_producto_id')::BIGINT AS variante_id,
           (x->>'temporada_id')::BIGINT AS temporada_id,
           (x->>'cantidad')::INTEGER AS cantidad
      FROM jsonb_array_elements(v_plan_lineas) x
     ORDER BY 2, 3
  LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended(format(
      'inventario:%s:%s:%s:%s', p_empresa_id, v_orden.sucursal_id,
      v_linea.variante_id, v_linea.temporada_id), 0));

    SELECT i.id INTO v_inv_id
      FROM public.inventario i
     WHERE i.empresa_id = p_empresa_id
       AND i.sucursal_id = v_orden.sucursal_id
       AND i.variante_producto_id = v_linea.variante_id
       AND i.temporada_id = v_linea.temporada_id
     FOR UPDATE;

    IF NOT FOUND THEN
      INSERT INTO public.inventario
        (empresa_id, sucursal_id, variante_producto_id, temporada_id,
         stock_actual, stock_reservado, fecha_actualizacion)
      VALUES
        (p_empresa_id, v_orden.sucursal_id, v_linea.variante_id,
         v_linea.temporada_id, 0, 0, CURRENT_TIMESTAMP)
      RETURNING id INTO v_inv_id;
    END IF;

    UPDATE public.inventario i
       SET stock_actual = i.stock_actual + v_linea.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_inv_id AND i.empresa_id = p_empresa_id;

    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_inv_id, p_usuario_id, 'ENTRADA_COMPRA', v_linea.cantidad,
       CURRENT_TIMESTAMP,
       format('Recepcion de orden de compra %s', p_orden_id),
       'RECEPCION_COMPRA', v_recepcion);

    INSERT INTO public.detalle_recepcion_compra
      (empresa_id, recepcion_id, detalle_orden_compra_id, cantidad)
    VALUES
      (p_empresa_id, v_recepcion, v_linea.detalle_id, v_linea.cantidad);

    v_stock := v_stock || jsonb_build_array(jsonb_build_object(
      'inventario_id', v_inv_id,
      'stock_actual', (SELECT i.stock_actual FROM public.inventario i WHERE i.id = v_inv_id),
      'stock_reservado', (SELECT i.stock_reservado FROM public.inventario i WHERE i.id = v_inv_id)));
  END LOOP;

  SELECT count(*) INTO v_restantes
    FROM public.detalle_orden_compra d
   WHERE d.orden_compra_id = p_orden_id AND d.empresa_id = p_empresa_id
     AND d.cantidad > COALESCE((
       SELECT sum(r.cantidad) FROM public.detalle_recepcion_compra r
        WHERE r.detalle_orden_compra_id = d.id AND r.empresa_id = p_empresa_id), 0);

  IF v_restantes = 0 THEN
    v_estado := 'RECIBIDA';
    UPDATE public.orden_compra o
       SET estado = 'RECIBIDA', fecha_recepcion = CURRENT_TIMESTAMP
     WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id;
  ELSE
    v_estado := 'PARCIAL';
    UPDATE public.orden_compra o SET estado = 'PARCIAL'
     WHERE o.id = p_orden_id AND o.empresa_id = p_empresa_id;
  END IF;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'RECEPCION_COMPRA', 'recepcion_compra',
    format('Recepcion %s de la orden %s: %s unidad(es), estado %s',
           v_recepcion, p_orden_id, v_total, v_estado),
    'recepcion_compra');

  v_resultado := jsonb_build_object(
    'recepcion_id', v_recepcion,
    'orden_compra_id', p_orden_id,
    'estado_orden', v_estado,
    'unidades_recibidas', v_total,
    'stock', v_stock,
    'operacion_id', p_operacion_id);

  UPDATE saas.operacion_idempotente o
     SET estado = 'PROCESADA', resultado = v_resultado, actualizado = NOW()
   WHERE o.empresa_id = p_empresa_id AND o.tipo = 'RECEPCION_COMPRA'
     AND o.operacion_id = p_operacion_id;

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE, 'recepcion', v_resultado);
END;
$sp$;

-- ===========================================================================
-- 5. Devoluciones: crear / aprobar / rechazar / procesar
-- ===========================================================================

CREATE OR REPLACE FUNCTION public.sp_crear_devolucion_tenant(
  p_empresa_id   BIGINT,
  p_venta_id     BIGINT,
  p_usuario_id   BIGINT,
  p_operacion_id UUID,
  p_huella       TEXT,
  p_motivo       TEXT,
  p_observacion  TEXT,
  p_items        JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_plan     RECORD;
  v_venta    RECORD;
  v_item     RECORD;
  v_detalle  RECORD;
  v_comprometida INTEGER;
  v_disponible INTEGER;
  v_devolucion BIGINT;
  v_existente RECORD;
  v_resultado JSONB;
  v_items_res JSONB := '[]'::JSONB;
  v_unidades INTEGER := 0;
  v_monto NUMERIC(12,2) := 0;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_venta_id IS NULL OR p_venta_id <= 0 THEN
    RAISE EXCEPTION 'Venta invalida' USING ERRCODE = '22023';
  END IF;
  IF p_operacion_id IS NULL THEN
    RAISE EXCEPTION 'operacion_id obligatorio' USING ERRCODE = '22023';
  END IF;
  IF p_huella IS NULL OR p_huella !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Huella de operacion invalida' USING ERRCODE = '22023';
  END IF;
  IF btrim(COALESCE(p_motivo, '')) = '' THEN
    RAISE EXCEPTION 'El motivo es obligatorio' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede registrar devoluciones', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_ventas IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite operar devoluciones de ventas'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO saas.operacion_idempotente
    (empresa_id, tipo, operacion_id, huella, estado)
  VALUES (p_empresa_id, 'DEVOLUCION_VENTA', p_operacion_id, p_huella, 'EN_PROCESO')
  ON CONFLICT (empresa_id, tipo, operacion_id) DO NOTHING;

  IF NOT FOUND THEN
    SELECT o.id, o.huella, o.estado, o.resultado INTO v_existente
      FROM saas.operacion_idempotente o
     WHERE o.empresa_id = p_empresa_id AND o.tipo = 'DEVOLUCION_VENTA'
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
        'devolucion', v_existente.resultado);
    END IF;
    RAISE EXCEPTION 'Operacion identica en curso; reintente' USING ERRCODE = '40001';
  END IF;

  SELECT v.id, v.estado, v.sucursal_id, v.total INTO v_venta
    FROM public.venta v
   WHERE v.id = p_venta_id AND v.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Venta no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_venta.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Venta fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;
  IF v_venta.estado <> 'COMPLETADA' THEN
    RAISE EXCEPTION 'Solo una venta COMPLETADA admite devolucion' USING ERRCODE = '23514';
  END IF;

  IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array'
     OR jsonb_array_length(p_items) < 1 OR jsonb_array_length(p_items) > 50 THEN
    RAISE EXCEPTION 'La devolucion requiere entre 1 y 50 lineas' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_items) x
     WHERE jsonb_typeof(x) <> 'object'
        OR (x->>'detalle_venta_id') IS NULL
        OR (x->>'cantidad') IS NULL
        OR (x->>'detalle_venta_id') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad') !~ '^[1-9][0-9]*$'
        OR (x->>'cantidad')::INTEGER > 100000
        OR (x ? 'reponer' AND jsonb_typeof(x->'reponer') <> 'boolean')
  ) THEN
    RAISE EXCEPTION 'Linea de devolucion invalida' USING ERRCODE = '22023';
  END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(p_items) x)
     <> (SELECT count(DISTINCT (x->>'detalle_venta_id'))
           FROM jsonb_array_elements(p_items) x) THEN
    RAISE EXCEPTION 'Detalle de venta repetido en la devolucion' USING ERRCODE = '22023';
  END IF;

  -- Bloqueo ordenado de las lineas de la venta antes de recalcular disponibilidad.
  PERFORM 1 FROM public.detalle_venta dv
   WHERE dv.venta_id = p_venta_id AND dv.empresa_id = p_empresa_id
   ORDER BY dv.id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'La venta no tiene lineas' USING ERRCODE = '23514';
  END IF;

  FOR v_item IN
    SELECT (x->>'detalle_venta_id')::BIGINT AS detalle_id,
           (x->>'cantidad')::INTEGER AS cantidad,
           COALESCE((x->>'reponer')::BOOLEAN, TRUE) AS reponer,
           NULLIF(btrim(COALESCE(x->>'motivo', '')), '') AS motivo
      FROM jsonb_array_elements(p_items) x
     ORDER BY 1
  LOOP
    SELECT dv.id, dv.inventario_id, dv.cantidad, dv.precio_unitario
      INTO v_detalle
      FROM public.detalle_venta dv
     WHERE dv.id = v_item.detalle_id AND dv.venta_id = p_venta_id
       AND dv.empresa_id = p_empresa_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Detalle % no pertenece a la venta', v_item.detalle_id
        USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(sum(dd.cantidad), 0) INTO v_comprometida
      FROM public.detalle_devolucion dd
      JOIN public.devolucion d ON d.id = dd.devolucion_id
     WHERE dd.detalle_venta_id = v_item.detalle_id
       AND dd.empresa_id = p_empresa_id
       AND d.estado IN ('SOLICITADA', 'APROBADA', 'COMPLETADA');
    v_disponible := v_detalle.cantidad - v_comprometida;
    IF v_disponible < 0 THEN
      v_disponible := 0;
    END IF;
    IF v_item.cantidad > v_disponible THEN
      RAISE EXCEPTION 'La linea % excede las unidades disponibles (% )',
        v_item.detalle_id, v_disponible USING ERRCODE = '23514';
    END IF;
    v_unidades := v_unidades + v_item.cantidad;
    v_monto := v_monto + (v_detalle.precio_unitario * v_item.cantidad);
    v_items_res := v_items_res || jsonb_build_array(jsonb_build_object(
      'detalle_venta_id', v_item.detalle_id,
      'inventario_id', v_detalle.inventario_id,
      'cantidad', v_item.cantidad,
      'reponer', v_item.reponer,
      'precio_unitario', v_detalle.precio_unitario));
  END LOOP;

  INSERT INTO public.devolucion
    (empresa_id, venta_id, fecha_hora, motivo, estado, observacion)
  VALUES
    (p_empresa_id, p_venta_id, CURRENT_TIMESTAMP, btrim(p_motivo), 'SOLICITADA',
     NULLIF(btrim(COALESCE(p_observacion, '')), ''))
  RETURNING id INTO v_devolucion;

  FOR v_item IN
    SELECT (x->>'detalle_venta_id')::BIGINT AS detalle_id,
           (x->>'cantidad')::INTEGER AS cantidad,
           COALESCE((x->>'reponer')::BOOLEAN, TRUE) AS reponer,
           NULLIF(btrim(COALESCE(x->>'motivo', '')), '') AS motivo
      FROM jsonb_array_elements(p_items) x
     ORDER BY 1
  LOOP
    INSERT INTO public.detalle_devolucion
      (empresa_id, devolucion_id, detalle_venta_id, cantidad, motivo, reponer)
    VALUES
      (p_empresa_id, v_devolucion, v_item.detalle_id, v_item.cantidad,
       v_item.motivo, v_item.reponer);
  END LOOP;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'DEVOLUCION_SOLICITADA', 'devolucion',
    format('Devolucion %s solicitada sobre la venta %s (%s unidad(es))',
           v_devolucion, p_venta_id, v_unidades),
    'devolucion');

  v_resultado := jsonb_build_object(
    'devolucion_id', v_devolucion,
    'venta_id', p_venta_id,
    'estado', 'SOLICITADA',
    'motivo', btrim(p_motivo),
    'unidades', v_unidades,
    'monto_estimado', v_monto,
    'moneda', 'BOB',
    'items', v_items_res,
    'reembolso', jsonb_build_object(
      'estado', 'PENDIENTE',
      'ejecutado', FALSE,
      'medio', 'NO_DISPONIBLE',
      'monto', v_monto),
    'stock_modificado', FALSE,
    'operacion_id', p_operacion_id);

  UPDATE saas.operacion_idempotente o
     SET estado = 'PROCESADA', resultado = v_resultado, actualizado = NOW()
   WHERE o.empresa_id = p_empresa_id AND o.tipo = 'DEVOLUCION_VENTA'
     AND o.operacion_id = p_operacion_id;

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE, 'devolucion', v_resultado);
END;
$sp$;

CREATE OR REPLACE FUNCTION public.sp_resolver_devolucion_tenant(
  p_empresa_id    BIGINT,
  p_devolucion_id BIGINT,
  p_usuario_id    BIGINT,
  p_accion        TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_devolucion RECORD;
  v_venta    RECORD;
  v_accion   TEXT := upper(btrim(COALESCE(p_accion, '')));
  v_conflictos BIGINT;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_devolucion_id IS NULL OR p_devolucion_id <= 0 THEN
    RAISE EXCEPTION 'Devolucion invalida' USING ERRCODE = '22023';
  END IF;
  IF v_accion NOT IN ('APROBAR', 'RECHAZAR') THEN
    RAISE EXCEPTION 'Accion de devolucion no admitida' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede resolver devoluciones', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT d.id, d.venta_id, d.estado INTO v_devolucion
    FROM public.devolucion d
   WHERE d.id = p_devolucion_id AND d.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Devolucion no disponible' USING ERRCODE = '42501';
  END IF;
  SELECT v.id, v.sucursal_id INTO v_venta
    FROM public.venta v
   WHERE v.id = v_devolucion.venta_id AND v.empresa_id = p_empresa_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Venta de la devolucion no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_venta.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Devolucion fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;

  IF v_accion = 'APROBAR' AND v_devolucion.estado = 'APROBADA' THEN
    RETURN jsonb_build_object('ok', TRUE, 'idempotente', TRUE,
      'devolucion_id', p_devolucion_id, 'estado', 'APROBADA');
  END IF;
  IF v_accion = 'RECHAZAR' AND v_devolucion.estado = 'RECHAZADA' THEN
    RETURN jsonb_build_object('ok', TRUE, 'idempotente', TRUE,
      'devolucion_id', p_devolucion_id, 'estado', 'RECHAZADA');
  END IF;
  IF v_devolucion.estado <> 'SOLICITADA' THEN
    RAISE EXCEPTION 'Solo una devolucion SOLICITADA puede resolverse; estado %',
      v_devolucion.estado USING ERRCODE = '23514';
  END IF;

  -- Revalidacion de disponibilidad al aprobar (evita sobredevolucion por carreras).
  IF v_accion = 'APROBAR' THEN
    SELECT count(*) INTO v_conflictos
      FROM public.detalle_devolucion dd
      JOIN public.detalle_venta dv ON dv.id = dd.detalle_venta_id
     WHERE dd.devolucion_id = p_devolucion_id
       AND dd.empresa_id = p_empresa_id
       AND dd.cantidad > dv.cantidad - COALESCE((
         SELECT sum(dd2.cantidad)
           FROM public.detalle_devolucion dd2
           JOIN public.devolucion d2 ON d2.id = dd2.devolucion_id
          WHERE dd2.detalle_venta_id = dd.detalle_venta_id
            AND dd2.empresa_id = p_empresa_id
            AND d2.estado IN ('SOLICITADA', 'APROBADA', 'COMPLETADA')
            AND d2.id <> p_devolucion_id), 0);
    IF v_conflictos > 0 THEN
      RAISE EXCEPTION 'La devolucion excede las unidades vendidas disponibles'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  UPDATE public.devolucion d
     SET estado = CASE WHEN v_accion = 'APROBAR' THEN 'APROBADA' ELSE 'RECHAZADA' END
   WHERE d.id = p_devolucion_id AND d.empresa_id = p_empresa_id;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id,
    CASE WHEN v_accion = 'APROBAR' THEN 'DEVOLUCION_APROBADA' ELSE 'DEVOLUCION_RECHAZADA' END,
    'devolucion',
    format('Devolucion %s %s', p_devolucion_id, lower(v_accion)),
    'devolucion');

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE,
    'devolucion_id', p_devolucion_id,
    'estado', CASE WHEN v_accion = 'APROBAR' THEN 'APROBADA' ELSE 'RECHAZADA' END);
END;
$sp$;

CREATE OR REPLACE FUNCTION public.sp_procesar_devolucion_tenant(
  p_empresa_id    BIGINT,
  p_devolucion_id BIGINT,
  p_usuario_id    BIGINT,
  p_operacion_id  UUID,
  p_huella        TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $sp$
DECLARE
  v_tenant   BIGINT := saas.tenant_actual();
  v_empleado BIGINT;
  v_rol      TEXT;
  v_sucursal_emp BIGINT;
  v_plan     RECORD;
  v_devolucion RECORD;
  v_venta    RECORD;
  v_linea    RECORD;
  v_existente RECORD;
  v_conflictos BIGINT;
  v_inv_id   BIGINT;
  v_reponer  INTEGER;
  v_stock    JSONB := '[]'::JSONB;
  v_unidades INTEGER := 0;
  v_reponer_total INTEGER := 0;
  v_monto    NUMERIC(12,2) := 0;
  v_reembolso BIGINT;
  v_resultado JSONB;
BEGIN
  IF v_tenant IS NULL OR v_tenant IS DISTINCT FROM p_empresa_id THEN
    RAISE EXCEPTION 'Contexto de empresa no autorizado' USING ERRCODE = '42501';
  END IF;
  IF p_devolucion_id IS NULL OR p_devolucion_id <= 0 THEN
    RAISE EXCEPTION 'Devolucion invalida' USING ERRCODE = '22023';
  END IF;
  IF p_operacion_id IS NULL THEN
    RAISE EXCEPTION 'operacion_id obligatorio' USING ERRCODE = '22023';
  END IF;
  IF p_huella IS NULL OR p_huella !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Huella de operacion invalida' USING ERRCODE = '22023';
  END IF;

  SELECT a.empleado_id, a.rol, a.sucursal_id
    INTO v_empleado, v_rol, v_sucursal_emp
    FROM saas.fs_actor_empresa(p_empresa_id, p_usuario_id) a;
  IF v_empleado IS NULL THEN
    RAISE EXCEPTION 'Se requiere personal activo de la empresa' USING ERRCODE = '42501';
  END IF;
  IF v_rol NOT IN ('ADMINISTRADOR', 'ENCARGADO_SUCURSAL') THEN
    RAISE EXCEPTION 'El rol % no puede procesar devoluciones', v_rol
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO v_plan FROM saas.fs_plan_vigente(p_empresa_id);
  IF v_plan.plan_id IS NULL OR v_plan.permite_ventas IS NOT TRUE THEN
    RAISE EXCEPTION 'La suscripcion no permite procesar devoluciones'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO saas.operacion_idempotente
    (empresa_id, tipo, operacion_id, huella, estado)
  VALUES (p_empresa_id, 'DEVOLUCION_PROCESO', p_operacion_id, p_huella, 'EN_PROCESO')
  ON CONFLICT (empresa_id, tipo, operacion_id) DO NOTHING;

  IF NOT FOUND THEN
    SELECT o.id, o.huella, o.estado, o.resultado INTO v_existente
      FROM saas.operacion_idempotente o
     WHERE o.empresa_id = p_empresa_id AND o.tipo = 'DEVOLUCION_PROCESO'
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
        'devolucion', v_existente.resultado);
    END IF;
    RAISE EXCEPTION 'Operacion identica en curso; reintente' USING ERRCODE = '40001';
  END IF;

  SELECT d.id, d.venta_id, d.estado INTO v_devolucion
    FROM public.devolucion d
   WHERE d.id = p_devolucion_id AND d.empresa_id = p_empresa_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Devolucion no disponible' USING ERRCODE = '42501';
  END IF;
  SELECT v.id, v.sucursal_id INTO v_venta
    FROM public.venta v
   WHERE v.id = v_devolucion.venta_id AND v.empresa_id = p_empresa_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Venta de la devolucion no disponible' USING ERRCODE = '42501';
  END IF;
  IF v_rol <> 'ADMINISTRADOR' AND v_venta.sucursal_id IS DISTINCT FROM v_sucursal_emp THEN
    RAISE EXCEPTION 'Devolucion fuera del alcance del usuario' USING ERRCODE = '42501';
  END IF;
  IF v_devolucion.estado = 'COMPLETADA' THEN
    RAISE EXCEPTION 'La devolucion ya fue procesada' USING ERRCODE = '23514';
  END IF;
  IF v_devolucion.estado <> 'APROBADA' THEN
    RAISE EXCEPTION 'Solo una devolucion APROBADA puede procesarse; estado %',
      v_devolucion.estado USING ERRCODE = '23514';
  END IF;

  SELECT count(*) INTO v_conflictos
    FROM public.detalle_devolucion dd
    JOIN public.detalle_venta dv ON dv.id = dd.detalle_venta_id
   WHERE dd.devolucion_id = p_devolucion_id
     AND dd.empresa_id = p_empresa_id
     AND dd.cantidad > dv.cantidad - COALESCE((
       SELECT sum(dd2.cantidad)
         FROM public.detalle_devolucion dd2
         JOIN public.devolucion d2 ON d2.id = dd2.devolucion_id
        WHERE dd2.detalle_venta_id = dd.detalle_venta_id
          AND dd2.empresa_id = p_empresa_id
          AND d2.estado IN ('SOLICITADA', 'APROBADA', 'COMPLETADA')
          AND d2.id <> p_devolucion_id), 0);
  IF v_conflictos > 0 THEN
    RAISE EXCEPTION 'La devolucion excede las unidades vendidas disponibles'
      USING ERRCODE = '23514';
  END IF;

  -- Reingreso fisico: SOLO lineas reponer=true tocan stock y generan kardex.
  FOR v_linea IN
    SELECT dd.detalle_venta_id, dd.cantidad, dd.reponer,
           dv.inventario_id, dv.precio_unitario
      FROM public.detalle_devolucion dd
      JOIN public.detalle_venta dv
        ON dv.id = dd.detalle_venta_id AND dv.empresa_id = dd.empresa_id
     WHERE dd.devolucion_id = p_devolucion_id AND dd.empresa_id = p_empresa_id
     ORDER BY dv.inventario_id
  LOOP
    v_unidades := v_unidades + v_linea.cantidad;
    v_monto := v_monto + (v_linea.precio_unitario * v_linea.cantidad);
    IF v_linea.reponer IS NOT TRUE THEN
      CONTINUE;
    END IF;
    PERFORM 1 FROM public.inventario i
      WHERE i.id = v_linea.inventario_id AND i.empresa_id = p_empresa_id
      FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Inventario % no disponible', v_linea.inventario_id
        USING ERRCODE = '42501';
    END IF;
    UPDATE public.inventario i
       SET stock_actual = i.stock_actual + v_linea.cantidad,
           fecha_actualizacion = CURRENT_TIMESTAMP
     WHERE i.id = v_linea.inventario_id AND i.empresa_id = p_empresa_id;

    INSERT INTO public.movimiento_inventario
      (empresa_id, inventario_id, usuario_id, tipo, cantidad, fecha_hora,
       observaciones, referencia_tipo, referencia_id)
    VALUES
      (p_empresa_id, v_linea.inventario_id, p_usuario_id, 'DEVOLUCION',
       v_linea.cantidad, CURRENT_TIMESTAMP,
       format('Reingreso autorizado por devolucion %s', p_devolucion_id),
       'DEVOLUCION', p_devolucion_id);

    v_reponer_total := v_reponer_total + v_linea.cantidad;
    v_stock := v_stock || jsonb_build_array(jsonb_build_object(
      'inventario_id', v_linea.inventario_id,
      'stock_actual', (SELECT i.stock_actual FROM public.inventario i
                        WHERE i.id = v_linea.inventario_id),
      'stock_reservado', (SELECT i.stock_reservado FROM public.inventario i
                           WHERE i.id = v_linea.inventario_id)));
  END LOOP;

  -- Reembolso: se declara, NO se ejecuta. Sin pasarela confirmada queda
  -- PENDIENTE/ejecutado=false y no se modifica `pago` ni `venta.total`.
  INSERT INTO public.reembolso_devolucion
    (empresa_id, devolucion_id, estado, monto, moneda, medio, ejecutado)
  VALUES
    (p_empresa_id, p_devolucion_id, 'PENDIENTE', v_monto, 'BOB', 'NO_DISPONIBLE', FALSE)
  ON CONFLICT (empresa_id, devolucion_id) DO NOTHING
  RETURNING id INTO v_reembolso;

  UPDATE public.devolucion d SET estado = 'COMPLETADA'
   WHERE d.id = p_devolucion_id AND d.empresa_id = p_empresa_id;

  PERFORM saas.fn_bitacora_evento(
    p_empresa_id, p_usuario_id, 'DEVOLUCION_COMPLETADA', 'devolucion',
    format('Devolucion %s completada: %s unidad(es), %s repuesta(s), monto %s pendiente',
           p_devolucion_id, v_unidades, v_reponer_total, v_monto),
    'devolucion');

  v_resultado := jsonb_build_object(
    'devolucion_id', p_devolucion_id,
    'venta_id', v_devolucion.venta_id,
    'estado', 'COMPLETADA',
    'unidades', v_unidades,
    'unidades_repuestas', v_reponer_total,
    'stock', v_stock,
    'reembolso', jsonb_build_object(
      'reembolso_id', v_reembolso,
      'estado', 'PENDIENTE',
      'ejecutado', FALSE,
      'medio', 'NO_DISPONIBLE',
      'monto', v_monto,
      'moneda', 'BOB'),
    'operacion_id', p_operacion_id);

  UPDATE saas.operacion_idempotente o
     SET estado = 'PROCESADA', resultado = v_resultado, actualizado = NOW()
   WHERE o.empresa_id = p_empresa_id AND o.tipo = 'DEVOLUCION_PROCESO'
     AND o.operacion_id = p_operacion_id;

  RETURN jsonb_build_object('ok', TRUE, 'idempotente', FALSE, 'devolucion', v_resultado);
END;
$sp$;

-- ===========================================================================
-- 6. ACL de las rutinas nuevas y de los helpers
-- ===========================================================================
DO $acl_5bn$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  r RECORD;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE (n.nspname = 'public' AND p.proname IN (
              'sp_crear_orden_compra_tenant', 'sp_enviar_orden_compra_tenant',
              'sp_cancelar_orden_compra_tenant', 'sp_recibir_orden_compra_tenant',
              'sp_crear_devolucion_tenant', 'sp_resolver_devolucion_tenant',
              'sp_procesar_devolucion_tenant'))
        OR (n.nspname = 'saas' AND p.proname IN (
              'fs_actor_empresa', 'fs_plan_vigente', 'fn_bitacora_evento'))
  LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', r.firma);
    EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO %I', r.firma, v_runtime);
  END LOOP;
END;
$acl_5bn$;

COMMIT;
