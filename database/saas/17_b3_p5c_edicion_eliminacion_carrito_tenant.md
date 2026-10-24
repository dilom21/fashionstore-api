# Bloque 3 — Paso 5C: modificación y eliminación del carrito de un cliente SaaS

**Estado:** **SUBFASE 5C VALIDADA** por el estudiante: `203 passed, 2 warnings in 4.77s`; **0 fallos**. El Paso 5 global permanece EN PROCESO. No tocar `main`, Supabase ni la base PostgreSQL restaurada; `SAAS_AUTH_ENABLED=false`.

## Alcance de la subfase

Tras la subfase 5B (`POST /tenant/carritos/items`, 176 pruebas aprobadas, preflight PostgreSQL READ ONLY), se migran tres operaciones más de CU15 sin alterar reservas, inventario ni ventas:

| Método | Ruta SaaS nueva | Regla |
|---|---|---|
| PATCH | `/tenant/carritos/{carrito_id}/items/{detalle_id}` | Establecer la cantidad absoluta de una línea propia; no sumarla |
| DELETE | `/tenant/carritos/{carrito_id}/items/{detalle_id}` | Eliminar físicamente la línea; cuando es la última, marcar el carrito `ELIMINADO` |
| DELETE | `/tenant/carritos/{carrito_id}` | Marcar el carrito como `ELIMINADO` sin borrar las líneas |

**No se habilitan GET de carrito de cliente en esta subfase.** Se mantienen las consultas comerciales de administrador y las escrituras del catálogo de pasos anteriores.

## Garantías del backend
- Todas requieren `Depends(get_current_tenant_cliente)`: JWT empresarial con membresía y suscripción vigentes, contexto cliente. El repositorio verifica una segunda vez `cliente.empresa_id = tenant.empresa_id` y `cliente.usuario_id = tenant.usuario_id`.
- Para el carrito: `id + empresa_id + cliente_id` obligatorios. Un carrito de otra empresa, de otro cliente o inexistente devuelve **404**, sin revelar qué caso ocurrió. Una línea debe tener también `id + carrito_id + empresa_id`, o devuelve **404**.
- Un carrito no ACTIVO o de más de dos horas devuelve **409**, sin reactivarlo. Una solicitud que falla revierte sus cambios. Nota: esta operación no persiste un estado EXPIRADO cuando la propia solicitud falla con 409; la rutina de POST del Paso 5B expira carritos en una transacción exitosa.
- PATCH usa `cantidad` obligatoria, positiva y `<= 1000`, con `extra="forbid"`. Comprueba pertenencia y estado activo de inventario, sucursal, temporada, variante, producto, categoría, talla y color; la cantidad final no supera `stock_actual - stock_reservado`. El cuerpo nunca acepta `empresa_id`, `cliente_id` ni cambio de `inventario_id`.
- DELETE de última línea marca el carrito `ELIMINADO`; con líneas restantes, sigue `ACTIVO`. DELETE de carrito es lógico y conserva sus líneas.
- Cada mutación usa **una transacción** con `commit` si tiene éxito y `rollback` para 404/409, FK/UNIQUE y errores de SQL. `SELECT ... FOR UPDATE` bloquea perfil de cliente, carrito y línea; PATCH bloquea también el inventario al validar stock. Las FK compuestas de PostgreSQL siguen reforzando pertenencia.
- No existe ninguna escritura de `stock_actual`, `stock_reservado`, `movimiento_inventario`, `reserva`, `venta`, `pago`, compra o devolución en los nuevos métodos.
- El ASGI gate solo abre PATCH/DELETE para los patrones exactos de carrito indicados; POST de cliente del paso anterior sigue permitido, sin permitir otros verbos ni rutas heredadas.

## Archivos modificados
- `app/modules/carrito/repositories/tenant_repository.py`: comprobación `_carrito_propio_activo`, `_detalle_propio`, `actualizar_cantidad`, `eliminar_detalle`, `eliminar_carrito`. Refuerzo de temporada empresarial en `_stock_valido`.
- `app/modules/carrito/api/tenant_router.py`: tres endpoints protegidos nuevos y esquema estricto. Atomicidad y rollback.
- `app/core/saas_route_gate.py`: tres allowlists exactas adicionales, sin abrir prefijos arbitrarios.
- `tests_saas/test_tenant_carrito_transacciones.py`: añade **27 casos nuevos**, incluido test de carrito de otra empresa, línea de otro carrito, vencimiento, stock, borrado lógico, borrado de última línea y bloqueo de demás rutas. Actualiza un test antiguo que esperaba 503 para estas rutas, manteniendo el bloqueo para PUT y URLs sin migrar.
- Esta guía `database/saas/17_b3_p5c_edicion_eliminacion_carrito_tenant.md`.

## Verificación local
Usar Windows PowerShell y conda `fashionstore-api` en `C:\SI2_Parcial1\fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado observado en PowerShell:** `203 passed, 2 warnings in 4.77s` = 176 pruebas previas + 27 nuevas. Las dos advertencias son deprecaciones conocidas de Starlette/FastAPI, no fallos. El `git pull --ff-only` actualizó la rama hasta `cacaa06` antes de ejecutar pytest.

Se usan dos empresas ficticias en **SQLite en memoria**. No se ejecutaron escrituras PostgreSQL ni migraciones en esta subfase. Los tests validan la lógica de los métodos, **no** triggers reales ni paralelismo en PostgreSQL.

## Pendiente para terminar el Paso 5
- Lectura propia del carrito y controles de pertenencia de sus líneas en la respuesta.
- Reservas y procedimiento `sp_crear_reserva_desde_carrito` con `empresa_id`, bloqueo y auditoría PostgreSQL.
- Ventas, pagos, compras, devoluciones y procesamiento de stock mediante procedimientos transaccionales tenant-aware.
- Pruebas reales con rollback contra PostgreSQL local, bajo rol sin `BYPASSRLS`; esto sigue prohibido contra Supabase.
- Los roles demo de otras empresas permanecen desactivados; las pruebas SQLite usan contextos simulados.
- No habilitar `SAAS_AUTH_ENABLED` ni desplegar. El Paso 5 no se da por concluido tras estas pruebas. Esperar `siguiente` para otra subfase.

## Cierre de la subfase 5C

La ejecución local de la suite completa obtuvo **203 pruebas superadas**, incluyendo 27 casos añadidos para las nuevas rutas PATCH/DELETE; no hubo fallos. La subfase 5C se marca como **VALIDADA en SQLite ficticio**, sin extender la conclusión a PostgreSQL transaccional o autenticación empresarial con usuarios reales. Mantener bloqueadas reservas y operaciones comerciales no migradas; no activar `SAAS_AUTH_ENABLED` ni desplegar. **Paso 5 continúa EN PROCESO**, esperar `siguiente` antes de otra subfase.
