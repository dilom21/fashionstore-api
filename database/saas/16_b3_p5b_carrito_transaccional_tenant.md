# Bloque 3 / Paso 5B — Primera transacción de carrito SaaS (CU15)

**Estado:** código y pruebas preparados en rama `feat/saas-multitenant-parcial2`; **PENDIENTE de pruebas Windows del estudiante**. Paso 5 completo sigue abierto. No modificar `main` ni Supabase.

## Principio conservado del primer parcial
CU15 **NO descuenta `stock_actual` ni incrementa `stock_reservado`** al agregar al carrito. El carrito expresa intención de compra y tiene vigencia de 2 horas. La reserva ocurre más tarde dentro del procedimiento PostgreSQL `sp_crear_reserva_desde_carrito` (CU16), que todavía no se ha migrado en SaaS y sigue BLOQUEADO. Los SP de venta/pago y todo cambio de stock también permanecen bloqueados.

## Solo endpoint habilitado
- `POST /tenant/carritos/items`
- JSON requerido: `{"sucursal_id": 1101, "inventario_id": 1111, "cantidad": 2}`; **los IDs de este ejemplo son ficticios y NO corresponden a tu base local**.
- Responde `{"carrito_id": ..., "detalle_id": ..., "inventario_id": ..., "cantidad": ..., "estado": "ACTIVO"}`.
- No acepta campos adicionales como `empresa_id`, `cliente_id`, `estado`; Pydantic devuelve 422.
- Requiere JWT empresarial, membresía y suscripción vigentes, y **contexto `cliente`**, vía `get_current_tenant_cliente`. El repositorio confirma en SQL que `cliente.usuario_id` está activo en esa empresa.
- Si faltan credenciales: 401; personal en lugar de cliente o sin perfil de ese tenant: 403; inventario ajeno/no disponible: 404; stock insuficiente o carrera de FK/UNIQUE: 409; SaaS apagado: 503.

## Integridad del algoritmo
1. Bloquea el perfil **del cliente y empresa correctos** con `SELECT ... FOR UPDATE` en PostgreSQL para serializar altas de carrito del mismo usuario.
2. Expira carritos `ACTIVO` del cliente/tenant cuyo `fecha_actualizacion` sea anterior a dos horas; el cambio y cualquier nueva línea forman **una sola transacción**.
3. Comprueba `empresa_id` y estado activo de inventario, sucursal, variante, producto, categoría, talla y color; además exige que el inventario corresponda a la sucursal indicada. Bloquea la fila del inventario antes de calcular `stock_actual - stock_reservado`.
4. Reutiliza o crea carrito ACTIVO dentro del tenant y misma sucursal; suma a la línea del inventario si existe, rechaza si la cantidad resultante supera la disponibilidad.
5. Las nuevas filas `carrito` y `detalle_carrito` incluyen `empresa_id` explícito. El router hace **un solo commit** después de todas las comprobaciones; ante errores hace rollback. El índice parcial `uq_carrito_activo_cliente_sucursal`, el trigger `trg_actualizar_actividad_carrito` y las FK compuestas siguen defendiéndolo en PostgreSQL.
6. **NO modifica** `stock_actual`, `stock_reservado`, `venta`, `pago`, `reserva`, `movimiento_inventario` ni sus procedimientos.

## Archivos nuevos y cambios
- `app/modules/carrito/repositories/tenant_repository.py`: algoritmo SQLAlchemy Core con empresa + cliente + sucursal + inventario explícitos.
- `app/modules/carrito/api/tenant_router.py`: endpoint protegido y atomicidad.
- `app/core/tenant_dependencies.py`: `get_current_tenant_cliente`.
- `app/core/saas_route_gate.py`: allowlist agrega SOLO `POST /tenant/carritos/items`; se mantienen bloqueadas las rutas de carrito antiguo y todos los flujos de reserva, venta y pago.
- `app/main.py`: registra nuevo router.
- `tests_saas/test_tenant_carrito_transacciones.py`: SQLite en memoria con dos empresas ficticias, perfiles cliente distintos y 24 pruebas parametrizadas. Verifica alta, suma, pertenencia, SKU/variante ajena, stock, expiración, rollback, perfil, rechazo de suplantación, flag apagado y bloqueo del resto de operaciones.
- `database/saas/09_preflight_carrito_tenant_local.sql` y `.ps1`: inspección de datos reales de la COPIA local con transacción `READ ONLY` y `ROLLBACK`. No se hace ninguna escritura.

## Ejecución de pruebas (Windows, Conda `fashionstore-api`)
Desde `C:\SI2_Parcial1\fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado esperado, no ejecutado aquí:** **176 passed** (152 anteriores + 24 nuevas).

Solo si pytest pasa:
```powershell
.\database\saas\09_preflight_carrito_tenant_local.ps1
```

Pedirá contraseña **LOCAL** de PostgreSQL. Comprueba exclusivamente `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`, verifica 48 carritos, 274 inventarios, 48 FK tenant, índice único parcial, trigger de actividad y relaciones de propiedad. Debe mostrar `PREFLIGHT CARRITO LOCAL SUPERADO (SIN ESCRITURAS)` y `ROLLBACK`.

## Límites y trabajo pendiente del Paso 5
- Los tests de SQLite reemplazan la verificación JWT/membresía con un tenant **ficticio** para ensayar la lógica empresarial. El login real con clientes habilitados todavía no ha sido probado en PostgreSQL.
- Las consultas SQLAlchemy `FOR UPDATE` no tienen bloqueo equivalente en SQLite: las pruebas **NO prueban concurrencia PostgreSQL**, triggers ni carreras reales.
- No se ejecuta INSERT/UPDATE sobre la copia PostgreSQL local, ni con rollback; el preflight PG es estrictamente de solo lectura.
- Faltan PATCH/DELETE y lectura detallada del carrito; falta migrar reservas, inventario con stock, ventas, pagos, órdenes y devoluciones, además de sus SP.
- Las claves tenant definitivas de PostgreSQL se validaron en Bloque 2; todavía falta RLS con rol sin `BYPASSRLS`.
- **Prohibido desplegar, habilitar `SAAS_AUTH_ENABLED` o usar los cinco tenants con endpoints legacy abiertos.**

**Criterio de cierre de la SUBFASE 5B:** recibir salida de 176 tests y preflight PG exitosos. **Paso 5 global seguirá EN PROCESO**, sin salto a Paso 6. Esperar `siguiente` para abordar la próxima subfase.
