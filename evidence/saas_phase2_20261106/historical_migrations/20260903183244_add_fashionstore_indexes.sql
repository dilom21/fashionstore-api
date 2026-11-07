-- Recovered read-only from supabase_migrations.schema_migrations
-- Project ref: zyitwlxfkphthlvlmuej
-- Version: 20260903183244; name: add_fashionstore_indexes; stored statements: 1
-- Evidence only: DO NOT EXECUTE

SET search_path TO public;

CREATE INDEX idx_usuario_rol ON usuario (rol_id);
CREATE UNIQUE INDEX uq_usuario_correo_lower ON usuario (LOWER(correo));
CREATE INDEX idx_funcion_modulo ON funcion (modulo_id);
CREATE INDEX idx_rol_funcion_funcion ON rol_funcion (funcion_id);
CREATE INDEX idx_rol_funcion_accion ON rol_funcion (accion_id);
CREATE INDEX idx_token_recuperacion_usuario_activo ON token_recuperacion (usuario_id, fecha_expiracion) WHERE usado = FALSE;
CREATE INDEX idx_bitacora_usuario_fecha ON bitacora (usuario_id, fecha_hora DESC);
CREATE INDEX idx_bitacora_entidad_fecha ON bitacora (entidad_afectada, fecha_hora DESC);
CREATE INDEX idx_empleado_sucursal ON empleado (sucursal_id);
CREATE INDEX idx_producto_categoria ON producto (categoria_id);
CREATE INDEX idx_producto_categoria_activo ON producto (categoria_id, nombre) WHERE estado = TRUE;
CREATE INDEX idx_producto_nombre_lower ON producto (LOWER(nombre));
CREATE INDEX idx_recurso_producto_producto ON recurso_producto (producto_id);
CREATE INDEX idx_recurso_producto_producto_color ON recurso_producto (producto_id, color_id);
CREATE UNIQUE INDEX uq_recurso_producto_principal ON recurso_producto (producto_id, COALESCE(color_id, 0)) WHERE es_principal = TRUE AND estado = TRUE;
CREATE INDEX idx_producto_coleccion_coleccion ON producto_coleccion (coleccion_id);
CREATE INDEX idx_temporada_fechas ON temporada (fecha_inicio, fecha_fin) WHERE estado = TRUE;
CREATE INDEX idx_promocion_producto_producto ON promocion_producto (producto_id);
CREATE INDEX idx_promocion_fechas_activas ON promocion (fecha_inicio, fecha_fin) WHERE estado = TRUE;
CREATE INDEX idx_proveedor_producto_producto ON proveedor_producto (producto_id) WHERE estado = TRUE;
CREATE INDEX idx_inventario_variante_sucursal ON inventario (variante_producto_id, sucursal_id, temporada_id);
CREATE INDEX idx_inventario_temporada ON inventario (temporada_id, sucursal_id);
CREATE INDEX idx_inventario_stock_positivo ON inventario (sucursal_id, variante_producto_id) WHERE stock_actual > stock_reservado;
CREATE INDEX idx_movimiento_inventario_fecha ON movimiento_inventario (inventario_id, fecha_hora DESC);
CREATE INDEX idx_movimiento_usuario_fecha ON movimiento_inventario (usuario_id, fecha_hora DESC);
CREATE INDEX idx_movimiento_referencia ON movimiento_inventario (referencia_tipo, referencia_id);
CREATE INDEX idx_movimiento_tipo_fecha ON movimiento_inventario (tipo, fecha_hora DESC);
CREATE INDEX idx_orden_compra_proveedor_fecha ON orden_compra (proveedor_id, fecha_orden DESC);
CREATE INDEX idx_orden_compra_sucursal_fecha ON orden_compra (sucursal_id, fecha_orden DESC);
CREATE INDEX idx_orden_compra_estado ON orden_compra (estado, fecha_orden DESC) WHERE estado IN ('BORRADOR', 'ENVIADA', 'PARCIAL');
CREATE INDEX idx_detalle_orden_compra_variante ON detalle_orden_compra (variante_producto_id, temporada_id);
CREATE UNIQUE INDEX uq_carrito_activo_cliente ON carrito (cliente_id) WHERE estado = 'ACTIVO';
CREATE INDEX idx_carrito_cliente_fecha ON carrito (cliente_id, fecha_creacion DESC);
CREATE INDEX idx_detalle_carrito_variante ON detalle_carrito (variante_producto_id);
CREATE INDEX idx_reserva_cliente_fecha ON reserva (cliente_id, fecha_reserva DESC);
CREATE INDEX idx_reserva_sucursal_atencion ON reserva (sucursal_id, fecha_atencion);
CREATE INDEX idx_reserva_pendiente_sucursal ON reserva (sucursal_id, fecha_atencion) WHERE estado IN ('PENDIENTE', 'CONFIRMADA');
CREATE INDEX idx_reserva_estado_fecha ON reserva (estado, fecha_atencion);
CREATE INDEX idx_detalle_reserva_inventario ON detalle_reserva (inventario_id);
CREATE INDEX idx_venta_sucursal_fecha ON venta (sucursal_id, fecha_hora DESC);
CREATE INDEX idx_venta_cliente_fecha ON venta (cliente_id, fecha_hora DESC);
CREATE INDEX idx_venta_empleado_fecha ON venta (empleado_id, fecha_hora DESC);
CREATE INDEX idx_venta_canal_fecha ON venta (canal, fecha_hora DESC);
CREATE INDEX idx_venta_pendiente ON venta (fecha_hora DESC) WHERE estado = 'PENDIENTE';
CREATE INDEX idx_venta_estado_fecha ON venta (estado, fecha_hora DESC);
CREATE INDEX idx_detalle_venta_inventario ON detalle_venta (inventario_id);
CREATE INDEX idx_pago_estado_fecha ON pago (estado, fecha_hora DESC);
CREATE INDEX idx_pago_metodo_fecha ON pago (metodo, fecha_hora DESC);
CREATE INDEX idx_devolucion_venta_fecha ON devolucion (venta_id, fecha_hora DESC);
CREATE INDEX idx_devolucion_pendientes ON devolucion (fecha_hora DESC) WHERE estado IN ('SOLICITADA', 'APROBADA');
CREATE INDEX idx_detalle_devolucion_detalle_venta ON detalle_devolucion (detalle_venta_id);

