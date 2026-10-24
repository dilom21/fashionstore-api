# Bloque 3 — Paso 5E: prevalidación de reserva y auditoría de SP PostgreSQL

**Estado:** **SUBFASE 5E VALIDADA** por el estudiante: `262 passed, 2 warnings in 6.11s` y auditoría de metadatos de tres SP PostgreSQL local con `ROLLBACK`, sin escrituras. **Paso 5 comercial global continúa abierto**. Solo la subfase 5E de prevalidación se someterá a prueba: NO se implementa ni habilita la creación, cancelación o finalización real de reservas.

**Rama:** `feat/saas-multitenant-parcial2`. Mantener `SAAS_AUTH_ENABLED=false` y no tocar `main`, Supabase ni PostgreSQL remoto.

## Hallazgo esencial del CU16 heredado

En `app/modules/reservas/repositories/repository.py`, el endpoint legacy termina invocando:

```sql
CALL public.sp_crear_reserva_desde_carrito(
  :carrito_id, :fecha_atencion, :observacion,
  :usuario_id, CAST(:reserva_id AS bigint)
)
```

El código Python del primer parcial describe que el procedimiento incrementa `inventario.stock_reservado`, crea `reserva`, `detalle_reserva` y movimiento `RESERVA`, y marca el carrito `CONVERTIDO` en una sola transacción. También existen llamadas a `sp_cancelar_reserva` y `sp_finalizar_reserva_sin_compra`.

**No se encontró la definición SQL completa de estos procedimientos en los archivos versionados de GitHub.** El `CALL` heredado **no recibe `empresa_id` como argumento**. Por tanto, sin inspeccionar su definición real en la copia PostgreSQL, **NO se puede certificar** que valide el tenant antes de manipular el stock. Los 48 FK compuestos ayudan a bloquear referencias cruzadas, pero no sustituyen una comprobación de propietario dentro de la transacción.

## Implementación segura y acotada

Se creó **solo** `POST /tenant/reservas/prevalidar`. Ejemplo de cuerpo ficticio:

```json
{"carrito_id": 1111, "fecha_atencion": "2026-12-12T15:00:00-04:00"}
```

El formulario conserva `observacion` opcional hasta 500 caracteres, pero no se guarda; los campos adicionales como `empresa_id`, `cliente_id`, `sucursal_id`, `inventario_id` o `cantidad` se rechazan (422). La fecha debe ser futura y se interpreta con la zona horaria recibida (fecha naive: UTC, igual que CU16 legacy).

La prevalidación exige:

1. `Depends(get_current_tenant_cliente)`, membresía JWT validada y existencia de perfil `cliente` activo para el `empresa_id` y `usuario_id` del contexto.
2. Carrito ACTIVO, con menos de 2 horas sin actividad, pertenencia propia y sucursal de la misma empresa. Sin carrito o si es ajeno: 404; vencido/inactivo: 409.
3. Por lo menos una línea, sin referencias corruptas. Para cada línea, correspondencia de `empresa_id` de `detalle_carrito`, `inventario`, `variante_producto`, `producto`, `categoria`, `temporada` y `sucursal`. Talla y color siguen siendo globales. Categoría, producto, variante, sucursal, talla y color deben permanecer activos al momento de la inspección.
4. `cantidad > 0` y `cantidad <= stock_actual - stock_reservado` en cada línea. Rechazo con 409 si no hay suficiente disponibilidad.
5. No debe existir ya `reserva` con `carrito_id` igual (la unicidad histórica es por carrito, no solo por tenant).

Respuesta de ejemplo:

```json
{
  "carrito_id": 1111,
  "sucursal_id": 2222,
  "fecha_atencion": "2026-12-12T19:00:00Z",
  "cantidad_lineas": 2,
  "cantidad_unidades": 3,
  "stock_verificado_preliminarmente": true,
  "reserva_creada": false,
  "requiere_verificacion_transaccional": true
}
```

**ADVERTENCIA DE NEGOCIO:** El endpoint solo hace `SELECT`, sin `FOR UPDATE`, `CALL`, `INSERT`, `UPDATE`, `DELETE`, ni `COMMIT`. Su resultado es una fotografía de disponibilidad, **NO reserva stock ni garantiza que ese stock continúe disponible** después de la consulta. La confirmación definitiva tendrá que revalidar dentro de un SP tenant-aware con bloqueos de PostgreSQL y rollback atómico.

Las rutas `POST /tenant/reservas`, cancelación/finalización y otras operaciones financieras **permanecen BLOQUEADAS (503)** bajo el gate SaaS. Solo se permite `POST /tenant/reservas/prevalidar` además de lo ya migrado en 5A–5D.

## Archivos preparados

- `app/modules/reservas/repositories/tenant_prevalidacion_repository.py`: consultas con filtro de empresa y cliente, cadenas de padres, stock preliminar y duplicado.
- `app/modules/reservas/api/tenant_prevalidacion_router.py`: contratos HTTP específicos, rechazo de extras, dependencia de cliente empresarial.
- `app/main.py`: registra router exclusivo de prevalidación.
- `app/core/saas_route_gate.py`: abre solo la ruta exacta de prevalidación.
- `tests_saas/test_tenant_carrito_transacciones.py`: 31 casos nuevos, con SQLite en memoria y dos empresas ficticias; fixture ampliado a `reserva`.
- `database/saas/10_auditar_procedimientos_reservas_local.sql` y `.ps1`: inspección **SOLO LECTURA** de metadatos reales de los tres SP, firmas y marcas textuales de `empresa_id`, `app.empresa_id`, `FOR UPDATE` y `stock_reservado`. Estas marcas **NO demuestran aislamiento**; su finalidad es guiar la revisión de SQL del siguiente trabajo.

## Comprobación en PowerShell

Desde `C:\SI2_Parcial1\fashionstore-api`, entorno Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado real de PowerShell:** **262 passed, 2 warnings in 6.11s** = 231 pruebas anteriores + 31 nuevas. El usuario actualizó la rama feature hasta `eff4ee3` antes de pytest; 0 fallos. Advertencias deprecatorias conocidas de Starlette/FastAPI.

**Solo si pytest pasa**, inspeccionar los SP de la copia local:

```powershell
.\database\saas\10_auditar_procedimientos_reservas_local.ps1
```

Solicita contraseña de PostgreSQL **local**, destino fijo `127.0.0.1:5432`, DB `fashionstore_restore_20261023_193554_594`. Ejecuta solo `BEGIN TRANSACTION READ ONLY`, consultas a `pg_proc`, `pg_get_functiondef` y recuentos históricos, termina en `ROLLBACK`. No llama a ningún SP y no modifica la base.

La salida mostrará tres procedimientos, sus firmas, y señales booleanas de expresiones SQL. **Incluso si alguna columna sale `t`/verdadero, eso NO equivale a una prueba de seguridad tenant**. Si faltan procedimientos, el script falla cerrado. Compartir la salida para decidir si corresponde un procedimiento nuevo `sp_*_tenant` o refactor del legado exclusivamente sobre una base local de pruebas aislada.

## Pendiente después de esta subfase

- Recuperar/inspeccionar las definiciones completas de los SP del PostgreSQL local y diseñar controles de `empresa_id`, `cliente_id`, sucursal, inventario, bloqueo `FOR UPDATE` y contexto de auditoría **dentro de la operación atómica**.
- Crear y probar SP tenant-aware en una **base de ensayo separada** o transacciones reversibles, solo con autorización de prueba local. Mantener la copia restaurada y Supabase sin escrituras.
- Probar carreras de stock, doble reserva, cancelaciones y movimientos Kardex en PostgreSQL bajo rol no superusuario y sin `BYPASSRLS`.
- Migrar reservas reales y posteriormente los flujos de ventas, pagos, compras, devoluciones, permisos específicos de rol; el Paso 5 y todo el Bloque 3 no quedan terminados.
- Mantener `SAAS_AUTH_ENABLED=false`, sin deploy ni migraciones remotas.

**No avanzar automáticamente:** ambas salidas recibidas y registradas. Detenerse. Solo continuar con `siguiente`, revisando primero el SQL completo de los tres SP y diseñando verificación tenant dentro de la misma transacción, **sin modificar la BD restaurada**.

## Evidencia de ejecución y hallazgo de seguridad de PostgreSQL

El estudiante ejecutó el 23-10-2026 desde PowerShell (Conda `fashionstore-api`) `python -m pytest -q tests_saas`, con **262 pruebas aprobadas, 0 fallos y 2 advertencias en 6.11 s**.

Después ejecutó `10_auditar_procedimientos_reservas_local.ps1` en la **base local** `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`. La auditoría reportó **5 empresas, 48 claves foráneas compuestas tenant y 3 procedimientos CU16/CU18**. La sentencia final fue `ROLLBACK` y PowerShell indicó `AUDITORIA SP RESERVAS LOCAL SUPERADA (SIN ESCRITURAS)`. Ningún procedimiento fue invocado.

| Procedimiento | Firma recuperada | Menciona `empresa_id` | Menciona `app.empresa_id` | Menciona `FOR UPDATE` | Menciona `stock_reservado` |
|---|---|---|---|---|---|
| `sp_crear_reserva_desde_carrito` | `IN p_carrito_id bigint, IN p_fecha_atencion timestamptz, IN p_observacion text, IN p_usuario_id bigint, INOUT p_reserva_id bigint` | `f` | `f` | `t` | `t` |
| `sp_cancelar_reserva` | `IN p_reserva_id bigint, IN p_usuario_id bigint, IN p_observacion text` | `f` | `f` | `t` | `t` |
| `sp_finalizar_reserva_sin_compra` | `IN p_reserva_id bigint, IN p_usuario_id bigint, IN p_observacion text` | `f` | `f` | `t` | `t` |

**Significado:** en el texto de las tres definiciones no aparece ninguna mención literal de `empresa_id` ni de `app.empresa_id`. Sí aparecen `FOR UPDATE` y `stock_reservado`. Es un **indicador de revisión de seguridad pendiente**, no una prueba exhaustiva de que carezcan de toda protección, porque faltan su cuerpo completo y posibles dependencias. Un bloqueo de filas tampoco equivale a autorización multiempresa.

**Datos comerciales observados:** `vanter-men` tiene 11 reservas, 16 `detalle_reserva`, 265 inventarios y 3 unidades de `stock_reservado`. Las otras cuatro empresas tienen 0 reservas, 0 líneas, y 0 unidades reservadas; cuentan respectivamente con 2, 3, 3 y 1 inventarios. Total de inventarios: 274. No se realizaron escrituras.

**Decisión de cierre:** marcar 5E **VALIDADA en el alcance de prevalidación SQLite y auditoría de metadatos READ ONLY**. La creación/cancelación/finalización verdadera de reservas sigue **BLOQUEADA (503)** en SaaS; no habilitar hasta verificar y adaptar controles empresariales dentro de un SP transaccional, evaluar concurrencia de stock y probar sobre un entorno PostgreSQL aislado autorizado. Mantener `SAAS_AUTH_ENABLED=false`, `main` y Supabase intactos.
