# Bloque 3 / Paso 5W — Preflight PGDATA y servicios de segunda instancia PostgreSQL 18

**Estado: 5W PREPARADA; pruebas y diagnóstico Windows pendientes.** Solo en `feat/saas-multitenant-parcial2`. **Paso 5 EN PROCESO, Bloque 3 4/7**; `SAAS_AUTH_ENABLED=false`, sin rutas comerciales nuevas.

## Base confirmada

5V se cerró con **`546 passed, 2 warnings in 7.92s`**; diagnóstico local de Windows mostró `docker.exe`, `initdb.exe`, `pg_ctl.exe`, `pg_config.exe` encontrados **NO EJECUTADOS**, directorio de prueba propuesto ausente y puerto 55432 sin listener observado. **Eso NO significa que exista un clúster nuevo ni que sea seguro ejecutar `initdb`.** No se midieron `system_identifier` ni el `PGDATA` del clúster fuente.

El servidor del primer parcial sigue en `127.0.0.1:5432`, con la base histórica restaurada `fashionstore_restore_20261023_193554_594` fuera de alcance y el sandbox vacío `fashionstore_saas_reservas_sandbox_5h`. El manifiesto 5U permanece `BORRADOR_NO_EJECUTAR`, `desplegable=false`, y los **7 bloqueantes** del diseño 5Q siguen abiertos. Los 14 ensayos reales RLS empresa A/B no se han ejecutado.

## Objetivo y alcance 5W

Preparar una comprobación **de solo lectura del sistema Windows** de la ubicación candidata para el `PGDATA` de una futura **segunda instancia PostgreSQL 18 nativa**. Esta subfase NO crea directorios, archivos, clústeres, roles, policies ni datos; tampoco consulta PostgreSQL, Docker Engine, Supabase ni Internet.

**Script nuevo:** `database/saas/27_preflight_directorio_pg18_laboratorio_5w.ps1`

El script revisa lo siguiente usando solamente comandos locales de consulta:

| Evidencia | Comprobación | Interpretación segura |
|---|---|---|
| Ruta candidata | `Test-Path` sobre `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data` | `EXISTE_BLOQUEAR` si ya hay algo: **no usar, sobrescribir o limpiar** |
| Carpeta padre | `Test-Path` | Si falta: `NO_EXISTE_BLOQUEAR` sin crearla |
| Reparse points | `Get-Item` y `FileAttributes.ReparsePoint` en todos los ancestros del padre | Si detecta junction/symlink: `DETECTADO_BLOQUEAR`; sin detección no equivale a prueba de aislamiento físico |
| Solapamiento textual | `Path.GetFullPath` y comparación `OrdinalIgnoreCase` con el directorio del repositorio y los binarios PG18 | `SIN_SOLAPAMIENTO_TEXTUAL` es una condición necesaria, no suficiente |
| Servicios instalados | `Get-CimInstance Win32_Service` | Cuenta servicios PostgreSQL conocidos; **NO imprime `PathName`**, directorio de datos, usuarios, secretos ni argumentos |
| Fuente PGDATA e ID real | No se interroga servidor PostgreSQL `5432` | `PGDATA_CLUSTER_5432=NO_VERIFICADO`, `SYSTEM_IDENTIFIER_ORIGEN_DESTINO=NO_MEDIDOS` |

El resultado termina obligatoriamente en:

```text
5W PGDATA_CLUSTER_5432=NO_VERIFICADO
5W SYSTEM_IDENTIFIER_ORIGEN_DESTINO=NO_MEDIDOS
5W CLUSTER_NATIVO_INDEPENDIENTE=NO_CERTIFICADO
5W DICTAMEN=BLOQUEADO_SIN_AUTORIZACION_EXPLICITA
5W SIN CAMBIOS: NO ejecutar initdb/pg_ctl ni crear roles, policies o datos
```

La inspección de `Win32_Service` puede omitir servidores iniciados fuera de servicios Windows; por eso contar servicios no prueba que el origen esté completamente identificado. Un resultado `SIN_SOLAPAMIENTO_TEXTUAL` tampoco resuelve enlaces simbólicos u otros mecanismos de redirección. La separación debe comprobarse nuevamente **después de una creación explícitamente autorizada**, con identidades de clúster distintas y credenciales SQL restringidas; **5W no la certifica**.

## Pruebas estáticas

Se añade `tests_saas/test_preflight_pgdata_windows_5w.py`, con **14 pruebas offline** que comprueban guardas de ruta, reparse points, consultas de servicio no reveladoras, prohibición de escritura, ausencia de llamadas a ejecutables/SQL, integración desligada de la API y los indicadores finales siempre bloqueados.

Anteriormente: `546 passed`. Suite esperada **`560 passed`** (=546 anteriores +14 nuevas), **todavía no confirmada en Windows**.

## Ejecución limitada solicitada al estudiante

Desde `C:\SI2_Parcial1\fashionstore-api`, ambiente Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Solo después de aprobar todas las pruebas**, ejecutar la inspección:

```powershell
.\database\saas\27_preflight_directorio_pg18_laboratorio_5w.ps1
```

El script **no pide contraseña**, no escribe archivos, no ejecuta `initdb` ni `pg_ctl`, y solo devuelve indicadores de entorno sin revelar rutas de los servicios PostgreSQL. Los valores de la carpeta, reparse points y servicios dependerán de la PC: no asumir resultado. Enviar al chat ambas salidas para revisar.

## Riesgos y decisiones posteriores

Aunque 5W complete sus comprobaciones, no se considerará autorizado crear el laboratorio. Antes de arrancar la segunda instancia deben existir una propuesta de `PGDATA` totalmente independiente y comprobación de que no se reutiliza el del servidor 5432; revisión de configuración de servicios, permisos de Windows, puerto, autenticación y plan seguro de cierre. Solo entonces solicitar **autorización explícita y separada** para crear infraestructura, y más adelante otra autorización precisa para `CREATE ROLE`, policies, grants y seed en el clúster desechable.

No tocar `main`, Supabase, servidor PostgreSQL original, BD histórica ni sandbox. No ejecutar pruebas A/B ni habilitar transacciones antes de demostrar SQL con identidad independiente. La matriz de 14 casos continúa `NO_EJECUTADO`, los **7 bloqueantes** abiertos. **Bloque 3 4/7** y `SAAS_AUTH_ENABLED=false` se mantienen.

**5W pendiente de pruebas locales.** Después de validarlas, cerrar solo la inspección de ubicación/servicios y **esperar un nuevo `siguiente`** antes de 5X. Esta instrucción jamás concede autorización implícita para inicializar o borrar PGDATA.
