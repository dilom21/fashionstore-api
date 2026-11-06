# 95 — Procedimiento único para cerrar B1 (5BQ / Etapa H)

**Objetivo:** obtener el esquema histórico **autorizado** del proyecto Supabase
`fashionstore` (PostgreSQL 17.6, `public`: 41 tablas, 27 triggers, 24 rutinas,
1 vista, 0 políticas) y convertirlo en un insumo que el comparador `87` pueda
usar, sin tocar el proyecto, sin datos y sin secretos en archivos.

**Estado hoy:** no existe ningún archivo de esquema autorizado en el equipo. Se
revisaron `C:\SI2_Parcial1`, `Documents`, `Downloads`, `Desktop`, `OneDrive` y el
temporal de OpenCode: **0 coincidencias**. Por eso B1 sigue **BLOQUEADA**.

**Regla:** este procedimiento lo ejecuta **el estudiante** (o quien tenga
autorización escrita del dueño del entorno). OpenCode no se conecta a Supabase.

---

## 0. Antes de empezar

| Requisito | Detalle |
|---|---|
| Autorización | Lectura del proyecto por el dueño de la cuenta. Sin esto, detenerse. |
| Binarios | PostgreSQL **17.x** (no 18). En este equipo: `%LOCALAPPDATA%\Temp\opencode\fs_pg17_5bp\pg17\pgsql\bin` (17.7). |
| Destino | Carpeta **fuera del repositorio**, p. ej. `%USERPROFILE%\Documents\fashionstore-esquema-5bq\`. |
| Prohibido | `supabase db push`, DDL/DML en el origen, aplicar `20261023000400_cuatro_tenants_demo_local.sql`, copiar datos personales, pegar contraseñas en el chat o en archivos. |

Diferencia de versiones a declarar: origen **17.6**, binarios locales **17.7**
(misma mayor) y laboratorio de G **17.7**. No es una diferencia de motor.

---

## Ruta A — preferida: `pg_dump --schema-only` + catálogo JSON

Produce el **DDL** (necesario para restaurar en un laboratorio y cerrar B1
completo).

### A.1 Ver el plan (no conecta a nada)

```powershell
cd C:\SI2_Parcial1\fashionstore-api
powershell -ExecutionPolicy Bypass -File database/saas/93_extraer_esquema_publico_5bq.ps1 `
  -Servidor "db.<proyecto>.supabase.co" -Puerto 5432 -BaseDatos "postgres" `
  -Usuario "postgres" `
  -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bq" -SoloPlan
```

Verificar en la salida: `pg_dump (PostgreSQL) 17.7`, `Esquemas: public`, y
`Salida (fuera de Git)`.

### A.2 Ejecutar con autorización

```powershell
powershell -ExecutionPolicy Bypass -File database/saas/93_extraer_esquema_publico_5bq.ps1 `
  -Servidor "db.<proyecto>.supabase.co" -Puerto 5432 -BaseDatos "postgres" `
  -Usuario "postgres" `
  -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bq" -Autorizado
```

- La contraseña se pide **interactivamente**; vive solo en `PGPASSWORD` del
  proceso hijo y se borra al terminar. No se imprime ni se escribe.
- Se ejecuta `pg_dump --schema-only --schema=public --no-publications
  --no-subscriptions` (**sin** `--no-owner` ni `--no-acl`: los propietarios y
  ACL son parte de la evidencia).
- Se ejecuta `92_catalogo_json_solo_lectura_5bq.sql` (solo `SELECT` de
  catálogo) para emitir el JSON con el contrato exacto de `87`.
- Se ejecuta `81_...sql` para el catálogo legible, y `94` valida ambos.

### A.3 Archivos esperados

```
esquema_public_<sello>.sql      pg_dump schema-only (DDL, sin filas)
catalogo_<sello>.json           contrato de 87 (UTF-8 SIN BOM)
catalogo_texto_<sello>.txt      catálogo legible (81)
inventario_<sello>.json         rutas, bytes, sha256, versiones
validacion_<sello>.json         salida de 94 (0=apto, 4=discrepancias, 5=no apto)
```

### A.4 Si el puerto 5432 directo no está disponible

Supabase exige a veces el *pooler*. `pg_dump` **no** funciona contra el pooler
en modo transacción: usar la conexión directa (`db.<ref>.supabase.co:5432`) o
la cadena "Direct connection" del panel. Si solo hay session pooler, usar la
Ruta B y declarar la limitación.

---

## Ruta B — solo catálogo, desde el SQL Editor

Sirve cuando no hay `pg_dump` de red. **No produce DDL**: no permite restaurar
el esquema ni cerrar el ensayo de migración.

1. Abrir el proyecto `fashionstore` → **SQL Editor** → *New query*.
2. Pegar **completo** `database/saas/92_catalogo_json_solo_lectura_5bq.sql`.
3. Ejecutar. Devuelve **una fila, una columna** `catalogo` con un JSON.
4. Copiar el valor completo de la celda y guardarlo como
   `%USERPROFILE%\Documents\fashionstore-esquema-5bq\catalogo_<sello>.json`
   (UTF-8 **sin BOM**; ver nota al final).
5. Validar:

```powershell
python database/saas/94_validar_insumo_esquema_5bq.py `
  --catalogo "$env:USERPROFILE\Documents\fashionstore-esquema-5bq\catalogo_<sello>.json"
```

Si el editor trunca la celda, usar la Ruta A: un JSON incompleto lo detecta
`94` (aridad, conteos y firma) y se rechaza.

---

## 1. Validar el insumo (obligatorio antes de comparar)

```powershell
# Solo dump
python database/saas/94_validar_insumo_esquema_5bq.py --dump "<ruta>\esquema_public_<sello>.sql"

# Dump + catálogo (lo que hace 93 automáticamente)
python database/saas/94_validar_insumo_esquema_5bq.py `
  --dump "<ruta>\esquema_public_<sello>.sql" `
  --catalogo "<ruta>\catalogo_<sello>.json" `
  --json --salida "<ruta>\validacion_<sello>.json"
```

Qué comprueba:

| Control | Rechaza |
|---|---|
| Cabecera `-- Dumped from database version` | archivos que no son `pg_dump` |
| Versión **17.x** del origen | un dump de 16.x o 18.x |
| Cero `COPY ... FROM stdin` / `INSERT` / `UPDATE` / `DELETE` | copias con datos |
| Cuerpos dollar-quoted excluidos del escaneo | falsos positivos por un `INSERT` dentro de una función |
| UTF-8 sin BOM | UTF-16 o BOM (87 falla con BOM) |
| Aridad exacta de las 10 listas | catálogos de otro script o truncados |
| Recalculo de `_firma` | archivos editados a mano |
| `meta.base` sin `lab/5bp/5bl/local/test` | autocomparación del laboratorio |
| Conteos 41/27/24/1 | discrepancias con la referencia externa (exit 4, revisar) |

Códigos: `0` apto · `4` apto con discrepancias frente a 41/27/24/1 · `5` no apto.

---

## 2. Comparar (reutiliza 87, no se reescribe)

```powershell
# a) catálogo del laboratorio PG17 (solo loopback)
python database/saas/87_comparar_catalogo_historico_5bp.py `
  --exportar-dsn "postgresql://<rol>:<clave>@127.0.0.1:55447/<base_lab>" `
  --salida-catalogo "%LOCALAPPDATA%\Temp\opencode\fs_5bq\catalogo_lab_5bq.json"

# b) comparación real
python database/saas/87_comparar_catalogo_historico_5bp.py `
  --origen-catalogo "<ruta fuera de Git>\catalogo_<sello>.json" `
  --destino-catalogo "%LOCALAPPDATA%\Temp\opencode\fs_5bq\catalogo_lab_5bq.json" `
  --origen-dump "<ruta fuera de Git>\esquema_public_<sello>.sql" `
  --json --salida database/saas/evidencias/EVIDENCIA_ESQUEMA_5BQ_<sello>.json

# c) separar ruido de plataforma (roles/extensiones de Supabase) de lo real
python database/saas/94_validar_insumo_esquema_5bq.py `
  --informe-87 database/saas/evidencias/EVIDENCIA_ESQUEMA_5BQ_<sello>.json
```

`87` compara por **objeto y firma**: tablas, vistas, `empresa_id`, rutinas
(firma con argumentos, `SECURITY DEFINER`, dueño, `search_path`), triggers,
políticas, propietarios, roles, extensiones y `EXECUTE` de los seis principales.
**No** compara columnas, restricciones ni índices: para eso el catálogo trae el
bloque `extra`, que hoy es evidencia para revisión, no un segundo comparador.

---

## 3. Después del insumo: qué cierra B1 y qué no

| Situación | B1 |
|---|---|
| Dump + catálogo de PG 17.6, `87` con diferencias resueltas, esquema restaurado en lab PG17 nuevo y pipeline aplicado | **CERRADA** |
| Solo catálogo (Ruta B), diferencias documentadas | **CERRADA EN COMPARACIÓN / ABIERTA EN RESTAURACIÓN** |
| Sin archivo autorizado | **BLOQUEADA** (estado actual) |

B2 (respaldo del origen con datos) **exige autorización separada y explícita**;
nunca se sube al repo ni se pega en el chat. Ver `89_runbook_staging_y_retorno_5bp.md`.

---

## 4. Nota técnica: BOM y JSON

`87` lee con `encoding="utf-8"`; un BOM UTF-8 inicial lo rompe con
`Unexpected UTF-8 BOM`. En PowerShell 5.1 `Out-File -Encoding utf8` **escribe
BOM**. `93` ya escribe sin BOM; si se genera a mano:

```powershell
$t = Get-Content -Raw "<archivo>.json"
[IO.File]::WriteAllText("<archivo>.json", $t, (New-Object Text.UTF8Encoding($false)))
```

---

## 5. Limpieza y retención

- El dump y el catálogo viven **fuera del repositorio** y no se envían a
  servicios externos ni a modelos.
- Al terminar la comparación: conservar `catalogo_<sello>.json`, `inventario` y
  `validacion`; el `.sql` puede conservarse cifrado o borrarse con registro de
  hash en la evidencia.
- Borrar cualquier archivo temporal con credenciales; nunca hubo contraseña en
  disco.
