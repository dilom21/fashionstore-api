# Bloque 3 / Paso 5I — Sandbox PostgreSQL separado (esquema public, SIN DATOS)

**Estado:** **SUBFASE 5I VALIDADA** tras recuperar el sandbox vacío: `357 passed, 2 warnings in 6.37s`, recuperación controlada correcta y verificación PostgreSQL READ ONLY independiente con `ROLLBACK`. **Paso 5 global EN PROCESO**, Bloque 3 4/7. Todavía NO se probaron RLS con rol restringido ni procedimientos de reservas.

**Rama EXCLUSIVA:** `feat/saas-multitenant-parcial2`. No tocar `main`, Supabase, ni ejecutar escrituras sobre `fashionstore_restore_20261023_193554_594`. Mantener `SAAS_AUTH_ENABLED=false` y las rutas reales de reservas, ventas y pagos bloqueadas.

## 1. Objetivo y límites

La fase 5H ya confirmó `332 passed, 2 warnings in 6.49s` y verificó el modo PLAN sin cambios. Para probar después los futuros SP tenant-aware en un entorno REAL de PostgreSQL, se necesita una base **físicamente separada** de la restaurada. En 5I solo se prevé prepararla **vacía y con objetos de esquema**, sin usuarios, contraseñas hash de la tabla `usuario`, productos, clientes, inventarios ni transacciones históricas. El esquema puede contener **definiciones de funciones, procedimientos heredados, triggers y políticas RLS**, pero no se deben invocar ni habilitar desde el backend.

**NO se instalan los tres archivos `.sql.txt` de P5G**, no se hace `CALL`, no hay seed ficticio todavía y no se concede `EXECUTE` al rol SaaS.

### Fuentes y destinos inmutables

| Función | Host/puerto | Base de datos | Operaciones permitidas en 5I |
|---|---|---|---|
| Origen restaurado | `127.0.0.1:5432` | `fashionstore_restore_20261023_193554_594` | Lectura validada y `pg_dump --schema-only --schema=public` exclusivamente |
| Destino nuevo | `127.0.0.1:5432` | `fashionstore_saas_reservas_sandbox_5h` | `createdb --template=template0` y `pg_restore --no-owner --no-acl` del esquema vacío, solo si el usuario ejecuta ambos switches |
| Supabase | NUNCA | Proyecto remoto de producción | **Ninguna operación** |

La copia restaurada **no se utiliza como TEMPLATE**. El script falla cerrado si el origen no tiene 5 empresas, 11 reservas, 274 inventarios o 48 FK tenant, si no es localhost, si ya existe el sandbox o si existe un dump temporal previo. Nunca ejecuta `DROP DATABASE` ni sobrescribe una base existente.

## 2. Controles nuevos incorporados a los scripts

`database/saas/13_preparar_sandbox_pg_reservas_local.ps1`:

1. **Sin parámetros** hace únicamente modo plan, no se conecta a PostgreSQL ni pide contraseña.
2. **Creación solo mediante DOS switches expresos** `-CrearSandbox -ConfirmoCopiaSinDatos`; esta ejecución es la autorización consciente del estudiante para crear exclusivamente la base NUEVA. Si no quiere crear una base local todavía, no debe ejecutar esa segunda modalidad.
3. Comprueba conexión al origen, historial esperado y que no exista el destino.
4. `pg_dump --schema-only --schema=public --format=custom` hacia TEMP, usando `default_transaction_read_only=on` en la conexión al origen.
5. **Inspecciona el índice del archivo** con `pg_restore --list` antes de crear DB; aborta si detecta secciones `TABLE DATA`, `MATERIALIZED VIEW DATA` o `BLOB`.
6. Crea la base de ensayo nueva desde `template0`; restaura el esquema con `--no-owner --no-acl --exit-on-error`. Este paso escribe **solo en la nueva base**, no en la restaurada.
7. Valida que cuatro tablas sensibles estén vacías y ejecuta además el nuevo verificador integral de solo lectura. **Solo imprime éxito si ambos aprueban**. Si falla, **NO borra automáticamente la base parcial**; informar salida y no usarla.
8. El dump temporal se limpia al salir; contraseña local leída como `SecureString`, restauración de variables de proceso. No se muestra, copia ni sube a GitHub.

`database/saas/14_verificar_sandbox_pg_reservas_local.sql` y `.ps1`:

- Se conectan **solo** al destino sandbox en localhost con `BEGIN TRANSACTION READ ONLY`, `default_transaction_read_only=on`, `ROLLBACK`.
- Verifican presencia de tablas empresariales, que **todas las tablas ordinarias del esquema `public` están vacías** (no solo cuatro), al menos 40 tablas de FashionStore, **48 FK tenant validadas**, flags de RLS en 6 tablas y que los **3 SP `sp_*_tenant` aún no existen**.
- Imprimen exclusivamente conteos y propiedades del rol, no PII. La comprobación de `relrowsecurity` solo verifica metadatos, **NO demuestra aislamiento de filas**. El usuario `postgres` puede omitir RLS por ser superusuario con `BYPASSRLS`.

**Advertencia:** una copia solo de esquema puede incluir código de funciones con configuraciones o literales; no garantiza que el esquema completo carezca de secretos incrustados. No compartir el dump ni subirlo a GitHub. El script lo borra al finalizar. Si la restauración falla por extensiones, roles, funciones, triggers o dependencias, detenerse y compartir solo la salida del error, sin `DROP` ni reintentos destructivos.

## 3. Pruebas antes de cualquier creación

PowerShell en `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultados reales:** inicialmente `345 passed, 2 warnings in 6.14s`; después de las 12 pruebas estáticas de recuperación, **`357 passed, 2 warnings in 6.37s`** (0 fallos). No se conecta a PostgreSQL ni ejecuta comandos shell durante la suite.

Si pytest **no** pasa, no ejecutar el modo creación. Reportar fallo.

## 4. Creación opcional, EXPRESA y local de la base de ensayo

Después de aprobar las pruebas, y **solo si deseas crear ya la base NUEVA de ensayo**, ejecutar:

```powershell
.\database\saas\13_preparar_sandbox_pg_reservas_local.ps1 -CrearSandbox -ConfirmoCopiaSinDatos
```

**Este comando SÍ escribe en tu servidor PostgreSQL local, exclusivamente en una base nueva.** No ejecutarlo si la nueva base no es deseada. Pedirá contraseña `postgres` LOCAL. El script aborta si existe previamente el destino; nunca lo sobrescribe.

**No repetir este comando de creación: la base ya existe parcialmente.** Utilizar solo la secuencia de recuperación documentada en la nueva sección 7. Para una base de ensayo NUEVA futura, un éxito contendría:

```text
SANDBOX PG OK: ... tablas vacias, 48 FK tenant, 6 flags RLS, 0 SP tenant.
ROLLBACK
SANDBOX LOCAL DE ESQUEMA PREPARADO (SIN DATOS HISTORICOS)
```

Si hubo fallo o se creó una base parcial, **no eliminar bases manualmente ni repetir con nombres distintos**; informar la salida antes de cualquier reparación. La copia original debe seguir intacta.

## 5. Verificación independiente read-only del destino (después de creación exitosa)

```powershell
.\database\saas\14_verificar_sandbox_pg_reservas_local.ps1
```

Debería terminar en `SANDBOX LOCAL DE ESQUEMA VERIFICADO (READ ONLY)`. Este comando nunca crea ni destruye bases.

**Solicitamos tres salidas:** `pytest`, script de creación opcional y verificador READ ONLY. Si eliges no crear todavía, enviar solo la suite y el modo plan, y 5I quedará pendiente de creación.

## 6. Trabajo pendiente (fuera de 5I)

- Crear cuentas y empresas **ficticias** solo en sandbox, sin copiar datos de producción.
- Revisar privilegios/RLS/políticas con usuario de ensayo sin superusuario, sin `BYPASSRLS` y no propietario de tablas.
- Corregir y ensayar SP `sp_*_tenant` exclusivamente en sandbox, con `REVOKE EXECUTE FROM PUBLIC`, roles mínimos y pruebas transaccionales concurrentes de stock, dobles reservas, cancelaciones, rollback, Kardex y cruces de empresa.
- Mantener `SAAS_AUTH_ENABLED=false`; no habilitar reservas reales, pagos, compras, ventas, devoluciones ni avanzar a Paso 6.

**No dar por validada la creación del sandbox ni las transacciones PostgreSQL hasta ver la salida real del estudiante.**

## 7. Incidente real y recuperación controlada sin borrados

El estudiante obtuvo **345 passed, 2 warnings in 6.14s** y autorizó el creador mediante ambos switches. La exportación del esquema terminó, pero `pg_restore` abortó con `ERROR: ya existe el esquema «public»` al intentar `CREATE SCHEMA public`. La base de ensayo NUEVA quedó creada parcialmente y **NO se considera apta**. La BD original fue utilizada para lectura y exportación, no para DML.

PostgreSQL 18 ya incluye el esquema `public` al crear una base desde `template0`. El dump `--schema-only --schema=public` contiene otra entrada para crearlo. Se actualizó el preparador original para excluir **solo** la entrada `SCHEMA - public` mediante `pg_restore --use-list`, y ahora usa `--single-transaction --exit-on-error` para deshacer íntegramente cualquier importación fallida. **Esto no habilita repetir la creación sobre una base existente.**

Se añadieron los archivos `15_inspeccionar_sandbox_parcial_local.sql` / `.ps1` (diagnóstico READ ONLY, cero escrituras), `16_recuperar_sandbox_parcial_local.ps1` (recuperación explícita de la MISMA base existente, sin DROP ni `--clean`) y `tests_saas/test_sandbox_recuperacion_5i.py` (12 pruebas estáticas). El script de recuperación exige dos switches, comprueba los conteos del origen en READ ONLY, la identidad exacta de la base local, y **cero relaciones, rutinas, tipos y extensiones** en el esquema `public` de la base parcial. Se niega a restaurar si encuentra cualquier objeto. Si está vacía, importa SOLO el esquema, usando `--single-transaction` y validación integral READ ONLY de 48 FK, 6 flags RLS y ausencia de SP tenant.

### Ejecutar en PowerShell

Primero sincronizar la rama y volver a ejecutar la suite (resultado esperado, NO confirmado: **357 passed**):

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

Solo si pytest pasa, inspeccionar el sandbox con consulta READ ONLY:

```powershell
.\database\saas\15_inspeccionar_sandbox_parcial_local.ps1
```

**Si la inspección NO muestra exactamente `SANDBOX_PARCIAL_VACIO_APTO_PARA_RECUPERACION_CONTROLADA`, DETENERSE y enviar salida.** No borrar nada ni volver a crear bases. Incluso si muestra resultado apto, la recuperación es opcional y necesita autorización expresa mediante estos dos switches:

```powershell
.\database\saas\16_recuperar_sandbox_parcial_local.ps1 -RecuperarSandboxParcial -ConfirmoSandboxVacio
```

Este comando **SÍ instala objetos de esquema únicamente en la base sandbox existente**. No modifica el origen ni ejecuta `sp_*_tenant`, pero podría fallar por dependencias del esquema; `--single-transaction` revierte el restore. No usar `DROP SCHEMA`, `dropdb`, `--clean` ni repetir indiscriminadamente. Si hay error, detenerse y compartir salida.

Solo después de una recuperación exitosa, verificar de manera independiente:

```powershell
.\database\saas\14_verificar_sandbox_pg_reservas_local.ps1
```

**5I CERRADA en su alcance comprobado:** verificador confirmó **46 tablas vacías, 48 FK, 6 indicadores RLS, 0 SP tenant instalados** y `ROLLBACK`. Esto no valida el funcionamiento de RLS en un rol sin `BYPASSRLS`, ni las transacciones reales de reserva. Conservar `SAAS_AUTH_ENABLED=false`, `main` y Supabase sin cambios.

## 8. Evidencia final recibida: sandbox recuperado y verificado

El estudiante sincronizó la rama feature a `b0e65ba` y ejecutó `python -m pytest -q tests_saas`: **357 passed, 2 warnings in 6.37s**, 0 fallos. La inspección READ ONLY del sandbox parcial reportó **0 relaciones, 0 rutinas, 0 tipos y 0 extensiones en `public`**, con resultado `SANDBOX_PARCIAL_VACIO_APTO_PARA_RECUPERACION_CONTROLADA` y `ROLLBACK`.

Con los dos switches expresos de `16_recuperar_sandbox_parcial_local.ps1`, el estudiante autorizó restaurar únicamente objetos del esquema en la base de ensayo existente `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`, sin datos históricos. El script confirmó la integridad del origen y del destino vacío antes del restore. Finalizó con **`RECUPERACION SANDBOX LOCAL SUPERADA (SIN DATOS HISTORICOS)`**.

El verificador integrado y la ejecución independiente posterior de `14_verificar_sandbox_pg_reservas_local.ps1` coincidieron: **46 tablas vacías, 48 FK tenant validadas, 6 flags RLS y cero SP tenant instalados**, ambos concluyeron en `ROLLBACK`. La verificación externa concluyó con `SANDBOX LOCAL DE ESQUEMA VERIFICADO (READ ONLY)`.

**Límites de certificación:** el rol consultante fue `postgres` con `rolsuper=t`, `rolbypassrls=t`. No se ha verificado aislamiento RLS efectivo con rol no privilegiado; tampoco se han instalado `sp_*_tenant` ni realizado pruebas reales de transacciones, concurrencia, Kardex o roles en PostgreSQL. El origen restaurado se empleó solo para lectura/exportación y no recibió DML; la única restauración fue en el sandbox separado.

**Decisión:** cerrar la subfase **5I VALIDADA**; Paso 5 comercial permanece EN PROCESO. No ejecutar más operaciones ahora; esperar `siguiente` antes de preparar usuarios y datos ficticios con seguridad RLS en sandbox. Conservar `SAAS_AUTH_ENABLED=false`, `main` y Supabase intactos.
