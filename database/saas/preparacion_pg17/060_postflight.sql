-- SOLO LECTURA. Ejecutar después de 010..040 en una base nueva aislada.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='60s';
DO $check$
DECLARE n integer; t text; vanter bigint;
 tables constant text[]:=ARRAY['bitacora','carrito','categoria','cliente','coleccion','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion','detalle_orden_compra','detalle_reserva','detalle_venta','devolucion','empleado','inventario','movimiento_inventario','orden_compra','pago','producto','producto_coleccion','promocion','promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva','sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 SELECT id INTO STRICT vanter FROM public.empresa WHERE slug='vanter-men';
 SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace WHERE ns.nspname='public' AND c.relkind='r';
 IF n<>46 THEN RAISE EXCEPTION 'Se esperaban 46 tablas, hay %',n; END IF;
 FOREACH t IN ARRAY tables LOOP
  IF NOT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=t AND column_name='empresa_id') THEN RAISE EXCEPTION 'Falta %.empresa_id',t; END IF;
  EXECUTE format('SELECT count(*) FROM public.%I WHERE empresa_id IS DISTINCT FROM $1',t) INTO n USING vanter;
  IF n<>0 THEN RAISE EXCEPTION 'Filas fuera de Vanter en %: %',t,n; END IF;
 END LOOP;
 IF (SELECT count(*) FROM public.usuario_empresa)<>9 OR (SELECT count(*) FROM public.sucursal)<>4
  OR (SELECT count(*) FROM public.cliente)<>5 OR (SELECT count(*) FROM public.empleado)<>4
  OR (SELECT count(*) FROM public.producto)<>14 OR (SELECT count(*) FROM public.variante_producto)<>120
  OR (SELECT count(*) FROM public.inventario)<>265 OR (SELECT count(*) FROM public.reserva)<>11
  OR (SELECT count(*) FROM public.venta)<>40 OR (SELECT count(*) FROM public.pago)<>30
  OR (SELECT count(*) FROM public.bitacora)<>806 THEN RAISE EXCEPTION 'Datos históricos críticos no preservados'; END IF;
 SELECT count(*) INTO n FROM pg_constraint WHERE conname LIKE 'fk_tenant_%' AND contype='f';
 IF n<>48 THEN RAISE EXCEPTION 'Se esperaban 48 FK tenant (46+2), hay %',n; END IF;
 IF (SELECT reloptions @> ARRAY['security_invoker=true'] FROM pg_class WHERE oid='public.vw_inventario_stock'::regclass) IS NOT TRUE THEN RAISE EXCEPTION 'Vista no es security_invoker'; END IF;
END $check$;
SELECT 'POSTCHECK_SCHEMA_INTEGRITY_OK_RLS_AUN_BLOQUEADO' resultado;
ROLLBACK;

