# Laboratorio de seguridad SaaS 5CA (copia de trabajo aislada)

Validación integrada de las propuestas `050a`/`050b`/`050c` (JWT verificado en
PostgreSQL, RLS por actor, ACL mínima, tres pools) **sin tocar Supabase, sin
tocar bases locales existentes y sin modificar el código original**.

## Cómo se ejecuta

```powershell
cd fashionstore-api\database\saas\laboratorio_seguridad_5ca
python 88_lab_validacion.py        # crea, prueba, evidencia y destruye
```

Pasos por separado (opcional, para depurar):

```powershell
python lab_baseline.py     # regenera sql/010_baseline_desde_catalogo.sql
python 90_lab_crear.py     # clúster efímero en %TEMP% (guarda estado)
python 99_lab_destruir.py  # apaga y elimina el clúster
```

Resultados en `resultados/5ca_evidencia.json`, `resultados/5ca_resultados.md`
y `resultados/5ca_salida_consola.log`.

## Qué hace el ejecutor

1. `Cluster.crear()`: `initdb` + `postgres.exe` en un directorio temporal con
   puerto libre, `scram-sha-256` y contraseña aleatoria en memoria.
2. `lab_baseline.py`: reconstruye el esquema histórico **desde el catálogo
   recuperado** (`evidence/saas_phase2_20261106/current_catalog/*.json`), porque
   las cinco migraciones históricas recuperadas están desactualizadas.
3. Aplica `010`–`040` sin cambios y las fixtures de dos empresas.
4. Crea el sustituto local de Vault y `extensions` (pgcrypto).
5. Copia `050a`-`050c` retirando **sólo** la guarda `BLOQUEADO` y las aplica.
6. Aplica `060_lab_correcciones.sql` (huecos encontrados) y `070_lab_sp_tenant.sql`.
7. Crea los tres logins de servicio y ejecuta 73 pruebas (SQL, pool, SP y HTTP).

## Archivos

| Archivo | Contenido |
|---|---|
| `lab_common.py` | clúster desechable, JWT HS256, registro PASS/FAIL |
| `lab_baseline.py` | generador de DDL desde el catálogo real |
| `lab_install.py` | instalación, copias 050a-c, Vault local, logins |
| `lab_tests.py` | pruebas de ACL, identidad, aislamiento, pool y SP |
| `lab_tests_fastapi.py` | pruebas HTTP del parche de identidad |
| `fastapi_lab/` | copia adaptada (pools + dependencia tenant), no sustituye al original |
| `sql/000` | sustituto local de Supabase Vault (**no equivalente**) |
| `sql/010` | baseline histórico generado (no editar a mano) |
| `sql/030` | fixtures ficticias de dos empresas |
| `sql/050a-c_lab` | copias de las propuestas sin la guarda de bloqueo |
| `sql/060` | correcciones de los seis huecos encontrados |
| `sql/070` | SP tenant con guardia `actor_coincide` |

## Advertencias

- El sustituto de Vault guarda el secreto en texto claro en el clúster temporal.
  **No equivale** a Supabase Vault: no prueba cifrado, KMS, ACL ni auditoría real.
- Ningún archivo de `sql/` se promociona a `supabase/migrations` sin revisión.
- `fastapi_lab/` importa `app.*` sin modificarlo; el parche real está descrito en
  `SEGURIDAD_ARQUITECTURA.md` y en el informe del laboratorio.
