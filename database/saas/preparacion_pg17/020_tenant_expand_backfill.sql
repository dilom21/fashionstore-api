BEGIN;
SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='180s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_pg17',0));
DO $expand$
DECLARE t text; v_empresa bigint; antes bigint; despues bigint;
 tables constant text[]:=ARRAY['bitacora','carrito','categoria','cliente','coleccion','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion','detalle_orden_compra','detalle_reserva','detalle_venta','devolucion','empleado','inventario','movimiento_inventario','orden_compra','pago','producto','producto_coleccion','promocion','promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva','sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 SELECT id INTO STRICT v_empresa FROM public.empresa WHERE slug='vanter-men';
 IF (SELECT count(*) FROM public.empresa)<>1 THEN RAISE EXCEPTION 'Expand sólo admite Vanter Men como tenant inicial'; END IF;
 FOREACH t IN ARRAY tables LOOP
  IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=t AND column_name='empresa_id') THEN RAISE EXCEPTION '%.empresa_id ya existe',t; END IF;
  EXECUTE format('SELECT count(*) FROM public.%I',t) INTO antes;
  -- PG17 puede materializar el DEFAULT constante sin ejecutar UPDATE ni triggers fila a fila.
  EXECUTE format('ALTER TABLE public.%I ADD COLUMN empresa_id bigint DEFAULT %s',t,v_empresa);
  EXECUTE format('ALTER TABLE public.%I ALTER COLUMN empresa_id DROP DEFAULT',t);
  IF t<>'bitacora' THEN EXECUTE format('ALTER TABLE public.%I ALTER COLUMN empresa_id SET NOT NULL',t); END IF;
  EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY(empresa_id) REFERENCES public.empresa(id) ON UPDATE CASCADE ON DELETE RESTRICT NOT VALID',t,'fk_'||t||'_empresa');
  EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I',t,'fk_'||t||'_empresa');
  EXECUTE format('SELECT count(*) FROM public.%I',t) INTO despues;
  IF antes<>despues THEN RAISE EXCEPTION 'Cambió cardinalidad en %',t; END IF;
  EXECUTE format('SELECT count(*) FROM public.%I WHERE empresa_id IS DISTINCT FROM $1',t) INTO despues USING v_empresa;
  IF despues<>0 THEN RAISE EXCEPTION 'Backfill incompleto en %',t; END IF;
 END LOOP;
END $expand$;
COMMIT;

