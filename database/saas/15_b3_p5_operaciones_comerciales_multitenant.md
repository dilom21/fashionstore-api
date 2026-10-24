# Bloque 3 — Paso 5/7: base segura para operaciones comerciales

**Estado:** FASE DE CONSULTA Y PERTENENCIA del Paso 5 **VALIDADA LOCALMENTE** con 152 pruebas exitosas y auditoría PostgreSQL READ ONLY superada. **Paso 5 completo sigue EN PROCESO**: todavía falta la migración de operaciones comerciales transaccionales.

**Rama exclusiva:** `feat/saas-multitenant-parcial2`. No tocar `main` ni Supabase remoto. Mantener `SAAS_AUTH_ENABLED=false`.

## Hallazgo arquitectónico
Los flujos de CU15–CU25 involucran inventario, reservas, ventas, cobros, devoluciones y procedimientos almacenados/triggers de PostgreSQL. Habilitar directamente las rutas heredadas o reproducir precipitadamente esos procedimientos en Python crearía riesgo de operaciones cruzadas, inventario inconsistente, doble pago y auditoría incompleta. En la base PostgreSQL local del Bloque 2 las 48 FK tenant refuerzan la integridad, pero no impiden una lectura `SELECT` sin filtro de empresa.

**Decisión:** migrar primero el acceso de consulta y prevalidación de propietario de los seis dominios. Las transacciones de reserva, venta, pago, compra, devolución y movimiento de stock siguen **bloqueadas (503)** en modo SaaS. Solo se habilitaron las operaciones CU15 de carrito con validación tenant propias de las subfases 5B/5C; todavía necesitan integración PostgreSQL y revisión transaccional completa. Esto NO completa todavía los casos de uso comerciales en SaaS.

## Cambios implementados en esta fase
1. `app/core/tenant_commercial.py`: `TenantComercialRepository` de lectura con SQLAlchemy Core, tenant obligatorio positivo, `INNER JOIN` de padres con `padre.empresa_id = empresa_id` y `hija.empresa_id = empresa_id`. No admite un tipo de recurso arbitrario: catálogo cerrado de seis familias.
2. `app/modules/ventas/api/tenant_commercial_router.py`: `GET /tenant/comercial/{tipo}` y `GET /tenant/comercial/{tipo}/{recurso_id}` con `tipo` limitado a `carritos|reservas|ventas|pagos|compras|devoluciones`. Solo devuelve `id` y `estado` (sin monto, total, PII, token ni otros recursos relacionados). Paginación 1–100; recurso ajeno o inexistente devuelve indistintamente 404.
3. `get_current_tenant_admin` ya implementado exige JWT/membresía y rol ADMINISTRADOR en contexto personal (sin alterar permisos globales). Los clientes y empleados normales no reciben acceso a estas vistas administrativas.
4. `app/core/saas_route_gate.py`: allowlist exacta solo para GET de estos seis tipos y IDs positivos sin ceros iniciales. Todos los POST/PATCH/DELETE/PUT comerciales heredados siguen dando 503 en modo SaaS.
5. `app/main.py`: registra el router separado del servicio de ventas legado.
6. `tests_saas/test_tenant_comercial_consultas.py`: SQLite en memoria con dos empresas ficticias y seis dominios. Se prueban 404 por ID ajeno, GET propio, cambio de contexto, manipulación `?empresa_id=`, falta de rol, métodos cerrados, paginación, JWT ausente, flag apagado y un pago cuyo `empresa_id` no coincide con el de la venta padre.
7. `database/saas/08_verificar_consultas_comerciales_local.sql` y `.ps1`: auditoría de **solo lectura** sobre cinco empresas reales de la copia local, con `BEGIN TRANSACTION READ ONLY`, conteos históricos, control de las seis tablas y relaciones padre, cierre `ROLLBACK`.

## Relaciones comerciales cubiertas por las consultas
| Recurso empresarial | Padre(s) cuyos `empresa_id` deben coincidir |
|---|---|
| Carritos | Cliente y sucursal |
| Reservas | Cliente y sucursal |
| Ventas | Sucursal |
| Pagos | Venta |
| Compras / órdenes | Proveedor y sucursal |
| Devoluciones | Venta |

Los detalles, stock, líneas de compra, pagos electrónicos y procesamiento financiero no quedan habilitados por esta fase.

## Ejecución local
En PowerShell con entorno conda `fashionstore-api` y rama feature:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado real recibido de PowerShell:** `152 passed, 2 warnings in 3.48s` (115 previas y 37 nuevas), 0 fallos. Advertencias deprecatorias de `starlette.testclient` y `HTTP_422_UNPROCESSABLE_ENTITY`, sin impacto en el resultado.

Solo si pasan, ejecutar la auditoría en la copia PostgreSQL local:

```powershell
.\database\saas\08_verificar_consultas_comerciales_local.ps1
```

Requiere contraseña `postgres` LOCAL. Destino fijo: `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`. El SQL no modifica registros, no ejecuta procedimientos con efectos ni cambia suscripciones.

## Continuación: subfase 5B de CU15 VALIDADA

La documentación específica de la siguiente subfase es
[`16_b3_p5b_carrito_transaccional_tenant.md`](16_b3_p5b_carrito_transaccional_tenant.md).

Se agregó exclusivamente `POST /tenant/carritos/items` para el perfil de cliente en su empresa, con transacción, validación de inventario y stock disponible, pero sin reservar ni descontar existencias. **Subfase 5B validada:** el estudiante ejecutó `176 passed, 2 warnings in 3.93s` y un preflight PostgreSQL READ ONLY sobre cinco empresas, con `ROLLBACK` y sin modificaciones. Esto **NO prueba una escritura real contra PostgreSQL**. El Paso 5 continúa abierto; no reinterpretar este avance como la habilitación de reservas, ventas, pagos, compras o devoluciones.

## Continuación: subfase 5C de CU15 VALIDADA en SQLite

Se implementaron `PATCH /tenant/carritos/{carrito_id}/items/{detalle_id}`,
`DELETE /tenant/carritos/{carrito_id}/items/{detalle_id}` y
`DELETE /tenant/carritos/{carrito_id}`, siempre con cliente y tenant
validados, límite de dos horas y sin movimientos de stock. Se agregaron
27 pruebas nuevas en SQLite en memoria y **la ejecución real del estudiante superó `203 passed, 2 warnings in 4.77s`** (176 previas + 27 nuevas). La guía específica está en
[`17_b3_p5c_edicion_eliminacion_carrito_tenant.md`](17_b3_p5c_edicion_eliminacion_carrito_tenant.md).
Aun con esas pruebas aprobadas, el Paso 5 seguirá abierto hasta migrar
reservas, ventas, pagos, compras, devoluciones y procedimientos transaccionales.

## Continuación: subfase 5D de CU15 VALIDADA en SQLite

Se implementó la **lectura exclusiva del carrito por el propio cliente**:
`GET /tenant/carritos` y `GET /tenant/carritos/{carrito_id}`. Ambos exigen
cliente activo y `empresa_id` consistente en el carrito, sucursal y cadena
completa de las líneas. Solo realizan `SELECT`, sin expirar carritos ni
modificar stock; rechazan con 409 un carrito con referencias cruzadas
deliberadamente corruptas, sin mostrar información parcial.

Se añadieron 28 pruebas nuevas al fixture SQLite ficticio. **El estudiante las ejecutó y obtuvo `231 passed, 2 warnings in 5.16s`**, 0 fallos. Consultar
[`18_b3_p5d_lectura_carritos_cliente.md`](18_b3_p5d_lectura_carritos_cliente.md).
El Paso 5 continúa en proceso; **no abrir reservas, pagos, ventas ni otros
procedimientos** hasta validarlos con PostgreSQL de forma segura.

## Trabajo PENDIENTE dentro de la migración comercial
- Implementar transacciones de carrito, reserva, venta, pago, orden de compra, devolución, detalles e inventarios para cada tenant, conservando semántica y triggers del primer parcial.
- Revisar y migrar los SP `sp_*` y sus llamadas con verificación empresarial **dentro del procedimiento** o en operaciones atómicas, no solo como verificación previa HTTP.
- Hacer pruebas de operaciones comerciales en PostgreSQL local usando `ROLLBACK` y cuentas de prueba seguras; verificar simultaneidad, reservas de stock, doble cargo y auditoría.
- Aplicar permisos `usuario_empresa` por función/acción a roles distintos del administrador; vistas de cliente limitadas a su `cliente_id` propio, no permitir listas globales del tenant por ser miembro.
- Desplegar RLS y usar un rol de conexión sin `BYPASSRLS`; falta verificar SELECT/UPDATE cross-tenant en la conexión real.
- No activar `SAAS_AUTH_ENABLED`, no usar base SaaS como backend legacy, no ejecutar `supabase db push`.

**Criterio de esta fase:** aprobar pytest + SQL READ ONLY; la implementación completa de casos de uso comerciales seguirá pendiente incluso si ambas pruebas pasan. No proceder a otro paso sin revisión de la salida y el próximo `siguiente`.

## Evidencia de auditoría y cierre de subfase (salida entregada por el estudiante)

La ejecución de `08_verificar_consultas_comerciales_local.ps1` confirmó conexión a la base local `fashionstore_restore_20261023_193554_594` en `127.0.0.1:5432`, dentro de `BEGIN TRANSACTION READ ONLY`. El servidor devolvió los seis avisos `PROPIEDAD OK` (`carrito`, `reserva`, `venta`, `pago`, `orden_compra`, `devolucion`), `RELACIONES OK: seis dominios sin cruces empresariales` y `DATOS ORIGINALES OK: Vanter Men conserva ventas, pagos y reservas`.

**Valores efectivamente observados:** `vanter-men` tiene 48 carritos, 11 reservas, 40 ventas, 30 pagos, 4 órdenes de compra y 4 devoluciones. `denim-house`, `elegancia-sur`, `sportline` y `urbanwear` tienen 0 registros en cada una de las seis tablas. El SQL finalizó con `ROLLBACK` y PowerShell informó `AUDITORIA COMERCIAL LOCAL SUPERADA (SIN ESCRITURAS)`.

**Resultado del Paso 5:** se han validado las subfases **5A (lectura administrativa y pertenencia), 5B (alta de líneas de carrito en SQLite), 5C (modificación y eliminación de carrito en SQLite) y 5D (lectura del carrito del propio cliente en SQLite)**. El Paso 5 sigue **EN PROCESO**, y el Bloque 3 permanece en **4/7 pasos finalizados**, hasta adaptar y probar las transacciones de los seis módulos. La evidencia no verifica RLS con rol sin `BYPASSRLS`, ni operaciones financieras, ni login real contra PostgreSQL con cuentas demo. Quedan prohibidos los despliegues y la activación de `SAAS_AUTH_ENABLED`.

**Siguiente intervención:** cuando el estudiante escriba `siguiente`, continuar exclusivamente dentro del Paso 5, comenzando por revisión de reglas, dependencias y transacciones de reserva sobre PostgreSQL sin cambios remotos. No saltar al Paso 6.

## Evidencia incorporada de la subfase 5C

El estudiante sincronizó la rama feature hasta `cacaa06` y ejecutó `python -m pytest -q tests_saas` con **203 pruebas aprobadas, 0 fallos y 2 advertencias deprecatorias en 4.77 s**. No se realizó ninguna escritura o prueba nueva en PostgreSQL durante esta subfase. Las 48 FK y la coherencia de stock de la copia local habían sido verificadas previamente mediante el preflight de solo lectura 5B. La subfase 5C se da por validada **solo en su alcance probado**. El Paso 5 sigue EN PROCESO hasta finalizar reservas, ventas, pagos, compras, devoluciones y ensayos PostgreSQL pertinentes.

## Evidencia incorporada de la subfase 5D

El estudiante actualizó mediante `git pull --ff-only` hasta `de12222` y ejecutó `python -m pytest -q tests_saas` con **231 passed, 2 warnings in 5.16s**. Las 28 pruebas nuevas comprueban lectura aislada de carrito y líneas, paginación, subtotales y ausencia de mutaciones con identidades empresariales ficticias. **No se ejecutaron operaciones PostgreSQL en 5D.** El Paso 5 sigue EN PROCESO porque aún no están migradas y comprobadas las transacciones de reservas, ventas, pagos, compras, devoluciones y RLS/integración PostgreSQL con rol sin `BYPASSRLS`.
