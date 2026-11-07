-- Recovered read-only from supabase_migrations.schema_migrations
-- Project ref: zyitwlxfkphthlvlmuej
-- Version: 20260903183744; name: add_fashionstore_triggers; stored statements: 1
-- Evidence only: DO NOT EXECUTE

SET search_path TO public;

CREATE OR REPLACE FUNCTION fn_normalizar_correo_usuario()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.correo IS NULL OR BTRIM(NEW.correo) = '' THEN
        RAISE EXCEPTION 'El correo del usuario no puede estar vacío';
    END IF;
    NEW.correo := LOWER(BTRIM(NEW.correo));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalizar_correo_usuario ON usuario;
CREATE TRIGGER trg_normalizar_correo_usuario
BEFORE INSERT OR UPDATE ON usuario
FOR EACH ROW
EXECUTE FUNCTION fn_normalizar_correo_usuario();

CREATE OR REPLACE FUNCTION fn_normalizar_sku()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.sku IS NULL OR BTRIM(NEW.sku) = '' THEN
        RAISE EXCEPTION 'El SKU de una variante no puede estar vacío';
    END IF;
    NEW.sku := UPPER(BTRIM(NEW.sku));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalizar_sku ON variante_producto;
CREATE TRIGGER trg_normalizar_sku
BEFORE INSERT OR UPDATE ON variante_producto
FOR EACH ROW
EXECUTE FUNCTION fn_normalizar_sku();

CREATE OR REPLACE FUNCTION fn_controlar_actualizacion_inventario()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.sucursal_id IS DISTINCT FROM OLD.sucursal_id
       OR NEW.variante_producto_id IS DISTINCT FROM OLD.variante_producto_id
       OR NEW.temporada_id IS DISTINCT FROM OLD.temporada_id THEN
        RAISE EXCEPTION 'No se puede cambiar la sucursal, variante o temporada de un inventario existente';
    END IF;
    IF NEW.stock_actual IS DISTINCT FROM OLD.stock_actual
       OR NEW.stock_reservado IS DISTINCT FROM OLD.stock_reservado THEN
        NEW.fecha_actualizacion := CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_controlar_actualizacion_inventario ON inventario;
CREATE TRIGGER trg_controlar_actualizacion_inventario
BEFORE UPDATE ON inventario
FOR EACH ROW
EXECUTE FUNCTION fn_controlar_actualizacion_inventario();

CREATE OR REPLACE FUNCTION fn_validar_detalle_reserva_sucursal()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_sucursal_reserva BIGINT;
    v_sucursal_inventario BIGINT;
BEGIN
    SELECT sucursal_id INTO v_sucursal_reserva FROM reserva WHERE id = NEW.reserva_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'La reserva % no existe', NEW.reserva_id;
    END IF;
    SELECT sucursal_id INTO v_sucursal_inventario FROM inventario WHERE id = NEW.inventario_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'El inventario % no existe', NEW.inventario_id;
    END IF;
    IF v_sucursal_reserva <> v_sucursal_inventario THEN
        RAISE EXCEPTION 'El inventario % no pertenece a la sucursal de la reserva %', NEW.inventario_id, NEW.reserva_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validar_detalle_reserva_sucursal ON detalle_reserva;
CREATE TRIGGER trg_validar_detalle_reserva_sucursal
BEFORE INSERT OR UPDATE ON detalle_reserva
FOR EACH ROW
EXECUTE FUNCTION fn_validar_detalle_reserva_sucursal();

CREATE OR REPLACE FUNCTION fn_validar_detalle_venta_sucursal()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_sucursal_venta BIGINT;
    v_sucursal_inventario BIGINT;
BEGIN
    SELECT sucursal_id INTO v_sucursal_venta FROM venta WHERE id = NEW.venta_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'La venta % no existe', NEW.venta_id;
    END IF;
    SELECT sucursal_id INTO v_sucursal_inventario FROM inventario WHERE id = NEW.inventario_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'El inventario % no existe', NEW.inventario_id;
    END IF;
    IF v_sucursal_venta <> v_sucursal_inventario THEN
        RAISE EXCEPTION 'El inventario % pertenece a una sucursal diferente a la venta %', NEW.inventario_id, NEW.venta_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validar_detalle_venta_sucursal ON detalle_venta;
CREATE TRIGGER trg_validar_detalle_venta_sucursal
BEFORE INSERT OR UPDATE ON detalle_venta
FOR EACH ROW
EXECUTE FUNCTION fn_validar_detalle_venta_sucursal();

CREATE OR REPLACE FUNCTION fn_proteger_detalle_venta()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_venta_id BIGINT;
    v_estado VARCHAR(30);
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_venta_id := OLD.venta_id;
    ELSE
        v_venta_id := NEW.venta_id;
    END IF;
    SELECT estado INTO v_estado FROM venta WHERE id = v_venta_id;
    IF FOUND AND v_estado IN ('COMPLETADA','CANCELADA','REEMBOLSADA') THEN
        RAISE EXCEPTION 'No se pueden modificar los detalles de la venta % porque está en estado %', v_venta_id, v_estado;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_proteger_detalle_venta ON detalle_venta;
CREATE TRIGGER trg_proteger_detalle_venta
BEFORE INSERT OR UPDATE OR DELETE ON detalle_venta
FOR EACH ROW
EXECUTE FUNCTION fn_proteger_detalle_venta();

CREATE OR REPLACE FUNCTION fn_recalcular_total_venta_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_venta_id BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_venta_id := OLD.venta_id;
        UPDATE venta SET total = (SELECT COALESCE(SUM(cantidad * precio_unitario),0) FROM detalle_venta WHERE venta_id = v_venta_id) WHERE id = v_venta_id;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.venta_id IS DISTINCT FROM NEW.venta_id THEN
            UPDATE venta SET total = (SELECT COALESCE(SUM(cantidad * precio_unitario),0) FROM detalle_venta WHERE venta_id = OLD.venta_id) WHERE id = OLD.venta_id;
        END IF;
        v_venta_id := NEW.venta_id;
    ELSE
        v_venta_id := NEW.venta_id;
    END IF;
    UPDATE venta SET total = (SELECT COALESCE(SUM(cantidad * precio_unitario),0) FROM detalle_venta WHERE venta_id = v_venta_id) WHERE id = v_venta_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_recalcular_total_venta ON detalle_venta;
CREATE TRIGGER trg_recalcular_total_venta
AFTER INSERT OR UPDATE OR DELETE ON detalle_venta
FOR EACH ROW
EXECUTE FUNCTION fn_recalcular_total_venta_trigger();

CREATE OR REPLACE FUNCTION fn_proteger_eliminacion_venta()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.estado IN ('PAGADA','COMPLETADA','REEMBOLSADA') THEN
        RAISE EXCEPTION 'No se puede eliminar la venta % porque está en estado %', OLD.id, OLD.estado;
    END IF;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_proteger_eliminacion_venta ON venta;
CREATE TRIGGER trg_proteger_eliminacion_venta
BEFORE DELETE ON venta
FOR EACH ROW
EXECUTE FUNCTION fn_proteger_eliminacion_venta();

CREATE OR REPLACE FUNCTION fn_proteger_detalle_orden_compra()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_orden_id BIGINT;
    v_estado VARCHAR(30);
BEGIN
    IF TG_OP = 'DELETE' THEN v_orden_id := OLD.orden_compra_id; ELSE v_orden_id := NEW.orden_compra_id; END IF;
    SELECT estado INTO v_estado FROM orden_compra WHERE id = v_orden_id;
    IF FOUND AND v_estado IN ('RECIBIDA','CANCELADA') THEN
        RAISE EXCEPTION 'No se pueden modificar los detalles de la orden % porque está en estado %', v_orden_id, v_estado;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_proteger_detalle_orden_compra ON detalle_orden_compra;
CREATE TRIGGER trg_proteger_detalle_orden_compra
BEFORE INSERT OR UPDATE OR DELETE ON detalle_orden_compra
FOR EACH ROW
EXECUTE FUNCTION fn_proteger_detalle_orden_compra();

CREATE OR REPLACE FUNCTION fn_validar_producto_proveedor_orden()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_proveedor_id BIGINT;
    v_producto_id BIGINT;
BEGIN
    SELECT proveedor_id INTO v_proveedor_id FROM orden_compra WHERE id = NEW.orden_compra_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'La orden de compra % no existe', NEW.orden_compra_id; END IF;
    SELECT producto_id INTO v_producto_id FROM variante_producto WHERE id = NEW.variante_producto_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'La variante % no existe', NEW.variante_producto_id; END IF;
    IF NOT EXISTS (SELECT 1 FROM proveedor_producto WHERE proveedor_id = v_proveedor_id AND producto_id = v_producto_id AND estado = TRUE) THEN
        RAISE EXCEPTION 'El producto % no está asociado al proveedor %', v_producto_id, v_proveedor_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validar_producto_proveedor_orden ON detalle_orden_compra;
CREATE TRIGGER trg_validar_producto_proveedor_orden
BEFORE INSERT OR UPDATE ON detalle_orden_compra
FOR EACH ROW
EXECUTE FUNCTION fn_validar_producto_proveedor_orden();

CREATE OR REPLACE FUNCTION fn_validar_detalle_devolucion()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_venta_devolucion BIGINT;
    v_venta_detalle BIGINT;
    v_cantidad_vendida INTEGER;
BEGIN
    SELECT venta_id INTO v_venta_devolucion FROM devolucion WHERE id = NEW.devolucion_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'La devolución % no existe', NEW.devolucion_id; END IF;
    SELECT venta_id, cantidad INTO v_venta_detalle, v_cantidad_vendida FROM detalle_venta WHERE id = NEW.detalle_venta_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'El detalle de venta % no existe', NEW.detalle_venta_id; END IF;
    IF v_venta_devolucion <> v_venta_detalle THEN
        RAISE EXCEPTION 'El detalle de venta % no pertenece a la venta asociada a la devolución %', NEW.detalle_venta_id, NEW.devolucion_id;
    END IF;
    IF NEW.cantidad > v_cantidad_vendida THEN
        RAISE EXCEPTION 'No se pueden devolver % unidades. Solo se vendieron %.', NEW.cantidad, v_cantidad_vendida;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validar_detalle_devolucion ON detalle_devolucion;
CREATE TRIGGER trg_validar_detalle_devolucion
BEFORE INSERT OR UPDATE ON detalle_devolucion
FOR EACH ROW
EXECUTE FUNCTION fn_validar_detalle_devolucion();

CREATE OR REPLACE FUNCTION fn_registrar_bitacora()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_usuario_text TEXT;
    v_usuario_id BIGINT;
    v_registro_id TEXT;
    v_accion TEXT;
BEGIN
    v_usuario_text := current_setting('app.usuario_id', TRUE);
    IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN
        v_usuario_id := v_usuario_text::BIGINT;
    ELSE
        v_usuario_id := NULL;
    END IF;
    CASE TG_OP
        WHEN 'INSERT' THEN v_accion := 'CREAR'; v_registro_id := NEW.id::TEXT;
        WHEN 'UPDATE' THEN v_accion := 'MODIFICAR'; v_registro_id := NEW.id::TEXT;
        WHEN 'DELETE' THEN v_accion := 'ELIMINAR'; v_registro_id := OLD.id::TEXT;
        ELSE v_accion := TG_OP;
    END CASE;
    INSERT INTO bitacora (usuario_id, fecha_hora, accion, entidad_afectada, descripcion)
    VALUES (v_usuario_id, CURRENT_TIMESTAMP, v_accion, TG_TABLE_NAME,
            FORMAT('Operación %s sobre registro ID %s', v_accion, v_registro_id));
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bitacora_usuario ON usuario;
CREATE TRIGGER trg_bitacora_usuario AFTER INSERT OR UPDATE OR DELETE ON usuario FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_rol ON rol;
CREATE TRIGGER trg_bitacora_rol AFTER INSERT OR UPDATE OR DELETE ON rol FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_producto ON producto;
CREATE TRIGGER trg_bitacora_producto AFTER INSERT OR UPDATE OR DELETE ON producto FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_variante_producto ON variante_producto;
CREATE TRIGGER trg_bitacora_variante_producto AFTER INSERT OR UPDATE OR DELETE ON variante_producto FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_sucursal ON sucursal;
CREATE TRIGGER trg_bitacora_sucursal AFTER INSERT OR UPDATE OR DELETE ON sucursal FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_empleado ON empleado;
CREATE TRIGGER trg_bitacora_empleado AFTER INSERT OR UPDATE OR DELETE ON empleado FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_cliente ON cliente;
CREATE TRIGGER trg_bitacora_cliente AFTER INSERT OR UPDATE OR DELETE ON cliente FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_inventario ON inventario;
CREATE TRIGGER trg_bitacora_inventario AFTER INSERT OR UPDATE OR DELETE ON inventario FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_orden_compra ON orden_compra;
CREATE TRIGGER trg_bitacora_orden_compra AFTER INSERT OR UPDATE OR DELETE ON orden_compra FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_reserva ON reserva;
CREATE TRIGGER trg_bitacora_reserva AFTER INSERT OR UPDATE OR DELETE ON reserva FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_venta ON venta;
CREATE TRIGGER trg_bitacora_venta AFTER INSERT OR UPDATE OR DELETE ON venta FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_pago ON pago;
CREATE TRIGGER trg_bitacora_pago AFTER INSERT OR UPDATE OR DELETE ON pago FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();
DROP TRIGGER IF EXISTS trg_bitacora_devolucion ON devolucion;
CREATE TRIGGER trg_bitacora_devolucion AFTER INSERT OR UPDATE OR DELETE ON devolucion FOR EACH ROW EXECUTE FUNCTION fn_registrar_bitacora();

