# Bloque 3 / Paso 5X — Identificador del clúster original y contraste de PGDATA (solo lectura)

**Estado: 5X VALIDADA Y CERRADA exclusivamente como auditoría READ ONLY de identidad del clúster fuente y comparación textual de PGDATA; clúster destino inexistente y RLS A/B no certificado.** Rama única `feat/saas-multitenant-parcial2`. Paso 5 EN PROCESO, Bloque 3 **4/7**.

## Evidencia previa

5W fue validada con **`560 passed, 2 warnings in 7.14s`**, sin fallos. El diagnóstico Windows reportó: carpeta `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data` `NO_EXISTE_NO_CREADO`, carpeta padre `EXISTE`, `NINGUNO_OBSERVADO` para reparse points en ancestros, `SIN_SOLAPAMIENTO_TEXTUAL` contra proyecto/binarios y **un servicio PostgreSQL** reconocido. El `PGDATA` real del servidor `127.0.0.1:5432` y su `system_identifier` seguían `NO_VERIFICADO/NO_MEDIDOS`. Ningún laboratorio nuevo ha sido iniciado.

5X prepara la primera **lectura de metadatos PostgreSQL del clúster fuente**, exclusivamente a través de la base local sandbox `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`. La fuente histórica `fashionstore_restore_20261023_193554_594`, Supabase, `main` y otros clústeres quedan fuera del alcance.

## Entrega acotada

1. `database/saas/28_auditar_identidad_cluster_fuente_5x.sql`: transacción `BEGIN TRANSACTION READ ONLY`, límites `statement_timeout`, `lock_timeout` y `row_security`, `DO` de **preflight fail-closed** verificando exactamente host `127.0.0.1`, puerto `5432`, base sandbox, `session_user=current_user=postgres`, PostgreSQL 18, 46 tablas públicas, 48 FK tenant validadas y **0 políticas en las 16 tablas críticas**. Consulta `pg_control_system().system_identifier` y `current_setting('data_directory')` solo para validar datos de origen. **NO IMPRIME RUTA REAL PGDATA**; devuelve solamente resultado de comparación **textual** de su ruta con la propuesta futura `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data`.
2. `database/saas/28_auditar_identidad_cluster_fuente_5x.ps1`: `psql.exe` PG18 local fijado a sandbox con `PGOPTIONS=-c default_transaction_read_only=on`, lectura de contraseña con `-AsSecureString`, recuperación de variables previas y `SSL disable` para localhost. Escribe un informe **únicamente en `$env:TEMP\fashionstore_5x_identidad_fuente_LOCAL_REVISAR.txt`**, sin sobrescribir archivos existentes. Valida `1_IDENTIDAD_CLUSTER_FUENTE`, `2_DICTAMEN_ALCANCE` y `ROLLBACK`, sin subirlo al repositorio. **Esta herramienta sí escribe su archivo de informe en TEMP, pero no realiza mutaciones en PostgreSQL.**
3. `tests_saas/test_identidad_fuente_pg18_5x.py`: 14 pruebas **estáticas offline** sin PostgreSQL, verifican preflight, lectura solo de catálogo/control y parámetros de conexión/privacidad.

**A diferencia de 5W, el informe local guarda el `system_identifier` verdadero del origen** para una eventual comparación posterior con otra instancia independiente. Es un identificador de infraestructura, no una contraseña, pero **revisarlo antes de compartirlo**; nunca publicar en GitHub ni copiar la ruta privada de `PGDATA`. Si se desea, se puede ocultar el valor numérico al compartirlo por el chat, dejando visibles las secciones y los dictámenes.

La comparación de directorios es **textual, no canónica**. No resuelve junctions, symlinks, permisos Windows, letras de unidad montadas ni rutas equivalentes. Ni un resultado `SIN_SOLAPAMIENTO_TEXTUAL_NO_CERTIFICA` ni el ID del origen garantizan independencia futura de un clúster destino **que todavía no existe**. En el futuro deberán compararse `system_identifier` origen/destino directamente y revisar aislamiento de volumen/PGDATA.

Documentación PostgreSQL 18: [`pg_control_system()` devuelve `system_identifier`](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-CONTROLDATA) y [`current_setting()` consulta configuración](https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADMIN-SET).

## Pasos de validación 5X

Suite anterior confirmada: **560 passed**. Suite nueva **CONFIRMADA EN WINDOWS: `574 passed, 2 warnings in 7.21s`** (14 pruebas adicionales), cero fallos.

Desde `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Solo si la suite pasa:**

```powershell
.\database\saas\28_auditar_identidad_cluster_fuente_5x.ps1
```

Ingresar **únicamente la contraseña del PostgreSQL LOCAL**, no la de Supabase. El script debe anunciar `5X AUDITORIA IDENTIDAD FUENTE READ ONLY COMPLETADA`. Para revisar el informe local sin moverlo al proyecto:

```powershell
notepad "$env:TEMP\fashionstore_5x_identidad_fuente_LOCAL_REVISAR.txt"
```

Marcadores esperados en el archivo: `BEGIN`, `1_IDENTIDAD_CLUSTER_FUENTE`, `2_DICTAMEN_ALCANCE`, `ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE` y `ROLLBACK`; el `system_identifier_fuente` deberá ser un valor devuelto por PostgreSQL real, **no se inventa en el documento**. Si surge error o incongruencia de ruta, compartir salida y **no** ejecutar otros SQL ni cambiar instancia/privilegios.

## Riesgos y alcance del cierre

Los 7 bloqueantes del contrato 5Q permanecen abiertos: bootstrap RLS; permisos del hash; identidad SQL de actor; GUC modificable; policies y grants; `EXECUTE PUBLIC` legacy; integración PostgreSQL con un usuario real restringido. Los 14 casos A/B siguen **`NO_EJECUTADO`**. `SAAS_AUTH_ENABLED=false`, transacciones de reserva/pago/venta SaaS cerradas. El manifiesto del futuro laboratorio conserva `BORRADOR_NO_EJECUTAR`, `desplegable=false`, `iniciar_laboratorio_autorizado=false`.

**5X VALIDADA Y CERRADA en su alcance de pruebas estáticas y auditoría SQL READ ONLY. No crear una instancia, PGDATA, roles, policies, grants, seed ni SP en 5X.** El usuario solo autorizó avanzar esta subfase de preparación y auditoría READ ONLY, **no autoriza** `initdb`, `pg_ctl` ni ningún DDL. Los resultados locales ya se recibieron y permitieron cerrar exclusivamente la auditoría 5X; **Paso 5 continúa EN PROCESO, Bloque 3 4/7**. Esperar un nuevo `siguiente` para 5Y, y solicitar autorización expresa separada antes de crear infraestructura.


## 5X — Resultado real de Windows y cierre (24 de octubre de 2026)

El estudiante actualizó `feat/saas-multitenant-parcial2` por fast-forward **`fab2e32..284f517`** y ejecutó `python -m pytest -q tests_saas`: **`574 passed, 2 warnings in 7.21s`**, cero fallos. Las dos advertencias son deprecaciones ya conocidas de Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`.

Después ejecutó el wrapper `28_auditar_identidad_cluster_fuente_5x.ps1`, que consultó metadatos del **sandbox local** con la identidad SQL `postgres` a `127.0.0.1:5432`. La auditoría devolvió `5X PREFLIGHT OK: sandbox localhost, postgres18, 46 tablas, 48 FK, 0 policies criticas` y `5X AUDITORIA IDENTIDAD FUENTE READ ONLY COMPLETADA`. La transacción terminó en **`ROLLBACK`**; el informe privado se escribió únicamente bajo `$env:TEMP`.

Evidencia recibida **sin registrar el número `system_identifier` en GitHub**:

| Campo | Resultado observado | Interpretación |
|---|---|---|
| Sección | `1_IDENTIDAD_CLUSTER_FUENTE` | Fuente PostgreSQL identificada |
| Base | `fashionstore_saas_reservas_sandbox_5h` | No se consultó base histórica |
| Identidad SQL | `postgres` / `postgres` | Cuenta privilegiada; no certifica RLS |
| Puerto y versión | `5432` / PostgreSQL `18` | Identificación del origen |
| ID de clúster fuente | **`MEDIDO_LOCALMENTE_NO_VERSIONADO`** | Valor real conservado solo en informe local; no se publica |
| PGDATA frente a ruta candidata | `SIN_SOLAPAMIENTO_TEXTUAL_NO_CERTIFICA` | No detectado solapamiento textual; sin garantía canónica/física |
| Privacidad | `RUTA_PGDATA_ORIGEN_NO_PUBLICADA` | No se reveló directorio de datos |
| Sección de alcance | `2_DICTAMEN_ALCANCE` | Conclusión sin creación ni pruebas A/B |
| Dictamen | `ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE` | El segundo clúster NO existe |
| Límite | `SIN_CLUSTER_NUEVO_SIN_ROLES_SIN_RLS_A_B` | No se certificó aislamiento |

**5X VALIDADA Y CERRADA únicamente como auditoría read-only del clúster fuente.** Aunque el identificador original fue medido, ni el análisis de rutas ni su identificación demuestran independencia del laboratorio futuro. **No se ha creado un segundo clúster, no se han creado roles ni policies, ni se han ejecutado pruebas A/B**. Los **7 bloqueantes** de 5Q siguen ABIERTOS; la matriz de 14 casos está **`NO_EJECUTADO`**; el manifiesto 5U sigue **`BORRADOR_NO_EJECUTAR`**, `desplegable=false`, `iniciar_laboratorio_autorizado=false`; `SAAS_AUTH_ENABLED=false`. No se despliega Supabase, no se altera `main`, BD histórica ni rutas financieras. **Esperar un nuevo `siguiente` antes de 5Y; incluso entonces, cualquier creación de clúster o DDL exige autorización explícita adicional.** No pedir nuevos comandos para 5X.
