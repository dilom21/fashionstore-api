-- Recovered read-only from supabase_migrations.schema_migrations
-- Project ref: zyitwlxfkphthlvlmuej
-- Version: 20260903183642; name: add_fashionstore_procedures; stored statements: 1
-- Evidence only: DO NOT EXECUTE

SET search_path TO public;

CREATE OR REPLACE PROCEDURE sp_crear_reserva(
    IN p_cliente_id BIGINT,
    IN p_sucursal_id BIGINT,
    IN p_fecha_atencion TIMESTAMPTZ,
    IN p_observacion TEXT,
    IN p_items JSONB,
    IN p_usuario_id BIGINT,
    INOUT p_reserva_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_item RECORD;
    v_inventario RECORD;
    v_stock_disponible INTEGER;
BEGIN
    IF p_fecha_atencion <= CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION 'La fecha de atención debe ser posterior a la fecha actual';
    END IF;

    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'La reserva debe contener al menos una prenda';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM cliente WHERE id = p_cliente_id AND estado = TRUE) THEN
        RAISE EXCEPTION 'El cliente % no existe o está inactivo', p_cliente_id;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sucursal WHERE id = p_sucursal_id AND estado = TRUE) THEN
        RAISE EXCEPTION 'La sucursal % no existe o está inactiva', p_sucursal_id;
    END IF;

    INSERT INTO reserva (cliente_id, sucursal_id, fecha_reserva, fecha_atencion, estado, observacion)
    VALUES (p_cliente_id, p_sucursal_id, CURRENT_TIMESTAMP, p_fecha_atencion, 'PENDIENTE', p_observacion)
    RETURNING id INTO p_reserva_id;

    FOR v_item IN
        SELECT inventario_id, SUM(cantidad)::INTEGER AS cantidad
        FROM jsonb_to_recordset(p_items) AS x(inventario_id BIGINT, cantidad INTEGER)
        GROUP BY inventario_id
        ORDER BY inventario_id
    LOOP
        IF v_item.cantidad IS NULL OR v_item.cantidad <= 0 THEN
            RAISE EXCEPTION 'La cantidad de una reserva debe ser mayor que cero';
        END IF;

        SELECT i.id, i.sucursal_id, i.stock_actual, i.stock_reservado, i.variante_producto_id, i.temporada_id
        INTO v_inventario
        FROM inventario i
        WHERE i.id = v_item.inventario_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'No existe el inventario %', v_item.inventario_id;
        END IF;

        IF v_inventario.sucursal_id <> p_sucursal_id THEN
            RAISE EXCEPTION 'El inventario % no pertenece a la sucursal de la reserva', v_item.inventario_id;
        END IF;

        v_stock_disponible := v_inventario.stock_actual - v_inventario.stock_reservado;

        IF v_stock_disponible < v_item.cantidad THEN
            RAISE EXCEPTION 'Stock insuficiente para inventario %. Disponible: %, solicitado: %',
                v_item.inventario_id, v_stock_disponible, v_item.cantidad;
        END IF;

        UPDATE inventario
        SET stock_reservado = stock_reservado + v_item.cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = v_item.inventario_id;

        INSERT INTO detalle_reserva (reserva_id, inventario_id, cantidad)
        VALUES (p_reserva_id, v_item.inventario_id, v_item.cantidad);

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_item.inventario_id, p_usuario_id, 'RESERVA', v_item.cantidad,
            CURRENT_TIMESTAMP, 'Stock reservado para cliente', 'RESERVA', p_reserva_id
        );
    END LOOP;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_cancelar_reserva(
    IN p_reserva_id BIGINT,
    IN p_usuario_id BIGINT,
    IN p_observacion TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_reserva RECORD;
    v_detalle RECORD;
BEGIN
    SELECT * INTO v_reserva
    FROM reserva
    WHERE id = p_reserva_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'La reserva % no existe', p_reserva_id;
    END IF;

    IF v_reserva.estado NOT IN ('PENDIENTE', 'CONFIRMADA') THEN
        RAISE EXCEPTION 'La reserva % no puede cancelarse porque se encuentra en estado %', p_reserva_id, v_reserva.estado;
    END IF;

    FOR v_detalle IN
        SELECT dr.inventario_id, dr.cantidad
        FROM detalle_reserva dr
        WHERE dr.reserva_id = p_reserva_id
        ORDER BY dr.inventario_id
    LOOP
        PERFORM 1 FROM inventario WHERE id = v_detalle.inventario_id FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario % de la reserva no encontrado', v_detalle.inventario_id;
        END IF;

        IF (SELECT stock_reservado FROM inventario WHERE id = v_detalle.inventario_id) < v_detalle.cantidad THEN
            RAISE EXCEPTION 'Inconsistencia en stock reservado del inventario %', v_detalle.inventario_id;
        END IF;

        UPDATE inventario
        SET stock_reservado = stock_reservado - v_detalle.cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = v_detalle.inventario_id;

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_detalle.inventario_id, p_usuario_id, 'LIBERACION_RESERVA',
            v_detalle.cantidad, CURRENT_TIMESTAMP,
            COALESCE(p_observacion, 'Reserva cancelada'), 'RESERVA', p_reserva_id
        );
    END LOOP;

    UPDATE reserva
    SET estado = 'CANCELADA',
        observacion = CASE
            WHEN p_observacion IS NULL THEN observacion
            WHEN observacion IS NULL THEN p_observacion
            ELSE observacion || ' | ' || p_observacion
        END
    WHERE id = p_reserva_id;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_recalcular_total_venta(
    IN p_venta_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_total NUMERIC(12,2);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM venta WHERE id = p_venta_id) THEN
        RAISE EXCEPTION 'La venta % no existe', p_venta_id;
    END IF;

    SELECT COALESCE(SUM(cantidad * precio_unitario), 0)
    INTO v_total
    FROM detalle_venta
    WHERE venta_id = p_venta_id;

    UPDATE venta SET total = v_total WHERE id = p_venta_id;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_confirmar_venta(
    IN p_venta_id BIGINT,
    IN p_usuario_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_venta RECORD;
    v_detalle RECORD;
    v_inventario RECORD;
    v_cantidad_reservada INTEGER;
    v_consumo_reserva INTEGER;
    v_cantidad_directa INTEGER;
    v_reserva_detalle RECORD;
    v_vendido INTEGER;
    v_consumido INTEGER;
    v_liberar INTEGER;
    v_total NUMERIC(12,2);
BEGIN
    SELECT * INTO v_venta
    FROM venta
    WHERE id = p_venta_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'La venta % no existe', p_venta_id;
    END IF;

    IF v_venta.estado NOT IN ('PENDIENTE', 'PAGADA') THEN
        RAISE EXCEPTION 'La venta % no puede confirmarse porque está en estado %', p_venta_id, v_venta.estado;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM detalle_venta WHERE venta_id = p_venta_id) THEN
        RAISE EXCEPTION 'La venta % no contiene productos', p_venta_id;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pago WHERE venta_id = p_venta_id AND estado = 'APROBADO') THEN
        RAISE EXCEPTION 'La venta % no posee un pago aprobado', p_venta_id;
    END IF;

    IF v_venta.reserva_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM reserva
            WHERE id = v_venta.reserva_id
              AND estado IN ('PENDIENTE', 'CONFIRMADA')
              AND sucursal_id = v_venta.sucursal_id
        ) THEN
            RAISE EXCEPTION 'La reserva asociada a la venta no es válida';
        END IF;
    END IF;

    FOR v_detalle IN
        SELECT * FROM detalle_venta
        WHERE venta_id = p_venta_id
        ORDER BY inventario_id
    LOOP
        SELECT * INTO v_inventario
        FROM inventario
        WHERE id = v_detalle.inventario_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario % no encontrado', v_detalle.inventario_id;
        END IF;

        IF v_inventario.sucursal_id <> v_venta.sucursal_id THEN
            RAISE EXCEPTION 'El producto del inventario % pertenece a otra sucursal', v_detalle.inventario_id;
        END IF;

        v_cantidad_reservada := 0;

        IF v_venta.reserva_id IS NOT NULL THEN
            SELECT COALESCE((
                SELECT cantidad
                FROM detalle_reserva
                WHERE reserva_id = v_venta.reserva_id
                  AND inventario_id = v_detalle.inventario_id
            ), 0)
            INTO v_cantidad_reservada;
        END IF;

        v_consumo_reserva := LEAST(v_cantidad_reservada, v_detalle.cantidad);
        v_cantidad_directa := v_detalle.cantidad - v_consumo_reserva;

        IF v_inventario.stock_actual < v_detalle.cantidad THEN
            RAISE EXCEPTION 'Stock físico insuficiente en inventario %. Disponible físico: %, solicitado: %',
                v_detalle.inventario_id, v_inventario.stock_actual, v_detalle.cantidad;
        END IF;

        IF (v_inventario.stock_actual - v_inventario.stock_reservado) < v_cantidad_directa THEN
            RAISE EXCEPTION 'Stock disponible insuficiente para inventario %. Cantidad adicional requerida: %',
                v_detalle.inventario_id, v_cantidad_directa;
        END IF;

        IF v_inventario.stock_reservado < v_consumo_reserva THEN
            RAISE EXCEPTION 'Existe inconsistencia en el stock reservado del inventario %', v_detalle.inventario_id;
        END IF;

        UPDATE inventario
        SET stock_actual = stock_actual - v_detalle.cantidad,
            stock_reservado = stock_reservado - v_consumo_reserva,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = v_detalle.inventario_id;

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_detalle.inventario_id, p_usuario_id, 'SALIDA_VENTA',
            v_detalle.cantidad, CURRENT_TIMESTAMP,
            'Salida de inventario por venta', 'VENTA', p_venta_id
        );
    END LOOP;

    IF v_venta.reserva_id IS NOT NULL THEN
        FOR v_reserva_detalle IN
            SELECT * FROM detalle_reserva
            WHERE reserva_id = v_venta.reserva_id
            ORDER BY inventario_id
        LOOP
            SELECT COALESCE(SUM(cantidad), 0)
            INTO v_vendido
            FROM detalle_venta
            WHERE venta_id = p_venta_id
              AND inventario_id = v_reserva_detalle.inventario_id;

            v_consumido := LEAST(v_reserva_detalle.cantidad, v_vendido);
            v_liberar := v_reserva_detalle.cantidad - v_consumido;

            IF v_liberar > 0 THEN
                PERFORM 1
                FROM inventario
                WHERE id = v_reserva_detalle.inventario_id
                FOR UPDATE;

                IF (SELECT stock_reservado FROM inventario WHERE id = v_reserva_detalle.inventario_id) < v_liberar THEN
                    RAISE EXCEPTION 'Inconsistencia al liberar inventario reservado %', v_reserva_detalle.inventario_id;
                END IF;

                UPDATE inventario
                SET stock_reservado = stock_reservado - v_liberar,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE id = v_reserva_detalle.inventario_id;

                INSERT INTO movimiento_inventario (
                    inventario_id, usuario_id, tipo, cantidad, fecha_hora,
                    observaciones, referencia_tipo, referencia_id
                ) VALUES (
                    v_reserva_detalle.inventario_id, p_usuario_id,
                    'LIBERACION_RESERVA', v_liberar, CURRENT_TIMESTAMP,
                    'Prenda reservada no adquirida durante la venta',
                    'RESERVA', v_venta.reserva_id
                );
            END IF;
        END LOOP;

        UPDATE reserva SET estado = 'ATENDIDA' WHERE id = v_venta.reserva_id;
    END IF;

    SELECT COALESCE(SUM(cantidad * precio_unitario), 0)
    INTO v_total
    FROM detalle_venta
    WHERE venta_id = p_venta_id;

    UPDATE venta
    SET total = v_total,
        estado = 'COMPLETADA'
    WHERE id = p_venta_id;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_recibir_orden_compra(
    IN p_orden_compra_id BIGINT,
    IN p_usuario_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_orden RECORD;
    v_detalle RECORD;
    v_inventario_id BIGINT;
BEGIN
    SELECT * INTO v_orden
    FROM orden_compra
    WHERE id = p_orden_compra_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'La orden de compra % no existe', p_orden_compra_id;
    END IF;

    IF v_orden.estado NOT IN ('ENVIADA', 'PARCIAL') THEN
        RAISE EXCEPTION 'La orden de compra % no puede recibirse porque está en estado %', p_orden_compra_id, v_orden.estado;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM detalle_orden_compra WHERE orden_compra_id = p_orden_compra_id) THEN
        RAISE EXCEPTION 'La orden de compra % no contiene productos', p_orden_compra_id;
    END IF;

    FOR v_detalle IN
        SELECT * FROM detalle_orden_compra
        WHERE orden_compra_id = p_orden_compra_id
        ORDER BY variante_producto_id, temporada_id
    LOOP
        INSERT INTO inventario (
            sucursal_id, variante_producto_id, temporada_id,
            stock_actual, stock_reservado, fecha_actualizacion
        ) VALUES (
            v_orden.sucursal_id, v_detalle.variante_producto_id,
            v_detalle.temporada_id, v_detalle.cantidad, 0,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (sucursal_id, variante_producto_id, temporada_id)
        DO UPDATE SET
            stock_actual = inventario.stock_actual + EXCLUDED.stock_actual,
            fecha_actualizacion = CURRENT_TIMESTAMP
        RETURNING id INTO v_inventario_id;

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_inventario_id, p_usuario_id, 'ENTRADA_COMPRA',
            v_detalle.cantidad, CURRENT_TIMESTAMP,
            'Recepción de orden de compra', 'ORDEN_COMPRA', p_orden_compra_id
        );
    END LOOP;

    UPDATE orden_compra
    SET estado = 'RECIBIDA',
        fecha_recepcion = CURRENT_TIMESTAMP
    WHERE id = p_orden_compra_id;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_registrar_devolucion(
    IN p_devolucion_id BIGINT,
    IN p_usuario_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_devolucion RECORD;
    v_detalle RECORD;
    v_cantidad_vendida INTEGER;
    v_devuelto_previamente INTEGER;
    v_inventario_id BIGINT;
BEGIN
    SELECT * INTO v_devolucion
    FROM devolucion
    WHERE id = p_devolucion_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'La devolución % no existe', p_devolucion_id;
    END IF;

    IF v_devolucion.estado <> 'APROBADA' THEN
        RAISE EXCEPTION 'La devolución % debe estar APROBADA antes de procesarse. Estado actual: %',
            p_devolucion_id, v_devolucion.estado;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM detalle_devolucion WHERE devolucion_id = p_devolucion_id) THEN
        RAISE EXCEPTION 'La devolución % no contiene productos', p_devolucion_id;
    END IF;

    FOR v_detalle IN
        SELECT * FROM detalle_devolucion
        WHERE devolucion_id = p_devolucion_id
        ORDER BY detalle_venta_id
    LOOP
        SELECT dv.cantidad, dv.inventario_id
        INTO v_cantidad_vendida, v_inventario_id
        FROM detalle_venta dv
        WHERE dv.id = v_detalle.detalle_venta_id
          AND dv.venta_id = v_devolucion.venta_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'El detalle de venta % no pertenece a la venta de esta devolución',
                v_detalle.detalle_venta_id;
        END IF;

        SELECT COALESCE(SUM(dd.cantidad), 0)
        INTO v_devuelto_previamente
        FROM detalle_devolucion dd
        INNER JOIN devolucion d ON d.id = dd.devolucion_id
        WHERE dd.detalle_venta_id = v_detalle.detalle_venta_id
          AND d.id <> p_devolucion_id
          AND d.estado IN ('APROBADA', 'COMPLETADA');

        IF (v_devuelto_previamente + v_detalle.cantidad) > v_cantidad_vendida THEN
            RAISE EXCEPTION 'No se pueden devolver % unidades. Vendidas: %, ya devueltas: %',
                v_detalle.cantidad, v_cantidad_vendida, v_devuelto_previamente;
        END IF;

        PERFORM 1 FROM inventario WHERE id = v_inventario_id FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario % no encontrado', v_inventario_id;
        END IF;

        UPDATE inventario
        SET stock_actual = stock_actual + v_detalle.cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = v_inventario_id;

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_inventario_id, p_usuario_id, 'DEVOLUCION',
            v_detalle.cantidad, CURRENT_TIMESTAMP,
            COALESCE(v_detalle.motivo, 'Devolución de producto'),
            'DEVOLUCION', p_devolucion_id
        );
    END LOOP;

    UPDATE devolucion SET estado = 'COMPLETADA' WHERE id = p_devolucion_id;
END;
$$;

CREATE OR REPLACE PROCEDURE sp_ajustar_inventario(
    IN p_inventario_id BIGINT,
    IN p_tipo VARCHAR,
    IN p_cantidad INTEGER,
    IN p_usuario_id BIGINT,
    IN p_observacion TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_inventario RECORD;
    v_disponible INTEGER;
BEGIN
    IF p_tipo NOT IN ('AJUSTE_ENTRADA', 'AJUSTE_SALIDA') THEN
        RAISE EXCEPTION 'Tipo de ajuste no válido: %', p_tipo;
    END IF;

    IF p_cantidad <= 0 THEN
        RAISE EXCEPTION 'La cantidad debe ser mayor que cero';
    END IF;

    IF p_observacion IS NULL OR LENGTH(TRIM(p_observacion)) = 0 THEN
        RAISE EXCEPTION 'Todo ajuste de inventario debe incluir una observación';
    END IF;

    SELECT * INTO v_inventario
    FROM inventario
    WHERE id = p_inventario_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Inventario % no encontrado', p_inventario_id;
    END IF;

    IF p_tipo = 'AJUSTE_ENTRADA' THEN
        UPDATE inventario
        SET stock_actual = stock_actual + p_cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = p_inventario_id;
    ELSE
        v_disponible := v_inventario.stock_actual - v_inventario.stock_reservado;

        IF v_disponible < p_cantidad THEN
            RAISE EXCEPTION 'No se puede retirar %. Stock disponible: %. Existen % unidades reservadas.',
                p_cantidad, v_disponible, v_inventario.stock_reservado;
        END IF;

        UPDATE inventario
        SET stock_actual = stock_actual - p_cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = p_inventario_id;
    END IF;

    INSERT INTO movimiento_inventario (
        inventario_id, usuario_id, tipo, cantidad, fecha_hora,
        observaciones, referencia_tipo, referencia_id
    ) VALUES (
        p_inventario_id, p_usuario_id, p_tipo, p_cantidad,
        CURRENT_TIMESTAMP, p_observacion, 'AJUSTE', NULL
    );
END;
$$;

