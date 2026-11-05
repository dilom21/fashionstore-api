-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA D (5BL)
-- COMPATIBILIDAD CON EL ESQUEMA HISTORICO: TRIGGERS, SP, BITACORA Y PRIVILEGIOS
--
-- Por que existe esta migracion
-- -----------------------------
-- Las migraciones 20261030000100..00400 fueron ensayadas sobre un esquema
-- reconstruido desde el ORM, SIN los triggers ni procedimientos del primer
-- parcial. Al incorporarlos aparecen cuatro incompatibilidades reales:
--
--   1. `fn_actualizar_actividad_carrito` y `fn_validar_sucursal_detalle_carrito`
--      no fijan `search_path`.
--   2. `fn_registrar_bitacora` escribe `empresa_id = NULL` para tablas globales;
--      con RLS eso crea filas invisibles y, bajo FORCE RLS, puede romper el
--      alta de usuarios si el actor no tiene politica de insercion.
--   3. Ningun trigger verifica que el `empresa_id` de la fila coincida con el
--      tenant autenticado: la FK compuesta y RLS lo cubren, pero la auditoria no
--      debe depender solo de ellas.
--   4. Las 13 rutinas historicas tienen `EXECUTE` para PUBLIC y omiten
--      `empresa_id` en sus INSERT: no son invocables por el principal SaaS.
--
-- Contrato de auditoria que se implanta
-- -------------------------------------
--   * Evento empresarial  -> `public.bitacora`, `empresa_id = tenant` obligatorio.
--   * Evento global nuevo -> `saas.bitacora_global`, solo escribible por actores
--     privilegiados (propietario NOLOGIN o superusuario); jamas por el runtime.
--   * Filas historicas con `empresa_id IS NULL` -> se conservan sin reasignar ni
--     borrar; quedan fuera del alcance de los tenants y se clasifican por la
--     vista `saas.bitacora_historica_global`.
--   * `public.bitacora` es append-only para el runtime: SELECT + INSERT, sin
--     UPDATE/DELETE, y un trigger rechaza cualquier insercion sin tenant.
--
-- Requisitos: 00100..00400 aplicadas, principal restringido y propietario
-- NOLOGIN existentes. Idempotente: reejecutarla no cambia el resultado.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', TRUE), ''),
                             'saas_rls_owner');
  v_faltan  text;
BEGIN
  IF to_regprocedure('saas.tenant_actual()') IS NULL THEN
    RAISE EXCEPTION '5BL: falta saas.tenant_actual(); aplique 20261030000400 antes';
  END IF;
  IF to_regclass('public.bitacora') IS NULL
     OR NOT EXISTS (
       SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='bitacora'
          AND column_name='empresa_id') THEN
    RAISE EXCEPTION '5BL: public.bitacora sin empresa_id; aplique 20261030000200 antes';
  END IF;
  SELECT string_agg(nombre, ', ') INTO v_faltan
    FROM unnest(ARRAY[
      'fn_registrar_bitacora','fn_actualizar_actividad_carrito',
      'fn_validar_sucursal_detalle_carrito','fn_validar_detalle_reserva_sucursal',
      'fn_controlar_actualizacion_inventario','fn_normalizar_correo_usuario',
      'fn_completar_ip_bitacora','fn_proteger_detalle_venta',
      'fn_proteger_detalle_orden_compra']) AS nombre
   WHERE to_regprocedure('public.' || nombre || '()') IS NULL;
  IF v_faltan IS NOT NULL THEN
    RAISE EXCEPTION
      '5BL: faltan funciones de trigger historicas (%): aplique la reconstruccion del esquema antes', v_faltan;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime)
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_owner) THEN
    RAISE EXCEPTION '5BL: faltan los roles SQL % / %', v_runtime, v_owner;
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = v_owner) THEN
    RAISE EXCEPTION '5BL: % no debe poder iniciar sesion', v_owner;
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Canal de auditoria global (eventos sin empresa identificable)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS saas.bitacora_global (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fecha_hora       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  empresa_id       BIGINT,
  usuario_id       BIGINT,          -- sin FK: la auditoria sobrevive al borrado
  actor_sql        TEXT NOT NULL,   -- session_user real que origino el evento
  accion           TEXT NOT NULL,
  entidad_afectada TEXT,
  entidad_id       BIGINT,
  descripcion      TEXT,
  ip               VARCHAR(45),
  ambito           TEXT NOT NULL DEFAULT 'GLOBAL',
  CONSTRAINT ck_bitacora_global_ambito CHECK (ambito = 'GLOBAL'),
  CONSTRAINT ck_bitacora_global_sin_tenant CHECK (empresa_id IS NULL)
);

COMMENT ON TABLE saas.bitacora_global IS
  'Canal de auditoria de eventos genuinamente globales. Sin privilegios para el runtime; no visible al tenant.';

CREATE OR REPLACE FUNCTION saas.registrar_evento_global(
  p_accion       TEXT,
  p_entidad      TEXT,
  p_entidad_id   BIGINT,
  p_descripcion  TEXT,
  p_usuario_id   BIGINT,
  p_ip           TEXT
)
RETURNS VOID
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $evento$
  INSERT INTO saas.bitacora_global
    (actor_sql, accion, entidad_afectada, entidad_id, descripcion, usuario_id, ip)
  VALUES
    (session_user, p_accion, p_entidad, p_entidad_id, p_descripcion,
     p_usuario_id, NULLIF(btrim(p_ip), ''));
$evento$;

COMMENT ON FUNCTION saas.registrar_evento_global(TEXT, TEXT, BIGINT, TEXT, BIGINT, TEXT) IS
  'Unica via admitida para eventos globales nuevos. No concedida al principal tenant.';

-- Predicado seguro: permite al trigger saber si el invocador es el runtime sin
-- darle SELECT sobre saas.principal_runtime.
CREATE OR REPLACE FUNCTION saas.es_principal_runtime()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, saas
AS $es$
  SELECT EXISTS (
    SELECT 1 FROM saas.principal_runtime p
     WHERE p.rol = session_user AND p.tipo = 'runtime' AND p.activo
  );
$es$;

-- ===========================================================================
-- 2. Endurecimiento de las funciones de trigger historicas
-- ===========================================================================

-- 2.1 Bitacora: atribucion obligatoria al tenant, canal global separado.
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
      RAISE EXCEPTION
        'Auditoria global rechazada para el principal tenant (tabla %)', TG_TABLE_NAME
        USING ERRCODE = '42501';
    END IF;
    INSERT INTO saas.bitacora_global
      (actor_sql, accion, entidad_afectada, entidad_id, descripcion, usuario_id, ip)
    VALUES
      (session_user, v_accion, TG_TABLE_NAME, v_registro_id,
       format('Operacion %s sobre registro ID %s', v_accion, COALESCE(v_registro_id::TEXT, '?')),
       v_usuario_id, v_ip);
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

-- 2.2 Actividad del carrito: search_path fijo y verificacion de tenant.
CREATE OR REPLACE FUNCTION public.fn_actualizar_actividad_carrito()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_carrito_id BIGINT := COALESCE(NEW.carrito_id, OLD.carrito_id);
  v_empresa    BIGINT;
  v_tenant     BIGINT;
BEGIN
  SELECT empresa_id INTO v_empresa FROM public.carrito WHERE id = v_carrito_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Carrito % inexistente', v_carrito_id;
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND v_tenant <> v_empresa THEN
    RAISE EXCEPTION 'Carrito % fuera del tenant autenticado', v_carrito_id
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.carrito
     SET fecha_actualizacion = NOW()
   WHERE id = v_carrito_id AND empresa_id = v_empresa;

  RETURN COALESCE(NEW, OLD);
END;
$function$;

-- 2.3 Validacion sucursal/empresa del detalle de carrito.
CREATE OR REPLACE FUNCTION public.fn_validar_sucursal_detalle_carrito()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_sucursal_carrito     BIGINT;
  v_empresa_carrito      BIGINT;
  v_sucursal_inventario  BIGINT;
  v_empresa_inventario   BIGINT;
  v_tenant               BIGINT;
BEGIN
  SELECT sucursal_id, empresa_id INTO v_sucursal_carrito, v_empresa_carrito
    FROM public.carrito WHERE id = NEW.carrito_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Carrito inexistente';
  END IF;

  SELECT sucursal_id, empresa_id INTO v_sucursal_inventario, v_empresa_inventario
    FROM public.inventario WHERE id = NEW.inventario_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Inventario inexistente';
  END IF;

  IF v_empresa_carrito <> v_empresa_inventario THEN
    RAISE EXCEPTION 'Carrito e inventario pertenecen a empresas distintas'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.empresa_id IS NOT NULL AND NEW.empresa_id <> v_empresa_carrito THEN
    RAISE EXCEPTION 'El detalle declara una empresa distinta a la del carrito'
      USING ERRCODE = '42501';
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND v_tenant <> v_empresa_carrito THEN
    RAISE EXCEPTION 'Detalle de carrito fuera del tenant autenticado'
      USING ERRCODE = '42501';
  END IF;

  IF v_sucursal_carrito <> v_sucursal_inventario THEN
    RAISE EXCEPTION
      'El inventario seleccionado no pertenece a la sucursal del carrito';
  END IF;

  RETURN NEW;
END;
$function$;

-- 2.4 Validacion sucursal/empresa del detalle de reserva.
CREATE OR REPLACE FUNCTION public.fn_validar_detalle_reserva_sucursal()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_sucursal_reserva    BIGINT;
  v_empresa_reserva     BIGINT;
  v_sucursal_inventario BIGINT;
  v_empresa_inventario  BIGINT;
  v_tenant              BIGINT;
BEGIN
  SELECT sucursal_id, empresa_id INTO v_sucursal_reserva, v_empresa_reserva
    FROM public.reserva WHERE id = NEW.reserva_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'La reserva % no existe', NEW.reserva_id;
  END IF;

  SELECT sucursal_id, empresa_id INTO v_sucursal_inventario, v_empresa_inventario
    FROM public.inventario WHERE id = NEW.inventario_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'El inventario % no existe', NEW.inventario_id;
  END IF;

  IF v_empresa_reserva <> v_empresa_inventario THEN
    RAISE EXCEPTION 'Reserva e inventario pertenecen a empresas distintas'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.empresa_id IS NOT NULL AND NEW.empresa_id <> v_empresa_reserva THEN
    RAISE EXCEPTION 'El detalle declara una empresa distinta a la de la reserva'
      USING ERRCODE = '42501';
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND v_tenant <> v_empresa_reserva THEN
    RAISE EXCEPTION 'Detalle de reserva fuera del tenant autenticado'
      USING ERRCODE = '42501';
  END IF;

  IF v_sucursal_reserva <> v_sucursal_inventario THEN
    RAISE EXCEPTION 'El inventario % no pertenece a la sucursal de la reserva %',
      NEW.inventario_id, NEW.reserva_id;
  END IF;

  RETURN NEW;
END;
$function$;

-- 2.5 Inventario: empresa_id inmutable y tenant coherente.
CREATE OR REPLACE FUNCTION public.fn_controlar_actualizacion_inventario()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_tenant BIGINT;
BEGIN
  IF NEW.empresa_id IS DISTINCT FROM OLD.empresa_id THEN
    RAISE EXCEPTION 'No se puede cambiar la empresa de un inventario existente'
      USING ERRCODE = '42501';
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND NEW.empresa_id IS DISTINCT FROM v_tenant THEN
    RAISE EXCEPTION 'Inventario fuera del tenant autenticado' USING ERRCODE = '42501';
  END IF;

  IF NEW.sucursal_id IS DISTINCT FROM OLD.sucursal_id
     OR NEW.variante_producto_id IS DISTINCT FROM OLD.variante_producto_id
     OR NEW.temporada_id IS DISTINCT FROM OLD.temporada_id THEN
    RAISE EXCEPTION
      'No se puede cambiar la sucursal, variante o temporada de un inventario existente';
  END IF;
  IF NEW.stock_actual IS DISTINCT FROM OLD.stock_actual
     OR NEW.stock_reservado IS DISTINCT FROM OLD.stock_reservado THEN
    NEW.fecha_actualizacion := CURRENT_TIMESTAMP;
  END IF;
  RETURN NEW;
END;
$function$;

-- 2.6 Normalizacion de correo (mismo comportamiento, search_path endurecido).
CREATE OR REPLACE FUNCTION public.fn_normalizar_correo_usuario()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
  IF NEW.correo IS NULL OR btrim(NEW.correo) = '' THEN
    RAISE EXCEPTION 'El correo del usuario no puede estar vacio';
  END IF;
  NEW.correo := lower(btrim(NEW.correo));
  RETURN NEW;
END;
$function$;

-- 2.7 IP de bitacora (mismo comportamiento).
CREATE OR REPLACE FUNCTION public.fn_completar_ip_bitacora()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_ip TEXT;
BEGIN
  IF NEW.ip IS NULL OR btrim(NEW.ip) = '' THEN
    v_ip := NULLIF(current_setting('app.ip', TRUE), '');
    IF v_ip IS NOT NULL THEN
      NEW.ip := v_ip;
    END IF;
  END IF;
  RETURN NEW;
END;
$function$;

-- 2.8 Proteccion de detalles comerciales cerrados + tenant.
CREATE OR REPLACE FUNCTION public.fn_proteger_detalle_venta()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_estado  TEXT;
  v_empresa BIGINT;
  v_tenant  BIGINT;
BEGIN
  SELECT estado, empresa_id INTO v_estado, v_empresa
    FROM public.venta WHERE id = COALESCE(NEW.venta_id, OLD.venta_id);
  IF v_estado IN ('COMPLETADA', 'CANCELADA', 'REEMBOLSADA') THEN
    RAISE EXCEPTION 'No se puede modificar el detalle de una venta en estado %', v_estado;
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND v_empresa IS NOT NULL AND v_tenant <> v_empresa THEN
    RAISE EXCEPTION 'Detalle de venta fuera del tenant autenticado'
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_proteger_detalle_orden_compra()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
  v_estado  TEXT;
  v_empresa BIGINT;
  v_tenant  BIGINT;
BEGIN
  SELECT estado, empresa_id INTO v_estado, v_empresa
    FROM public.orden_compra WHERE id = COALESCE(NEW.orden_compra_id, OLD.orden_compra_id);
  IF v_estado IN ('RECIBIDA', 'CANCELADA') THEN
    RAISE EXCEPTION
      'No se puede modificar el detalle de una orden de compra en estado %', v_estado;
  END IF;
  v_tenant := saas.tenant_actual();
  IF v_tenant IS NOT NULL AND v_empresa IS NOT NULL AND v_tenant <> v_empresa THEN
    RAISE EXCEPTION 'Detalle de orden de compra fuera del tenant autenticado'
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$function$;

-- 2.9 Ninguna fila nueva de la bitacora empresarial puede quedar sin tenant.
CREATE OR REPLACE FUNCTION public.fn_rechazar_bitacora_sin_tenant()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
  IF NEW.empresa_id IS NULL THEN
    RAISE EXCEPTION
      'public.bitacora exige empresa_id; los eventos globales van a saas.bitacora_global'
      USING ERRCODE = '23502';
  END IF;
  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_bitacora_rechazar_sin_tenant ON public.bitacora;
CREATE TRIGGER trg_bitacora_rechazar_sin_tenant
BEFORE INSERT ON public.bitacora
FOR EACH ROW EXECUTE FUNCTION public.fn_rechazar_bitacora_sin_tenant();

-- ===========================================================================
-- 3. Politicas de bitacora: append-only y tenant obligatorio
-- ===========================================================================

DO $bitacora_politicas$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE 'ALTER TABLE public.bitacora ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.bitacora FORCE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS tenant_aislamiento ON public.bitacora';
  EXECUTE 'DROP POLICY IF EXISTS bitacora_lectura_tenant ON public.bitacora';
  EXECUTE 'DROP POLICY IF EXISTS bitacora_insercion_tenant ON public.bitacora';
  EXECUTE format(
    'CREATE POLICY bitacora_lectura_tenant ON public.bitacora AS PERMISSIVE '
    'FOR SELECT TO %I USING (empresa_id = saas.tenant_actual())', v_runtime);
  EXECUTE format(
    'CREATE POLICY bitacora_insercion_tenant ON public.bitacora AS PERMISSIVE '
    'FOR INSERT TO %I WITH CHECK (empresa_id = saas.tenant_actual())', v_runtime);
  -- Sin politicas de UPDATE/DELETE: aunque se concediera el privilegio, la RLS
  -- no lo permitiria. La auditoria no se edita ni se borra desde el runtime.
  EXECUTE format('REVOKE ALL ON TABLE public.bitacora FROM %I', v_runtime);
  EXECUTE format('GRANT SELECT, INSERT ON TABLE public.bitacora TO %I', v_runtime);
END;
$bitacora_politicas$;

-- Clasificacion y conservacion de filas historicas sin tenant. La vista usa
-- security_invoker: el runtime no ve nada; un administrador puede clasificarlas.
CREATE OR REPLACE VIEW saas.bitacora_historica_global
WITH (security_invoker = true)
AS
SELECT id, fecha_hora, usuario_id, empresa_id, accion, entidad_afectada, descripcion, ip
  FROM public.bitacora
 WHERE empresa_id IS NULL;

CREATE OR REPLACE VIEW saas.bitacora_clasificacion
WITH (security_invoker = true)
AS
SELECT CASE WHEN empresa_id IS NULL THEN 'HISTORICO_SIN_TENANT' ELSE 'EMPRESARIAL' END AS clase,
       count(*) AS eventos,
       min(fecha_hora) AS desde,
       max(fecha_hora) AS hasta
  FROM public.bitacora
 GROUP BY 1;

-- ===========================================================================
-- 4. Rutinas historicas: cerrar EXECUTE y ofrecer una via tenant segura
-- ===========================================================================

DO $revocar_legacy$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  r RECORD;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS firma
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND (p.proname LIKE 'sp\_%' ESCAPE '\' OR p.proname LIKE 'fn\_%' ESCAPE '\')
  LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', r.firma);
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM %I', r.firma, v_runtime);
  END LOOP;
  -- Las funciones de trigger no necesitan EXECUTE para dispararse; el permiso
  -- se comprueba al crear el trigger, no al ejecutarlo.
END;
$revocar_legacy$;

CREATE OR REPLACE FUNCTION saas.sp_ajustar_inventario_tenant(
  p_inventario_id BIGINT,
  p_delta         INTEGER,
  p_tipo          TEXT DEFAULT NULL,
  p_motivo        TEXT DEFAULT NULL
)
RETURNS TABLE(inventario_id BIGINT, stock_actual INTEGER, stock_reservado INTEGER)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, saas
AS $ajuste$
DECLARE
  v_empresa BIGINT := saas.tenant_actual();
  v_inv     RECORD;
  v_nuevo   INTEGER;
  v_tipo    TEXT := NULLIF(btrim(COALESCE(p_tipo, '')), '');
BEGIN
  IF v_empresa IS NULL THEN
    RAISE EXCEPTION 'Sin contexto de empresa autenticado' USING ERRCODE = '42501';
  END IF;
  IF p_delta IS NULL OR p_delta = 0 THEN
    RAISE EXCEPTION 'El ajuste requiere un delta distinto de cero' USING ERRCODE = '22023';
  END IF;

  IF v_tipo IS NULL THEN
    v_tipo := CASE WHEN p_delta > 0 THEN 'AJUSTE_ENTRADA' ELSE 'AJUSTE_SALIDA' END;
  END IF;
  IF v_tipo NOT IN ('AJUSTE_ENTRADA','AJUSTE_SALIDA','ENTRADA_COMPRA',
                    'SALIDA_VENTA','DEVOLUCION','TRANSFERENCIA_ENTRADA',
                    'TRANSFERENCIA_SALIDA') THEN
    RAISE EXCEPTION 'Tipo de movimiento no admitido: %', v_tipo USING ERRCODE = '22023';
  END IF;
  IF (v_tipo IN ('AJUSTE_ENTRADA','ENTRADA_COMPRA','DEVOLUCION','TRANSFERENCIA_ENTRADA')
      AND p_delta < 0)
     OR (v_tipo IN ('AJUSTE_SALIDA','SALIDA_VENTA','TRANSFERENCIA_SALIDA')
         AND p_delta > 0) THEN
    RAISE EXCEPTION 'Signo del delta incompatible con el tipo %', v_tipo
      USING ERRCODE = '22023';
  END IF;

  SELECT i.id, i.stock_actual, i.stock_reservado
    INTO v_inv
    FROM public.inventario i
   WHERE i.id = p_inventario_id AND i.empresa_id = v_empresa
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Inventario % no encontrado en la empresa autenticada', p_inventario_id
      USING ERRCODE = 'P0002';
  END IF;

  v_nuevo := v_inv.stock_actual + p_delta;
  IF v_nuevo < 0 OR v_nuevo < v_inv.stock_reservado THEN
    RAISE EXCEPTION 'Stock resultante invalido: % (reservado %)', v_nuevo, v_inv.stock_reservado
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.inventario
     SET stock_actual = v_nuevo
   WHERE id = p_inventario_id AND empresa_id = v_empresa;

  INSERT INTO public.movimiento_inventario
    (inventario_id, usuario_id, tipo, cantidad, fecha_hora, observaciones, empresa_id)
  VALUES
    (p_inventario_id,
     NULLIF(current_setting('app.usuario_id', TRUE), '')::BIGINT,
     v_tipo, abs(p_delta), CURRENT_TIMESTAMP, p_motivo, v_empresa);

  RETURN QUERY SELECT p_inventario_id, v_nuevo, v_inv.stock_reservado;
END;
$ajuste$;

COMMENT ON FUNCTION saas.sp_ajustar_inventario_tenant(BIGINT, INTEGER, TEXT, TEXT) IS
  'Ajuste de stock tenant-aware (SECURITY INVOKER). Deriva la empresa de saas.tenant_actual() y nunca acepta empresa por parametro.';

-- ===========================================================================
-- 5. Verificacion de compatibilidad y fail-closed
-- ===========================================================================

CREATE OR REPLACE VIEW saas.compatibilidad_5bl
WITH (security_invoker = true)
AS
SELECT c.relname                              AS tabla,
       t.tgname                               AS trigger,
       p.proname                              AS funcion,
       p.prosecdef                            AS security_definer,
       pg_get_userbyid(p.proowner)            AS propietario,
       COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>') AS configuracion,
       (SELECT r.rolcanlogin FROM pg_roles r
         WHERE r.rolname = pg_get_userbyid(p.proowner)) AS propietario_puede_login
  FROM pg_trigger t
  JOIN pg_class c     ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p      ON p.oid = t.tgfoid
 WHERE n.nspname = 'public' AND NOT t.tgisinternal;

CREATE OR REPLACE FUNCTION saas.validar_triggers_5bl()
RETURNS TABLE(violacion TEXT)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public, saas
AS $val$
  SELECT format('%s.%s: definer=%s owner=%s config=%s',
                tabla, trigger, security_definer, propietario, configuracion)
    FROM saas.compatibilidad_5bl
   WHERE security_definer
      OR configuracion = '<sin search_path>'
      OR configuracion NOT LIKE '%search_path=%';
$val$;

DO $fail_closed$
DECLARE
  v TEXT;
BEGIN
  SELECT string_agg(violacion, ' | ') INTO v FROM saas.validar_triggers_5bl();
  IF v IS NOT NULL THEN
    RAISE EXCEPTION
      '5BL BLOQUEADO: hay triggers SECURITY DEFINER, sin search_path fijo o con propietario capaz de login: %', v;
  END IF;
  EXECUTE 'ALTER VIEW public.vw_inventario_stock SET (security_invoker = true)';
END;
$fail_closed$;

-- ===========================================================================
-- 6. Propiedad y ACL finales
-- ===========================================================================

DO $acl_final$
DECLARE
  v_runtime text := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  v_owner   text := COALESCE(NULLIF(current_setting('saas.rls_owner_role', TRUE), ''),
                             'saas_rls_owner');
BEGIN
  EXECUTE format('ALTER FUNCTION saas.es_principal_runtime() OWNER TO %I', v_owner);
  EXECUTE format(
    'ALTER FUNCTION saas.registrar_evento_global(TEXT, TEXT, BIGINT, TEXT, BIGINT, TEXT) OWNER TO %I',
    v_owner);

  EXECUTE format('REVOKE ALL ON TABLE saas.bitacora_global FROM PUBLIC, %I', v_runtime);
  EXECUTE format('REVOKE ALL ON TABLE saas.bitacora_global FROM %I', v_owner);
  EXECUTE format('GRANT INSERT ON TABLE saas.bitacora_global TO %I', v_owner);

  EXECUTE format('REVOKE ALL ON FUNCTION saas.registrar_evento_global(TEXT, TEXT, BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC, %I', v_runtime);
  EXECUTE format('REVOKE ALL ON FUNCTION saas.validar_triggers_5bl() FROM PUBLIC, %I', v_runtime);
  EXECUTE format('REVOKE ALL ON FUNCTION saas.sp_ajustar_inventario_tenant(BIGINT, INTEGER, TEXT, TEXT) FROM PUBLIC');
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.sp_ajustar_inventario_tenant(BIGINT, INTEGER, TEXT, TEXT) TO %I', v_runtime);
  EXECUTE format('GRANT EXECUTE ON FUNCTION saas.es_principal_runtime() TO %I, %I', v_runtime, v_owner);

  -- La vista historica usa security_invoker: el runtime solo ve sus filas, pero
  -- necesita el privilegio explicito sobre la vista (no lo hereda de las tablas).
  EXECUTE format('REVOKE ALL ON TABLE public.vw_inventario_stock FROM PUBLIC, %I', v_runtime);
  EXECUTE format('GRANT SELECT ON TABLE public.vw_inventario_stock TO %I', v_runtime);

  -- El propietario NOLOGIN conserva lectura para validar la asercion (00400) y
  -- escritura del canal global; no recibe DML sobre tablas empresariales.
  EXECUTE format('GRANT SELECT ON TABLE saas.compatibilidad_5bl, saas.bitacora_clasificacion TO %I', v_owner);
END;
$acl_final$;

COMMIT;
