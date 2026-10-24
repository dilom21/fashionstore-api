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

## Continuación: subfase 5E de CU16 VALIDADA en prevalidación y auditoría

La revisión del repositorio legacy halló que la llamada
`sp_crear_reserva_desde_carrito(carrito_id, fecha_atencion, observacion,
usuario_id, reserva_id)` **no incluye empresa_id**. El código fuente SQL
completo del procedimiento no aparece versionado en GitHub; no se debe
suponer que verifica aislamiento en el stock.

Como avance limitado, se incorporó exclusivamente
`POST /tenant/reservas/prevalidar`, con perfil de cliente empresarial,
carrito y líneas propias, productos activos, disponibilidad calculada
`stock_actual - stock_reservado` y revisión de reserva duplicada.
**NO crea ni cancela reservas, NO mueve stock ni llama SP**; es una
fotografía preliminar sin garantía de disponibilidad futura. Se
agregaron 31 pruebas nuevas a SQLite, **aprobadas por el estudiante en su ejecución de `262 passed, 2 warnings in 6.11s`** (231 anteriores + 31 nuevas).

La guía [`19_b3_p5e_prevalidacion_reserva_y_sp.md`](19_b3_p5e_prevalidacion_reserva_y_sp.md)
incluye una auditoría READ ONLY de firmas y texto de los 3 SP en
PostgreSQL local. La auditoría local terminó con `ROLLBACK` y halló tres SP CU16/CU18: **ninguno menciona literalmente `empresa_id` ni `app.empresa_id`** (`f`, `f`), los tres mencionan `FOR UPDATE` y `stock_reservado` (`t`, `t`). Esto requiere revisión del SQL completo; los marcadores no acreditan seguridad tenant.
**El Paso 5 permanece EN PROCESO**. La reserva real, cancelación, venta,
pago y modificaciones de stock siguen cerradas en modo SaaS.

## Continuación: subfase 5F de CU16/CU18 VALIDADA como revisión SQL

La auditoría 5E terminó con **262 pruebas superadas y 3 SP detectados**
y luego el estudiante proporcionó los tres cuerpos SQL íntegros, ahora revisados.
Los SP originales no tienen menciones literales de `empresa_id` ni
`app.empresa_id`, aunque sí `FOR UPDATE` y `stock_reservado`.
No habilitar escritura SaaS apoyándose solo en esas marcas de texto.

Se prepararon:
- `database/saas/11_extraer_definiciones_reserva_solo_lectura.sql`: extraer el texto `pg_get_functiondef`, propietario, `SECURITY DEFINER`, privilegios y `search_path` con `READ ONLY` y `ROLLBACK`.
- `database/saas/11_extraer_definiciones_reserva_solo_lectura.ps1`: exportar a TEMP local y fuera de GitHub, sin ejecutar SP ni migraciones.
- `database/saas/20_b3_p5f_diseno_sp_reserva_tenant_y_revision.md`: contrato propuesto para nuevas transacciones de reserva, cancelación y finalización, **no implementadas**.
- `tests_saas/test_reserva_procedimientos_revision_5f.py`: 18 pruebas estáticas ejecutadas junto a 262 anteriores: **280 passed, 2 warnings in 6.40s**, sin fallos.

Esta subfase **ya validó revisión/diseño y extracción de definiciones**, pero **NO implementó ni validó seguridad funcional de reservas**. Los SP vigentes, inventario, RLS y datos restaurados permanecen intactos; `SAAS_AUTH_ENABLED=false`, sin deploy ni escrituras. Esperar confirmación del estudiante antes de diseñar SQL nuevo según las funciones reales.

## Continuación: subfase 5G de CU16/CU17/CU18 VALIDADA como borradores estáticos y preflight READ ONLY (NO INSTALADA)

Tras la revisión real de los cuerpos SQL en 5F, se crearon **tres borradores
inertes de nuevos SP multitenant** (`.sql.txt`) exclusivamente dentro de
`database/saas/borradores`. Su código introduce `p_empresa_id`,
verificación de usuario/membresía/suscripción/perfil o empleado y permisos
según CU16/CU17/CU18, `empresa_id` explícito en los INSERT y UPDATE,
control de estados y stock, `FOR UPDATE` sobre recursos propios, Kardex
y un `REVOKE ... FROM PUBLIC` destinado a la futura migración.

**Los borradores NO se ejecutaron, instalaron ni probaron con PostgreSQL**.
Se detectó un posible deadlock por el orden desigual de locks entre la
operación de carrito 5B y el borrador de conversión a reserva; exige
corrección y prueba dinámica antes de habilitar transacciones. Además,
quedan por validar permisos efectivos, triggers, RLS y autenticación.

Se incorporaron 40 pruebas **solo estáticas** y un preflight de **solo lectura** de columnas, constraints, triggers y rol RLS de PostgreSQL local. El estudiante confirmó **`320 passed, 2 warnings in 6.30s`** y **`PREFLIGHT SP TENANT LOCAL SUPERADO (SIN ESCRITURAS)`** con `ROLLBACK`. La subfase 5G queda **validada solo en este alcance estático y de metadatos**, sin ejecutar los nuevos procedimientos.

Documento de 5G:
[`22_b3_p5g_borradores_sp_tenant.md`](22_b3_p5g_borradores_sp_tenant.md).
El Paso 5 sigue abierto; las reservas reales y ventas/pagos/compras/
devoluciones continúan bloqueadas en SaaS, sin avance al Paso 6.

## Continuación: subfase 5H de CU15/CU16 VALIDADA en pruebas SQLite y modo plan

Se reordenó `CarritoTenantRepository.agregar_item` en CU15: bloquea primero el
cliente y luego el **carrito propio**, antes de adquirir el bloqueo de
inventario. Al fallar validación de stock/inventario, el router revierte el
carrito recién creado junto con cualquier expiración, sin alterar stock.
Esta secuencia se acerca al orden del borrador de CU16
`sp_crear_reserva_tenant`: **carrito -> inventario**.

Se añadieron cuatro pruebas que inspeccionan el orden de SQL emitido y el
rollback en SQLite, y ocho pruebas estáticas del preparador de sandbox.
**Resultado confirmado: `332 passed, 2 warnings in 6.49s`, 0 fallos.**

`database/saas/13_preparar_sandbox_pg_reservas_local.ps1` es una
herramienta **futura** para preparar una copia LOCAL SOLO DE ESQUEMA, sin
filas históricas, en una base distinta. Por defecto funciona solo en
**modo plan sin conexiones ni contraseña**; el estudiante lo ejecutó sin parámetros y obtuvo `MODO PLAN SIN CAMBIOS: aun NO se creo ninguna base`. Requiere un doble opt-in para
crear nada y **NO se autoriza ejecutar ese modo** en 5H. La nueva guía
[`23_b3_p5h_orden_bloqueos_y_sandbox_pg.md`](23_b3_p5h_orden_bloqueos_y_sandbox_pg.md)
contiene el alcance, riesgos y comandos seguros.

**Precaución:** esto reduce una inversión de orden conocida, pero **no
demuestra ausencia de deadlocks en PostgreSQL**. Siguen sin examinarse
triggers/locks implícitos mediante pruebas concurrentes con rol restringido.
No se instalaron SP, no se ejecutó DML sobre PostgreSQL restaurado y se
mantiene `SAAS_AUTH_ENABLED=false`, reservas reales bloqueadas 503,
`main` y Supabase intactos. **Paso 5 sigue EN PROCESO**.

## Continuación: subfase 5I — SANDBOX SEPARADO PREPARADO (pendiente de ejecución)

Se reforzó el preparador de sandbox de la subfase 5H. Ahora exporta
exclusivamente el esquema `public` con `pg_dump --schema-only`, revisa
el índice del archivo antes de crear nada y llama obligatoriamente a
`14_verificar_sandbox_pg_reservas_local.sql` antes de declarar el
destino apto. El verificador recorre **todas** las tablas ordinarias
`public` exigiendo cero filas, comprueba 48 FK tenant, flags RLS en
seis tablas y ausencia de los tres `sp_*_tenant`.

La base destino permitida es únicamente
`127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`; el origen
`fashionstore_restore_20261023_193554_594` está protegido para
lectura. Crear el destino **es opcional y requiere que el estudiante
ejecute expresamente los dos switches de consentimiento**. Por defecto
el script continúa siendo un modo plan SIN cambios. No se ha ejecutado
la creación por ChatGPT, no se ha sembrado información, no se instalaron
SP, ni se ha activado SaaS.

Se incorporaron 13 pruebas estáticas en `tests_saas/test_sandbox_reservas_5i.py`: el estudiante confirmó **`345 passed, 2 warnings in 6.14s`** (332 anteriores + 13 nuevos). Sin embargo, la creación del sandbox FALLÓ en `CREATE SCHEMA public`; el destino existe parcialmente, no apto para uso. Se añadió corrección de `pg_restore --use-list` para no recrear `public` y `--single-transaction` para evitar restauraciones parciales futuras. Se preparó diagnóstico READ ONLY y reparación sin DROP para esa base preexistente, más 12 pruebas estáticas adicionales; nuevo total esperado **357 passed**, pendiente de ejecución. La guía y los comandos están en
[`24_b3_p5i_creacion_controlada_sandbox_pg.md`](24_b3_p5i_creacion_controlada_sandbox_pg.md).

Aunque se cree la copia de esquema, los SP heredados, RLS efectivo,
transacciones y bloqueos concurrentes **NO quedarán validados**; usar
un rol no superusuario sin `BYPASSRLS` y datos ficticios será trabajo
posterior. **Paso 5 global EN PROCESO, Bloque 3 en 4/7 pasos.**

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

**Resultado del Paso 5:** se han validado las subfases **5A (lectura administrativa y pertenencia), 5B (alta de líneas de carrito en SQLite), 5C (modificación y eliminación de carrito en SQLite), 5D (lectura del carrito del propio cliente en SQLite) y 5E (prevalidación de reserva en SQLite y auditoría de SP PostgreSQL READ ONLY)**. El Paso 5 sigue **EN PROCESO**, y el Bloque 3 permanece en **4/7 pasos finalizados**, hasta adaptar y probar las transacciones de los seis módulos. La evidencia no verifica RLS con rol sin `BYPASSRLS`, ni operaciones financieras, ni login real contra PostgreSQL con cuentas demo. Quedan prohibidos los despliegues y la activación de `SAAS_AUTH_ENABLED`.

**Siguiente intervención:** cuando el estudiante escriba `siguiente`, continuar exclusivamente dentro del Paso 5, comenzando por revisión de reglas, dependencias y transacciones de reserva sobre PostgreSQL sin cambios remotos. No saltar al Paso 6.

## Evidencia incorporada de la subfase 5C

El estudiante sincronizó la rama feature hasta `cacaa06` y ejecutó `python -m pytest -q tests_saas` con **203 pruebas aprobadas, 0 fallos y 2 advertencias deprecatorias en 4.77 s**. No se realizó ninguna escritura o prueba nueva en PostgreSQL durante esta subfase. Las 48 FK y la coherencia de stock de la copia local habían sido verificadas previamente mediante el preflight de solo lectura 5B. La subfase 5C se da por validada **solo en su alcance probado**. El Paso 5 sigue EN PROCESO hasta finalizar reservas, ventas, pagos, compras, devoluciones y ensayos PostgreSQL pertinentes.

## Evidencia incorporada de la subfase 5D

El estudiante actualizó mediante `git pull --ff-only` hasta `de12222` y ejecutó `python -m pytest -q tests_saas` con **231 passed, 2 warnings in 5.16s**. Las 28 pruebas nuevas comprueban lectura aislada de carrito y líneas, paginación, subtotales y ausencia de mutaciones con identidades empresariales ficticias. **No se ejecutaron operaciones PostgreSQL en 5D.** El Paso 5 sigue EN PROCESO porque aún no están migradas y comprobadas las transacciones de reservas, ventas, pagos, compras, devoluciones y RLS/integración PostgreSQL con rol sin `BYPASSRLS`.

## Evidencia incorporada de la subfase 5E

El estudiante actualizó la rama hasta `eff4ee3` y ejecutó `python -m pytest -q tests_saas`: **262 passed, 2 warnings in 6.11s**, sin fallos. Después auditó `sp_crear_reserva_desde_carrito`, `sp_cancelar_reserva` y `sp_finalizar_reserva_sin_compra` mediante `10_auditar_procedimientos_reservas_local.ps1`, exclusivamente en PostgreSQL local, `BEGIN TRANSACTION READ ONLY` y `ROLLBACK`. Los tres SP existen y tienen referencias textuales a `FOR UPDATE` y `stock_reservado`, pero **no presentan coincidencias literales `empresa_id` ni `app.empresa_id`**. Las cinco empresas y 48 FK tenant se conservaron. `vanter-men` mantiene 11 reservas, 16 líneas y 3 unidades reservadas, las otras cuatro 0 reservas/0 líneas/0 unidades reservadas. **No se ejecutaron SP ni se cambió stock**.

**Resultado:** 5E validada en alcance de prevalidación e inspección read-only, **Paso 5 EN PROCESO**. No habilitar reservas reales, cancelar, finalizar, ventas ni pagos hasta examinar el cuerpo SQL íntegro, añadir controles empresariales transaccionales y verificar en PostgreSQL aislado con pruebas autorizadas. Esperar `siguiente`; sin activación SaaS, sin modificar `main` ni Supabase.

## Evidencia incorporada de la subfase 5F

El estudiante ejecutó con éxito **280 tests, 2 advertencias en 6.40s** y la extracción READ ONLY de `sp_crear_reserva_desde_carrito`, `sp_cancelar_reserva` y `sp_finalizar_reserva_sin_compra` desde la copia PostgreSQL local. Se recibió el archivo de 780 líneas completo, con las tres definiciones `CREATE OR REPLACE PROCEDURE`, datos de privilegios y `ROLLBACK` final.

La inspección REAL confirmó ausencia de `empresa_id` en todas las tres definiciones, `p_usuario_id` utilizado solo como autor del movimiento de Kardex, `INSERT` sin `empresa_id`, falta de validación de las 2 horas del carrito, `SECURITY INVOKER` (no definer), propietario `postgres`, y `EXECUTE` a `PUBLIC`. Se mantienen `FOR UPDATE` y reglas de disponibilidad/liberación originales. No se declara una explotación confirmada; permisos efectivos, RLS y triggers requieren auditoría. Informe específico: [`21_b3_p5f_hallazgos_sql_legacy.md`](21_b3_p5f_hallazgos_sql_legacy.md).

**5F VALIDADA dentro de revisión y diseño, Paso 5 aún EN PROCESO.** Próxima subfase, únicamente cuando el estudiante diga `siguiente`, será preparar versiones `sp_*_tenant` seguras y ensayos aislados, no desplegar ni activar nada. `SAAS_AUTH_ENABLED=false`, `main` y Supabase intactos.

## Evidencia incorporada de la subfase 5G

El estudiante ejecutó `git pull --ff-only origin feat/saas-multitenant-parcial2` (hasta `5c6e21e`) y `python -m pytest -q tests_saas` desde PowerShell: **320 pruebas aprobadas, 0 fallos, 2 advertencias en 6.30 s**. Las 40 pruebas nuevas son estáticas sobre tres borradores inertes `.sql.txt`, validación de filtros por empresa y bloqueo de rutas; **no ejecutaron SQL PL/pgSQL real**.

El preflight `12_preflight_borradores_reservas_local.ps1` se realizó exclusivamente en `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594` y confirmó **5 empresas, 48 FK tenant, 57 columnas de metadatos, 77 restricciones, 23 índices y 8 triggers no internos habilitados** en las tablas consultadas. La auditoría cerró en `ROLLBACK` y notificó **`PREFLIGHT SP TENANT LOCAL SUPERADO (SIN ESCRITURAS)`**. Se preservaron CHECK de stock y UNIQUE `uq_reserva_carrito`.

**Bloqueador RLS:** el usuario de PostgreSQL observado fue `postgres` con `rolsuper=t` y `rolbypassrls=t`. Las seis tablas comerciales revisadas tienen `relrowsecurity=t` y `relforcerowsecurity=f`, con propietario `postgres`. **La comprobación no certifica las políticas ni el aislamiento RLS efectivo en el rol de aplicación**. Falta revisar triggers internos, permisos efectivos, corregir orden de bloqueos entre carrito y reserva y ejecutar ensayos de los SP nuevos en PostgreSQL separado con rol sin `BYPASSRLS` antes de habilitar reservas SaaS.

**Cierre 5G: VALIDADA como preparación estática y auditoría READ ONLY**, sin instalación ni DML, pero **Paso 5 global EN PROCESO (Bloque 3 sigue 4/7)**. No ejecutar los tres borradores `.sql.txt`, no desplegar ni activar `SAAS_AUTH_ENABLED`, no modificar `main` ni Supabase. Esperar `siguiente` para la continuación controlada dentro del Paso 5.

## Evidencia incorporada de la subfase 5H

Se recibió salida PowerShell del estudiante con `git pull --ff-only` hasta `947d848`, seguida de la suite SaaS completa: **`332 passed, 2 warnings in 6.49s`** (320 anteriores y 12 casos nuevos), sin fallos. El script `13_preparar_sandbox_pg_reservas_local.ps1` se ejecutó **sin parámetros** y emitió `MODO PLAN SIN CAMBIOS: aun NO se creo ninguna base`; no solicitó contraseña, no abrió conexión de escritura ni creó el sandbox propuesto. Sigue intacta la base restaurada de PostgreSQL y la instancia Supabase.

**5H VALIDADA únicamente como corrección del orden de SQL emitido por CU15 y revisión estática/modo plan del script local**. No se ha comprobado la ausencia de deadlocks en PostgreSQL ni se han ejecutado los borradores `sp_*_tenant`. Mantener los endpoints transaccionales de reserva bloqueados y `SAAS_AUTH_ENABLED=false`. **Paso 5 EN PROCESO; Bloque 3 en 4/7 pasos**, esperando la palabra `siguiente` antes de una posible fase de entorno PostgreSQL de ensayo independiente y pruebas con rol sin `BYPASSRLS`.

## Incidente y corrección de 5I — RESTORE PENDIENTE

Resultado local recibido: `345 passed, 2 warnings in 6.14s`, 0 fallos. El usuario ejecutó con los dos switches `13_preparar_sandbox_pg_reservas_local.ps1`; `pg_dump --schema-only` terminó, `createdb` creó `fashionstore_saas_reservas_sandbox_5h`, pero `pg_restore` se detuvo ante `ERROR: ya existe el esquema «public»` / `CREATE SCHEMA public`. Es un conflicto entre la entrada SCHEMA del dump y el `public` predeterminado de PostgreSQL 18/template0. La fuente restaurada no recibió DML. **No repetir el creador ni borrar la base parcial.**

Corrección en rama SaaS: el creador ahora filtra exactamente la entrada `SCHEMA - public` mediante `pg_restore --use-list`, conservando los demás objetos, y usa `--single-transaction --exit-on-error`. Nuevo `15_inspeccionar_sandbox_parcial_local.ps1` realiza inspección READ ONLY. Nuevo `16_recuperar_sandbox_parcial_local.ps1` permite, solo con doble opt-in explícito y comprobación de **cero objetos en `public`**, importar los objetos de esquema en la misma base parcial SIN `DROP`, SIN `--clean` ni recreación de DB. Ejecuta verificación final READ ONLY. Si el sandbox no está absolutamente vacío de objetos, **la reparación se rechaza**.

Las 12 pruebas de regresión de recuperación son estáticas y están pendientes (`357 passed` esperado). **5I EN PROCESO** hasta ver resultados de las nuevas pruebas, inspección READ ONLY y verificador de sandbox tras recuperación autorizada. Continúan cerrados los SP/routers de reservas y RLS real no verificado; `SAAS_AUTH_ENABLED=false`, `main` y Supabase intactos. Guía detallada: [`24_b3_p5i_creacion_controlada_sandbox_pg.md`](24_b3_p5i_creacion_controlada_sandbox_pg.md).
