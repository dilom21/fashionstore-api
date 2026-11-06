# 122 — Puerta de staging CLOUD SaaS (5C0): matriz accionable, SIN ejecutar

**Estado:** **NO-GO**. Este documento **no autoriza** crear recursos, gastar,
desplegar ni publicar. Es la lista de decisiones y pruebas que deben aprobarse y
ejecutarse en una etapa posterior.
**Ejecutado en 5C0:** 0 recursos cloud, 0 llamadas a Supabase/Render/Vercel,
0 DNS, 0 secretos productivos, 0 despliegues.

Base local verificada: PostgreSQL 17.7 `127.0.0.1:55460` (sintético), FastAPI
SaaS con guardia fail-closed, Angular SaaS en `saas-local`, 23/23 E2E reales.

---

## 1. Topología exigida (coexistencia A, ya aceptada)

```text
FashionStore LEGACY (intacto)
  ├── Frontend legacy actual        -> proyecto/servicio EXISTENTE (no tocar)
  ├── API legacy actual             -> proceso EXISTENTE, SAAS_* = OFF
  └── PostgreSQL legacy/producción  -> DSN EXISTENTE (no reutilizar)

FashionStore SaaS STAGING (nuevo, aislado)
  ├── Frontend Angular SaaS staging -> NUEVO proyecto Vercel
  │     https://staging-saas.<dominio>
  ├── API FastAPI SaaS staging      -> NUEVO servicio Render (web service)
  │     https://api-staging-saas.<dominio>
  ├── PostgreSQL 17 staging         -> NUEVO proyecto Supabase
  │     db.<ref-staging>.supabase.co:5432/postgres  (host real al crear)
  └── Gestor de secretos            -> Render Environment / Supabase Vault
```

Reglas duras:

1. Dos procesos FastAPI independientes: `SAAS_AUTH_ENABLED` es global al proceso.
2. Dos bases independientes: sin host, base, usuario ni contraseña compartidos.
3. Cero reutilización del DSN de producción o del legacy.
4. Cero PII real: solo fixtures sintéticos A/B. El paquete B2 **no** se usa.
5. El legacy no se modifica: ni ACL, ni flags, ni DNS, ni despliegue.

---

## 2. Recursos exactos a crear (cuando se autorice)

| Recurso | Proveedor propuesto | Identificador exacto | Coste | Aprobación |
|---|---|---|---|---|
| Proyecto DB staging PG17 | Supabase (proyecto NUEVO) | `<PROYECTO_STAGING_REF>` (se asigna al crear) | Free tier si la política vigente lo permite; cualquier cargo requiere aprobación | **A1** |
| Servicio API SaaS staging | Render Web Service (NUEVO) | `fashionstore-saas-api-staging` | Plan mínimo; cargo posible | **A2** |
| Sitio Angular SaaS staging | Vercel (proyecto NUEVO) | `fashionstore-saas-web-staging` | Free/Hobby si aplica; cargo posible | **A3** |
| Gestor de secretos | Render Environment + Supabase | variables del servicio, nunca en Git | Incluido | **A4** |
| Dominio/subdominios | DNS del propietario | `staging-saas.<dominio>`, `api-staging-saas.<dominio>` | Puede requerir plan/registro | **A5** |

**No se crean** recursos en 5C0. Los nombres son propuesta; el propietario puede
cambiarlos antes de ejecutar. Ningún `ref`, host ni credencial real aparece aquí.

---

## 3. Configuración exacta del proceso SaaS staging

Valores de forma (el gestor inyecta los secretos). **Nunca** `SAAS_LOCAL_ONLY`
ni `SAAS_ALLOW_REMOTE_DB` en cloud.

```dotenv
APP_ENV=staging
SAAS_DEPLOY_ENV=staging
SAAS_DEPLOY_DB_HOST=<host-exacto-del-proyecto-staging>
SAAS_DEPLOY_DB_PORT=5432
SAAS_DEPLOY_DB_NAME=postgres
SAAS_DEPLOY_DB_USER=fs_saas_runtime
SAAS_DEPLOY_ADMIN_USER=saas_admin_runtime
SAAS_DEPLOY_DENY_HOSTS=<host-legacy>,<host-produccion>

DATABASE_URL=postgresql+psycopg://fs_saas_runtime:<gestor>@<host-exacto>:5432/postgres
SAAS_ADMIN_DATABASE_URL=postgresql+psycopg://saas_admin_runtime:<gestor>@<host-exacto>:5432/postgres

SAAS_LOCAL_ONLY=
SAAS_ALLOW_REMOTE_DB=

SECRET_KEY=<gestor, >=48 bytes aleatorios>
SAAS_TENANT_ASSERTION_KEY=<gestor, >=48 bytes aleatorios>
SAAS_SUPERADMIN_KEY_SHA256=<huella, no la clave>
SAAS_SUPERADMIN_ENABLED=true
SAAS_SUPERADMIN_REQUIRE_HTTPS=true
SAAS_SUPERADMIN_TRUSTED_PROXIES=<IP/CIDR exactos del proxy>

SAAS_AUTH_ENABLED=false        # encendido escalonado §6
SAAS_VENTAS_ENABLED=false
SAAS_COMPRAS_ENABLED=false     # habilita también POST /tenant/proveedores
SAAS_DEVOLUCIONES_ENABLED=false
SAAS_RESERVAS_ENABLED=false
SAAS_INVITATION_RATE_LIMIT_ENABLED=true
SAAS_ACTOR_SQL_OWNER=saas_actor_sql_owner
```

Notas:

- `launch_guard` **exige** que host, puerto, base y usuario coincidan con
  `SAAS_DEPLOY_*`. Un `APP_ENV` distinto de `SAAS_DEPLOY_ENV` aborta el arranque.
- El usuario de `DATABASE_URL` **no** puede ser `postgres`;
  `SAAS_ADMIN_DATABASE_URL` usa `saas_admin_runtime`.
- La base de Supabase se llama `postgres`; la separación se garantiza por
  **proyecto/host nuevos**, no por el nombre de la base.
- El frontend se compila con `environment.saas-local` parametrizado a la URL de
  la API staging; **no** se publica `environment.ts` legacy con APIs locales.
- CORS debe listar **solo** `https://staging-saas.<dominio>`; retirar
  `localhost:4200`/`127.0.0.1:4200` del artefacto publicado.

---

## 4. Matriz C1–C18 (bloqueante)

| # | Dependencia | Prueba exacta | Propietario de la decisión | PASS | FAIL | Rollback |
|---|---|---|---|---|---|---|
| C1 | A1 | `SELECT current_database(), inet_server_addr()` en el proyecto nuevo; host/base distintos de legacy y producción | Propietario + DBA | valores distintos | cualquier coincidencia | destruir proyecto staging |
| C2 | A1 | `SHOW server_version` ⇒ `17.*` | DBA | 17.x | ≠17 | recrear con PG17 |
| C3 | A1 | `\du fs_saas_runtime` ⇒ LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT; `has_table_privilege('public.usuario','SELECT')=false` | DBA | todo cumple | alguno falla | revoke/ALTER ROLE |
| C4 | A1 | `saas_admin_runtime`: LOGIN, NOSUPERUSER, NOBYPASSRLS; solo EXECUTE en `saas_admin.*` | DBA | cumple | exceso | revoke |
| C5 | A1 | owners NOLOGIN: `saas_rls_owner`, `saas_admin_owner`, `saas_actor_sql_owner`, `saas_personal_owner` | DBA | 4 existen, NOLOGIN | falta/LOGIN | crear/ALTER |
| C6 | A1 | `anon/authenticated/service_role` sin privilegios en `public/saas/saas_admin` (consulta `aclexplode` = 0 filas) | DBA | 0 filas | >0 | revoke |
| C7 | A4 | `SECRET_KEY` y `SAAS_TENANT_ASSERTION_KEY` ≥48 bytes en gestor; `git grep` no los encuentra | Propietario | 0 en repo | hallado | rotar |
| C8 | A1 | Aplicar manifiesto **126** (19 fases, 14 SQL) con checksums; seed demo **no** aplicado | DBA | 19/19 ok, demo ausente | falta fase o demo aplicado | restaurar backup staging |
| C9 | C8 | `saas_migracion.version_aplicada` = 19; 49 tablas FORCE RLS (o número real reportado) | DBA | cifras esperadas | discrepancia | reaplicar/manual |
| C10 | C8 | `anon/authenticated/service_role` con 0 concesiones en `public/saas/saas_admin` | DBA | 0 | >0 | revoke |
| C11 | C8 | Sin aserción ⇒ 0 filas; aserción A no ve B; aserción falsificada ⇒ NULL/0 | DBA + backend | 3/3 | cualquiera | rotar `SAAS_TENANT_ASSERTION_KEY` |
| C12 | C8 | Base sin PII: solo fixtures sintéticos A/B o alta por superadmin | Propietario | 0 PII | PII | destruir/recrear |
| C13 | A2/A3/A5 | HTTPS en API y frontend; `SAAS_SUPERADMIN_REQUIRE_HTTPS=true`; proxies de confianza exactos | Propietario | TLS válido | HTTP/plantilla mal | corregir config |
| C14 | A2 | Arrancar con **todos** los `SAAS_*` de escritura OFF; `/health` 200 y `/health/db` sin datos | Backend | OFF + 200 | flag ON | detener servicio |
| C15 | A1/A2 | PostgREST: `anon` y `authenticated` ⇒ 401/403/404 sin datos sobre `producto`, `usuario`, RPC `tenant_actual`; `service_role` no se usa en frontend | DBA + backend | ningún 200 con datos | 200 con datos | revocar GRANT y detener |
| C16 | A3 | CORS solo origen exacto staging; preflight 204; origen local rechazado | Backend | exacto | comodín/local | corregir CORS |
| C17 | A2 | Logs sin DSN/tokens; auditoría global activa; `/health` y `/health/db` sin secretos | Backend | 0 fugas | fuga | rotar y corregir |
| C18 | C1–C17 | Apagar solo el servicio SaaS; legacy sigue 200 en su URL/DSN; base SaaS descartable | Propietario | legacy intacto | legacy afectado | restaurar legacy (no debería) |

**C15 es el bloqueo histórico de cloud:** sin prueba PostgREST real no hay GO.

---

## 5. Prueba PostgREST real (cuando exista A1)

```bash
# 1) anon sobre tabla de negocio: 401/403/404, nunca 200 con datos
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<ref>.supabase.co/rest/v1/producto?select=id&limit=1" \
  -H "apikey: <ANON_KEY>"

# 2) anon sobre RPC de saas
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<ref>.supabase.co/rest/v1/rpc/tenant_actual" -H "apikey: <ANON_KEY>"

# 3) authenticated con JWT de empresa A sobre tabla de negocio
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<ref>.supabase.co/rest/v1/usuario?select=id&limit=1" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <JWT_A>"

# 4) service_role: NO se usa desde el frontend (riesgo documentado)
```

SQL de comprobación (0 filas):

```sql
SELECT r.rolname, c.relname, a.privilege_type
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(rn)
  JOIN pg_roles r ON r.rolname = v.rn
  CROSS JOIN LATERAL aclexplode(c.relacl) a
 WHERE a.grantee = r.oid AND n.nspname IN ('public','saas','saas_admin');
```

---

## 6. Encendido escalonado y contrato de rollback

1. Desplegar API con **todos** los flags de escritura OFF y verificar C14.
2. Encender `SAAS_AUTH_ENABLED`; probar login A/B y `/health/db`.
3. Encender `SAAS_COMPRAS_ENABLED`; ejecutar el flujo 5C0 (alta proveedor +
   orden + recepción) contra la base staging.
4. Encender ventas/devoluciones; repetir las matrices HTTP 122 y el E2E 123
   contra las URLs staging.
5. Si cualquier criterio falla: **apagar solo el servicio SaaS**. El legacy
   permanece en su app/DSN original. No se mezclan bases ni dominios.

Runbook de retorno: `database/saas/103_runbook_backup_restore_retorno_5bs.md`
(adaptado al proyecto staging) + backup sintético antes de cada migración.

---

## 7. Autorizaciones humanas pendientes (exactas)

| ID | Autorización requerida | Alcance | Coste |
|---|---|---|---|
| **A1** | Crear proyecto Supabase NUEVO PostgreSQL 17 para staging SaaS | proyecto, base, roles, migraciones 126 | posible según plan |
| **A2** | Crear servicio Render NUEVO para FastAPI SaaS staging | build, env vars, secretos | posible |
| **A3** | Crear proyecto Vercel NUEVO para Angular SaaS staging | build `saas-local`, dominio | posible |
| **A4** | Cargar secretos en el gestor (no en Git) | `SECRET_KEY`, `SAAS_TENANT_ASSERTION_KEY`, DSNs, huella superadmin | — |
| **A5** | Configurar DNS/subdominios staging | `staging-saas`, `api-staging-saas` | posible |
| **A6** | Autorizar `git commit` y `push` de ambos repos | ramas de release y PR | — |
| **A7** | Autorizar el despliegue del commit publicado a staging | deploy API + frontend + migraciones | — |
| **A8** | Aprobar cualquier cargo/facturación derivada | Supabase/Render/Vercel/DNS | explícito |

Sin **A1–A5** no se puede ejecutar C1–C17. Sin **A6** no hay código publicado que
desplegar. Sin **A7** no se despliega. Sin **A8** no se acepta ningún cargo.

---

## 8. Verificación offline ya realizada en 5C0 (sin cloud)

```text
python database/saas/125_preflight_estatico_staging_5c0.py
  -> RESULTADO: ACEPTA (identidad exacta, 0 sockets)
python ... --env <host-legacy>
  -> RESULTADO: RECHAZO (host denegado / contradicción)
pytest tests_saas/test_guardia_despliegue_5c0.py
  -> allowlist exacta, loopback, puerto, base, usuario, APP_ENV, PG*, override
```

Esto **no sustituye** C1–C18: solo demuestra que la configuración de arranque no
puede desbloquearse con una bandera genérica ni apuntar a un host arbitrario.

---

## 9. Dictamen

| Ámbito | Dictamen |
|---|---|
| **Preparación documental cloud** | **GO** (matriz C1–C18 + recursos + autorizaciones) |
| **Ejecución cloud** | **NO-GO** (0 recursos, 0 pruebas PostgREST/TLS reales) |
| **Despliegue desde el remoto actual** | **NO-GO** (rama remota obsoleta; el trabajo está sin commit) |
