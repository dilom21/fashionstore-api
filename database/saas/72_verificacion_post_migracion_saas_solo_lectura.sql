-- FASHIONSTORE / SAAS / VERIFICACION POSTERIOR A LA MIGRACION - SOLO LECTURA
-- Ejecutar contra la instancia destino DESPUES de aplicar
-- supabase/migrations/20261030000100..000300.
--
-- No modifica datos ni estructura. Sirve como comprobante del dia de
-- migracion: todo control debe salir en verde (0 cruces, 0 sin tenant).
--
--   psql "$DESTINO" -v ON_ERROR_STOP=1 -f database/saas/72_verificacion_post_migracion_saas_solo_lectura.sql

\pset pager off

-- 1) Nucleo SaaS y semilla de planes.
SELECT 'nucleo_saas' AS control, t.nombre AS detalle,
       CASE WHEN to_regclass('public.' || t.nombre) IS NULL
            THEN 'FALTA' ELSE 'OK' END AS valor
FROM (VALUES ('empresa'), ('plan_saas'), ('suscripcion'),
             ('usuario_empresa'), ('empresa_configuracion')) AS t(nombre);

SELECT 'planes' AS control, codigo AS detalle,
       format('max_sucursales=%s ia=%s activo=%s',
              max_sucursales_activas, permite_reportes_ia, estado) AS valor
FROM public.plan_saas
ORDER BY codigo;

-- 2) Tenencia por tabla: columna, nulabilidad, filas y filas sin empresa.
DO $post_tenencia$
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
  v_tabla TEXT;
  v_nulos BIGINT;
  v_total BIGINT;
  v_nullable TEXT;
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    SELECT is_nullable INTO v_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = v_tabla
      AND column_name = 'empresa_id';

    IF v_nullable IS NULL THEN
      RAISE NOTICE 'tenencia | % | FALTA empresa_id', v_tabla;
      CONTINUE;
    END IF;

    EXECUTE format('SELECT count(*), count(*) FILTER (WHERE empresa_id IS NULL) FROM public.%I', v_tabla)
      INTO v_total, v_nulos;

    RAISE NOTICE 'tenencia | % | nullable=% filas=% sin_empresa=%',
      v_tabla, v_nullable, v_total, v_nulos;
  END LOOP;
END;
$post_tenencia$;

-- 3) Restricciones SaaS instaladas.
SELECT 'restricciones' AS control,
       format('fk_empresa=%s fk_tenant=%s uq_tenant=%s',
              count(*) FILTER (WHERE conname LIKE 'fk_empresa_%'),
              count(*) FILTER (WHERE conname LIKE 'fk_tenant_%'),
              count(*) FILTER (WHERE conname LIKE 'uq_tenant_%')) AS detalle,
       CASE WHEN count(*) FILTER (WHERE conname LIKE 'fk_tenant_%') >= 40
                 AND count(*) FILTER (WHERE conname LIKE 'uq_tenant_%') >= 25
            THEN 'OK' ELSE 'REVISAR' END AS valor
FROM pg_constraint;

-- 4) Indices por empresa y RLS.
SELECT 'indices_empresa' AS control, 'conteo' AS detalle,
       count(*)::text || ' (esperado 31: 30 heredadas + bitacora)' AS valor
FROM pg_indexes
WHERE schemaname = 'public' AND indexname LIKE 'idx_%_empresa';

SELECT 'rls' AS control, c.relname AS detalle,
       format('rls=%s force=%s', c.relrowsecurity, c.relforcerowsecurity) AS valor
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('empresa','plan_saas','suscripcion','usuario_empresa',
                    'empresa_configuracion')
ORDER BY c.relname;

-- 5) Cruces padre/hija entre empresas: todos deben ser 0.
DO $post_cruces$
DECLARE
  r RECORD;
  v_n BIGINT;
  v_alertas INTEGER := 0;
BEGIN
  FOR r IN
    SELECT child.relname AS hija, parent.relname AS padre,
           childcol.attname AS columna
    FROM pg_catalog.pg_constraint co
    JOIN pg_catalog.pg_class child ON child.oid = co.conrelid
    JOIN pg_catalog.pg_class parent ON parent.oid = co.confrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_catalog.pg_attribute childcol
      ON childcol.attrelid = child.oid AND childcol.attnum = co.conkey[1]
    WHERE ns.nspname = 'public' AND co.contype = 'f'
      AND cardinality(co.conkey) = 1
      AND co.conname NOT LIKE 'fk_tenant_%'
      AND EXISTS (SELECT 1 FROM information_schema.columns c
                  WHERE c.table_schema = 'public' AND c.table_name = child.relname
                    AND c.column_name = 'empresa_id')
      AND EXISTS (SELECT 1 FROM information_schema.columns p
                  WHERE p.table_schema = 'public' AND p.table_name = parent.relname
                    AND p.column_name = 'empresa_id')
    ORDER BY child.relname, co.conname
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I c JOIN public.%I p ON c.%I = p.id WHERE c.empresa_id IS DISTINCT FROM p.empresa_id',
      r.hija, r.padre, r.columna
    ) INTO v_n;
    IF v_n > 0 THEN
      v_alertas := v_alertas + 1;
      RAISE NOTICE 'cruce | % -> % por % | FILAS CRUZADAS=%', r.hija, r.padre, r.columna, v_n;
    END IF;
  END LOOP;
  RAISE NOTICE 'cruce | resumen | relaciones_con_cruce=%', v_alertas;
END;
$post_cruces$;

-- 6) Identidad: usuarios con membresia y perfiles coherentes.
DO $post_identidad$
DECLARE
  v_sin_membresia BIGINT;
  v_clientes BIGINT;
  v_empleados BIGINT;
BEGIN
  EXECUTE 'SELECT count(*) FROM public.usuario u
           WHERE NOT EXISTS (SELECT 1 FROM public.usuario_empresa ue
                             WHERE ue.usuario_id = u.id)' INTO v_sin_membresia;
  EXECUTE 'SELECT count(*) FROM public.cliente c
           WHERE c.usuario_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM public.usuario_empresa ue
                             WHERE ue.empresa_id = c.empresa_id
                               AND ue.usuario_id = c.usuario_id)' INTO v_clientes;
  EXECUTE 'SELECT count(*) FROM public.empleado e
           WHERE NOT EXISTS (SELECT 1 FROM public.usuario_empresa ue
                             WHERE ue.empresa_id = e.empresa_id
                               AND ue.usuario_id = e.usuario_id)' INTO v_empleados;
  RAISE NOTICE 'identidad | usuarios_sin_membresia=%', v_sin_membresia;
  RAISE NOTICE 'identidad | clientes_sin_membresia=%', v_clientes;
  RAISE NOTICE 'identidad | empleados_sin_membresia=%', v_empleados;
END;
$post_identidad$;

-- 7) Vigencia de suscripciones por empresa.
SELECT 'suscripciones' AS control, e.slug AS detalle,
       format('plan=%s estado=%s vence=%s vigente=%s',
              p.codigo, s.estado, s.fecha_vencimiento,
              (s.estado = 'ACTIVA' AND s.fecha_vencimiento >= CURRENT_DATE)) AS valor
FROM public.empresa e
LEFT JOIN public.suscripcion s ON s.empresa_id = e.id AND s.estado = 'ACTIVA'
LEFT JOIN public.plan_saas p ON p.id = s.plan_saas_id
ORDER BY e.slug;

-- 8) Triggers heredados conservados.
SELECT 'triggers' AS control, c.relname AS detalle,
       format('habilitados=%s deshabilitados=%s',
              count(*) FILTER (WHERE t.tgenabled <> 'D'),
              count(*) FILTER (WHERE t.tgenabled = 'D')) AS valor
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
GROUP BY c.relname
ORDER BY c.relname;
