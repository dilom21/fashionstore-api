# 117 — Preparación del staging CLOUD del SaaS (Etapa 5BX, sin desplegar)

**Estado:** **NO-GO** para crear/desplegar. Este documento es una lista de
trabajo verificable, no una autorización.
**Base de la evaluación:** staging LOCAL 5BX aprobado (71/71 HTTP, 11/11 SQL,
OFF legacy 200 con DSN del legado).
**Prohibido en esta etapa:** crear proyectos, tocar Supabase/Render/Vercel,
migrar datos reales, usar el paquete cifrado B2.

---

## 1. Diferencia entre lo probado y lo que falta

Lo probado en 5BX es un staging local con PostgreSQL 17 real, RLS real, roles
restringidos reales y HTTP real. **No** se probó:

- PostgREST real (la Data API es un servicio aparte; en local no existe).
- Supabase Auth/Storage/roles de plataforma reales.
- TLS, proxy, dominios, secreto en gestor externo.
- Cliente Angular consumiendo el backend SaaS.

Por eso el staging local es condición necesaria, **no suficiente**.

## 2. Checklist de aprovisionamiento (bloqueante)

| # | Requisito | Criterio de aceptación | Estado |
|---|---|---|---|
| C1 | Proyecto **separado** de producción | host/puerto/proyecto distintos; sin DSN compartido | ⬜ |
| C2 | PostgreSQL **17** (no 18) | `SHOW server_version` = 17.x | ⬜ |
| C3 | Rol `fs_saas_runtime` | LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT | ⬜ |
| C4 | Rol `saas_admin_runtime` | LOGIN, NOSUPERUSER, NOBYPASSRLS | ⬜ |
| C5 | Owners NOLOGIN | `saas_rls_owner`, `saas_admin_owner`, `saas_actor_sql_owner`, `saas_personal_owner` | ⬜ |
| C6 | Roles de plataforma | `anon`, `authenticated`, `service_role`, `supabase_admin` presentes (los crea la plataforma) | ⬜ |
| C7 | Secretos en gestor externo | HMAC, superadmin, DSNs fuera de Git y de `.env` versionado | ⬜ |
| C8 | Semilla ficticia | sin PII; `empresa-a/b` sintéticas | ⬜ |
| C9 | Pipeline curado | manifiesto `113` (F00R..F17) con checksums verificados | ⬜ |
| C10 | ACL `01100` y `01200` | 0 concesiones a roles de plataforma | ⬜ |
| C11 | `01300` (nuevo 5BX) | política de diccionarios incluye `saas_personal_owner` | ⬜ |
| C12 | Red | sólo TLS; `SAAS_SUPERADMIN_REQUIRE_HTTPS=true` y proxy listado | ⬜ |
| C13 | Flags de producto | arranque en OFF; encendido escalonado con checkpoint | ⬜ |
| C14 | Observabilidad | logs sin secretos; auditoría `saas.bitacora_global` operativa | ⬜ |

## 3. Prueba PostgREST real (la que el local NO puede dar)

Con la Data API del proyecto **separado** y credenciales de prueba:

```bash
# 1) Tabla de negocio con anon: debe ser 401/403, NUNCA 200 con datos
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/producto?select=id&limit=1" \
  -H "apikey: <ANON_KEY>"

# 2) RPC de función de `saas`: también 401/403
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/rpc/es_principal_runtime" \
  -H "apikey: <ANON_KEY>"

# 3) authenticated con JWT de un usuario de la empresa A: 401/403 sobre tablas
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/usuario?select=id&limit=1" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <JWT_A>"
```

**Criterio:** ningún `200` con datos de negocio. Un `200` detiene la activación
y obliga a auditar qué `GRANT` lo permite (no se corrige ampliando privilegios).

Complementario en SQL (ya validado en local, debe repetirse en cloud):

```sql
-- 0 filas esperadas
SELECT r.rolname, c.relname, a.privilege_type
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(rn)
  JOIN pg_roles r ON r.rolname = v.rn
  CROSS JOIN LATERAL aclexplode(c.relacl) a
 WHERE a.grantee = r.oid AND n.nspname IN ('public','saas','saas_admin');
```

## 4. Variables de entorno (valores de muestra, sin credenciales)

```dotenv
APP_ENV=staging
SAAS_AUTH_ENABLED=false              # arranque cerrado
SAAS_SUPERADMIN_ENABLED=false
SAAS_SUPERADMIN_KEY_SHA256=v1:<hex64>   # sólo huella; la clave vive en el gestor
SAAS_SUPERADMIN_REQUIRE_HTTPS=true
SAAS_SUPERADMIN_TRUSTED_PROXIES=<ip-proxy>
SAAS_TENANT_ASSERTION_KEY=<48+ bytes desde gestor>
SAAS_ACTOR_SQL_OWNER=saas_actor_sql_owner
SAAS_RESERVAS_ENABLED=false
SAAS_VENTAS_ENABLED=false
SAAS_COMPRAS_ENABLED=false
SAAS_DEVOLUCIONES_ENABLED=false
SAAS_INVITATION_RATE_LIMIT_ENABLED=true
DATABASE_URL=postgresql+psycopg://fs_saas_runtime:<gestor>@<host>:5432/<base>
SAAS_ADMIN_DATABASE_URL=postgresql+psycopg://saas_admin_runtime:<gestor>@<host>:5432/<base>
```

`SAAS_SP_SQL_OWNER` **no** se define mientras los SP de reserva sean INVOKER
(caso del pipeline 113). Definirlo provoca 503 por “SP esperado DEFINER”.

## 5. Encendido escalonado y reversión

1. OFF total → `/health` y rutas legacy del proceso legado.
2. `SAAS_AUTH_ENABLED=true` → login A/B, aislamiento, revocación.
3. `SAAS_RESERVAS_ENABLED=true` → prevalidar + crear + cancelar.
4. `SAAS_VENTAS_ENABLED=true` → venta contado + replay idempotente.
5. `SAAS_COMPRAS_ENABLED=true` → orden + envío + recepción.
6. `SAAS_DEVOLUCIONES_ENABLED=true` → crear + aprobar + procesar.
7. `SAAS_SUPERADMIN_ENABLED=true` → alta de empresa + administrador inicial.

Reversión: apagar flags en orden inverso (≤5 min). Fallo de aislamiento A/B:
apagar `SAAS_AUTH_ENABLED`, restaurar respaldo del destino, abrir incidente.
`01100`/`01200` no se revierten a mano: se restaura el respaldo previo.

## 6. Coexistencia con el FashionStore legacy

| Opción | Descripción | Riesgo |
|---|---|---|
| A (recomendada) | Proceso/instancia SaaS separada; el legado conserva su DSN y su flag OFF | bajo |
| B | Misma instancia con router de compatibilidad explícito | alto: no existe hoy |
| C | Mantener OFF hasta completar la migración Angular P0 | bajo, retrasa activación |

Medido en 5BX: con OFF y el DSN restringido, el login legacy responde **500**
(`fs_saas_runtime` no tiene privilegios sobre `public.usuario`). Apagar el flag
no sustituye un DSN adecuado.

## 7. Dependencias humanas antes de pedir autorización cloud

1. Proyecto Supabase/Postgres 17 separado, con ventana aprobada.
2. Gestor de secretos con las claves del punto 4.
3. Decisión escrita sobre la Data API (cerrada o excepciones mínimas).
4. Autorización para ejecutar migraciones curadas **en ese** proyecto.
5. Autorización para modificar `fashionstore-web` (hoy prohibido).
6. Aprobación del plan de rollback y del plan de coexistencia.

## 8. Dictamen

**CLOUD STAGING: NO-GO** hasta cerrar C1–C14 y la prueba PostgREST real.
**PRODUCCIÓN: NO-GO** hasta que el staging cloud esté desplegado, aprobado, con
frontend integrado (P0 de `116`), restore verificado y liberación explícita.
La autorización B2 no concede ninguno de estos permisos.
