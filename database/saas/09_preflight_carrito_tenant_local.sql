-- Bloque 3/Paso 5B: PREFLIGHT de carrito y stock; SOLO LECTURA.
-- No hay INSERT, UPDATE, DELETE, funciones mutadoras ni llamadas a SP.
-- En cualquier error se aborta la transaccion READ ONLY.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='30s';

DO $preflight$
DECLARE
    v_num BIGINT;
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino no autorizado: % / %',
          current_database(), host(inet_server_addr());
    END IF;

    IF (SELECT count(*) FROM public.empresa) <> 5
       OR (SELECT count(*) FROM public.carrito) <> 48
       OR (SELECT count(*) FROM public.inventario) <> 274
       OR (SELECT count(*) FROM public.venta) <> 40
       OR (SELECT count(*) FROM public.pago) <> 30 THEN
        RAISE EXCEPTION 'La copia local no coincide con el inventario auditado';
    END IF;

    IF NOT EXISTS (
       SELECT 1 FROM pg_indexes
       WHERE schemaname='public' AND tablename='carrito'
       AND indexname='uq_carrito_activo_cliente_sucursal'
    ) THEN
        RAISE EXCEPTION 'Falta indice unico parcial de carrito activo';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='detalle_carrito'
          AND t.tgname='trg_actualizar_actividad_carrito'
          AND t.tgenabled <> 'D'
    ) THEN
        RAISE EXCEPTION 'Falta trigger de actividad de carrito';
    END IF;

    SELECT count(*) INTO v_num
    FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname='public' AND c.contype='f'
      AND c.convalidated AND c.conname LIKE 'fk_tenant_%';
    IF v_num<>48 THEN
        RAISE EXCEPTION 'FK tenant esperadas 48; reales %',v_num;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.carrito ca
        JOIN public.cliente cl ON cl.id=ca.cliente_id
        JOIN public.sucursal s ON s.id=ca.sucursal_id
        WHERE ca.empresa_id<>cl.empresa_id OR ca.empresa_id<>s.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.detalle_carrito d
        JOIN public.carrito ca ON ca.id=d.carrito_id
        JOIN public.inventario i ON i.id=d.inventario_id
        WHERE d.empresa_id<>ca.empresa_id
           OR d.empresa_id<>i.empresa_id
           OR ca.sucursal_id<>i.sucursal_id
    ) OR EXISTS (
        SELECT 1 FROM public.inventario i
        JOIN public.sucursal s ON s.id=i.sucursal_id
        JOIN public.variante_producto v ON v.id=i.variante_producto_id
        JOIN public.temporada t ON t.id=i.temporada_id
        WHERE i.empresa_id<>s.empresa_id
           OR i.empresa_id<>v.empresa_id
           OR i.empresa_id<>t.empresa_id
    ) THEN
        RAISE EXCEPTION 'Referencias cruzadas de carrito o inventario';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.inventario i
        WHERE i.stock_actual < 0
          OR i.stock_reservado < 0
          OR i.stock_actual < i.stock_reservado
    ) THEN
        RAISE EXCEPTION 'Stock reservado mayor que stock actual en inventario';
    END IF;

    RAISE NOTICE 'PREFLIGHT OK: indice de carrito, trigger actividad y 48 FK tenant';
    RAISE NOTICE 'PREFLIGHT OK: carritos e inventarios sin cruces de empresa';
    RAISE NOTICE 'PREFLIGHT OK: stock actual y reservado consistentes';
END;
$preflight$;

SELECT e.slug,
       (SELECT count(*) FROM public.carrito c WHERE c.empresa_id=e.id) AS carritos,
       (SELECT count(*) FROM public.inventario i WHERE i.empresa_id=e.id) AS inventarios,
       (SELECT count(*) FROM public.detalle_carrito d WHERE d.empresa_id=e.id) AS detalles_carrito
FROM public.empresa e
ORDER BY e.slug;

ROLLBACK;
