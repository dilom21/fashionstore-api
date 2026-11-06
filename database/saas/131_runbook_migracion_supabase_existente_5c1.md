# 131 — Runbook: migrar el Supabase EXISTENTE a SaaS (5C1)

**Estado:** preparado, **NO ejecutado**. Ninguna instruccion de este documento se
ha corrido contra Supabase real, Render ni Vercel.

**Objetivo:** llevar la base historica de FashionStore al esquema multiempresa que
el backend SaaS necesita, preservando todos los datos y sin usar
`supabase db push`.

---

## 0. Decisiones que debe tomar el propietario ANTES de empezar

| # | Decision | Por que bloquea |
|---|---|---|
| D1 | Slug definitivo de la empresa historica (ej. `fashionstore`) | 00200 exige `SET saas.bootstrap_empresa_slug`; asigna todo el dato historico a esa empresa. Cambiarlo despues es un UPDATE masivo. |
| D2 | Contrasena y propietario de `fs_saas_runtime` / `saas_admin_runtime` | 00400 aborta si el runtime es SUPERUSER o BYPASSRLS. |
| D3 | Ventana de mantenimiento (lectura/escritura bloqueadas) | 00200/00300/00400 toman locks `ACCESS EXCLUSIVE` por tabla. |
| D4 | Backend en modo coexistencia o corte | Ver matriz de banderas al final. |

## 1. Prerrequisitos verificables

```bash
# PostgreSQL >= 17 en el proyecto (el pipeline usa 17.7 en laboratorio).
psql "$SUPABASE_DB_URL" -c "select version();"
```

Si el proyecto esta en PostgreSQL 15/16 **no se puede aplicar**: el pipeline se
probo solo en 17. Actualizar el motor es un cambio de produccion y requiere
autorizacion aparte.

Roles a crear (una sola vez, como `postgres` del proyecto):

```sql
CREATE ROLE fs_saas_runtime  LOGIN    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '<D2>';
CREATE ROLE saas_admin_runtime LOGIN  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '<D2>';
CREATE ROLE saas_rls_owner   NOLOGIN  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
```

`saas_rls_owner` es propietario de las funciones `SECURITY DEFINER`; no puede
iniciar sesion.

## 2. Respaldo (obligatorio, antes de cualquier DDL)

1. Confirmar PITR activo **o** tomar un `pg_dump` completo verificado.
2. Restaurar ese respaldo en un proyecto de ensayo y aplicar alli el pipeline
   completo (pasos 4-8) antes de tocar produccion.
3. Registrar fecha, tamano y checksum del respaldo en la evidencia del redeploy.

## 3. Congelar escrituras

Poner el backend en mantenimiento (o detener el servicio Render) para que
ninguna escritura caiga entre el respaldo y el corte. La app legacy puede seguir
en lectura hasta el paso 5.

## 4. Preflight de solo lectura

```bash
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f database/saas/130_preflight_supabase_existente_5c1.sql
```

Guardar la salida. Interpretacion:

- `version_motor` distinto de `OK` → parar.
- `tablas_public` < 41 → la base no es la historica; parar.
- `tablas_con_empresa_id` entre 1 y 30 → migracion parcial previa: **no
  reaplicar a ciegas**, restaurar el respaldo y reiniciar.
- `roles_plataforma` en `BLOQUEA` → crear los roles del paso 1.
- `clientes_sin_membresia` > 0 → se corrige en el paso 6.
- `duplicados_*` > 0 → corregir esos datos con el propietario antes de 00300.

## 5. Crear la empresa historica (antes de 00200)

```sql
-- bootstrap_empresa.sql  (ejecutar UNA vez, en la misma sesion que 00200)
INSERT INTO public.plan_saas (id, codigo, nombre, max_sucursales_activas)
VALUES (1, 'HISTORICO', 'Plan historico', 50)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.empresa (razon_social, nombre_comercial, slug)
SELECT 'FashionStore', 'FashionStore', '<D1>'
WHERE NOT EXISTS (SELECT 1 FROM public.empresa);

INSERT INTO public.suscripcion (empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado)
SELECT e.id, 1, CURRENT_DATE - 1, CURRENT_DATE + 3650, 'ACTIVA'
  FROM public.empresa e
 WHERE e.slug = '<D1>'
   AND NOT EXISTS (SELECT 1 FROM public.suscripcion s WHERE s.empresa_id = e.id);
```

> Esta tabla `empresa` la crea la migracion `00100`. Si aun no existe, ejecutar
> `00100` primero y despues este bloque.

## 6. Aplicar el pipeline en el orden del manifiesto

Orden exacto (manifiesto `126_manifiesto_migracion_staging_5c0.json`, campo
`orden`). **Nunca** por orden alfabetico y **nunca** toda la carpeta.

```bash
PSQL='psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1'

$PSQL -f supabase/migrations/20261030000100_saas_core_portable.sql
$PSQL -f bootstrap_empresa.sql \
      -f supabase/migrations/20261030000200_saas_empresa_id_portable.sql
#   ^ el SET saas.bootstrap_empresa_slug='<D1>' debe ir dentro de bootstrap_empresa.sql
#     para que 00200 lo lea en la MISMA sesion.

$PSQL -f supabase/migrations/20261030000300_saas_integridad_tenant_portable.sql
```

**Antes de 00300** (bloqueo medido en laboratorio: *"Hay 1 clientes cuyo usuario
no tiene membresia en su empresa"*):

```sql
INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado)
SELECT u.id, e.id, u.rol_id, TRUE
  FROM public.usuario u
 CROSS JOIN public.empresa e
 WHERE e.slug = '<D1>'
   AND NOT EXISTS (
     SELECT 1 FROM public.usuario_empresa ue
      WHERE ue.usuario_id = u.id AND ue.empresa_id = e.id
   );
```

Continuar en orden:

```bash
$PSQL -f supabase/migrations/20261030000400_saas_rls_principal_restringido_portable.sql
$PSQL -f supabase/migrations/20261030000500_saas_compatibilidad_5bl.sql
$PSQL -f supabase/migrations/20261030000550_saas_compatibilidad_esquema_real_5br.sql
$PSQL -f supabase/migrations/20261030000600_saas_operativo_5bm.sql
$PSQL -f supabase/migrations/20261030000700_saas_compras_devoluciones_5bn.sql
$PSQL -f supabase/migrations/20261030000800_saas_personal_rbac_5bo.sql
$PSQL -f supabase/migrations/20261030000900_saas_superadmin_hardening_5bo.sql
$PSQL -f supabase/migrations/20261030001000_saas_invitaciones_rate_limit_5bp.sql
$PSQL -f supabase/migrations/20261030001100_saas_acl_plataforma_esquema_real_5br.sql
$PSQL -f supabase/migrations/20261030001200_saas_acl_superficies_5bs.sql
$PSQL -f supabase/migrations/20261030001300_saas_rbac_catalogos_personal_owner_5bx.sql
$PSQL -f supabase/migrations/20261031001400_saas_proveedores_tenant_5c0.sql
```

Cada archivo abre su propia transaccion con `lock_timeout='5s'`: si una tabla
esta bloqueada, la fase aborta sin dejar estados intermedios.

## 7. Verificacion posterior (obligatoria)

```sql
SELECT count(DISTINCT table_name) FROM information_schema.columns
 WHERE table_schema='public' AND column_name='empresa_id';        -- esperado >= 31

SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity;  -- esperado 49

SELECT count(*) FROM public.usuario WHERE empresa_id IS NULL;     -- esperado 0
SELECT count(*) FROM public.producto WHERE empresa_id IS NULL;    -- esperado 0
```

Comparar los conteos de tabla del preflight (§4, bloque 9) con los de ahora: no
debe faltar ninguna fila. `pg_stat_user_tables` es estimado; para cifras exactas
usar `SELECT count(*)` por tabla.

## 8. Rollback

- El pipeline **no borra filas** de negocio; si falla antes de 00300, el
  rollback es `ROLLBACK` (cada fase es transaccional).
- Si falla despues de 00300/00400, 00300 elimina unicidades globales y agrega
  FKs: el rollback realista es **restaurar el respaldo** del paso 2, no un
  down-script.
- No ejecutar `DROP SCHEMA saas CASCADE` en produccion: se pierde la clave de
  asercion y toda membresia.

## 9. Variables del backend para el redeploy existente

| Variable | Valor | Efecto |
|---|---|---|
| `DATABASE_URL` | DSN de `fs_saas_runtime` sobre Supabase | Base multiempresa (superficie tenant) |
| `SAAS_TENANT_ASSERTION_KEY` | = `saas.rls_secret.clave` | Sin ella, RLS deny-all (503) |
| `SAAS_AUTH_ENABLED` | `true` | Abre `/auth/tenant` y `/tenant/**` |
| `LEGACY_SURFACE_ENABLED` | `true` + `LEGACY_DATABASE_URL` | Coexistencia con el sistema original |
| `LEGACY_SURFACE_ENABLED` | `false` | Corte a SaaS: el legacy responde 503 |
| `SAAS_ADMIN_DATABASE_URL` | DSN de `saas_admin_runtime` | Solo `/saas/superadmin/*` e invitaciones |
| `SAAS_VENTAS_ENABLED` / `SAAS_COMPRAS_ENABLED` / `SAAS_DEVOLUCIONES_ENABLED` / `SAAS_RESERVAS_ENABLED` | `true` por modulo validado | Doble opt-in por dominio |
| `SAAS_DEPLOY_ENV` + `SAAS_DEPLOY_DB_*` | identidad exacta del destino | Obligatorio en `APP_ENV` distinto de local |
| `LEGACY_DEPLOY_DB_*` | identidad exacta del destino legacy | Obligatorio si ambas superficies estan activas |

`SAAS_SP_SQL_OWNER` debe quedar **sin definir** con los procedimientos
`SECURITY INVOKER` actuales.

## 10. Matriz de arranque (la decide `app/core/surface_guard.py`)

| `SAAS_AUTH_ENABLED` | `LEGACY_SURFACE_ENABLED` | `LEGACY_DATABASE_URL` | Resultado |
|---|---|---|---|
| false | true | — | Despliegue actual, sin cambios |
| true | false | — | Corte limpio a SaaS |
| true | true | presente y distinta | Coexistencia validada |
| true | true | ausente o igual a `DATABASE_URL` | **Arranque abortado** |
| false | false | — | **Arranque abortado** |
