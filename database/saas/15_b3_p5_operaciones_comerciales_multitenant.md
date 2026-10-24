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

## Incidente y corrección de 5I — RECUPERACIÓN COMPLETADA

Resultado local recibido: `345 passed, 2 warnings in 6.14s`, 0 fallos. El usuario ejecutó con los dos switches `13_preparar_sandbox_pg_reservas_local.ps1`; `pg_dump --schema-only` terminó, `createdb` creó `fashionstore_saas_reservas_sandbox_5h`, pero `pg_restore` se detuvo ante `ERROR: ya existe el esquema «public»` / `CREATE SCHEMA public`. Es un conflicto entre la entrada SCHEMA del dump y el `public` predeterminado de PostgreSQL 18/template0. La fuente restaurada no recibió DML. **No repetir el creador ni borrar la base parcial.**

Corrección en rama SaaS: el creador ahora filtra exactamente la entrada `SCHEMA - public` mediante `pg_restore --use-list`, conservando los demás objetos, y usa `--single-transaction --exit-on-error`. Nuevo `15_inspeccionar_sandbox_parcial_local.ps1` realiza inspección READ ONLY. Nuevo `16_recuperar_sandbox_parcial_local.ps1` permite, solo con doble opt-in explícito y comprobación de **cero objetos en `public`**, importar los objetos de esquema en la misma base parcial SIN `DROP`, SIN `--clean` ni recreación de DB. Ejecuta verificación final READ ONLY. Si el sandbox no está absolutamente vacío de objetos, **la reparación se rechaza**.

Las 12 pruebas de regresión de recuperación son estáticas y quedaron **aprobadas junto con toda la suite: `357 passed, 2 warnings in 6.37s`**. La inspección READ ONLY confirmó que la base parcial no tenía objetos en `public`; después de la recuperación autorizada, el verificador integrado y el independiente confirmaron 46 tablas vacías, 48 FK tenant, 6 flags RLS y 0 SP tenant, terminando en `ROLLBACK`. **5I VALIDADA en su alcance de sandbox de esquema vacío**, sin autorizar aún operaciones transaccionales. Continúan cerrados los SP/routers de reservas y RLS real no verificado; `SAAS_AUTH_ENABLED=false`, `main` y Supabase intactos. Guía detallada: [`24_b3_p5i_creacion_controlada_sandbox_pg.md`](24_b3_p5i_creacion_controlada_sandbox_pg.md).


## Evidencia final incorporada: subfase 5I VALIDADA

El estudiante confirmó `357 passed, 2 warnings in 6.37s` en Windows, sin fallos. Su inspección de solo lectura de `fashionstore_saas_reservas_sandbox_5h` mostró **cero relaciones, rutinas, tipos y extensiones**, con `SANDBOX_PARCIAL_VACIO_APTO_PARA_RECUPERACION_CONTROLADA` y `ROLLBACK`. A continuación ejecutó explícitamente el recuperador con `-RecuperarSandboxParcial -ConfirmoSandboxVacio`. El script autorizó la importación solo después de comprobar origen local intacto y destino `public` vacío, y finalizó **`RECUPERACION SANDBOX LOCAL SUPERADA (SIN DATOS HISTORICOS)`**.

La verificación READ ONLY integrada y la independiente posterior coincidieron: **46 tablas públicas vacías, 48 FK tenant validadas, 6 indicadores RLS, 0 procedimientos `sp_*_tenant` instalados**, con `ROLLBACK` y `SANDBOX LOCAL DE ESQUEMA VERIFICADO (READ ONLY)`. La comprobación se realizó con `postgres` superusuario (`rolsuper=t`, `rolbypassrls=t`): **NO confirma aislamiento RLS efectivo ni concurrencia transaccional**. Ningún SP nuevo se instaló o ejecutó, los endpoints reales de reserva permanecen cerrados en SaaS, la fuente restaurada no recibió DML y `SAAS_AUTH_ENABLED=false`.

**5I CERRADA** como preparación/verificación de sandbox; **Paso 5 sigue EN PROCESO y Bloque 3 en 4/7 pasos**. Continuar únicamente cuando el estudiante escriba `siguiente`: diseño de roles PostgreSQL restringidos y datos académicos ficticios en sandbox antes de ensayar SP; preservar `main` y Supabase.


## Continuación: subfase 5J — Auditoría de roles RLS y dependencias de seed (PREPARADA)

Tras la validación de 5I (suite **357 passed, 2 warnings in 6.37s**, sandbox independiente con 46 tablas vacías, 48 FK tenant, seis flags RLS y cero SP nuevos), se preparó una auditoría **EXCLUSIVAMENTE READ ONLY** para averiguar las políticas, privilegios y columnas efectivas que necesitarán un futuro rol no superusuario y dos tenants académicos ficticios.

Archivos nuevos:
- `17_auditar_roles_rls_y_seed_sandbox_local.sql`: solo sandbox `fashionstore_saas_reservas_sandbox_5h`, `BEGIN TRANSACTION READ ONLY`, guardas 46 tablas / 48 FK / cero SP nuevos / tablas centrales vacías; reporta `pg_policies`, RLS, PUBLIC grants, roles globales con prefijo de ensayo, columnas obligatorias, FK y triggers, `ROLLBACK`.
- `17_auditar_roles_rls_y_seed_sandbox_local.ps1`: conexión localhost, password local, `default_transaction_read_only=on`, salida de auditoría únicamente al TEMP local de Windows y NO al repositorio.
- `tests_saas/test_rls_roles_seed_preflight_5j.py`: 13 tests estáticos nuevos; **370 passed, 2 warnings in 5.91s** confirmados por el estudiante.
- `25_b3_p5j_preflight_rls_y_seed_ficticio.md`: criterios de seguridad y diseño preliminar de roles/datos inventados, sin DDL ni DML.

**Advertencia:** `CREATE ROLE` es GLOBAL a todo el clúster PostgreSQL, no solo a una base. En 5J no se crea ninguno; el rol futuro requiere `NOSUPERUSER, NOBYPASSRLS, NOLOGIN` (para ensayo sin autenticación) o un LOGIN restringido autorizado aparte, y grants/políticas sujetos a una inspección previa. No confiar en `postgres` superusuario para pruebas RLS ni conceder BYPASSRLS. El seed futuro usará identidades inventadas, previa revisión de columnas NOT NULL y triggers; **NO SEED en 5J**.

**5J VALIDADA COMO AUDITORÍA READ ONLY Y DICTAMEN DEL INFORME: 20/20 tablas RLS habilitado, 0 políticas, sin rol de ensayo y sin privilegios PUBLIC en las 10 tablas muestreadas. Paso 5 global EN PROCESO; Bloque 3 4/7**. Mantener reservas reales cerradas y `SAAS_AUTH_ENABLED=false`; `main`, Supabase y base restaurada intactos. Esperar salida de PowerShell y el informe TEMP revisado antes de avanzar con roles/datos en sandbox. Guía: [`25_b3_p5j_preflight_rls_y_seed_ficticio.md`](25_b3_p5j_preflight_rls_y_seed_ficticio.md).


## Evidencia histórica de consola: auditoría 5J (informe analizado posteriormente)

El estudiante sincronizó `8844e47` y obtuvo **`370 passed, 2 warnings in 5.91s`** (0 fallos). Ejecutó en PostgreSQL local el preflight `17_auditar_roles_rls_y_seed_sandbox_local.ps1`, que emitió **`5J PREFLIGHT OK: 46 tablas, 48 FK tenant, cero SP SaaS, sin datos centrales`** y **`AUDITORIA 5J SANDBOX READ ONLY COMPLETADA`**. El informe se guardó en TEMP local con el nombre `fashionstore_5j_rls_roles_columnas_sandbox_LOCAL.txt`.

**Actualización:** el informe TEMP fue entregado y examinado íntegramente; las conclusiones están documentadas en la sección siguiente. La auditoría de consola por sí sola no demostraba RLS efectivo, y el informe completo tampoco: no hay ninguna política definida en las 20 tablas revisadas. No hubo DDL/DML ni instalación de SP; mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase y la base restaurada sin cambios.


## Dictamen final 5J: informe completo recibido y analizado

Se recibió el archivo de auditoría READ ONLY del sandbox (`BEGIN`, `SET`, `DO`, final `ROLLBACK`), que permite **cerrar 5J únicamente como inspección y diseño**, no como validación operativa RLS. La suite previa confirmó **370 passed, 2 warnings in 5.91s**.

- **RLS:** **20 tablas empresariales y de identidad inspeccionadas** tienen `relrowsecurity=t`, `relforcerowsecurity=f` y propietario `postgres`; todas mostraron **0 políticas**. El detalle `pg_policies` devolvió **0 filas**. En un rol sujeto a RLS rige denegación por defecto; `postgres` sigue evitando la barrera (`rolsuper=t`, `rolbypassrls=t`). **Impedimento para validar `sp_*_tenant SECURITY INVOKER` con rol restringido** hasta diseñar políticas y grants mínimos.
- **Roles y privilegios:** sin roles con prefijo `fashionstore_5j_%`. La muestra de **10 tablas** auditadas mostró **sin GRANT a PUBLIC**, sin extrapolarlo a todas las 46 tablas o a roles concretos.
- **Dependencias del futuro seed ficticio:** **26 tablas** descritas (columnas, NOT NULL, defaults e identidad); **136 constraints** (PK, FK, UNIQUE, CHECK) y **10 triggers activos** descritos en la selección del SQL 5J. `empresa_id` es obligatorio en todas las relaciones empresariales de la ruta de carrito/reserva; `cliente` y `empleado` deben referenciar `usuario_empresa` del mismo tenant. `usuario.password_hash`, rol, plan y suscripción son dependencias; el trigger de correo, los de bitácora, sucursal/actividad y actualización de inventario requieren auditoría de sus **cuerpos SQL**, no solo la firma del trigger.
- **Decisión:** no crear aún `CREATE ROLE` (global al clúster), `CREATE POLICY`, `GRANT`, seed, SP ni rutas transaccionales. La próxima subfase deberá preparar el diseño de permisos con rol `NOSUPERUSER/NOBYPASSRLS`, la política `USING/WITH CHECK`, lectura de funciones de trigger y datos inventados exclusivamente para el sandbox. Los argumentos de tenant o variables SQL definibles por el llamador no prueban identidad sin un modelo de confianza validado.

**5J VALIDADA para auditoría read-only + dictamen.** **Paso 5 EN PROCESO; Bloque 3 4/7.** El sandbox permanece sin seed ni procedimientos nuevos; `SAAS_AUTH_ENABLED=false`, `main`, Supabase y PostgreSQL restaurado intactos. Se detiene el trabajo hasta `siguiente`. Informe detallado: [`25_b3_p5j_preflight_rls_y_seed_ficticio.md`](25_b3_p5j_preflight_rls_y_seed_ficticio.md).


## Continuación: subfase 5K — auditoría de cuerpos de triggers y diseño de permisos restringidos (PREPARADA)

Después de cerrar 5J con **370 passed, 2 warnings in 5.91s** y el dictamen de **20/20 tablas RLS habilitadas pero ninguna política**, se prepararon archivos nuevos para revisar el código SQL original de los triggers y los privilegios efectivos sobre rutinas, tablas y secuencias **solo dentro del sandbox** `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`.

- `18_auditar_cuerpos_triggers_y_privilegios_sandbox.sql`: guardas 46 tablas, 48 FK tenant, cero políticas, cero `sp_*_tenant`, sin seed central; `BEGIN TRANSACTION READ ONLY`, `ROLLBACK`. Lista triggers/funciones y extrae sus cuerpos con `pg_get_functiondef` sin ejecutarlas, consulta `SECURITY DEFINER`, owner, `search_path`/proconfig, `EXECUTE` a PUBLIC y privilegios de roles existentes.
- `18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1`: cliente `psql` sobre sandbox local, obliga `default_transaction_read_only=on` y guarda salida en TEMP Windows, revisable antes de compartir.
- `26_b3_p5k_cuerpos_triggers_diseno_rls_privilegios.md`: matriz de permisos deseados, modelo de confianza de la API (contexto token validado vs. parámetros SQL modificables) y prueba futura de acceso cruzado, rollback y concurrencia; no otorga privilegios ni crea policies.
- `tests_saas/test_trigger_privilegios_preflight_5k.py`: **14 pruebas nuevas ESTÁTICAS**, confirmadas junto con la suite completa: **384 passed, 2 warnings in 6.05s**.

**5K VALIDADA exclusivamente como suite estática, extracción READ ONLY y análisis de los cuerpos reales recibidos.** No se crearon roles globales ni LOGIN, no se otorgaron GRANT, no se instalaron políticas RLS, triggers, procedimientos nuevos ni seed. Continúan prohibidas las operaciones reales de reserva; `SAAS_AUTH_ENABLED=false`, `main`, Supabase y PostgreSQL restaurado intactos. No avanzar Paso 6. Revisar los cuerpos originales antes de cualquier plan SQL ejecutable o acceso de rol restringido.


## Evidencia final y dictamen 5K — triggers/privilegios PostgreSQL

El estudiante sincronizó la rama a `89dfea6` y ejecutó la suite SaaS en Windows: **`384 passed, 2 warnings in 6.05s`** (cero fallos). La auditoría `18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1` en `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h` mostró `5K PREFLIGHT OK: sandbox, 46 tablas, 48 FK, 0 policies, 0 SP tenant, sin seed principal`; completó `ROLLBACK` sin mutaciones. El estudiante adjuntó el contenido del informe TEMP para su revisión, que ya fue examinada.

**Resultados reales:** diez triggers activos examinados sobre seis funciones distintas. Todas las seis funciones son `SECURITY INVOKER` (`prosecdef=f`), propiedad de `postgres`. Dos carecen de `SET search_path` propio: `fn_actualizar_actividad_carrito` y `fn_validar_sucursal_detalle_carrito`. El primero realiza `UPDATE public.carrito` desde cambios a `detalle_carrito` y afecta permisos/locks. `fn_registrar_bitacora` inserta en `public.bitacora`, deriva empresa de la fila y actor de `current_setting('app.usuario_id',true)`: este GUC es modificable y no constituye identidad confiable. Los triggers de validación de sucursal solo comparan sucursal, no `empresa_id` explícitamente; FK compuestas tenant complementan la integridad. El trigger de inventario impide cambios a sucursal/variante/temporada, pero no impide expresamente cambios de `empresa_id` ni valida derechos o movimientos de stock.

**ACL:** diez rutinas examinadas tienen **`EXECUTE` concedido a PUBLIC**, comprendiendo seis funciones de trigger y cuatro procedimientos heredados `sp_crear_reserva`, `sp_crear_reserva_desde_carrito`, `sp_cancelar_reserva`, `sp_finalizar_reserva_sin_compra`. Este permiso NO demuestra capacidad de leer/modificar tablas bajo rol restringido, pero requiere cierre/revisión antes de habilitar accesos SQL. Se catalogaron catorce secuencias de identidad con propietario `postgres` y ACL `<DEFAULT>`. Entre roles consultados solo apareció `postgres`, superusuario que evita RLS; no se certificó una credencial limitada. Otras funciones relacionadas con inventario/bitácora se inspeccionaron solo por metadatos, no por todo su cuerpo.

**5K CERRADA** estrictamente como auditoría READ ONLY y diseño, sin crear roles globales, políticas, grants, seed ni instalar/invocar SP tenant. **Paso 5 sigue EN PROCESO y Bloque 3 en 4/7 pasos.** Trabajo siguiente tras `siguiente`: auditar `bitacora`, diseñar políticas `USING/WITH CHECK` y permisos mínimos, revisar cuerpos auxiliares y decidir una estrategia de ensayo con rol `NOSUPERUSER/NOBYPASSRLS` sin credenciales privilegiadas, todo exclusivamente dentro del sandbox con autorización expresa si implica DDL/DML. `SAAS_AUTH_ENABLED=false`, `main`, Supabase y restaurada local intactos. Guía completa: [`26_b3_p5k_cuerpos_triggers_diseno_rls_privilegios.md`](26_b3_p5k_cuerpos_triggers_diseno_rls_privilegios.md).


## Continuación: subfase 5L — bitácora, función auxiliar de IP y diseño RLS (PREPARADA)

Tras la validación de **5K** en Windows (`384 passed, 2 warnings in 6.05s`) y examen íntegro de los cuerpos de seis funciones de trigger y diez rutinas con `EXECUTE PUBLIC`, se detectó que `fn_registrar_bitacora()` inserta directamente en `public.bitacora` y obtiene el actor a través del parámetro mutable `app.usuario_id`; `fn_completar_ip_bitacora()` fue identificada únicamente por metadatos. **No se ha certificado la seguridad de auditoría ni los permisos de una cuenta SQL restringida**.

5L incorpora exclusivamente artefactos de **consulta READ ONLY**:
- `19_auditar_bitacora_y_rls_sandbox.sql`: guardas de base y host localhost, 46 tablas vacías (incluida bitácora), 48 FK tenant, 0 policies comerciales examinadas, 0 SP tenant. `BEGIN TRANSACTION READ ONLY` / `ROLLBACK`. Extrae columnas/constraints/indexes/estado RLS/policies/ACL/triggers de `bitacora`, cuerpos originales de `fn_registrar_bitacora` y `fn_completar_ip_bitacora`, `EXECUTE` a `PUBLIC`, roles existentes y metadatos de secuencias.
- `19_auditar_bitacora_y_rls_sandbox.ps1`: lectura exclusivamente de `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h` con `PGOPTIONS=default_transaction_read_only=on` y salida temporal de Windows; no sobrescribe informes previos.
- `27_b3_p5l_bitacora_rls_permisos_readonly.md`: matriz de permisos mínimos, diseño de identidad confiable, y límites de políticas `USING/WITH CHECK` para futura prueba de rol `NOSUPERUSER/NOBYPASSRLS`.
- `tests_saas/test_bitacora_rls_preflight_5l.py`: **16 casos ESTÁTICOS** nuevos; el estudiante confirmó la suite completa **400 passed, 2 warnings in 6.48s**.

**5L VALIDADA exclusivamente como pruebas estáticas, auditoría READ ONLY y dictamen del reporte íntegro**. No se creó rol global, `CREATE POLICY`, GRANT, seed, procedimiento nuevo ni se ejecutó `CALL`. `SAAS_AUTH_ENABLED=false`, rutas reales de reserva bloqueadas, `main`, Supabase y BD restaurada intactos. **Paso 5 global EN PROCESO, Bloque 3 4/7**. La guía y comandos están en [`27_b3_p5l_bitacora_rls_permisos_readonly.md`](27_b3_p5l_bitacora_rls_permisos_readonly.md).


## Evidencia final 5L — 400 tests y dictamen técnico sobre bitácora

El estudiante actualizó la rama a `ac411b5`, ejecutó `python -m pytest -q tests_saas` y obtuvo **`400 passed, 2 warnings in 6.48s`**, sin fallos. El script de preflight PostgreSQL exclusivo del sandbox `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h` informó `5L PREFLIGHT OK: sandbox local, 46 tablas vacias (incl. bitacora), 48 FK, 0 policies comerciales, 0 SP tenant`, terminó en `ROLLBACK` y guardó informe. Se recibió y analizó el archivo completo sin ejecutar DDL/DML.

- **Tabla `bitacora`:** 8 columnas, 7 restricciones, 4 índices, secuencia IDENTITY `bitacora_id_seq`, propietario `postgres`. `usuario_id` y `empresa_id` son nullable y poseen FK independientes hacia `usuario` y `empresa`, **no una FK conjunta que verifique la pertenencia del actor al tenant**. Esto requiere política de eventos empresariales y tratamiento explícito para eventos globales.
- **RLS:** `bitacora` tiene `relrowsecurity=t`, `relforcerowsecurity=f` y **cero policies**, al igual que otras siete tablas de identidad/comercio consultadas. La ACL de `bitacora` lista únicamente ocho privilegios de `postgres`, sin concesión a `PUBLIC`. La auditoría de roles efectivos solo encontró `postgres` (`rolsuper=t`, `rolbypassrls=t`); **no está probado ningún rol SQL restringido**.
- **Trigger IP:** `trg_bitacora_completar_ip` (BEFORE INSERT) llama `fn_completar_ip_bitacora()`, `SECURITY INVOKER`, `search_path=public, pg_temp`. Si `NEW.ip` es NULL/vacía, la función copia `current_setting('app.ip', TRUE)` sin verificar si es IPv4/IPv6 real ni que venga del proxy de confianza; conserva una IP no vacía suministrada explícitamente. La longitud varchar(45) no autentica IP.
- **Auditoría de actor:** `fn_registrar_bitacora()`, también `SECURITY INVOKER`, obtiene `app.usuario_id` modificable por sesión y `empresa_id` desde `NEW/OLD`, luego inserta en `bitacora`. La autenticidad de actor/tenant no está garantizada por estos dos parámetros o las FK individuales; ambas funciones muestran `EXECUTE PUBLIC=t`, aunque eso no suple grants sobre tabla ni políticas RLS.
- **Implicación:** antes de ensayar SP de reserva de tipo `SECURITY INVOKER`, diseñar permisos INSERT/RLS WITH CHECK seguros para el evento de bitácora (incluido el trigger transitivo), y para UPDATE de `carrito` y movimientos de stock/Kardex; no permitir GUC manipulables como autenticación. Probar cruces A/B, caso sin IP, auditoría inválida y rollback bajo rol sin `BYPASSRLS` SOLO en sandbox y con datos totalmente ficticios cuando se autoricen explícitamente.

**5L CERRADA en alcance de auditoría y diseño**, no como prueba transaccional, RLS o confiabilidad de IP/actor. **Paso 5 global EN PROCESO, Bloque 3 4/7**. No hay roles de ensayo, policies, seed ni SP SaaS instalados; `SAAS_AUTH_ENABLED=false`, `main`, Supabase y base restaurada sin cambios. Guía: [`27_b3_p5l_bitacora_rls_permisos_readonly.md`](27_b3_p5l_bitacora_rls_permisos_readonly.md). Detenerse hasta `siguiente`.


## Continuación: subfase 5M — contrato de identidad/RLS y análisis offline (PREPARADO)

Después del cierre 5L con **`400 passed, 2 warnings in 6.48s`** y el informe de bitácora, se revisaron los archivos de autenticación SaaS, listener de transacciones y los tres SP `SECURITY INVOKER` redactados en 5G. Se identificaron **tres BLOQUEADORES de seguridad** antes de configurar policies reales:

1. **Bootstrap de autenticación:** `tenant_router.login_tenant` autentica usuario y obtiene membresía mediante consultas a `usuario`, `usuario_empresa`, `rol`, `empresa`, `suscripcion`, `plan_saas`, perfiles `cliente/empleado` y sucursal **ANTES** de `set_config('app.empresa_id',...)`. Deny-by-default RLS bloquearía login si no existe ruta dedicada de consulta mínima y no recursiva; una policy general `USING(true)` sería insegura.
2. **Actor y GUC:** `tenant_dependencies` solo aplica `app.empresa_id` centralmente, y `database.after_begin` reaplica tenant e IP, pero no `app.usuario_id` validado. El GUC de actor sirve para auditoría, pero la identidad de la sesión SQL no se demuestra con `set_config`, pues SQL arbitrario puede modificar esos valores.
3. **Suplantación potencial vía argumentos SQL:** los SP tenant comprueban que `p_usuario_id` sea un miembro válido de `p_empresa_id`, pero no vinculan estos argumentos al principal autenticado de la llamada. Si se concediera CALL a clientes SQL o a una credencial compartida vulnerable, un actor podría proporcionar otro par de IDs con membresía válida. `SECURITY INVOKER` tampoco protege contra ese supuesto de confianza equivocado.

Se prepararon **SIN DDL, SIN MIGRACIONES, SIN DATOS y SIN conexión PostgreSQL**:
- `28_b3_p5m_contrato_identidad_politicas_rls_no_ejecutable.md`: modelos comparados (bootstrap dedicado, rol SQL de servicio limitado, separación de login y operación, RLS `USING/WITH CHECK`, bitácora, SP, defensa en profundidad y limitaciones de GUC), matriz por tablas y criterios obligatorios de seguridad.
- `20_revision_estatica_contrato_rls_5m.py`: revisión local del **código fuente solamente**, con guardas de feature flag/routers y lista de dependencias; imprime advertencias y NO importa la API, no lee `.env`, no ejecuta SQL ni instala roles.
- `tests_saas/test_contrato_confianza_rls_5m.py`: **12 pruebas ESTÁTICAS adicionales**. Suite completa confirmada por el estudiante: **`412 passed, 2 warnings in 8.81s`** = 400 anteriores + 12, sin fallos.

**5M VALIDADA en alcance documental y estático, con 5 guardas OK y 5 bloqueantes de seguridad intencionalmente detectados; pendientes de resolución posterior**. Los tres borradores `sp_*_tenant.sql.txt` siguen INACTIVOS; no se han creado roles, GRANT, policies ni seed; `SAAS_AUTH_ENABLED=false`, `main`, Supabase y la BD restaurada sin modificaciones. **Paso 5 global EN PROCESO; Bloque 3 4/7.** No habilitar reservas ni avanzar al Paso 6.


## Evidencia final 5M: 412 pruebas y contrato de confianza RLS (NO implementado)

El estudiante sincronizó la rama feature a `68a0621` y ejecutó en Windows `python -m pytest -q tests_saas`: **`412 passed, 2 warnings in 8.81s`**, 0 fallos. El revisor offline `20_revision_estatica_contrato_rls_5m.py` produjo 5 `GUARDA OK` (flag SaaS inactivo, login con token+membresía, contexto establecido después de autorizar empresa, gate de reservas cerrado y 3 SP tenant inactivos `SECURITY INVOKER`).

Informó **5 `BLOQUEANTE DE DISENO` esperados**: `bootstrap_login_sin_politicas_definidas`, `actor_auditoria_no_automatizado_en_dependencia_saas`, `actor_no_reaplicado_automaticamente_tras_nueva_transaccion`, `args_sp_no_vinculados_al_principal_sql_autenticado` y `guc_empresa_no_es_identidad_independiente`. Terminó con `5M ANALISIS DE CODIGO COMPLETADO; RLS REAL TODAVIA NO VALIDADO`. Estos bloqueantes **no son fallos de pytest y NO están corregidos**; son riesgos de diseño pendientes antes de crear policies `USING/WITH CHECK`, rol SQL restringido o instalar SP tenant.

**5M CERRADA** solo como auditoría estática/diseño de autenticación y RLS, sin ejecutar SQL en PostgreSQL ni crear roles, policies, grants, seed o SP nuevos. El Paso 5 comercial sigue EN PROCESO y Bloque 3 en **4/7 pasos**. `SAAS_AUTH_ENABLED=false` y rutas comerciales transaccionales bloqueadas. `main`, Supabase y BD restaurada sin modificaciones. Informe detallado: [`28_b3_p5m_contrato_identidad_politicas_rls_no_ejecutable.md`](28_b3_p5m_contrato_identidad_politicas_rls_no_ejecutable.md). No avanzar a otra subfase hasta `siguiente`.


## Continuación: subfase 5N — actor de auditoría SaaS entre transacciones (CÓDIGO LISTO, PRUEBAS PENDIENTES)

Después de 5M (`412 passed, 2 warnings in 8.81s`) se abordaron únicamente **dos de los cinco bloqueos de diseño** relativos a la integridad de auditoría:

- `app/core/tenant_dependencies.py`: tras verificar token JWT y membresía empresarial, ahora ejecuta `set_config('app.usuario_id', :usuario_id, true)` y `set_config('app.empresa_id', :empresa_id, true)` con parámetros de SQLAlchemy. Solo cuando ambos ajustes completan correctamente guarda `usuario_id` y `empresa_id` en `Session.info`. Ante fallo no persiste esos marcadores; un token inválido o membresía revocada no debe establecer contexto de identidad.
- `app/core/database.py`: el listener `after_begin` sigue reaplicando IP y empresa, y agrega la reaplicación **opcional** de `app.usuario_id` desde `Session.info` validado. No altera los servicios legacy que no introducen tal marca y no cambia el alcance de `set_config(..., true)` (transacción).
- `tests_saas/test_contexto_actor_tenant_5n.py`: **12 pruebas unitarias aisladas** de aplicación, fallos de autenticación/membresía, errores de set_config, eventos de transacciones y separación de sesiones. Suite completa **`424 passed, 2 warnings in 6.99s` CONFIRMADOS en Windows**, cero fallos.
- `29_b3_p5n_actor_transaccional_saas_y_riesgos_restantes.md`: matriz de dos cuestiones abordadas en código frente a los **tres bloqueos arquitectónicos aún SIN RESOLVER** (`bootstrap_login_sin_politicas_definidas`, `args_sp_no_vinculados_al_principal_sql_autenticado`, `guc_empresa_no_es_identidad_independiente`). El GUC `app.usuario_id` **no es prueba de autenticación SQL** y no autoriza `CALL` desde un usuario de BD arbitrario.

**5N VALIDADA solo como cambio Python y pruebas unitarias; Paso 5 EN PROCESO; Bloque 3 4/7**. Sin creación de roles, grants, policies, seed ni instalación de SP. **Sin conexiones a PostgreSQL en esta subfase**; `SAAS_AUTH_ENABLED=false`, `main`, Supabase y BD restaurada sin cambios. Esperar resultados del estudiante y no iniciar 5O o Paso 6 sin la palabra `siguiente`.


## Resultado final 5N: actor de auditoría en la transacción SaaS

El estudiante sincronizó la rama a `cfa09b0` y confirmó la suite SaaS en Windows: **`424 passed, 2 warnings in 6.99s`**, sin fallos. El revisor offline histórico 5M verificó **5 guardas OK**; las dos advertencias relativas a auditoría (`actor_auditoria_no_automatizado_en_dependencia_saas` y `actor_no_reaplicado_automaticamente_tras_nueva_transaccion`) ahora se imprimen como **`REVISAR CAMBIO`** por los cambios de código 5N. Las tres barreras arquitectónicas aún pendientes son: (1) bootstrap de login con RLS sin políticas, (2) vincular `p_usuario_id`/`p_empresa_id` con el principal SQL autenticado y (3) reconocer que los GUC de sesión no autentican ante SQL arbitrario.

**5N CERRADA solo en alcance código/unit tests**, no RLS real ni transacciones PG. No hubo SQL, roles/policies/grants/seed, ni `CALL` de SP tenant. `SAAS_AUTH_ENABLED=false`, `main`, Supabase, sandbox y base histórica sin cambios. Paso 5 global EN PROCESO y Bloque 3 en **4/7**. No ejecutar acciones adicionales hasta `siguiente`. Informe específico: [`29_b3_p5n_actor_transaccional_saas_y_riesgos_restantes.md`](29_b3_p5n_actor_transaccional_saas_y_riesgos_restantes.md).


## Continuación: subfase 5O — contrato HTTP→SP tenant sin invocación y bootstrap RLS (PREPARADA)

Tras la validación real de 5N (`424 passed, 2 warnings in 6.99s`, 2 diagnósticos de actor cambiados a `REVISAR CAMBIO`) siguen tres barreras pendientes: bootstrap de identidad bajo RLS sin policies, suplantación de `p_usuario_id`/`p_empresa_id` desde SQL arbitrario y el hecho de que `app.empresa_id` es GUC mutable.

**Cambio de aplicación exclusivamente INERTE:** `app/modules/reservas/services/tenant_sp_calls_5o.py` introduce tres funciones constructoras que generan objetos de sentencia SQLAlchemy parametrizada para `sp_crear_reserva_tenant`, `sp_cancelar_reserva_tenant` y `sp_finalizar_reserva_sin_compra_tenant`. **No hay Session.execute/psql, conexión, CALL ni migración**. Solo admiten `TenantContext` como fuente de `empresa_id` y `usuario_id` (no los reciben como argumentos de la petición), filtran `contexto/rol_nombre`, verifican IDs positivos y fecha timezone-aware, y retornan parámetros con mapa inmutable. El futuro backend **deberá** obtener `TenantContext` desde `get_current_tenant_context`, verificar permisos efectivos y garantizar aislamiento en DB; un `TenantContext` instanciable y un GUC mutable NO prueban identidad frente a SQL arbitrario. `INOUT p_reserva_id` y compatibilidad de CALL seguirán requiriendo ensayo real aislado.

**Diseño propuesto sin SQL ejecutable:** `database/saas/30_b3_p5o_contrato_llamadas_y_bootstrap_rls.md` separa rol/credencial de bootstrap del rol de operación empresarial (mínimos privilegios, no superusuarios, NO `BYPASSRLS`), documenta riesgos de credencial SQL compartida y compara modelo backend HTTP confiable con modelo que resista SQL arbitrario. **No se implementaron autenticación SQL, `CREATE ROLE`, `CREATE POLICY`, grants, seed ni SP tenant**.

**Pruebas nuevas:** `tests_saas/test_contrato_invocacion_sp_5o.py` incorpora **16 pruebas unitarias sin PostgreSQL**, con resultado total **CONFIRMADO `440 passed, 2 warnings in 7.17s`** (424 previas +16), cero fallos. **5O VALIDADA solo como código inerte y pruebas unitarias; Paso 5 EN PROCESO, Bloque 3 4/7**. `SAAS_AUTH_ENABLED=false`, `main`, Supabase, sandbox y BD restaurada sin cambios.


## Cierre real 5O — 440 pruebas superadas y tres riesgos de identidad aún abiertos

El estudiante efectuó fast-forward `cfa09b0..73e4de8` en `feat/saas-multitenant-parcial2` y ejecutó `python -m pytest -q tests_saas` en Windows. Resultado observado: **`440 passed, 2 warnings in 7.17s`**, sin fallos; las dos advertencias corresponden a deprecaciones ya conocidas de Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`.

**5O CERRADA** solo en alcance de interfaz Python inerte para preparar tres procedimientos tenant parametrizados con IDs de `TenantContext`. Las funciones no ejecutan `CALL` ni conectan a PostgreSQL y no están integradas a rutas comerciales. Los borradores `sp_*_tenant.sql.txt` siguen **sin instalar**, el sandbox continúa sin roles de ensayo, seed o políticas RLS aplicadas según la última auditoría disponible (NO se re-auditó PostgreSQL en 5O). **No declarar aislamiento SQL verificado**: permanecen sin resolver el bootstrap de login con RLS, la vinculación DB-side del invocador `p_usuario_id`/`p_empresa_id` y la posibilidad de alterar GUC de sesión mediante SQL arbitrario.

**Paso 5 comercial EN PROCESO; Bloque 3 4/7.** `SAAS_AUTH_ENABLED=false`, reserva/pago/venta transaccionales bloqueados. No se ejecutaron SQL ni DDL/DML contra el sandbox, Supabase o la fuente restaurada como parte de 5O; cambios exclusivamente en rama feature, sin tocar `main`. Informe: [`30_b3_p5o_contrato_llamadas_y_bootstrap_rls.md`](30_b3_p5o_contrato_llamadas_y_bootstrap_rls.md). **Esperar `siguiente`** para continuar a otra subfase, sin avanzar aún al Paso 6.
