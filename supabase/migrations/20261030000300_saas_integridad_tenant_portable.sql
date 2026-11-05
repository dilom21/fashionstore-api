-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / BLOQUE 2 / PASO 7
-- MIGRACION PORTABLE DE INTEGRIDAD MULTITENANT.
--
-- Reemplaza a 20261023000300_integridad_tenant_local.sql conservando su
-- proposito y su semantica, pero sin atarse al laboratorio:
--   * no fija current_database() ni conteos exactos (46 tablas, 48 FK, ...);
--   * descubre restricciones e indices por catalogo, no por nombre fijo;
--   * es idempotente;
--   * se detiene con mensaje explicito si hay datos que impedirian las FK.
--
-- Contenido:
--   1. UNIQUE (empresa_id, id) en cada tabla empresarial con PK simple `id`.
--   2. Relajacion de UNIQUE globales historicos a UNIQUE por empresa.
--   3. FK compuestas (empresa_id, columna_hija) -> (empresa_id, id) en todas
--      las relaciones padre/hija entre tablas empresariales.
--   4. Defensa de identidad: cliente/empleado solo pueden apuntar a usuarios
--      con membresia en el mismo tenant.
--
-- RLS y privilegios SQL efectivos NO se resuelven aqui (etapa de seguridad).
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '300s';

DO $integridad_portable$
DECLARE
  v_tablas CONSTANT TEXT[] := ARRAY[
    'bitacora', 'sucursal', 'empleado', 'cliente', 'categoria', 'producto',
    'variante_producto', 'recurso_producto', 'temporada', 'coleccion',
    'producto_coleccion', 'promocion', 'promocion_producto', 'proveedor',
    'proveedor_producto', 'orden_compra', 'detalle_orden_compra',
    'inventario', 'movimiento_inventario', 'carrito', 'detalle_carrito',
    'reserva', 'detalle_reserva', 'venta', 'detalle_venta', 'pago',
    'devolucion', 'detalle_devolucion', 'configuracion_vestidor_ar',
    'sesion_vestidor_ar', 'prueba_vestidor_ar'
  ];
  r RECORD;
  v_nombre TEXT;
  v_cols TEXT;
  v_creadas INTEGER := 0;
  v_fks INTEGER := 0;
  v_huerfanos BIGINT;
BEGIN
  -- 1) UNIQUE (empresa_id, id) para PK simple `id`.
  FOR r IN
    SELECT cl.relname AS tabla
    FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
    JOIN pg_catalog.pg_attribute a
      ON a.attrelid = cl.oid AND a.attnum = co.conkey[1]
    WHERE ns.nspname = 'public' AND co.contype = 'p'
      AND cardinality(co.conkey) = 1
      AND a.attname = 'id'
      AND cl.relname = ANY (v_tablas)
    ORDER BY cl.relname
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_constraint co2
      JOIN pg_catalog.pg_class cl2 ON cl2.oid = co2.conrelid
      JOIN pg_catalog.pg_namespace ns2 ON ns2.oid = cl2.relnamespace
      WHERE ns2.nspname = 'public' AND cl2.relname = r.tabla
        AND co2.contype = 'u'
        AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
             FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = cl2.oid AND a.attnum = ANY (co2.conkey))
            = ARRAY['empresa_id', 'id']
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (empresa_id, id)',
        r.tabla, 'uq_tenant_' || r.tabla || '_id'
      );
      v_creadas := v_creadas + 1;
    END IF;
  END LOOP;

  -- 2) UNIQUE historicos globales -> UNIQUE por empresa.
  --    Se identifican por conjunto de columnas, no por nombre.
  FOR r IN
    SELECT *
    FROM (VALUES
      ('categoria',         ARRAY['nombre'],                'uq_categoria_empresa_nombre'),
      ('temporada',         ARRAY['nombre'],                'uq_temporada_empresa_nombre'),
      ('variante_producto', ARRAY['sku'],                   'uq_variante_empresa_sku'),
      ('sucursal',          ARRAY['ciudad_id', 'nombre'],   'uq_sucursal_empresa_ciudad_nombre'),
      ('proveedor',         ARRAY['nit'],                   'uq_proveedor_empresa_nit'),
      ('cliente',           ARRAY['ci'],                    'uq_cliente_empresa_ci'),
      ('cliente',           ARRAY['usuario_id'],            'uq_cliente_empresa_usuario'),
      ('empleado',          ARRAY['ci'],                    'uq_empleado_empresa_ci'),
      ('empleado',          ARRAY['usuario_id'],            'uq_empleado_empresa_usuario')
    ) AS t(tabla, columnas, nombre)
  LOOP
    FOR v_nombre IN
      SELECT co.conname
      FROM pg_catalog.pg_constraint co
      JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
      JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
      WHERE ns.nspname = 'public' AND cl.relname = r.tabla
        AND co.contype = 'u'
        AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
             FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = cl.oid AND a.attnum = ANY (co.conkey))
            = (SELECT array_agg(x ORDER BY x) FROM unnest(r.columnas) AS x)
    LOOP
      EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', r.tabla, v_nombre);
    END LOOP;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_constraint co
      JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
      JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
      WHERE ns.nspname = 'public' AND cl.relname = r.tabla
        AND co.contype = 'u'
        AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
             FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = cl.oid AND a.attnum = ANY (co.conkey))
            = (SELECT array_agg(x ORDER BY x)
               FROM unnest(r.columnas || ARRAY['empresa_id']) AS x)
    ) THEN
      SELECT string_agg(quote_ident(x), ', ') INTO v_cols
      FROM unnest(r.columnas) AS x;
      BEGIN
        EXECUTE format(
          'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (empresa_id, %s)',
          r.tabla, r.nombre, v_cols
        );
        v_creadas := v_creadas + 1;
      EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION
          'No se puede crear UNIQUE por empresa en %.%: existen duplicados historicos que deben resolverse manualmente',
          r.tabla, v_cols;
      END;
    END IF;
  END LOOP;

  -- 3) FK compuestas empresa-padre derivadas de las FK simples existentes.
  FOR r IN
    SELECT child.relname AS tabla_hija,
           parent.relname AS tabla_padre,
           co.conname AS fk_legada,
           childcol.attname AS columna_hija
    FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class child ON child.oid = co.conrelid
    JOIN pg_catalog.pg_class parent ON parent.oid = co.confrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_catalog.pg_attribute childcol
      ON childcol.attrelid = child.oid AND childcol.attnum = co.conkey[1]
    JOIN pg_catalog.pg_attribute parentcol
      ON parentcol.attrelid = parent.oid AND parentcol.attnum = co.confkey[1]
    WHERE ns.nspname = 'public' AND co.contype = 'f'
      AND cardinality(co.conkey) = 1
      AND child.relname = ANY (v_tablas)
      AND parent.relname = ANY (v_tablas)
      AND parentcol.attname = 'id'
      AND co.conname NOT LIKE 'fk_tenant_%'
    ORDER BY child.relname, co.conname
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_constraint co2
      JOIN pg_catalog.pg_class cl2 ON cl2.oid = co2.conrelid
      JOIN pg_catalog.pg_namespace ns2 ON ns2.oid = cl2.relnamespace
      WHERE ns2.nspname = 'public' AND cl2.relname = r.tabla_hija
        AND co2.contype = 'f'
        AND co2.confrelid = format('public.%I', r.tabla_padre)::regclass
        AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
             FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = cl2.oid AND a.attnum = ANY (co2.conkey))
            = (SELECT array_agg(x ORDER BY x)
               FROM unnest(ARRAY['empresa_id'::text, r.columna_hija::text]) AS x)
    ) THEN
      v_nombre := 'fk_tenant_' || r.fk_legada;
      IF length(v_nombre) > 63 THEN
        v_nombre := 'fk_tenant_' || substr(md5(r.fk_legada), 1, 20);
      END IF;
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (empresa_id, %I) REFERENCES public.%I(empresa_id, id) ON UPDATE NO ACTION ON DELETE NO ACTION',
        r.tabla_hija, v_nombre, r.columna_hija, r.tabla_padre
      );
      v_fks := v_fks + 1;
    END IF;
  END LOOP;

  -- 4) Defensa de identidad por tenant.
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
    WHERE ns.nspname = 'public' AND cl.relname = 'usuario_empresa'
      AND co.conname = 'uq_usuario_empresa_composite'
  ) THEN
    ALTER TABLE public.usuario_empresa
      ADD CONSTRAINT uq_usuario_empresa_composite
      UNIQUE (empresa_id, usuario_id);
  END IF;

  SELECT count(*) INTO v_huerfanos
  FROM public.cliente c
  WHERE c.usuario_id IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM public.usuario_empresa ue
      WHERE ue.empresa_id = c.empresa_id AND ue.usuario_id = c.usuario_id
    );
  IF v_huerfanos > 0 THEN
    RAISE EXCEPTION
      'Hay % clientes cuyo usuario no tiene membresia en su empresa; cree las membresias antes de aplicar esta migracion',
      v_huerfanos;
  END IF;

  SELECT count(*) INTO v_huerfanos
  FROM public.empleado e
  WHERE NOT EXISTS (
      SELECT 1 FROM public.usuario_empresa ue
      WHERE ue.empresa_id = e.empresa_id AND ue.usuario_id = e.usuario_id
    );
  IF v_huerfanos > 0 THEN
    RAISE EXCEPTION
      'Hay % empleados cuyo usuario no tiene membresia en su empresa; cree las membresias antes de aplicar esta migracion',
      v_huerfanos;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
    WHERE ns.nspname = 'public' AND cl.relname = 'cliente'
      AND co.conname = 'fk_tenant_cliente_usuario'
  ) THEN
    ALTER TABLE public.cliente
      ADD CONSTRAINT fk_tenant_cliente_usuario
      FOREIGN KEY (empresa_id, usuario_id)
      REFERENCES public.usuario_empresa (empresa_id, usuario_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
    WHERE ns.nspname = 'public' AND cl.relname = 'empleado'
      AND co.conname = 'fk_tenant_empleado_usuario'
  ) THEN
    ALTER TABLE public.empleado
      ADD CONSTRAINT fk_tenant_empleado_usuario
      FOREIGN KEY (empresa_id, usuario_id)
      REFERENCES public.usuario_empresa (empresa_id, usuario_id);
  END IF;

  RAISE NOTICE 'Integridad: % restricciones UNIQUE creadas, % FK compuestas nuevas',
    v_creadas, v_fks;
END;
$integridad_portable$;

COMMIT;
