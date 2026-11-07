-- COMPATIBILIDAD TEMPORAL MONOCOMPANIA.
-- Ejecutar solamente despues de 040 y antes de reabrir la API legacy.
-- Debe retirarse (triggers de asignacion + guarda) antes de crear otra empresa.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_pg17',0));

DO $preflight$
DECLARE v_empresa bigint; n bigint; t text;
 tables constant text[]:=ARRAY[
  'bitacora','carrito','categoria','cliente','coleccion',
  'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
  'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
  'empleado','inventario','movimiento_inventario','orden_compra','pago',
  'producto','producto_coleccion','promocion','promocion_producto','proveedor',
  'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
  'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 IF (SELECT count(*) FROM public.empresa)<>1 THEN
  RAISE EXCEPTION 'La compatibilidad legacy exige exactamente una empresa';
 END IF;
 SELECT id INTO STRICT v_empresa
 FROM public.empresa WHERE slug='vanter-men' AND estado IS TRUE;

 FOREACH t IN ARRAY tables LOOP
  IF NOT EXISTS(
   SELECT 1 FROM information_schema.columns
   WHERE table_schema='public' AND table_name=t AND column_name='empresa_id'
  ) THEN
   RAISE EXCEPTION 'Falta %.empresa_id; ejecute primero 020',t;
  END IF;
  EXECUTE format(
   'SELECT count(*) FROM public.%I WHERE empresa_id IS DISTINCT FROM $1',t
  ) INTO STRICT n USING v_empresa;
  IF n<>0 THEN
   RAISE EXCEPTION 'La tabla % contiene filas fuera de Vanter Men',t;
  END IF;
 END LOOP;
END $preflight$;

CREATE OR REPLACE FUNCTION public.fn_guard_legacy_vanter_monotenant()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog,pg_temp
AS $fn$
BEGIN
 IF TG_OP='INSERT' THEN
  RAISE EXCEPTION 'Modo legacy temporal: no se permiten empresas adicionales'
   USING ERRCODE='42501';
 ELSIF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Modo legacy temporal: Vanter Men no puede eliminarse'
   USING ERRCODE='42501';
 ELSIF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.slug IS DISTINCT FROM OLD.slug
    OR NEW.slug<>'vanter-men'
    OR NEW.estado IS NOT TRUE THEN
  RAISE EXCEPTION 'Modo legacy temporal: identidad y estado de Vanter Men son inmutables'
   USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION public.fn_guard_legacy_vanter_monotenant() FROM PUBLIC;

CREATE TRIGGER trg_empresa_guard_legacy_vanter_monotenant
BEFORE INSERT OR DELETE OR UPDATE OF id,slug,estado ON public.empresa
FOR EACH ROW EXECUTE FUNCTION public.fn_guard_legacy_vanter_monotenant();

CREATE OR REPLACE FUNCTION public.fn_asignar_vanter_insert_legacy()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog,pg_temp
AS $fn$
DECLARE v_empresa bigint;
BEGIN
 IF (SELECT count(*) FROM public.empresa)<>1 THEN
  RAISE EXCEPTION 'Modo legacy temporal requiere exactamente una empresa'
   USING ERRCODE='42501';
 END IF;
 SELECT id INTO STRICT v_empresa
 FROM public.empresa WHERE slug='vanter-men' AND estado IS TRUE;
 IF NEW.empresa_id IS NULL THEN
  NEW.empresa_id:=v_empresa;
 ELSIF NEW.empresa_id<>v_empresa THEN
  RAISE EXCEPTION 'Modo legacy temporal sólo admite datos de Vanter Men'
   USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION public.fn_asignar_vanter_insert_legacy() FROM PUBLIC;

DO $insert_triggers$
DECLARE t text;
 tables constant text[]:=ARRAY[
  'bitacora','carrito','categoria','cliente','coleccion',
  'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
  'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
  'empleado','inventario','movimiento_inventario','orden_compra','pago',
  'producto','producto_coleccion','promocion','promocion_producto','proveedor',
  'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
  'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 FOREACH t IN ARRAY tables LOOP
  EXECUTE format(
   'CREATE TRIGGER %I BEFORE INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.fn_asignar_vanter_insert_legacy()',
   'trg_'||t||'_asignar_vanter_legacy',t
  );
 END LOOP;
END $insert_triggers$;

-- Las altas legacy crean usuario antes que cliente/empleado. 030 exige que la
-- membresia exista para que su FK compuesta sea valida.
CREATE OR REPLACE FUNCTION public.fn_sincronizar_usuario_empresa_vanter_legacy()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog,pg_temp
AS $fn$
DECLARE v_empresa bigint;
BEGIN
 IF (SELECT count(*) FROM public.empresa)<>1 THEN
  RAISE EXCEPTION 'Modo legacy temporal requiere exactamente una empresa'
   USING ERRCODE='42501';
 END IF;
 SELECT id INTO STRICT v_empresa
 FROM public.empresa WHERE slug='vanter-men' AND estado IS TRUE;
 INSERT INTO public.usuario_empresa(usuario_id,empresa_id,rol_id,estado)
 VALUES(NEW.id,v_empresa,NEW.rol_id,NEW.estado)
 ON CONFLICT(usuario_id,empresa_id) DO UPDATE
 SET rol_id=EXCLUDED.rol_id,estado=EXCLUDED.estado;
 RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION public.fn_sincronizar_usuario_empresa_vanter_legacy() FROM PUBLIC;

CREATE TRIGGER trg_usuario_empresa_vanter_legacy
AFTER INSERT OR UPDATE OF rol_id,estado ON public.usuario
FOR EACH ROW EXECUTE FUNCTION public.fn_sincronizar_usuario_empresa_vanter_legacy();

-- Las cinco tablas nuevas no forman parte de la API legacy ni deben quedar
-- expuestas por los roles de la Data API durante esta etapa sin RLS SaaS.
REVOKE ALL ON TABLE public.empresa,public.plan_saas,public.suscripcion,
 public.usuario_empresa,public.empresa_configuracion FROM PUBLIC;
DO $data_api$
DECLARE r text;
BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format(
    'REVOKE ALL ON TABLE public.empresa,public.plan_saas,public.suscripcion,public.usuario_empresa,public.empresa_configuracion FROM %I',r
   );
  END IF;
 END LOOP;
END $data_api$;

DO $postcheck$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n
 FROM pg_trigger tg
 JOIN pg_class c ON c.oid=tg.tgrelid
 JOIN pg_namespace ns ON ns.oid=c.relnamespace
 WHERE ns.nspname='public' AND NOT tg.tgisinternal
   AND tg.tgname LIKE 'trg_%_asignar_vanter_legacy'
   AND c.relname=ANY(ARRAY[
    'bitacora','carrito','categoria','cliente','coleccion',
    'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
    'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
    'empleado','inventario','movimiento_inventario','orden_compra','pago',
    'producto','producto_coleccion','promocion','promocion_producto','proveedor',
    'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
    'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta']);
 IF n<>31 THEN RAISE EXCEPTION 'Se esperaban 31 triggers temporales; hay %',n; END IF;
 IF EXISTS(
  SELECT 1 FROM information_schema.columns
  WHERE table_schema='public' AND column_name='empresa_id'
    AND table_name=ANY(ARRAY[
     'bitacora','carrito','categoria','cliente','coleccion',
     'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
     'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
     'empleado','inventario','movimiento_inventario','orden_compra','pago',
     'producto','producto_coleccion','promocion','promocion_producto','proveedor',
     'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
     'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'])
    AND column_default IS NOT NULL
 ) THEN RAISE EXCEPTION 'No deben existir defaults globales de empresa_id'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM pg_trigger
  WHERE tgrelid='public.empresa'::regclass
    AND tgname='trg_empresa_guard_legacy_vanter_monotenant'
    AND NOT tgisinternal
 ) THEN RAISE EXCEPTION 'Falta la guarda monocompania'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM pg_trigger
  WHERE tgrelid='public.usuario'::regclass
    AND tgname='trg_usuario_empresa_vanter_legacy'
    AND NOT tgisinternal
 ) THEN RAISE EXCEPTION 'Falta la sincronizacion de membresia legacy'; END IF;
END $postcheck$;

COMMIT;
