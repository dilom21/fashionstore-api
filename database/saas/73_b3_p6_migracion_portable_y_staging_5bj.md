# SaaS / Bloque 2 / Paso 6 — Migración portable y staging desechable (5BJ)

Corte: 30 de octubre de 2026. Rama `feat/saas-multitenant-parcial2`.
Modo SaaS de la aplicación: **APAGADO**. No se modificó Supabase, ni Render,
ni ningún flag público. No hubo `commit` ni `push`.

## 1. Clasificación de las migraciones existentes

| Archivo | Clase | Motivo |
|---|---|---|
| `20261023000100_saas_base_tables.sql` | Portable con reservas | Crea las 5 tablas del núcleo con el significado correcto, pero **aborta si ya existen** (no idempotente) y revoca privilegios de `anon/authenticated` en duro. |
| `20261023000200_vanter_tenancy_historica.sql` | **Atada al laboratorio** | Exige `current_database() = fashionstore_restore_20261023_193554_594`, 46 tablas, 27 triggers y conteos exactos (9 usuarios, 14 productos, 40 ventas...). Inservible sobre otro esquema. |
| `20261023000300_integridad_tenant_local.sql` | **Atada al laboratorio** | Misma guarda de nombre de base, 46 tablas, y nombres de restricción fijos asumidos como existentes. |
| `20261023000400_cuatro_tenants_demo_local.sql` | **Datos de demostración** | Siembra UrbanWear, Elegancia Sur, Denim House y SportLine con datos ficticios. No es migración de esquema y nunca debe aplicarse en producción. |

Ninguna de las cuatro se borró ni se modificó: siguen siendo evidencia del
laboratorio y no deben llegar a la instancia central.

## 2. Nuevo set portable (idempotente)

| Archivo | Contenido | Garantías |
|---|---|---|
| `20261030000100_saas_core_portable.sql` | `empresa`, `plan_saas`, `suscripcion`, `usuario_empresa`, `empresa_configuracion`, semilla BASICO/PROFESIONAL, RLS deny-by-default, revocaciones. | `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ON CONFLICT (codigo) DO NOTHING`, revocación condicionada a que el rol exista. No crea empresas ni membresías reales. |
| `20261030000200_saas_empresa_id_portable.sql` | `empresa_id` en las 31 tablas empresariales + FK a `empresa` + índice por empresa. | Resuelve el tenant histórico por `saas.bootstrap_empresa_slug`; usa `ADD COLUMN ... DEFAULT constante` (PostgreSQL 11+: sin reescritura y sin disparar triggers), retira el `DEFAULT` y exige `NOT NULL` salvo en `bitacora`. Membresías históricas solo con `saas.bootstrap_crear_membresias=true`. |
| `20261030000300_saas_integridad_tenant_portable.sql` | `UNIQUE (empresa_id, id)`, relajación de UNIQUE globales a por-empresa, FK compuestas `(empresa_id, hija) -> (empresa_id, id)`, `usuario_empresa` compuesta y FK de `cliente`/`empleado` a membresía. | Descubre restricciones **por catálogo**, no por nombre. Idempotente. Aborta con mensaje explícito si hay duplicados o perfiles sin membresía. |

Orden de aplicación y parámetros (misma sesión):

```sql
SET saas.bootstrap_empresa_slug = '<slug-empresa-historica>';
SET saas.bootstrap_crear_membresias = 'true';   -- solo si se autoriza
```

```text
20261030000100  ->  (registrar la empresa histórica)  ->  20261030000200  ->  20261030000300
```

La empresa histórica se registra con la API de superadministrador o, en
migración manual, con un `INSERT` revisado por el responsable; **no** la
inventa la migración.

## 3. Staging desechable

`database/saas/70_staging_saas_desde_cero_5bj.py` reconstruye el esquema
heredado con el mapeo ORM, lo reduce al estado pre-SaaS (sin `empresa_id`, con
los UNIQUE globales históricos), siembra 39 tablas con datos ficticios, aplica
el bloque portable, verifica y **vuelve a aplicar** para probar idempotencia.

Guardas de seguridad del arnés: rechaza hosts no locales y bases cuyo nombre no
contenga `staging` o `lab` (salvo `--allow-remote` explícito), y no lee
`DATABASE_URL` real (inyecta un valor ficticio antes de importar el ORM).

Clúster usado (desechable, fuera del repositorio):

```powershell
# crear una vez
& "C:\Program Files\PostgreSQL\18\bin\initdb.exe" -D "$env:LOCALAPPDATA\Temp\opencode\fs_staging\pgdata" -U postgres --auth=trust --encoding=UTF8 --locale=C
# arrancar en una consola propia (no con pg_ctl detachado en este equipo:
# los procesos hijo fallan con 0xC0000142)
& "C:\Program Files\PostgreSQL\18\bin\postgres.exe" -D "$env:LOCALAPPDATA\Temp\opencode\fs_staging\pgdata" -p 55440 -c listen_addresses=127.0.0.1
# detener: Ctrl+C en esa consola
```

### Evidencia ejecutada (PostgreSQL 18.6, clúster temporal en `127.0.0.1:55440`)

| Control | Resultado |
|---|---|
| Filas conservadas en las 31 tablas | Sí, antes = después |
| Tablas con `empresa_id` | 31 / 31 |
| Filas sin tenant | 0 |
| FK empresariales `fk_empresa_*` | 32 (31 tablas + `usuario_empresa`) |
| FK compuestas `fk_tenant_*` | 48 |
| UNIQUE por tenant `uq_tenant_*` | 28 |
| Índices `idx_*_empresa` | 31 |
| RLS en las 5 tablas del núcleo | 5 / 5 |
| Membresías históricas creadas | 2 (los 2 usuarios ficticios) |
| Referencia A→B en FK compuesta | Rechazada |
| Mismo nombre de categoría en otra empresa | Permitido |
| Duplicado intra-tenant | Rechazado |
| Segunda aplicación del bloque | Sin cambios (48/28 estables) |
| Cruces padre/hija entre empresas (script 72) | 0 |
| Clientes/empleados sin membresía | 0 |
| Usuarios sin membresía | 0 |

Estos números coinciden con los del laboratorio (48 FK, 28 UNIQUE), lo que
confirma que la versión portable conserva la semántica probada.

### Pruebas

```powershell
python -m pytest -q tests_saas/test_cu01_cu02_superadmin_sqlite_offline.py `
  tests_saas/test_cu01_primer_administrador_sqlite_offline.py `
  tests_saas/test_cu04_membresias_empresa_sqlite_offline.py `
  tests_saas/test_saas_route_gate.py tests_saas/test_auth_tenant_unit.py `
  tests_saas/test_tenant_orm_mapping.py `
  tests_saas/test_auditar_fuentes_esquema_sin_conexion_5bh.py `
  tests_saas/test_dependencias_esquema_historico_offline_5bi.py `
  tests_saas/test_migraciones_saas_portables_staging_5bj.py
# 154 passed, 1 skipped (la de integracion sin DSN)
```

```powershell
# Integracion real contra una base descartable recien creada:
$env:FASHIONSTORE_STAGING_DSN = "postgresql+psycopg://postgres@127.0.0.1:55440/fashionstore_staging_5bj2"
python -m pytest -q tests_saas/test_migraciones_saas_portables_staging_5bj.py
# 11 passed
```

## 4. Guiones del día de migración

- `database/saas/71_preflight_migracion_saas_solo_lectura.sql`: identidad de
  conexión, tablas SaaS presentes, tenencia por tabla, restricciones, RLS,
  duplicados que romperían UNIQUE por empresa, triggers, estado de suscripción
  y usuarios sin membresía. Solo `SELECT` y `DO` de lectura; probado sobre el
  estado pre-migración.
- `database/saas/72_verificacion_post_migracion_saas_solo_lectura.sql`:
  núcleo y planes, tenencia por tabla, restricciones, índices, RLS, cruces
  padre/hija entre empresas, perfiles sin membresía, vigencia de suscripciones
  y triggers. Ejecutado sobre staging: 0 cruces, 0 perfiles huérfanos.

## 5. Brechas honestas de esta etapa

1. **Triggers heredados**: el esquema reconstruido por ORM no incluye los 27
   triggers del primer parcial, por lo que el staging no los ejercita. La
   propiedad "no disparar triggers" se apoya en `ADD COLUMN ... DEFAULT`
   (sin `UPDATE`), verificada en el laboratorio anterior; falta repetirla sobre
   una copia con triggers reales.
2. **Esquema fuente**: el DDL histórico completo no está versionado (41 tablas
   estimadas frente a 40 del ORM). El staging reconstruye una aproximación
   fiel al mapeo, no una copia byte a byte de producción.
3. **RLS efectivo**: solo está habilitado en las 5 tablas del núcleo y sin
   políticas; el aislamiento real por principal SQL restringido sigue pendiente
   (Etapa C).
4. **Datos**: no se creó ningún tenant real, ni se tocaron datos de Supabase.

## 6. Reversión

Cada archivo portable abre y cierra su propia transacción: si falla una
sentencia, no queda estado parcial. La reversión fina, en orden inverso y solo
con respaldo verificado:

1. `DROP` de las restricciones `fk_tenant_*` y `uq_tenant_*` creadas, y de los
   índices `idx_*_empresa` (no se pierden datos).
2. `ALTER TABLE ... DROP COLUMN empresa_id` en las 31 tablas: elimina el dato
   de pertenencia; **irreversible sin respaldo**.
3. `DROP` de las 5 tablas del núcleo (contienen empresas, planes y
   suscripciones).

La reversión de la migración de datos exige restaurar el respaldo previo; no
se implementa como script automático para evitar un borrado accidental.

## 7. Siguiente paso concreto

Aplicar este bloque en un staging con el **esquema real** (respaldo
despersonalizado o `pg_dump` del esquema de producción sin datos) y repetir el
preflight 71 → migración → verificación 72. Después, Etapa C: principal SQL
restringido, RLS con políticas por tenant y pruebas HTTP A/B con dos empresas.
