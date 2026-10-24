-- BLOQUE 3 / PASO 5: AUDITORIA SOLO LECTURA EN POSTGRESQL LOCAL
-- Solo fashionstore_restore_20261023_193554_594, 127.0.0.1
-- No se ejecutan SP de pagos/ventas/stock, ni INSERT/UPDATE/DELETE.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $auditoria$
DECLARE
    v_tabla TEXT;
    v_cantidad BIGINT;
    v_tenantes BIGINT;
    v_esperado RECORD;
    v_tablas TEXT[] := ARRAY[
        'carrito','reserva','venta','pago','orden_compra','devolucion'
    ];
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
        OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino no autorizado: % / %',
            current_database(), host(inet_server_addr());
    END IF;
    IF (SELECT count(*) FROM public.empresa) <> 5
       OR (SELECT count(*) FROM public.venta) <> 40
       OR (SELECT count(*) FROM public.pago) <> 30
       OR (SELECT count(*) FROM public.reserva) <> 11
       OR (SELECT count(*) FROM public.bitacora) <> 844 THEN
        RAISE EXCEPTION 'Conteos de seguridad distintos del Bloque 2';
    END IF;
    SELECT count(*) INTO v_cantidad
    FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname='public' AND c.contype='f'
      AND c.convalidated AND c.conname LIKE 'fk_tenant_%';
    IF v_cantidad <> 48 THEN
        RAISE EXCEPTION 'Faltan FK compuestas: % de 48',v_cantidad;
    END IF;

    -- Cada uno de los seis dominios tiene empresa_id y apunta a empresa real.
    FOREACH v_tabla IN ARRAY v_tablas LOOP
        EXECUTE format(
            'SELECT count(*) FROM public.%I x ' ||
            'LEFT JOIN public.empresa e ON x.empresa_id=e.id ' ||
            'WHERE x.empresa_id IS NULL OR e.id IS NULL', v_tabla
        ) INTO v_cantidad;
        IF v_cantidad <> 0 THEN
            RAISE EXCEPTION 'Tenant invalido detectado en %',v_tabla;
        END IF;
        RAISE NOTICE 'PROPIEDAD OK: %', v_tabla;
    END LOOP;

    -- Este recorrido reproduce los padres usados por el repositorio admin.
    -- FK compuestas de PG impedirian estas inconsistencias, se auditan
    -- ademas los datos historicos concretos.
    IF EXISTS(
        SELECT 1 FROM public.carrito a
        JOIN public.cliente c ON c.id=a.cliente_id
        JOIN public.sucursal s ON s.id=a.sucursal_id
        WHERE a.empresa_id<>c.empresa_id OR a.empresa_id<>s.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.reserva r
        JOIN public.cliente c ON c.id=r.cliente_id
        JOIN public.sucursal s ON s.id=r.sucursal_id
        WHERE r.empresa_id<>c.empresa_id OR r.empresa_id<>s.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.venta v
        JOIN public.sucursal s ON s.id=v.sucursal_id
        WHERE v.empresa_id<>s.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.pago p
        JOIN public.venta v ON v.id=p.venta_id
        WHERE p.empresa_id<>v.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.orden_compra o
        JOIN public.proveedor p ON p.id=o.proveedor_id
        JOIN public.sucursal s ON s.id=o.sucursal_id
        WHERE o.empresa_id<>p.empresa_id OR o.empresa_id<>s.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.devolucion d
        JOIN public.venta v ON v.id=d.venta_id
        WHERE d.empresa_id<>v.empresa_id
    ) THEN
        RAISE EXCEPTION 'Hay relaciones comerciales cruzadas';
    END IF;
    RAISE NOTICE 'RELACIONES OK: seis dominios sin cruces empresariales';

    -- Las demos no incluyen ventas, pagos o reservas reales.
    FOR v_esperado IN SELECT e.id, e.slug FROM public.empresa e
        WHERE e.slug <> 'vanter-men'
    LOOP
        IF EXISTS(SELECT 1 FROM public.venta WHERE empresa_id=v_esperado.id)
          OR EXISTS(SELECT 1 FROM public.pago WHERE empresa_id=v_esperado.id)
          OR EXISTS(SELECT 1 FROM public.reserva WHERE empresa_id=v_esperado.id) THEN
            RAISE EXCEPTION 'Datos historicos filtrados a demo %',v_esperado.slug;
        END IF;
    END LOOP;
    RAISE NOTICE 'DATOS ORIGINALES OK: Vanter Men conserva ventas, pagos y reservas';
END;
$auditoria$;

SELECT e.slug,
   (SELECT count(*) FROM public.carrito c WHERE c.empresa_id=e.id) AS carritos,
   (SELECT count(*) FROM public.reserva r WHERE r.empresa_id=e.id) AS reservas,
   (SELECT count(*) FROM public.venta v WHERE v.empresa_id=e.id) AS ventas,
   (SELECT count(*) FROM public.pago p WHERE p.empresa_id=e.id) AS pagos,
   (SELECT count(*) FROM public.orden_compra o WHERE o.empresa_id=e.id) AS compras,
   (SELECT count(*) FROM public.devolucion d WHERE d.empresa_id=e.id) AS devoluciones
FROM public.empresa e
ORDER BY e.slug;

ROLLBACK;
