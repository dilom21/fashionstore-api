# Comparación: historial original, catálogo actual y migraciones SaaS

Fecha de captura: 2026-11-06. Todas las consultas remotas fueron `SELECT` sobre metadatos, definiciones o conteos agregados. No se ejecutó el SQL recuperado.

## Historial original frente al catálogo actual

| Tipo | Cinco migraciones históricas | Catálogo actual | Diferencia confirmada |
|---|---:|---:|---|
| Tablas | 38 | 41 | +3 |
| Vistas | 1 | 1 | 0 |
| Secuencias identity | 34 inferidas | 37 | +3 |
| Funciones | 12 | 15 | +3 |
| Procedimientos | 7 | 9 | +2 |
| Triggers | 24 | 27 | +3 |
| Claves foráneas | 53 | 60 | +7 netas |
| Índices | 124 inferidos | 134 | +10 netos |
| Tablas con RLS | 38 | 38 | las 3 posteriores no tienen RLS |
| FORCE RLS | 0 | 0 | 0 |
| Políticas RLS | 0 | 0 | 0 |

El total histórico de índices se obtiene de 57 `CREATE INDEX`, 38 claves primarias y 29 restricciones `UNIQUE` en las fuentes originales.

## Objetos posteriores no presentes en las cinco migraciones

### Tablas

- `configuracion_vestidor_ar`
- `sesion_vestidor_ar`
- `prueba_vestidor_ar`

Las tres tienen `empresa_id` ausente, RLS desactivado y FORCE RLS desactivado.

### Cambios de tablas preexistentes

- `carrito`: añadió `fecha_actualizacion`.
- `detalle_carrito`: eliminó `variante_producto_id` y añadió `inventario_id`.
- `reserva`: añadió `carrito_id`.
- `venta`: añadió `carrito_id`.
- `pago`: ya no conserva la restricción histórica `uq_pago_venta`, por lo que el esquema actual permite un modelo distinto de pagos por venta.

### Funciones añadidas

- `fn_actualizar_actividad_carrito()`
- `fn_completar_ip_bitacora()`
- `fn_validar_sucursal_detalle_carrito()`

### Procedimientos añadidos

- `sp_crear_reserva_desde_carrito(bigint,timestamptz,text,bigint,bigint)`
- `sp_finalizar_reserva_sin_compra(bigint,bigint,text)`

### Triggers añadidos

- `trg_actualizar_actividad_carrito`
- `trg_bitacora_completar_ip`
- `trg_validar_sucursal_detalle_carrito`

### Constraints e índices posteriores relevantes

- Constraints de las tres tablas AR.
- `fk_detalle_carrito_inventario` y `uq_detalle_carrito_inventario`.
- `fk_reserva_carrito`, `uq_reserva_carrito`.
- `fk_venta_carrito`, `uq_venta_carrito`.
- `ix_carrito_cliente_estado_actualizacion`.
- `uq_carrito_activo_cliente_sucursal`.
- Índices de sesión, prueba y configuración AR.

Objetos históricos que ya no existen con su forma/nombre original:

- `fk_detalle_carrito_variante`.
- `uq_detalle_carrito`.
- `uq_pago_venta`.
- `idx_detalle_carrito_variante`.
- `uq_carrito_activo_cliente`.

## Seguridad actual

- 38 tablas originales tienen RLS habilitado por la migración histórica de hardening.
- Las 3 tablas AR posteriores tienen RLS desactivado.
- No existen políticas RLS en `public`.
- Ninguna tabla usa FORCE RLS.
- `vw_inventario_stock` conserva `security_invoker=true`.
- Las 24 rutinas actuales son `SECURITY INVOKER` y propiedad de `postgres`.
- Los ACL nulos de rutinas implican los privilegios PostgreSQL predeterminados, incluido `EXECUTE` para `PUBLIC`, salvo revocación externa no reflejada en el ACL.

## Compatibilidad con las cuatro migraciones SaaS

Coinciden con el catálogo actual:

- 41 tablas actuales y 0 tablas SaaS base.
- `usuario.id` y `rol.id` son `bigint`.
- 31 tablas empresariales encontradas.
- 28 tablas empresariales con PK simple `id`.
- 46 FK simples entre tablas empresariales, exactamente el supuesto de `00300`.
- Las 9 restricciones globales que `00300` pretende reemplazar existen con los nombres esperados.
- 27 triggers.
- Los conteos agregados exigidos por `00200` y `00400` coinciden con la fotografía remota actual.

Limitaciones y riesgos:

- `00200`, `00300` y `00400` siguen bloqueadas al nombre de una restauración local y no son aplicables al remoto.
- Las cinco migraciones históricas no reconstruyen el catálogo actual: faltan todos los objetos enumerados arriba.
- La cadena histórica recuperada no puede considerarse un bootstrap reproducible hasta incorporar los cambios posteriores como fuentes versionadas.
- Las tres tablas AR forman parte de las 31 tablas SaaS previstas, pero actualmente son precisamente las únicas tablas sin RLS.
- Las migraciones SaaS aún no proporcionan políticas RLS productivas, FORCE RLS, principal SQL restringido ni adaptación completa de las 24 rutinas actuales.
