# Bloque 3 / Paso 5V — Inspección local de herramientas para un laboratorio PostgreSQL independiente

**Estado: 5V PREPARADA, pendiente de pruebas locales y diagnóstico Windows.** Paso 5 comercial EN PROCESO, **Bloque 3 4/7**. Solo rama `feat/saas-multitenant-parcial2`.

## 1. Evidencia confirmada y objetivo concreto

En 5U el estudiante ejecutó **`532 passed, 2 warnings in 7.33s`**, cero fallos, y el validador offline confirmó 14 escenarios A/B para dos empresas ficticias, sin iniciar ningún clúster. El contrato sigue `BORRADOR_NO_EJECUTAR` / `desplegable=false`; `system_identifier` de origen/destino aún no medidos y **7 bloqueantes** de seguridad abiertos.

En 5S se verificó que `postgres` usa `SUPERUSER/BYPASSRLS` y que `row_security_active()=false` en 16 tablas con RLS habilitado. En 5T se preparó un preflight de principal SQL restringido, pero no se ejecutó con un rol real.

**5V no pretende probar RLS:** su objetivo es únicamente identificar las herramientas disponibles en la PC Windows, para elegir después —con autorización explícita y separada— entre clúster PostgreSQL 18 desechable vía Docker o una segunda instancia `initdb` totalmente separada. **NO se ha creado un laboratorio PostgreSQL.**

## 2. Script nuevo de inspección, sin efectos

`database/saas/26_inspeccionar_herramientas_laboratorio_5v.ps1` ejecuta solo consultas del sistema operativo que **no modifican archivos ni bases de datos**:

| Verificación | Operación | Límite de lo que demuestra |
|---|---|---|
| Cliente Docker | `Get-Command docker.exe` | Saber si existe el binario; NO indica que Docker Desktop/daemon funcione |
| PostgreSQL 18 | `Test-Path` de `initdb.exe`, `pg_ctl.exe`, `pg_config.exe` bajo `C:\Program Files\PostgreSQL\18\bin` | Saber si existen esos archivos; NO inicia instancia ni comprueba permisos de servicio |
| Puerto futuro 55432 | `Get-NetTCPConnection -State Listen` (filtrado) | `OCUPADO`, `SIN_LISTENER_OBSERVADO` o `DESCONOCIDO`; NO garantiza que pueda reservarse ni demuestra independencia de clúster |
| Directorio futuro | `Test-Path C:\SI2_Parcial1\fashionstore-rls-lab-5v-data` | Si existe, **NO USAR SIN REVISAR**; si falta, NO crear en 5V |
| Seguridad | Mensajes fijos `CLUSTER_INDEPENDIENTE=NO_VERIFICADO`, `SYSTEM_IDENTIFIER=NO_MEDIDO`, `AISLAMIENTO_RLS_A_B=NO_CERTIFICADO` | Declara explícitamente que no hay evidencia de laboratorio real ni de aislamiento |

El diagnóstico **NO invoca Docker, `initdb`, `pg_ctl`, `psql`, `pg_dump` o `pg_restore`**, ni entra en las bases PostgreSQL. No lee contraseñas ni `.env`, no abre sockets de red ni cambia permisos. No escribe informe en `TEMP`; muestra líneas `5V` únicamente en PowerShell. Si `Get-NetTCPConnection` no está disponible o falla, muestra `DESCONOCIDO` sin asumir disponibilidad del puerto.

**NO usar el script como aprobación para arrancar procesos, levantar contenedores o crear roles**. El resultado solo determinará cuál vía es candidata a un futuro ensayo.

## 3. Pruebas unitarias estáticas

Se añadió `tests_saas/test_entorno_laboratorio_5v.py`: **14 pruebas offline**, que inspeccionan el código PowerShell y su documentación para detectar la introducción de escrituras, comandos de Docker/PostgreSQL, conexiones SQL, lectura de secretos, certificaciones falsas o habilitación del SaaS. No ejecutan el `.ps1`, no requieren privilegios ni conectan a ninguna DB.

Suite anterior confirmada: **532 passed**. **Resultado esperado: `546 passed`** (=532+14); no verificado aún en el Windows del estudiante.

## 4. Pasos seguros para validación Windows

Desde `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

Solo si pasan las pruebas:

```powershell
.\database\saas\26_inspeccionar_herramientas_laboratorio_5v.ps1
```

Se espera que el diagnóstico empiece y termine de esta manera; **los valores de Docker, binarios, carpeta y puerto dependen de la PC, por lo que no deben inventarse**:

```text
5V INSPECCION WINDOWS SOLO LECTURA - SIN SERVIDORES NI DDL
...
5V CLUSTER_INDEPENDIENTE=NO_VERIFICADO
5V SYSTEM_IDENTIFIER=NO_MEDIDO
5V AISLAMIENTO_RLS_A_B=NO_CERTIFICADO
5V SIN CAMBIOS: NO iniciar Docker/initdb/pg_ctl ni crear roles sin autorizacion
```

Solicitar al estudiante la salida del pytest y del script. No pedir contraseña local ni realizar auditoría SQL. Un puerto `SIN_LISTENER_OBSERVADO` no prueba que exista laboratorio; una herramienta `ENCONTRADO_NO_EJECUTADO` no significa que haya servidor operativo.

## 5. Barreras todavía pendientes para cualquier aislamiento real

El plan 5U conserva 14 casos A/B **no ejecutados**, las identidades de `empresa-lab-a` y `empresa-lab-b` son propuestas ficticias y hay **7 bloqueantes** de RLS/ACL y autenticación por resolver. `SAAS_AUTH_ENABLED=false`, operaciones de reserva, venta y pago SaaS todavía cerradas.

Para el futuro laboratorio harían falta: confirmar una opción de entorno, consentimiento separado para crear/arrancar infraestructura, `system_identifier` de origen y destino distintos, autenticación real de principales SQL no `SUPERUSER/BYPASSRLS`, esquema vacío sin datos históricos, policy RLS y grants auditados dentro del clúster desechable, 2 tenants completamente ficticios, `USING/WITH CHECK`, pruebas adversariales del GUC, triggers y permisos transitivos, rollback y concurrencia. **Ninguno de estos efectos se activa en 5V.**

El eventual `CREATE ROLE` afecta a todo el clúster donde se ejecute; por eso el plan prefiere una segunda instalación PostgreSQL independiente. **No crear roles en `127.0.0.1:5432` ni en la base restaurada**. El clúster nuevo, si llega a autorizarse, también requiere permiso explícito antes de crear roles/políticas o datos, y antes de su eventual eliminación. No derivar esa autorización de la palabra `siguiente`.

## 6. Cierre previsto

5V se podrá cerrar **solo como diagnóstico de disponibilidad de herramientas** cuando el estudiante confirme `546 passed` y el informe PowerShell. La salida no reducirá automáticamente los 7 bloqueos ni completará las transacciones del Paso 5. **Paso 5 EN PROCESO, Bloque 3 4/7.** No iniciar 5W sin el próximo `siguiente` y no crear infraestructura sin autorización específica. Conservar `main`, Supabase, sandbox y `fashionstore_restore_20261023_193554_594` sin cambios.
