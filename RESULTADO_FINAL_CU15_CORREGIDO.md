# CU15 corregido — entrega revisable

## Resultado

La migración quedó corregida y **no fue aplicada a Supabase**. No se modificaron
datos comerciales ni los casos de uso de Harold.

## Cambios principales

- `database/saas/CU15_MIGRACION_FINAL_REVISION.sql` es el script único transaccional.
- Se eliminó el doble `GENERATED AS IDENTITY` de `cu15_nota` y se usa una secuencia
  explícita para el número por empresa.
- La solicitud ya no publica `CU15_PRENDA_RECIBIDA`; ese evento ocurre solo después
  de inspeccionar todos los ítems.
- Se valida titularidad `venta -> cliente.usuario_id`, JWT/tenant, permiso RBAC
  `GESTIONAR_DEVOLUCIONES`, acción y sucursal. Se fuerza RLS y se revocan ACL públicas.
- El origen monetario se calcula con pagos `APROBADO` prorrateados contra la línea;
  no se acepta un precio enviado por el cliente.
- `cu15_cotizar_diferencia` persiste la cotización antes del cobro. Un cambio más caro
  solo completa con una referencia de pago aprobada y coincidente; el cambio más barato
  queda bloqueado porque no existe política de abono acordada.
- `cu15_completar` bloquea venta, ítems e inventarios en orden determinista, ejecuta
  restock únicamente `APTO + autorización`, descuenta sustituto disponible, registra
  movimientos `CU15`, nota y outbox en una sola transacción. Cualquier fallo revierte todo.
- `SIN_CAMBIO` calcula 50% del importe efectivamente pagado y deja el reembolso
  `PENDIENTE`; no se marca `ENTREGADO` con comprobante vacío o no validable.
- Se añadió `POST /tenant/devoluciones/{operacion_id}/cotizar` y la inspección acepta
  resultados por ítem sin eliminar el contrato simple existente.

## Verificación

| Prueba | Resultado |
|---|---|
| `pytest tests_saas/test_cu15_migracion_corregida.py -q` | PASS (3 controles estáticos) |
| `py_compile` router y schemas CU15 | PASS |
| Pruebas transaccionales PostgreSQL aisladas | SKIP: la migración no se instaló y no se debe mutar Supabase |
| E2E con JWT A/B, concurrencia, stock y 7 días | SKIP por la misma restricción |

Las pruebas ejecutables/guía están en
`database/saas/CU15_PRUEBAS_TRANSACCIONALES.sql`. Incluyen rollback, idempotencia,
M→L, falta de stock, NO_APTO, cobro pendiente, autorización A/B y el límite exacto
de 168 horas; requieren una base de laboratorio, no los 40 registros reales.

## Archivos entregados

- `database/saas/CU15_MIGRACION_FINAL_REVISION.sql`
- `database/saas/CU15_MIGRACION_FINAL_7DIAS_MODELOS_REVISION.sql`
- `database/saas/CU15_PRUEBAS_TRANSACCIONALES.sql`
- `app/modules/devoluciones/api/tenant_cu15_router.py`
- `app/modules/devoluciones/schemas/cu15_schemas.py`
- `tests_saas/test_cu15_migracion_corregida.py`
