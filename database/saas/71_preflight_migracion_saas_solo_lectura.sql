-- FASHIONSTORE / SAAS / PREFLIGHT DE MIGRACION - SOLO LECTURA
-- Ejecutar contra la instancia destino ANTES de aplicar
-- supabase/migrations/20261030000100..000300.
--
-- Este archivo NO modifica datos ni estructura: solo consulta catalogos y
-- cuenta filas. Guarde la salida completa como evidencia del dia de migracion.
--
-- Lectura sugerida:
--   psql "$DESTINO" -v ON_ERROR_STOP=1 -f database/saas/71_preflight_migracion_saas_solo_lectura.sql
--
-- Criterio: si algun control marca RIESGO, detener la migracion y resolverlo.

\pset pager off

-- 1) Identidad efectiva de la conexion (nunca pegar la salida con secretos).
SELECT 'conexion' AS control,
       current_database() AS detalle,
       format('usuario=%s version=%s encoding=%s',
              current_user, current_setting('server_version'),
              current_setting('server_encoding')) AS valor;

SELECT 'privilegios_sesion' AS control,
       'rol actual: superuser / bypassrls' AS detalle,
       format('superuser=%s bypassrls=%s', rolsuper, rolbypassrls) AS valor
FROM pg_roles WHERE rolname = current_user;

-- 2) Tablas del nucleo SaaS ya presentes (re-ejecucion parcial).
SELECT 'tabla_saas' AS control, t.nombre AS detalle,
       CASE WHEN to_regclass('public.' || t.nombre) IS NULL
            THEN 'AUSENTE' ELSE 'PRESENTE' END AS valor
FROM (VALUES ('empresa'), ('plan_saas'), ('suscripcion'),
             ('usuario_empresa'), ('empresa_configuracion')) AS t(nombre);

-- 3) Tenencia por tabla empresarial: columna, filas y filas sin empresa.
SELECT 'tenencia' AS control, s.tabla AS detalle,
       format('empresa_id=%s filas=%s sin_empresa=%s',
              CASE WHEN s.tiene THEN 'SI' ELSE 'NO' END,
              s.filas, s.sin_empresa) AS valor
FROM (
  SELECT 'bitacora' AS tabla, count(*) AS filas,
         count(*) FILTER (WHERE to_jsonb(b) ? 'empresa_id'
                          AND (to_jsonb(b)->>'empresa_id') IS NULL) AS sin_empresa,
         EXISTS (SELECT 1 FROM information_schema.columns c
                 WHERE c.table_schema='public' AND c.table_name='bitacora'
                   AND c.column_name='empresa_id') AS tiene
  FROM public.bitacora b
  UNION ALL SELECT 'sucursal', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='sucursal' AND c.column_name='empresa_id')
  FROM public.sucursal x
  UNION ALL SELECT 'empleado', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='empleado' AND c.column_name='empresa_id')
  FROM public.empleado x
  UNION ALL SELECT 'cliente', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='cliente' AND c.column_name='empresa_id')
  FROM public.cliente x
  UNION ALL SELECT 'categoria', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='categoria' AND c.column_name='empresa_id')
  FROM public.categoria x
  UNION ALL SELECT 'producto', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='producto' AND c.column_name='empresa_id')
  FROM public.producto x
  UNION ALL SELECT 'variante_producto', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='variante_producto' AND c.column_name='empresa_id')
  FROM public.variante_producto x
  UNION ALL SELECT 'recurso_producto', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='recurso_producto' AND c.column_name='empresa_id')
  FROM public.recurso_producto x
  UNION ALL SELECT 'temporada', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='temporada' AND c.column_name='empresa_id')
  FROM public.temporada x
  UNION ALL SELECT 'coleccion', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='coleccion' AND c.column_name='empresa_id')
  FROM public.coleccion x
  UNION ALL SELECT 'producto_coleccion', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='producto_coleccion' AND c.column_name='empresa_id')
  FROM public.producto_coleccion x
  UNION ALL SELECT 'promocion', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='promocion' AND c.column_name='empresa_id')
  FROM public.promocion x
  UNION ALL SELECT 'promocion_producto', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='promocion_producto' AND c.column_name='empresa_id')
  FROM public.promocion_producto x
  UNION ALL SELECT 'proveedor', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='proveedor' AND c.column_name='empresa_id')
  FROM public.proveedor x
  UNION ALL SELECT 'proveedor_producto', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='proveedor_producto' AND c.column_name='empresa_id')
  FROM public.proveedor_producto x
  UNION ALL SELECT 'orden_compra', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='orden_compra' AND c.column_name='empresa_id')
  FROM public.orden_compra x
  UNION ALL SELECT 'detalle_orden_compra', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='detalle_orden_compra' AND c.column_name='empresa_id')
  FROM public.detalle_orden_compra x
  UNION ALL SELECT 'inventario', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='inventario' AND c.column_name='empresa_id')
  FROM public.inventario x
  UNION ALL SELECT 'movimiento_inventario', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='movimiento_inventario' AND c.column_name='empresa_id')
  FROM public.movimiento_inventario x
  UNION ALL SELECT 'carrito', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='carrito' AND c.column_name='empresa_id')
  FROM public.carrito x
  UNION ALL SELECT 'detalle_carrito', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='detalle_carrito' AND c.column_name='empresa_id')
  FROM public.detalle_carrito x
  UNION ALL SELECT 'reserva', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='reserva' AND c.column_name='empresa_id')
  FROM public.reserva x
  UNION ALL SELECT 'detalle_reserva', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='detalle_reserva' AND c.column_name='empresa_id')
  FROM public.detalle_reserva x
  UNION ALL SELECT 'venta', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='venta' AND c.column_name='empresa_id')
  FROM public.venta x
  UNION ALL SELECT 'detalle_venta', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='detalle_venta' AND c.column_name='empresa_id')
  FROM public.detalle_venta x
  UNION ALL SELECT 'pago', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='pago' AND c.column_name='empresa_id')
  FROM public.pago x
  UNION ALL SELECT 'devolucion', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='devolucion' AND c.column_name='empresa_id')
  FROM public.devolucion x
  UNION ALL SELECT 'detalle_devolucion', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='detalle_devolucion' AND c.column_name='empresa_id')
  FROM public.detalle_devolucion x
  UNION ALL SELECT 'configuracion_vestidor_ar', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='configuracion_vestidor_ar' AND c.column_name='empresa_id')
  FROM public.configuracion_vestidor_ar x
  UNION ALL SELECT 'sesion_vestidor_ar', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='sesion_vestidor_ar' AND c.column_name='empresa_id')
  FROM public.sesion_vestidor_ar x
  UNION ALL SELECT 'prueba_vestidor_ar', count(*),
         count(*) FILTER (WHERE to_jsonb(x)->>'empresa_id' IS NULL),
         EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prueba_vestidor_ar' AND c.column_name='empresa_id')
  FROM public.prueba_vestidor_ar x
) AS s
ORDER BY s.tabla;

-- 4) Restricciones e indices SaaS ya existentes.
SELECT 'restricciones_saas' AS control, 'conteo' AS detalle,
       format('fk_tenant=%s uq_tenant=%s fk_empresa=%s',
              count(*) FILTER (WHERE conname LIKE 'fk_tenant_%'),
              count(*) FILTER (WHERE conname LIKE 'uq_tenant_%'),
              count(*) FILTER (WHERE conname LIKE 'fk_empresa_%')) AS valor
FROM pg_constraint;

-- 5) RLS: las cinco tablas SaaS y las heredadas.
SELECT 'rls' AS control, c.relname AS detalle,
       format('rls=%s force=%s', c.relrowsecurity, c.relforcerowsecurity) AS valor
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('empresa','plan_saas','suscripcion','usuario_empresa',
                    'empresa_configuracion')
ORDER BY c.relname;

-- 6) Duplicados que impedirian los UNIQUE por empresa (deben ser cero).
SELECT 'duplicados' AS control, d.detalle, d.valor
FROM (
  SELECT 'categoria.nombre' AS detalle,
         coalesce(string_agg(nombre || '=' || n, ', '), '0') AS valor
  FROM (SELECT nombre, count(*) n FROM public.categoria GROUP BY nombre HAVING count(*) > 1) t
  UNION ALL
  SELECT 'temporada.nombre', coalesce(string_agg(nombre || '=' || n, ', '), '0')
  FROM (SELECT nombre, count(*) n FROM public.temporada GROUP BY nombre HAVING count(*) > 1) t
  UNION ALL
  SELECT 'variante_producto.sku', coalesce(string_agg(sku || '=' || n, ', '), '0')
  FROM (SELECT sku, count(*) n FROM public.variante_producto GROUP BY sku HAVING count(*) > 1) t
  UNION ALL
  SELECT 'sucursal.ciudad+nombre', coalesce(string_agg(ciudad_id || ':' || nombre || '=' || n, ', '), '0')
  FROM (SELECT ciudad_id, nombre, count(*) n FROM public.sucursal GROUP BY ciudad_id, nombre HAVING count(*) > 1) t
  UNION ALL
  SELECT 'proveedor.nit', coalesce(string_agg(nit || '=' || n, ', '), '0')
  FROM (SELECT nit, count(*) n FROM public.proveedor WHERE nit IS NOT NULL GROUP BY nit HAVING count(*) > 1) t
  UNION ALL
  SELECT 'cliente.ci', coalesce(string_agg(ci || '=' || n, ', '), '0')
  FROM (SELECT ci, count(*) n FROM public.cliente WHERE ci IS NOT NULL GROUP BY ci HAVING count(*) > 1) t
  UNION ALL
  SELECT 'cliente.usuario', coalesce(string_agg(usuario_id || '=' || n, ', '), '0')
  FROM (SELECT usuario_id, count(*) n FROM public.cliente WHERE usuario_id IS NOT NULL GROUP BY usuario_id HAVING count(*) > 1) t
  UNION ALL
  SELECT 'empleado.ci', coalesce(string_agg(ci || '=' || n, ', '), '0')
  FROM (SELECT ci, count(*) n FROM public.empleado WHERE ci IS NOT NULL GROUP BY ci HAVING count(*) > 1) t
  UNION ALL
  SELECT 'empleado.usuario', coalesce(string_agg(usuario_id || '=' || n, ', '), '0')
  FROM (SELECT usuario_id, count(*) n FROM public.empleado WHERE usuario_id IS NOT NULL GROUP BY usuario_id HAVING count(*) > 1) t
) AS d;

-- 7) Triggers heredados: cantidad y estado (la migracion no debe alterarlos).
SELECT 'triggers' AS control,
       c.relname AS detalle,
       format('habilitados=%s deshabilitados=%s',
              count(*) FILTER (WHERE t.tgenabled <> 'D'),
              count(*) FILTER (WHERE t.tgenabled = 'D')) AS valor
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
GROUP BY c.relname
ORDER BY c.relname;

-- 8) Estado SaaS previo: empresas, planes y suscripciones.
--    Se consulta de forma dinamica porque en la primera migracion esas
--    tablas aun no existen.
DO $preflight_estado$
DECLARE
  v_texto TEXT;
BEGIN
  IF to_regclass('public.empresa') IS NULL THEN
    RAISE NOTICE 'estado_saas | pre-migracion | nucleo SaaS ausente: primera aplicacion';
    RETURN;
  END IF;
  EXECUTE 'SELECT format(''empresas=%s planes=%s membresias=%s suscripciones_activas=%s vencidas=%s'',
                  (SELECT count(*) FROM public.empresa),
                  (SELECT count(*) FROM public.plan_saas),
                  (SELECT count(*) FROM public.usuario_empresa),
                  (SELECT count(*) FROM public.suscripcion WHERE estado = ''ACTIVA''),
                  (SELECT count(*) FROM public.suscripcion
                    WHERE estado = ''ACTIVA'' AND fecha_vencimiento < CURRENT_DATE))'
    INTO v_texto;
  RAISE NOTICE 'estado_saas | %', v_texto;
END;
$preflight_estado$;

-- 9) Usuarios sin membresia: deben crearse o justificarse antes de migrar.
DO $preflight_membresias$
DECLARE
  v_total BIGINT;
BEGIN
  IF to_regclass('public.usuario_empresa') IS NULL THEN
    RAISE NOTICE 'usuarios_sin_membresia | no aplica | nucleo SaaS ausente';
    RETURN;
  END IF;
  EXECUTE 'SELECT count(*) FROM public.usuario u
           WHERE NOT EXISTS (SELECT 1 FROM public.usuario_empresa ue
                             WHERE ue.usuario_id = u.id)'
    INTO v_total;
  RAISE NOTICE 'usuarios_sin_membresia | usuarios=%', v_total;
END;
$preflight_membresias$;
