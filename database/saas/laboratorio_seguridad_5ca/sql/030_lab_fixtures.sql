-- LABORATORIO 5CA — datos ficticios mínimos para probar aislamiento.
-- Empresa 1 = Vanter Men (creada por 010). Empresa 2 = segundo tenant ficticio.
-- Ningún dato proviene de producción: correos @lab.test y contraseñas aleatorias
-- que sólo existen en memoria del ejecutor (aquí se deja un marcador inválido).
BEGIN;
SET LOCAL lock_timeout = '5s';

INSERT INTO public.rol (id, nombre) VALUES
  (1,'ADMINISTRADOR'), (2,'CLIENTE'), (3,'VENDEDOR');
INSERT INTO public.ciudad (id, nombre) VALUES
  (1,'La Paz'), (2,'Santa Cruz');
INSERT INTO public.talla (id, nombre) VALUES (1,'M'), (2,'L');
INSERT INTO public.color (id, nombre) VALUES (1,'Negro');

INSERT INTO public.empresa (id, razon_social, nombre_comercial, slug)
VALUES (2,'Tienda B','Tienda B','tienda-b');
INSERT INTO public.empresa_configuracion (empresa_id) VALUES (2);
INSERT INTO public.suscripcion (empresa_id, plan_saas_id, fecha_inicio,
                                fecha_vencimiento, estado)
SELECT 2, p.id, CURRENT_DATE - 10, CURRENT_DATE + 300, 'ACTIVA'
FROM public.plan_saas p WHERE p.codigo = 'BASICO';

INSERT INTO public.usuario (id, rol_id, correo, password_hash) VALUES
  (101,1,'a1.admin@lab.test','$lab$pendiente'),
  (102,2,'a2.cliente@lab.test','$lab$pendiente'),
  (103,2,'a3.cliente2@lab.test','$lab$pendiente'),
  (201,2,'b1.cliente@lab.test','$lab$pendiente'),
  (202,1,'b2.admin@lab.test','$lab$pendiente');

INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id) VALUES
  (101,1,1),(102,1,2),(103,1,2),(201,2,2),(202,2,1);

INSERT INTO public.sucursal (id, empresa_id, ciudad_id, nombre, direccion) VALUES
  (11,1,1,'Centro','Calle 1'),(21,2,2,'B Centro','Calle 2');

INSERT INTO public.empleado (id, empresa_id, usuario_id, sucursal_id, nombres,
                             apellidos, ci, fecha_contratacion) VALUES
  (101,1,101,11,'Ana','Admin','CI-A1',CURRENT_DATE - 400),
  (202,2,202,21,'Bruno','Admin','CI-B2',CURRENT_DATE - 200);

INSERT INTO public.cliente (id, empresa_id, usuario_id, nombre, apellido, ci) VALUES
  (102,1,102,'Carla','Cliente','CI-A2'),
  (103,1,103,'Diego','Cliente','CI-A3'),
  (201,2,201,'Elena','Cliente','CI-B1');

INSERT INTO public.categoria (id, empresa_id, nombre) VALUES
  (1,1,'Poleras'),(2,2,'Poleras');
INSERT INTO public.temporada (id, empresa_id, nombre, fecha_inicio, fecha_fin) VALUES
  (1,1,'Verano 2026',CURRENT_DATE - 30,CURRENT_DATE + 60),
  (2,2,'Verano 2026',CURRENT_DATE - 30,CURRENT_DATE + 60);

INSERT INTO public.producto (id, empresa_id, categoria_id, nombre, precio) VALUES
  (1,1,1,'Polera A',100.00),(2,2,2,'Polera B',50.00);
INSERT INTO public.variante_producto (id, empresa_id, producto_id, talla_id,
                                      color_id, sku) VALUES
  (1,1,1,1,1,'A-SKU-M'),(2,1,1,2,1,'A-SKU-L'),(3,2,2,1,1,'B-SKU-M');

INSERT INTO public.inventario (id, empresa_id, sucursal_id, variante_producto_id,
                               temporada_id, stock_actual, stock_reservado) VALUES
  (1,1,11,1,1,1,0),(2,1,11,2,1,5,0),(3,2,21,3,2,5,0);

INSERT INTO public.proveedor (id, empresa_id, razon_social) VALUES
  (1,1,'Proveedor A'),(2,2,'Proveedor B');

INSERT INTO public.carrito (id, empresa_id, cliente_id, sucursal_id, estado) VALUES
  (1,1,102,11,'ACTIVO'),(2,1,103,11,'CONVERTIDO'),(3,2,201,21,'CONVERTIDO'),
  (4,1,103,11,'ACTIVO');
INSERT INTO public.detalle_carrito (id, empresa_id, carrito_id, cantidad,
                                    inventario_id) VALUES
  (1,1,1,1,1),(2,1,2,1,2),(3,2,3,1,3),(4,1,4,1,1);

INSERT INTO public.reserva (id, empresa_id, cliente_id, sucursal_id,
                            fecha_atencion, estado, carrito_id) VALUES
  (1,1,103,11,now() + interval '2 days','PENDIENTE',2),
  (2,2,201,21,now() + interval '2 days','PENDIENTE',3);
INSERT INTO public.detalle_reserva (id, empresa_id, reserva_id, inventario_id,
                                    cantidad) VALUES
  (1,1,1,2,1),(2,2,2,3,1);
UPDATE public.inventario SET stock_reservado = 1 WHERE id IN (2,3);

INSERT INTO public.venta (id, empresa_id, cliente_id, empleado_id, sucursal_id,
                          canal, estado, total) VALUES
  (1,1,102,101,11,'PRESENCIAL','PENDIENTE',100.00),
  (2,2,201,202,21,'PRESENCIAL','PENDIENTE',50.00);
INSERT INTO public.detalle_venta (id, empresa_id, venta_id, inventario_id,
                                  cantidad, precio_unitario) VALUES
  (1,1,1,2,1,100.00),(2,2,2,3,1,50.00);
INSERT INTO public.pago (id, empresa_id, venta_id, monto, metodo, estado) VALUES
  (1,1,1,100.00,'EFECTIVO','APROBADO'),(2,2,2,50.00,'EFECTIVO','APROBADO');
-- El trigger fn_proteger_detalle_venta impide añadir líneas a una venta cerrada.
UPDATE public.venta SET estado = 'COMPLETADA' WHERE id IN (1, 2);
COMMIT;

-- Las fixtures usan IDs explícitos: se adelantan las secuencias de identidad
-- para que las inserciones posteriores sin id no colisionen (por ejemplo la
-- reserva que crea el SP).
DO $secuencias$
DECLARE t text; s text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sucursal','empleado','cliente','categoria','temporada',
    'producto','variante_producto','inventario','proveedor','carrito',
    'detalle_carrito','reserva','detalle_reserva','venta','detalle_venta','pago']
  LOOP
    s := pg_get_serial_sequence('public.' || t, 'id');
    IF s IS NOT NULL THEN
      EXECUTE format('SELECT setval(%L, coalesce((SELECT max(id) FROM public.%I), 1))',
                     s, t);
    END IF;
  END LOOP;
END $secuencias$;

DO $verifica$
BEGIN
  IF (SELECT count(*) FROM public.empresa) <> 2 THEN
    RAISE EXCEPTION 'Se esperaban 2 empresas';
  END IF;
  IF (SELECT count(*) FROM public.usuario_empresa) <> 5 THEN
    RAISE EXCEPTION 'Se esperaban 5 membresías';
  END IF;
  IF (SELECT count(*) FROM public.cliente) <> 3 THEN
    RAISE EXCEPTION 'Se esperaban 3 clientes';
  END IF;
END $verifica$;
