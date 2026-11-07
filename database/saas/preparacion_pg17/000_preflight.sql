-- SOLO LECTURA. Baseline de FASE 2; no aplicar como migración.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $preflight$
DECLARE
  v integer;
  t text;
  company_tables constant text[] := ARRAY[
    'bitacora','carrito','categoria','cliente','coleccion',
    'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
    'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
    'empleado','inventario','movimiento_inventario','orden_compra','pago',
    'producto','producto_coleccion','promocion','promocion_producto','proveedor',
    'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
    'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
  IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
    RAISE EXCEPTION 'Se requiere PostgreSQL 17; servidor=%', version();
  END IF;
  SELECT count(*) INTO v FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r';
  IF v<>41 THEN RAISE EXCEPTION 'Baseline incompatible: se esperaban 41 tablas, hay %',v; END IF;
  IF cardinality(company_tables)<>31 THEN RAISE EXCEPTION 'Inventario tenant interno inválido'; END IF;
  FOREACH t IN ARRAY company_tables LOOP
    IF to_regclass(format('public.%I',t)) IS NULL THEN RAISE EXCEPTION 'Falta tabla %',t; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=t AND column_name='empresa_id') THEN
      RAISE EXCEPTION 'Baseline ya alterado: %.empresa_id existe',t;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM unnest(ARRAY['empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion']) x
             WHERE to_regclass(format('public.%I',x)) IS NOT NULL) THEN
    RAISE EXCEPTION 'Una o más tablas SaaS ya existen';
  END IF;
  IF (SELECT count(*) FROM public.usuario)<>9 OR (SELECT count(*) FROM public.sucursal)<>4
     OR (SELECT count(*) FROM public.cliente)<>5 OR (SELECT count(*) FROM public.empleado)<>4
     OR (SELECT count(*) FROM public.producto)<>14 OR (SELECT count(*) FROM public.variante_producto)<>120
     OR (SELECT count(*) FROM public.inventario)<>265 OR (SELECT count(*) FROM public.reserva)<>11
     OR (SELECT count(*) FROM public.venta)<>40 OR (SELECT count(*) FROM public.pago)<>30
     OR (SELECT count(*) FROM public.bitacora)<>806 THEN
    RAISE EXCEPTION 'Las cantidades históricas críticas difieren de la FASE 2';
  END IF;
  IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f')<>15
     OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='p')<>9
     OR (SELECT count(*) FROM pg_trigger tg JOIN pg_class c ON c.oid=tg.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT tg.tgisinternal)<>27 THEN
    RAISE EXCEPTION 'Routines/triggers no coinciden con el catálogo recuperado';
  END IF;
END $preflight$;

SELECT 'PRECHECK_OK' AS resultado;
ROLLBACK;

