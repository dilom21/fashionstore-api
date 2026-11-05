-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA I (5BR)
-- SEMILLA HISTORICA FICTICIA PARA EL ESQUEMA ORIGINAL RESTAURADO
--
-- Por que este archivo existe
-- ---------------------------
-- La semilla de la Etapa B (`SEMILLA` de 70_staging_saas_desde_cero_5bj.py) se
-- escribio contra el mapeo ORM, que NO reproduce los dominios CHECK reales. El
-- esquema original restaurado (41 tablas) define dominios distintos, por ejemplo:
--
--   movimiento_inventario.tipo : ENTRADA_COMPRA | SALIDA_VENTA | RESERVA |
--                                LIBERACION_RESERVA | DEVOLUCION | AJUSTE_ENTRADA |
--                                AJUSTE_SALIDA | TRANSFERENCIA_ENTRADA/SALIDA
--   venta.canal                : WEB | MOVIL | PRESENCIAL   (no 'TIENDA')
--   pago.estado                : PENDIENTE | APROBADO | RECHAZADO | ANULADO | REEMBOLSADO
--   reserva.estado             : PENDIENTE | CONFIRMADA | ATENDIDA | CANCELADA | VENCIDA
--   prueba_vestidor_ar.estado  : INICIADA | COMPLETADA | CANCELADA | ERROR
--   configuracion_vestidor_ar.tipo_asset : PNG_2D | GLB_3D
--
-- Esta semilla usa SOLO valores admitidos por el esquema ORIGINAL. No contiene
-- datos de negocio reales: todos los identificadores son ficticios.
--
-- Uso: el runner 99 la ejecuta con `session_replication_role = replica` (los
-- triggers historicos no se disparan durante la carga, igual que un pg_restore)
-- y despues ajusta las secuencias.

INSERT INTO public.rol (id, nombre, estado) VALUES
  (1, 'ADMINISTRADOR', TRUE),
  (2, 'CLIENTE', TRUE);

INSERT INTO public.ciudad (id, nombre, estado) VALUES (1, 'Ciudad Ficticia', TRUE);
INSERT INTO public.color (id, nombre, estado) VALUES (1, 'Negro', TRUE);
INSERT INTO public.talla (id, nombre, estado) VALUES (1, 'M', TRUE);
INSERT INTO public.modulo (id, nombre, estado) VALUES (1, 'Ventas', TRUE);
INSERT INTO public.accion (id, nombre, estado) VALUES (1, 'CREAR', TRUE);
INSERT INTO public.funcion (id, modulo_id, nombre, estado)
VALUES (1, 1, 'Registrar venta', TRUE);
INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id) VALUES (1, 1, 1);

INSERT INTO public.usuario (id, rol_id, correo, password_hash, fecha_creacion, estado)
VALUES
  (1, 1, 'admin@staging.invalid', 'hash-ficticio-no-usable', NOW(), TRUE),
  (2, 2, 'cliente@staging.invalid', 'hash-ficticio-no-usable', NOW(), TRUE);

INSERT INTO public.sucursal (id, ciudad_id, nombre, direccion, estado)
VALUES (1, 1, 'Sucursal Ficticia 5BR', 'Calle Falsa 123', TRUE);

INSERT INTO public.categoria (id, nombre, estado) VALUES (1, 'Polos', TRUE);
INSERT INTO public.temporada (id, nombre, fecha_inicio, fecha_fin, estado)
VALUES (1, '2026', DATE '2026-01-01', DATE '2026-12-31', TRUE);
INSERT INTO public.producto (id, categoria_id, nombre, precio, estado)
VALUES (1, 1, 'Polo ficticio 5BR', 99.90, TRUE);
INSERT INTO public.variante_producto
  (id, producto_id, talla_id, color_id, sku, estado)
VALUES (1, 1, 1, 1, 'SKU-5BR-FICTICIO', TRUE);
INSERT INTO public.recurso_producto
  (id, producto_id, color_id, tipo, url, es_principal, estado)
VALUES (1, 1, 1, 'imagen', 'https://lab5br.invalid/1.png', TRUE, TRUE);

INSERT INTO public.coleccion (id, temporada_id, nombre, estado)
VALUES (1, 1, 'Verano 5BR', TRUE);
INSERT INTO public.producto_coleccion (producto_id, coleccion_id) VALUES (1, 1);
INSERT INTO public.promocion
  (id, nombre, tipo_descuento, valor_descuento, fecha_inicio, fecha_fin, estado)
VALUES (1, 'Promo ficticia 5BR', 'PORCENTAJE', 10, NOW(), NOW() + INTERVAL '30 days', TRUE);
INSERT INTO public.promocion_producto (promocion_id, producto_id) VALUES (1, 1);

INSERT INTO public.proveedor (id, razon_social, nit, estado)
VALUES (1, 'Proveedor Ficticio 5BR SRL', 'NIT-5BR-1', TRUE);
INSERT INTO public.proveedor_producto (proveedor_id, producto_id, costo_referencia, estado)
VALUES (1, 1, 50.00, TRUE);

INSERT INTO public.inventario
  (id, sucursal_id, variante_producto_id, temporada_id,
   stock_actual, stock_reservado, fecha_actualizacion)
VALUES (1, 1, 1, 1, 20, 2, NOW());
INSERT INTO public.movimiento_inventario
  (id, inventario_id, usuario_id, tipo, cantidad, fecha_hora, observaciones)
VALUES (1, 1, 1, 'ENTRADA_COMPRA', 20, NOW(), 'Carga historica ficticia 5BR');

INSERT INTO public.cliente (id, usuario_id, nombre, apellido, ci, estado)
VALUES (1, 2, 'Ana', 'Ficticia', 'CI-5BR-1', TRUE);
INSERT INTO public.empleado
  (id, usuario_id, sucursal_id, nombres, apellidos, ci, fecha_contratacion, estado)
VALUES (1, 1, 1, 'Luis', 'Ficticio', 'CI-5BR-2', DATE '2026-01-02', TRUE);

INSERT INTO public.carrito
  (id, cliente_id, sucursal_id, fecha_creacion, fecha_actualizacion, estado)
VALUES (1, 1, 1, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days', 'ACTIVO');
INSERT INTO public.detalle_carrito (id, carrito_id, inventario_id, cantidad)
VALUES (1, 1, 1, 2);

INSERT INTO public.reserva
  (id, cliente_id, sucursal_id, carrito_id, fecha_reserva, fecha_atencion, estado, observacion)
VALUES (1, 1, 1, 1, NOW() - INTERVAL '1 day', NOW() + INTERVAL '2 days',
        'PENDIENTE', 'Reserva historica ficticia');
INSERT INTO public.detalle_reserva (id, reserva_id, inventario_id, cantidad)
VALUES (1, 1, 1, 2);

INSERT INTO public.venta
  (id, cliente_id, empleado_id, sucursal_id, reserva_id, fecha_hora, canal, estado, total, carrito_id)
VALUES (1, 1, 1, 1, NULL, NOW() - INTERVAL '1 day', 'PRESENCIAL', 'COMPLETADA', 99.90, 1);
INSERT INTO public.detalle_venta (id, venta_id, inventario_id, cantidad, precio_unitario)
VALUES (1, 1, 1, 1, 99.90);
INSERT INTO public.pago (id, venta_id, fecha_hora, monto, metodo, estado)
VALUES (1, 1, NOW() - INTERVAL '1 day', 99.90, 'EFECTIVO', 'APROBADO');

INSERT INTO public.devolucion (id, venta_id, fecha_hora, motivo, estado, observacion)
VALUES (1, 1, NOW() - INTERVAL '12 hours', 'Talla incorrecta', 'APROBADA',
        'Devolucion historica ficticia');
INSERT INTO public.detalle_devolucion
  (id, devolucion_id, detalle_venta_id, cantidad, motivo)
VALUES (1, 1, 1, 1, 'Talla incorrecta');

INSERT INTO public.bitacora (id, usuario_id, fecha_hora, ip, accion, entidad_afectada, descripcion)
VALUES (1, 1, NOW() - INTERVAL '3 days', '127.0.0.1', 'LOGIN', 'usuario',
        'Evento historico ficticio sin tenant');

INSERT INTO public.configuracion_vestidor_ar
  (id, recurso_producto_id, zona_cuerpo, tipo_asset, factor_ancho, factor_alto,
   offset_x, offset_y, rotacion_offset, orden_capa, opacidad, estado)
VALUES (1, 1, 'TORSO', 'PNG_2D', 1, 1, 0, 0, 0, 1, 1, TRUE);
INSERT INTO public.sesion_vestidor_ar (id, cliente_id, fecha_inicio, estado)
VALUES (1, 1, NOW() - INTERVAL '5 hours', 'ACTIVA');
INSERT INTO public.prueba_vestidor_ar
  (id, sesion_vestidor_ar_id, configuracion_vestidor_ar_id, variante_producto_id,
   fecha_inicio, estado)
VALUES (1, 1, 1, 1, NOW() - INTERVAL '4 hours', 'INICIADA');
