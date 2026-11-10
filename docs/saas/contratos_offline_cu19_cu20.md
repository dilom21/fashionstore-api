# FashionStore SaaS — Contratos offline CU19/CU20

Referencia obligatoria para el agente Angular. El backend aplica aislamiento
tenant, permisos, stock real, precios oficiales y ledger idempotente.

## 1. Estado de habilitación

La sincronización offline **no está operativa** hasta que se cumplan TODAS:

1. Migración `database/saas/preparacion_pg17/offline/106_offline_cu19_cu20_PROPUESTA_NO_EJECUTAR.sql`
   instalada y validada en PostgreSQL 17.
2. `SAAS_AUTH_ENABLED=true`.
3. `SAAS_OFFLINE_ENABLED=true` (flag independiente; no reemplaza
   `SAAS_VENTAS_ENABLED`, que pertenece a CU13).
4. `DATABASE_URL` y pools tenant funcionando con JWT real.

Mientras falte 106 o el flag esté apagado, ambos endpoints responden **503**
`Sincronizacion offline no habilitada`.

## 2. Endpoints

### Crear o reintentar una operación

```http
POST /tenant/offline/operaciones
Authorization: Bearer <JWT tenant>
Content-Type: application/json
Idempotency-Key: <mismo UUID que operacion_uuid>   # opcional, si se envía debe coincidir
```

### Recuperar el resultado de una operación

```http
GET /tenant/offline/operaciones/{operacion_uuid}
Authorization: Bearer <JWT tenant>
```

`operacion_uuid` es un UUID inmutable generado por Angular/IndexedDB. Es la
clave de idempotencia. No se acepta `empresa_id`, `usuario_id`, `rol`, `total`,
`precios`, `estado` ni `empleado_id` en el body.

## 3. Contextos y roles

| Tipo | Contexto JWT | Rol permitido | Regla adicional |
|---|---|---|---|
| `RESERVA` | `cliente` | `CLIENTE` | Cliente activo de la empresa; `fecha_atencion` futura con zona horaria |
| `VENTA_CONTADO` | `personal` | `CAJERO` o `ADMINISTRADOR` | CAJERO: empleado activo y sucursal asignada. ADMIN: sucursal activa de la empresa, sin exigir empleado |

Rechazos:

- `ENCARGADO_SUCURSAL`, `CLIENTE`, `SUPERADMINISTRADOR` global, roles
  personalizados o contexto incorrecto → **403**.
- El cliente de `RESERVA` se deriva del JWT; no se acepta `cliente_id`.
- En `VENTA_CONTADO`, `cliente_id` es opcional; si se envía, PostgreSQL
  comprueba que pertenezca a la empresa. No autoriza por sí solo.

## 4. DTO común

```json
{
  "operacion_uuid": "9f4c1e8e-7a2b-4f6c-9d2e-3a1b5c7d9e01",
  "tipo": "RESERVA",
  "sucursal_id": 1,
  "lineas": [
    {"inventario_id": 5, "cantidad": 1},
    {"inventario_id": 7, "cantidad": 2}
  ],
  "fecha_atencion": "2026-11-15T15:30:00-04:00",
  "observacion": "Reserva creada sin conexion"
}
```

Reglas:

- `operacion_uuid`: UUID; se recomienda v4. Inmutable para todos los reintentos.
- `tipo`: `RESERVA` o `VENTA_CONTADO`.
- `sucursal_id`: entero positivo, activo y de la empresa del JWT.
- `lineas`: 1–100; `inventario_id` único por operación; `cantidad` 1–1000.
- No se envían precios ni totales. El backend obtiene precios del servidor y
  PostgreSQL recalcula el total oficial.
- `observacion`: máximo 500 caracteres.

### 4.1 `RESERVA`

Requiere `fecha_atencion` con zona horaria y futura. No acepta
`efectivo_recibido` ni `cliente_id`.

```json
{
  "operacion_uuid": "9f4c1e8e-7a2b-4f6c-9d2e-3a1b5c7d9e01",
  "tipo": "RESERVA",
  "sucursal_id": 1,
  "lineas": [{"inventario_id": 5, "cantidad": 1}],
  "fecha_atencion": "2026-11-15T15:30:00-04:00",
  "observacion": "Reserva offline"
}
```

### 4.2 `VENTA_CONTADO`

No acepta `fecha_atencion`. El único método de pago es `EFECTIVO`.

```json
{
  "operacion_uuid": "1b2c3d4e-5f60-4718-9a2b-3c4d5e6f7081",
  "tipo": "VENTA_CONTADO",
  "sucursal_id": 1,
  "lineas": [{"inventario_id": 5, "cantidad": 1}],
  "efectivo_recibido": "100.00",
  "cliente_id": null
}
```

- `efectivo_recibido` es opcional. Si se envía, debe cubrir el total oficial
  recalculado. Si no lo cubre, la operación se rechaza y el monto queda en el
  ledger para conciliación.
- El pago se registra por el total oficial, no por el importe indicado por
  Angular. El vuelto se calcula como `efectivo_recibido - total`.
- No se crea comprobante definitivo antes de confirmar la venta.

## 5. Respuesta

```json
{
  "operacion_uuid": "9f4c1e8e-7a2b-4f6c-9d2e-3a1b5c7d9e01",
  "tipo": "RESERVA",
  "estado": "ACEPTADA",
  "id_definitivo": 123,
  "resultado": {
    "reserva_id": 123,
    "carrito_id": 456,
    "estado": "PENDIENTE",
    "sucursal_id": 1,
    "fecha_atencion": "2026-11-15T19:30:00+00:00",
    "lineas": [{"inventario_id": 5, "cantidad": 1}]
  },
  "error_codigo": null,
  "error_mensaje": null,
  "reintentable": false
}
```

Para `VENTA_CONTADO`, `resultado` contiene:

```json
{
  "venta_id": 456,
  "pago_id": 789,
  "estado": "COMPLETADA",
  "total": "89.90",
  "sucursal_id": 1,
  "canal": "PRESENCIAL",
  "metodo": "EFECTIVO",
  "lineas": [
    {"inventario_id": 5, "cantidad": 1,
     "precio_unitario": "89.90", "subtotal_linea": "89.90"}
  ],
  "efectivo_recibido": "100.00",
  "vuelto": "10.10"
}
```

## 6. Estados de transporte

| Estado | Significado | HTTP | Reintento |
|---|---|---|---|
| `PENDIENTE` | Operación registrada pero aún no procesada/recuperable | 202 | Sí, consultar por UUID |
| `ENVIANDO` | Procesamiento en curso o bloqueado por concurrencia | 202 | Sí, consultar por UUID |
| `ACEPTADA` | Operación comercial confirmada; resultado durable | 200 | No repetir efectos; se puede consultar |
| `CONFLICTO` | UUID repetido con otro contenido, stock/rama/estado incompatible | 409 | Solo con un UUID nuevo o corrección autorizada |
| `RECHAZADA` | Regla de negocio o autorización rechazó la operación | 403/422 | No con el mismo UUID; corregir y usar UUID nuevo |
| `ERROR_REINTENTABLE` | Fallo técnico transitorio | 503 | Sí, mismo UUID |

Estos estados son de **transporte**. El estado comercial definitivo está en
`resultado.estado` (`PENDIENTE` de reserva o `COMPLETADA` de venta).

## 7. Idempotencia, concurrencia y respuesta perdida

- El ledger `fashionstore_private.offline_operacion` tiene
  `UNIQUE (empresa_id, operacion_uuid)`.
- La operación comercial y la fila del ledger se confirman en la misma
  transacción; un rollback no deja venta, reserva, pago, movimiento ni ledger
  parcial.
- Si dos dispositivos envían el mismo UUID, PostgreSQL serializa con
  `INSERT ... ON CONFLICT` + `SELECT ... FOR UPDATE`; uno procesa y el otro
  recibe el resultado ya confirmado.
- UUID repetido con el mismo contenido y estado `ACEPTADA` devuelve el
  resultado original sin volver a descontar stock.
- UUID repetido con otro contenido → `CONFLICTO` (`UUID_PAYLOAD_MISMATCH`).
- Si la respuesta HTTP se pierde después del commit, Angular debe:
  1. reintentar `POST` con el MISMO UUID y payload, o
  2. llamar `GET /tenant/offline/operaciones/{uuid}`.
  El ledger devolverá `ACEPTADA` y el `id_definitivo` original.

## 8. Códigos de error

| Código | HTTP | Causa |
|---|---|---|
| `NO_AUTORIZADO` | 403 | Actor/rol/contexto no permitido |
| `CLIENTE_NO_ACTIVO` | 403 | RESERVA sin perfil cliente activo |
| `SUCURSAL_NO_AUTORIZADA` | 403 | CAJERO fuera de su sucursal |
| `SUCURSAL_NO_DISPONIBLE` | 409 | Sucursal inexistente/inactiva/ajena |
| `STOCK_O_INVENTARIO` | 409 | Línea inexistente, inactiva, de otra sucursal o sin stock |
| `CONFLICTO_COMERCIAL` | 409 | Restricción de venta/reserva/stock |
| `EFECTIVO_INSUFICIENTE` | 422 | Efectivo recibido menor al total oficial |
| `UUID_PAYLOAD_MISMATCH` | 409 | Mismo UUID con contenido distinto |
| `IDEMPOTENCY_KEY_INVALIDA` | 422 | Header distinto de `operacion_uuid` |
| `OPERACION_RECHAZADA` | 422 | Regla comercial no satisfecha |
| `INTEGRIDAD_COMERCIAL` | 409 | Resultado del SP no coincide con lo esperado |
| `SP_NO_DISPONIBLE` | 503 | Procedimiento/migración no instalado |
| `LEDGER_NO_DISPONIBLE` | 503 | No se pudo registrar/leer el ledger |
| `ERROR_TECNICO` | 503 | Fallo transitorio; reintentar con el mismo UUID |

Un error **nunca** devuelve SQL, DSN, token, contraseña ni detalles internos.

## 9. Inventario de referencia para el dispositivo

El CAJERO/ADMINISTRADOR puede usar la proyección de solo lectura ya existente:

```http
GET /tenant/inventario/pos?sucursal_id=1&disponibilidad=CON_STOCK&limit=50&offset=0
Authorization: Bearer <JWT personal>
```

Devuelve inventario, producto, variante, SKU, talla, color, temporada, precio
de referencia y stock conocido, limitado a la empresa y sucursal autorizadas.
No expone Kardex ni permite escrituras. Para `ADMINISTRADOR`, la sucursal debe
pertenecer a su empresa. Para `CAJERO`, la sucursal es la de su empleado.

La disponibilidad descargada es **solo una pista**: el backend revalida stock,
producto, variante, sucursal y reglas dentro de la transacción.

## 10. Flujo recomendado para Angular/IndexedDB

1. Generar `operacion_uuid` una sola vez y guardarlo con la solicitud.
2. Guardar `tipo`, `sucursal_id`, `lineas`, `fecha_atencion` (RESERVA),
   `efectivo_recibido` (VENTA) y `cliente_id` opcional.
3. Enviar `POST /tenant/offline/operaciones` con el JWT tenant.
4. Ante timeout/red, no generar un UUID nuevo: reintentar el mismo payload.
5. Ante 202, consultar `GET` por UUID antes de reintentar.
6. Ante 409/422, conservar evidencia y no reintentar el mismo UUID; corregir
   datos y generar uno nuevo.
7. Ante 503 `ERROR_REINTENTABLE`, reintentar con backoff y el mismo UUID.
8. Al recibir `ACEPTADA`, guardar `id_definitivo` y `resultado`.

## 11. Límites y decisiones

- No se acepta SQL libre ni parámetros de empresa/usuario desde el dispositivo.
- La reserva offline reconstruye un carrito `origen='OFFLINE'` transitorio y
  usa el procedimiento CU11 existente; no requiere un carrito online previo.
- La venta offline reutiliza los procedimientos CU13
  `sp_crear_venta_presencial_tenant` y
  `sp_registrar_pago_presencial_tenant`; no crea otro sistema paralelo.
- No se rebajan permisos de CU13: la venta presencial online conserva sus
  roles actuales. CU20 aplica una allowlist más estricta solo a la ruta
  offline.
- La migración 106 no está ejecutada en Supabase. La sincronización no debe
  declararse operativa hasta instalarla y repetir el laboratorio.
