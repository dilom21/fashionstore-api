-- FASHIONSTORE / B2 PASO 5 / EXCLUSIVAMENTE PRUEBA LOCAL.
-- PostgreSQL 18 - NO EJECUTAR EN SUPABASE.
-- Requiere resultado del Paso 4 en la base local con ID de empresa
-- obtenido dinamicamente desde slug, nunca asumir ID=1.
-- FK compuestas: evitan asociar una fila con un padre de otra empresa.
-- La seguridad de lectura/escritura del API depende tambien de FastAPI y RLS.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

DO $tenant_guard$
DECLARE
 v_actual BIGINT;
 v_emp BIGINT;
 v_t TEXT;
 v_total BIGINT;
 v_padres INTEGER;
 v_fk INTEGER;
 v_lista TEXT[] := ARRAY['bitacora', 'carrito', 'categoria', 'cliente', 'coleccion', 'configuracion_vestidor_ar', 'detalle_carrito', 'detalle_devolucion', 'detalle_orden_compra', 'detalle_reserva', 'detalle_venta', 'devolucion', 'empleado', 'inventario', 'movimiento_inventario', 'orden_compra', 'pago', 'producto', 'producto_coleccion', 'promocion', 'promocion_producto', 'proveedor', 'proveedor_producto', 'prueba_vestidor_ar', 'recurso_producto', 'reserva', 'sesion_vestidor_ar', 'sucursal', 'temporada', 'variante_producto', 'venta'];
BEGIN
 IF current_database() <> 'fashionstore_restore_20261023_193554_594' THEN
   RAISE EXCEPTION 'Migracion local: base incorrecta (%)', current_database();
 END IF;
 IF cardinality(v_lista) <> 31 OR (SELECT count(DISTINCT t) FROM unnest(v_lista) u(t)) <> 31 THEN
   RAISE EXCEPTION 'Inventario de tablas empresariales incorrecto';
 END IF;
 SELECT id INTO STRICT v_emp FROM public.empresa WHERE slug='vanter-men';
 SELECT count(*) INTO v_total FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r';
 IF v_total <> 46 OR (SELECT count(*) FROM public.empresa) <> 1 THEN
   RAISE EXCEPTION 'Se esperan 46 tablas y solo Vanter Men, hay %',v_total;
 END IF;
 -- Solo avanzar si todas las tablas tienen empresa_id y conservan al tenant.
 FOREACH v_t IN ARRAY v_lista LOOP
   IF NOT EXISTS(SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name=v_t AND column_name='empresa_id') THEN
      RAISE EXCEPTION 'Falta empresa_id: %',v_t;
   END IF;
   EXECUTE format('SELECT count(*) FROM public.%I WHERE empresa_id IS DISTINCT FROM $1',v_t)
     INTO v_actual USING v_emp;
   IF v_actual <> 0 THEN RAISE EXCEPTION 'Propietarios historicos no consistentes: %',v_t; END IF;
 END LOOP;
 -- Uniques compuestos para todas las tablas con PK simple id; conservar PK original.
 FOREACH v_t IN ARRAY v_lista LOOP
   IF EXISTS(
      SELECT 1 FROM pg_constraint co
      JOIN pg_class cl ON cl.oid=co.conrelid
      JOIN pg_namespace ns ON ns.oid=cl.relnamespace
      WHERE ns.nspname='public' AND cl.relname=v_t AND co.contype='p'
        AND array_length(co.conkey,1)=1
        AND (SELECT attname FROM pg_attribute WHERE attrelid=cl.oid
             AND attnum=co.conkey[1])='id'
   ) THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (empresa_id,id)',
                     v_t,'uq_tenant_' || v_t || '_id');
   END IF;
 END LOOP;

 -- Catalogos y personas: relajar SOLO los uniques globales identificados.
 -- No cambiar referencias de pasarela: pago.referencia_transaccion queda global.
 ALTER TABLE public.categoria DROP CONSTRAINT uq_categoria_nombre;
 ALTER TABLE public.categoria ADD CONSTRAINT uq_categoria_empresa_nombre
   UNIQUE(empresa_id,nombre);
 ALTER TABLE public.temporada DROP CONSTRAINT uq_temporada_nombre;
 ALTER TABLE public.temporada ADD CONSTRAINT uq_temporada_empresa_nombre
   UNIQUE(empresa_id,nombre);
 ALTER TABLE public.variante_producto DROP CONSTRAINT uq_variante_producto_sku;
 ALTER TABLE public.variante_producto ADD CONSTRAINT uq_variante_empresa_sku
   UNIQUE(empresa_id,sku);
 ALTER TABLE public.sucursal DROP CONSTRAINT uq_sucursal_ciudad_nombre;
 ALTER TABLE public.sucursal ADD CONSTRAINT uq_sucursal_empresa_ciudad_nombre
   UNIQUE(empresa_id,ciudad_id,nombre);
 ALTER TABLE public.proveedor DROP CONSTRAINT uq_proveedor_nit;
 ALTER TABLE public.proveedor ADD CONSTRAINT uq_proveedor_empresa_nit
   UNIQUE(empresa_id,nit);
 ALTER TABLE public.cliente DROP CONSTRAINT uq_cliente_ci;
 ALTER TABLE public.cliente ADD CONSTRAINT uq_cliente_empresa_ci UNIQUE(empresa_id,ci);
 ALTER TABLE public.cliente DROP CONSTRAINT uq_cliente_usuario;
 ALTER TABLE public.cliente ADD CONSTRAINT uq_cliente_empresa_usuario UNIQUE(empresa_id,usuario_id);
 ALTER TABLE public.empleado DROP CONSTRAINT uq_empleado_ci;
 ALTER TABLE public.empleado ADD CONSTRAINT uq_empleado_empresa_ci UNIQUE(empresa_id,ci);
 ALTER TABLE public.empleado DROP CONSTRAINT uq_empleado_usuario;
 ALTER TABLE public.empleado ADD CONSTRAINT uq_empleado_empresa_usuario UNIQUE(empresa_id,usuario_id);

 -- Endurecer TODAS las FK empresariales del esquema legado de forma automatica.
 -- Mantener las 60 FK legadas simples y sumar una FK compuesta paralela.
 -- Los catálogos globales no participan; referencia a usuario global se
 -- protege mediante membresía dedicada (abajo) en las relaciones directas.
END;
$tenant_guard$;

-- Las FKs compuestas se generan a partir de metadatos en tiempo de ejecución.
DO $tenant_fks$
DECLARE
 v_child TEXT;
 v_parent TEXT;
 v_column TEXT;
 v_fk_name TEXT;
 r RECORD;
 v_company_tables TEXT[] := ARRAY['bitacora', 'carrito', 'categoria', 'cliente', 'coleccion', 'configuracion_vestidor_ar', 'detalle_carrito', 'detalle_devolucion', 'detalle_orden_compra', 'detalle_reserva', 'detalle_venta', 'devolucion', 'empleado', 'inventario', 'movimiento_inventario', 'orden_compra', 'pago', 'producto', 'producto_coleccion', 'promocion', 'promocion_producto', 'proveedor', 'proveedor_producto', 'prueba_vestidor_ar', 'recurso_producto', 'reserva', 'sesion_vestidor_ar', 'sucursal', 'temporada', 'variante_producto', 'venta'];
 v_expected INTEGER;
 v_actual INTEGER := 0;
BEGIN
 FOR r IN
   SELECT child.relname AS tabla_hija, parent.relname AS tabla_padre,
          co.conname AS fk_legada, childcol.attname AS columna_hija
   FROM pg_constraint co
   JOIN pg_class child ON child.oid=co.conrelid
   JOIN pg_class parent ON parent.oid=co.confrelid
   JOIN pg_namespace ns ON ns.oid=child.relnamespace
   JOIN pg_attribute childcol ON childcol.attrelid=child.oid
     AND childcol.attnum=co.conkey[1]
   JOIN pg_attribute parentcol ON parentcol.attrelid=parent.oid
     AND parentcol.attnum=co.confkey[1]
   WHERE ns.nspname='public' AND co.contype='f'
     AND array_length(co.conkey,1)=1
     AND child.relname = ANY(v_company_tables)
     AND parent.relname = ANY(v_company_tables)
     AND parentcol.attname='id'
     AND NOT co.conname LIKE 'fk_tenant_%'
   ORDER BY child.relname,co.conname
 LOOP
   v_fk_name := 'fk_tenant_' || r.fk_legada;
   -- Límite 63 caracteres. En esquemas actuales nombres quedan < 63.
   IF length(v_fk_name)>63 THEN RAISE EXCEPTION 'Nombre FK excesivo: %',v_fk_name; END IF;
   EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (empresa_id,%I) REFERENCES public.%I(empresa_id,id) ON UPDATE NO ACTION ON DELETE NO ACTION',
       r.tabla_hija,v_fk_name,r.columna_hija,r.tabla_padre);
   v_actual := v_actual+1;
 END LOOP;
 IF v_actual = 0 THEN RAISE EXCEPTION 'No se encontraron FK empresariales; revisar inventario'; END IF;
 RAISE NOTICE 'FK compuestas empresa-padre agregadas: %',v_actual;
END;
$tenant_fks$;

-- NUEVA DEFENSA: impedir registros de cliente/empleado que apunten a
-- identidades globales sin membresia en el mismo tenant. 
-- FK empresa_id/usuario_id con ON DELETE RESTRICT; columnas NULL en cliente
-- siguen siendo permitidas. No crea membresias ni modifica usuarios.
ALTER TABLE public.usuario_empresa ADD CONSTRAINT uq_usuario_empresa_composite
  UNIQUE(empresa_id,usuario_id);
ALTER TABLE public.cliente ADD CONSTRAINT fk_tenant_cliente_usuario
  FOREIGN KEY(empresa_id,usuario_id)
  REFERENCES public.usuario_empresa(empresa_id,usuario_id);
ALTER TABLE public.empleado ADD CONSTRAINT fk_tenant_empleado_usuario
  FOREIGN KEY(empresa_id,usuario_id)
  REFERENCES public.usuario_empresa(empresa_id,usuario_id);

-- Prevenir alteracion silenciosa de la titularidad durante actualizaciones
-- de recursos historicos es obligacion de API + politicas (paso posterior).
COMMIT;
