BEGIN;
SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='300s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_pg17',0));

DO $keys$
DECLARE t text; n integer:=0;
 tables constant text[]:=ARRAY['bitacora','carrito','categoria','cliente','coleccion','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion','detalle_orden_compra','detalle_reserva','detalle_venta','devolucion','empleado','inventario','movimiento_inventario','orden_compra','pago','producto','producto_coleccion','promocion','promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva','sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 FOREACH t IN ARRAY tables LOOP
  EXECUTE format('CREATE INDEX %I ON public.%I(empresa_id)','idx_'||t||'_empresa',t);
  IF EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_class x ON x.oid=c.conrelid JOIN pg_namespace ns ON ns.oid=x.relnamespace
    WHERE ns.nspname='public' AND x.relname=t AND c.contype='p' AND array_length(c.conkey,1)=1
      AND (SELECT attname FROM pg_attribute WHERE attrelid=x.oid AND attnum=c.conkey[1])='id') THEN
    EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE(empresa_id,id)',t,'uq_tenant_'||t||'_id'); n:=n+1;
  END IF;
 END LOOP;
 IF n<>28 THEN RAISE EXCEPTION 'Se esperaban 28 PK id para claves tenant; hay %',n; END IF;
END $keys$;

ALTER TABLE public.categoria DROP CONSTRAINT uq_categoria_nombre;
ALTER TABLE public.categoria ADD CONSTRAINT uq_categoria_empresa_nombre UNIQUE(empresa_id,nombre);
ALTER TABLE public.temporada DROP CONSTRAINT uq_temporada_nombre;
ALTER TABLE public.temporada ADD CONSTRAINT uq_temporada_empresa_nombre UNIQUE(empresa_id,nombre);
ALTER TABLE public.variante_producto DROP CONSTRAINT uq_variante_producto_sku;
ALTER TABLE public.variante_producto ADD CONSTRAINT uq_variante_empresa_sku UNIQUE(empresa_id,sku);
ALTER TABLE public.sucursal DROP CONSTRAINT uq_sucursal_ciudad_nombre;
ALTER TABLE public.sucursal ADD CONSTRAINT uq_sucursal_empresa_ciudad_nombre UNIQUE(empresa_id,ciudad_id,nombre);
ALTER TABLE public.proveedor DROP CONSTRAINT uq_proveedor_nit;
ALTER TABLE public.proveedor ADD CONSTRAINT uq_proveedor_empresa_nit UNIQUE(empresa_id,nit);
ALTER TABLE public.cliente DROP CONSTRAINT uq_cliente_ci;
ALTER TABLE public.cliente ADD CONSTRAINT uq_cliente_empresa_ci UNIQUE(empresa_id,ci);
ALTER TABLE public.cliente DROP CONSTRAINT uq_cliente_usuario;
ALTER TABLE public.cliente ADD CONSTRAINT uq_cliente_empresa_usuario UNIQUE(empresa_id,usuario_id);
ALTER TABLE public.empleado DROP CONSTRAINT uq_empleado_ci;
ALTER TABLE public.empleado ADD CONSTRAINT uq_empleado_empresa_ci UNIQUE(empresa_id,ci);
ALTER TABLE public.empleado DROP CONSTRAINT uq_empleado_usuario;
ALTER TABLE public.empleado ADD CONSTRAINT uq_empleado_empresa_usuario UNIQUE(empresa_id,usuario_id);

DO $fks$
DECLARE r record; n integer:=0; fk text; upd text; del text; def text;
 tables constant text[]:=ARRAY['bitacora','carrito','categoria','cliente','coleccion','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion','detalle_orden_compra','detalle_reserva','detalle_venta','devolucion','empleado','inventario','movimiento_inventario','orden_compra','pago','producto','producto_coleccion','promocion','promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva','sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 FOR r IN SELECT ch.relname child,pa.relname parent,c.conname,a.attname col,c.confupdtype,c.confdeltype,c.condeferrable,c.condeferred
  FROM pg_constraint c JOIN pg_class ch ON ch.oid=c.conrelid JOIN pg_class pa ON pa.oid=c.confrelid
  JOIN pg_namespace ns ON ns.oid=ch.relnamespace JOIN pg_attribute a ON a.attrelid=ch.oid AND a.attnum=c.conkey[1]
  JOIN pg_attribute b ON b.attrelid=pa.oid AND b.attnum=c.confkey[1]
  WHERE ns.nspname='public' AND c.contype='f' AND array_length(c.conkey,1)=1
   AND ch.relname=ANY(tables) AND pa.relname=ANY(tables) AND b.attname='id' AND c.conname NOT LIKE 'fk_tenant_%'
  ORDER BY ch.relname,c.conname
 LOOP
  fk:='fk_tenant_'||r.conname; IF length(fk)>63 THEN RAISE EXCEPTION 'Nombre FK largo: %',fk; END IF;
  upd:=CASE r.confupdtype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END;
  del:=CASE r.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END;
  def:=CASE WHEN r.condeferrable THEN ' DEFERRABLE'||CASE WHEN r.condeferred THEN ' INITIALLY DEFERRED' ELSE ' INITIALLY IMMEDIATE' END ELSE '' END;
  EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY(empresa_id,%I) REFERENCES public.%I(empresa_id,id) ON UPDATE %s ON DELETE %s%s NOT VALID',r.child,fk,r.col,r.parent,upd,del,def);
  EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I',r.child,fk);
  EXECUTE format('CREATE INDEX %I ON public.%I(empresa_id,%I)','idx_tenant_fk_'||n,r.child,r.col); n:=n+1;
 END LOOP;
 IF n<>46 THEN RAISE EXCEPTION 'Se esperaban 46 FK empresa-padre; hay %',n; END IF;
END $fks$;

ALTER TABLE public.usuario_empresa ADD CONSTRAINT uq_usuario_empresa_composite UNIQUE(empresa_id,usuario_id);
ALTER TABLE public.cliente ADD CONSTRAINT fk_tenant_cliente_usuario FOREIGN KEY(empresa_id,usuario_id) REFERENCES public.usuario_empresa(empresa_id,usuario_id) NOT VALID;
ALTER TABLE public.empleado ADD CONSTRAINT fk_tenant_empleado_usuario FOREIGN KEY(empresa_id,usuario_id) REFERENCES public.usuario_empresa(empresa_id,usuario_id) NOT VALID;
ALTER TABLE public.cliente VALIDATE CONSTRAINT fk_tenant_cliente_usuario;
ALTER TABLE public.empleado VALIDATE CONSTRAINT fk_tenant_empleado_usuario;
COMMIT;

