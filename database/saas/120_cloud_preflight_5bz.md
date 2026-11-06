# 120 — Preflight de staging CLOUD del SaaS (Etapa 5BZ)

**Estado:** **NO-GO** para crear o desplegar. Este documento es una lista de
trabajo verificable y una puerta de aprobación, **no** una autorización.
**Nada de este documento se ejecutó**: no se creó ningún proyecto, no se tocó
Supabase/Render/Vercel, no se migraron datos y no se usó el paquete B2.

Base de la evaluación: staging LOCAL 5BZ (PostgreSQL 17.7, FastAPI SaaS ON con
guardia de arranque, Angular SaaS con E2E real de navegador y matrices HTTP/SQL).

---

## 0. Qué cambió en 5BZ y afecta al despliegue

| Cambio 5BZ | Consecuencia para cloud |
|---|---|
| Guardia fail-closed de arranque (`app/core/launch_guard.py`) | El despliegue cloud debe usar `APP_ENV=staging` o `production` (modo «desplegado»). Un `APP_ENV` local con DSN remoto **aborta** salvo `SAAS_ALLOW_REMOTE_DB=true`. |
| Lanzador `119` con allowlist de entorno | Es una herramienta **solo local**; en cloud el gestor de secretos inyecta el entorno. No se copia el `.env` sintético. |
| 8 endpoints tenant de lectura nuevos | La allowlist del gate es método+ruta exactos: cualquier proxy/WAF debe preservar el path sin barras finales. |
| Panel SaaS pasa a render en cliente (`RenderMode.Client`); login e invitación siguen en servidor | El host de frontend necesita servir el shell SPA para `/saas/**` y no depender de SSR para rutas privadas. El SSR sigue existiendo para `/saas/login`. |
| CORS actual: `localhost:4200`, `127.0.0.1:4200`, Vercel público | **Debe** añadirse el origen exacto del Angular de staging (https) y retirar los orígenes locales en el artefacto de staging si se publica. |
| E2E real 23/23 + 96 PASS HTTP | El staging cloud debe repetir esas dos matrices como criterio de aceptación. |

---

## 1. Topología exigida (coexistencia A, ya aceptada por el propietario)

```
USUARIOS
  ├── fashionstore legacy (intacto)          → proceso + DSN + flags OFF actuales
  └── SaaS staging (nuevo, aislado)
        ├── Frontend Angular staging (build saas-local parametrizado)  → https://staging-saas.<dominio>
        ├── API SaaS (FastAPI, proceso nuevo)                           → https://api-staging-saas.<dominio>
        └── PostgreSQL 17 staging (proyecto/base NUEVOS)                → sin datos reales
```

Reglas duras:

1. **Dos procesos FastAPI independientes** (legacy y SaaS). `SAAS_AUTH_ENABLED`
   es global al proceso: no se puede encender en la instancia del legacy.
2. **Dos bases independientes**. La base SaaS staging no comparte host, base ni
   rol con producción ni con el legacy.
3. **Cero reutilización del DSN de producción.** Ningún secreto de producción se
   copia al staging.
4. El legado **no se modifica** en esta etapa (ni ACL, ni flags, ni despliegue).

---

## 2. Checklist de aprovisionamiento (bloqueante, 0 % ejecutado)

| # | Requisito | Criterio de aceptación verificable | Estado |
|---|---|---|---|
| C1 | Proyecto/instancia PostgreSQL **nueva** | host y base distintos de producción y del legacy; `SELECT current_database()` esperado | ⬜ |
| C2 | PostgreSQL **17.x** | `SHOW server_version` ⇒ 17.x (no 18) | ⬜ |
| C3 | Rol `fs_saas_runtime` | LOGIN, **NOSUPERUSER**, **NOBYPASSRLS**, **NOINHERIT**; sin `SELECT` en `public.usuario` | ⬜ |
| C4 | Rol `saas_admin_runtime` | LOGIN, NOSUPERUSER, NOBYPASSRLS; solo `EXECUTE` sobre `saas_admin.*` | ⬜ |
| C5 | Owners NOLOGIN | `saas_rls_owner`, `saas_admin_owner`, `saas_actor_sql_owner`, `saas_personal_owner` | ⬜ |
| C6 | Roles de plataforma | `anon`, `authenticated`, `service_role` presentes **y sin privilegios** en `public`/`saas`/`saas_admin` | ⬜ |
| C7 | Secretos en gestor externo | `SECRET_KEY`, `SAAS_TENANT_ASSERTION_KEY` (≥48 bytes), huellas superadmin y ambos DSN fuera de Git y del frontend | ⬜ |
| C8 | Migraciones curadas | manifiesto `113` (F00R…F17, 18 fases) con checksums; **excluida** `20261023000400_cuatro_tenants_demo_local.sql`; incluye `20261030001300_*` | ⬜ |
| C9 | Estructura esperada | 41 tablas base + `saas.*`; `49` tablas con FORCE RLS; `saas_migracion.version_aplicada` = 18 filas | ⬜ |
| C10 | ACL de plataforma | `anon/authenticated/service_role` con **0** concesiones en `public`/`saas`/`saas_admin` | ⬜ |
| C11 | RLS efectivo | sin aserción ⇒ 0 filas; aserción de A no ve B; aserción falsificada ⇒ NULL/0 | ⬜ |
| C12 | Datos | **sin datos reales**: solo fixtures sintéticos A/B (o base vacía + alta por superadmin) | ⬜ |
| C13 | TLS | HTTPS obligatorio en API y frontend; `SAAS_SUPERADMIN_REQUIRE_HTTPS=true` + proxies de confianza listados | ⬜ |
| C14 | Flags al arrancar | **todos OFF**; encendido escalonado §5 | ⬜ |
| C15 | PostgREST/Data API | 401/403 para `anon` y `authenticated` sobre tablas de negocio (§3) | ⬜ |
| C16 | CORS | solo el origen exacto del frontend de staging; sin orígenes locales en el artefacto publicado | ⬜ |
| C17 | Observabilidad | logs sin DSN/tokens; auditoría global operativa; `/health` y `/health/db` sin datos sensibles | ⬜ |
| C18 | Rollback probado | proceso legacy intacto + base SaaS descartable; runbook §6 ejecutado en ensayo | ⬜ |

---

## 3. Prueba PostgREST real (lo que el local no puede dar)

Con la Data API del proyecto **nuevo** (nunca la de producción) y claves de prueba:

```bash
# 1) anon sobre tabla de negocio: 401/403, NUNCA 200 con datos
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/producto?select=id&limit=1" -H "apikey: <ANON_KEY>"

# 2) anon sobre RPC de `saas`: 401/403
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/rpc/tenant_actual" -H "apikey: <ANON_KEY>"

# 3) authenticated con JWT de empresa A: 401/403 sobre tablas
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/usuario?select=id&limit=1" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <JWT_A>"

# 4) service_role: NO debe usarse desde el frontend; si se prueba, registrar el riesgo
```

**Criterio:** ningún `200` con datos de negocio. Un `200` detiene la activación y
obliga a auditar el `GRANT` que lo permite; **no** se corrige ampliando privilegios.

SQL de comprobación (0 filas esperadas):

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

## 4. Variables del proceso SaaS staging (valores de muestra, sin credenciales)

```dotenv
APP_ENV=staging                       # modo «desplegado» de la guardia 5BZ
SAAS_LOCAL_ONLY=                      # NUNCA en cloud
SAAS_ALLOW_REMOTE_DB=                 # NUNCA en cloud
DATABASE_URL=postgresql+psycopg://fs_saas_runtime:<gestor>@<host>:5432/<base_saas_staging>
SAAS_ADMIN_DATABASE_URL=postgresql+psycopg://saas_admin_runtime:<gestor>@<host>:5432/<base_saas_staging>
SECRET_KEY=<gestor>
SAAS_TENANT_ASSERTION_KEY=<gestor, 48+ bytes>
SAAS_AUTH_ENABLED=false               # arranque cerrado
SAAS_SUPERADMIN_ENABLED=false
SAAS_SUPERADMIN_KEY_SHA256=v1:<hex64> # solo huella; la clave vive en el gestor
SAAS_SUPERADMIN_REQUIRE_HTTPS=true
SAAS_SUPERADMIN_TRUSTED_PROXIES=<ip-proxy>
SAAS_ACTOR_SQL_OWNER=saas_actor_sql_owner
SAAS_SP_SQL_OWNER=                    # NO definir: SP de reserva son INVOKER
SAAS_RESERVAS_ENABLED=false
SAAS_VENTAS_ENABLED=false
SAAS_COMPRAS_ENABLED=false
SAAS_DEVOLUCIONES_ENABLED=false
SAAS_INVITATION_RATE_LIMIT_ENABLED=true
```

Frontend (build `saas-local` parametrizado por entorno, **sin claves**):
`environment.saas-local.ts` → `apiUrl: 'https://api-staging-saas.<dominio>/'`,
`saasMode: true`. El artefacto no debe contener ningún DSN ni token.

---

## 5. Encendido escalonado y reversión

1. OFF total → `/health` 200 y `/health/db` 200 (con DSN SaaS).
2. `SAAS_AUTH_ENABLED=true` → login A/B, `/auth/tenant/me`, aislamiento, revocación.
3. Seeds/alta mínima de empresa + administrador inicial (superadmin con clave).
4. `SAAS_RESERVAS_ENABLED=true` → prevalidar, crear, cancelar.
5. `SAAS_VENTAS_ENABLED=true` → venta contado + replay idempotente + stock delta 1.
6. `SAAS_COMPRAS_ENABLED=true` → orden + envío + recepción.
7. `SAAS_DEVOLUCIONES_ENABLED=true` → líneas de venta + crear + aprobar + procesar.
8. Catálogos de selección (5BZ) activos desde el paso 2: tallas, colores,
   ciudades, proveedores, temporadas, variantes, inventario.

En cada paso: matriz HTTP (`122` adaptada) + E2E navegador (`123` adaptado) +
`/health`. Un fallo de aislamiento A/B ⇒ apagar `SAAS_AUTH_ENABLED` y restaurar
respaldo del destino staging.

---

## 6. Rollback

| Escenario | Acción | Tiempo objetivo |
|---|---|---|
| Flag nuevo rompe un flujo | apagar el flag correspondiente (orden inverso) | ≤ 5 min |
| Fuga entre empresas | apagar `SAAS_AUTH_ENABLED`, retirar el frontend de staging, abrir incidente | inmediato |
| Migración defectuosa | restaurar respaldo previo del staging (base descartable) | ≤ 30 min |
| Frontend defectuoso | volver al artefacto anterior del staging | ≤ 10 min |
| Afectación al legado | **no aplica por diseño**: legado en proceso/base propios; verificar y documentar | — |

El legado permanece operativo durante todo el proceso: no se apaga, no se
migra y no se le revocan ACL.

---

## 7. Aprobaciones humanas necesarias (no ejecutadas)

1. Crear el proyecto/instancia PostgreSQL 17 y su Data API (si aplica facturación, aprobarla).
2. Crear el servicio de API staging y el del frontend staging, con DNS/TLS propios.
3. Cargar los secretos en el gestor del proveedor.
4. Autorizar la ejecución de las 18 fases curadas **en la base nueva**.
5. Autorizar el cambio de `apiUrl` y CORS para el artefacto de staging.
6. Aprobar el runbook de rollback de §6 y la ventana de encendido escalonado.
7. Declarar explícitamente que el staging **no** usará datos reales ni el paquete B2.

---

## 8. Dictamen

- **CLOUD_STAGING: NO-GO** mientras C1–C18 no estén cerrados y la prueba
  PostgREST real no devuelva 401/403.
- **PRODUCCIÓN: NO-GO** hasta staging cloud aprobado, repetición de las matrices
  HTTP/E2E contra ese entorno, plan de retorno firmado y liberación explícita.
- Este documento **no** autoriza aprovisionar nada.
