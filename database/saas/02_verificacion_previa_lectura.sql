-- FASHIONSTORE — SEGUNDO PARCIAL — BLOQUE 1 / PASO 4
-- SOLO LECTURA: no INSERT, UPDATE, DELETE, CREATE ni ALTER.
-- Ejecutar manualmente en Supabase SQL Editor cuando se desee revisar.
-- NO contiene contrasenas, filas personales ni datos identificables.

-- Q1. Inventario del esquema antes de la migracion (esperado: 41 tablas).
SELECT COUNT(*) AS tablas_publicas
FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE';

-- Q2. Actualmente ninguna tabla tiene empresa_id (esperado: 0).
SELECT COUNT(*) AS columnas_empresa_id
FROM information_schema.columns
WHERE table_schema='public' AND column_name='empresa_id';

-- Q3. Verificar que aun no se ejecutaron las 5 tablas del Paso 3
-- (esperado: 0 hasta aprobar la migracion).
SELECT COUNT(*) AS tablas_saas_creadas
FROM information_schema.tables
WHERE table_schema='public' AND table_name IN
    ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion');

-- Q4. Baseline de registros existentes a preservar.
SELECT 'usuario' AS tabla, COUNT(*) AS total FROM public.usuario
UNION ALL SELECT 'cliente', COUNT(*) FROM public.cliente
UNION ALL SELECT 'empleado', COUNT(*) FROM public.empleado
UNION ALL SELECT 'sucursal', COUNT(*) FROM public.sucursal
UNION ALL SELECT 'producto', COUNT(*) FROM public.producto
UNION ALL SELECT 'variante_producto', COUNT(*) FROM public.variante_producto
UNION ALL SELECT 'inventario', COUNT(*) FROM public.inventario
UNION ALL SELECT 'carrito', COUNT(*) FROM public.carrito
UNION ALL SELECT 'reserva', COUNT(*) FROM public.reserva
UNION ALL SELECT 'venta', COUNT(*) FROM public.venta
UNION ALL SELECT 'pago', COUNT(*) FROM public.pago
UNION ALL SELECT 'orden_compra', COUNT(*) FROM public.orden_compra
UNION ALL SELECT 'bitacora', COUNT(*) FROM public.bitacora
UNION ALL SELECT 'configuracion_vestidor_ar', COUNT(*) FROM public.configuracion_vestidor_ar
UNION ALL SELECT 'sesion_vestidor_ar', COUNT(*) FROM public.sesion_vestidor_ar
UNION ALL SELECT 'prueba_vestidor_ar', COUNT(*) FROM public.prueba_vestidor_ar
ORDER BY tabla;

-- Q5. Perfil historico de membresias; muestra solo conteos.
SELECT r.nombre AS rol, COUNT(u.id) AS cantidad_usuarios
FROM public.rol AS r
LEFT JOIN public.usuario AS u ON u.rol_id = r.id
GROUP BY r.nombre
ORDER BY r.nombre;

-- Q6. Perfiles sin usuario, clientes sin usuario y usuarios sin perfil.
SELECT
    (SELECT COUNT(*) FROM public.empleado e LEFT JOIN public.usuario u
     ON u.id=e.usuario_id WHERE u.id IS NULL) AS empleados_sin_usuario,
    (SELECT COUNT(*) FROM public.cliente WHERE usuario_id IS NULL)
      AS clientes_sin_usuario,
    (SELECT COUNT(*) FROM public.usuario u WHERE NOT EXISTS
      (SELECT 1 FROM public.empleado e WHERE e.usuario_id=u.id)
      AND NOT EXISTS (SELECT 1 FROM public.cliente c WHERE c.usuario_id=u.id))
      AS usuarios_sin_perfil;

-- Q7. Estados historicos: importante para triggers de bloqueo.
SELECT 'venta' AS tabla, estado, COUNT(*) AS total
FROM public.venta GROUP BY estado
UNION ALL
SELECT 'orden_compra', estado, COUNT(*)
FROM public.orden_compra GROUP BY estado
ORDER BY tabla, estado;

-- Q8. Revisar seguridad actual RLS.
SELECT c.relname AS tabla, c.relrowsecurity AS rls_activo
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN ('configuracion_vestidor_ar','sesion_vestidor_ar',
                    'prueba_vestidor_ar')
ORDER BY c.relname;

-- Q9. Verificacion de relaciones historicas existentes.
SELECT 'ventas_sin_sucursal' AS control, COUNT(*) AS errores
FROM public.venta v LEFT JOIN public.sucursal s ON s.id=v.sucursal_id
WHERE s.id IS NULL
UNION ALL SELECT 'inventario_sin_sucursal', COUNT(*)
FROM public.inventario i LEFT JOIN public.sucursal s ON s.id=i.sucursal_id
WHERE s.id IS NULL
UNION ALL SELECT 'inventario_sin_variante', COUNT(*)
FROM public.inventario i LEFT JOIN public.variante_producto vp
  ON vp.id=i.variante_producto_id WHERE vp.id IS NULL
UNION ALL SELECT 'reservas_sin_sucursal', COUNT(*)
FROM public.reserva r LEFT JOIN public.sucursal s ON s.id=r.sucursal_id
WHERE s.id IS NULL;

-- Q10. Observar posibles cambios de la estructura en el tiempo.
SELECT
    (SELECT COUNT(*) FROM pg_constraint c JOIN pg_namespace n
     ON n.oid=c.connamespace WHERE n.nspname='public' AND c.contype='f')
      AS claves_foraneas,
    (SELECT COUNT(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND NOT t.tgisinternal) AS triggers,
    (SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='public') AS rutinas;
