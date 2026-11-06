# 5BS / Etapa J — Runbook de staging y activación (con reversión)

**Estado:** CANDIDATO LISTO PARA STAGING. **Staging real: NO-GO hasta cerrar las dependencias humanas.**
**Producción: NO-GO.**

---

## 0. Reglas que no se negocian

1. **Staging primero, en un proyecto/cluster separado de producción.** PostgreSQL 17, sin datos reales de clientes salvo autorización B2.
2. **Nunca** se habilita `SAAS_AUTH_ENABLED=true` en la misma instancia que sirve el sistema legacy sin un plan de corte: el middleware SaaS bloquea rutas comerciales legacy y rompería el primer parcial.
3. **Nunca** se aplican migraciones directamente a producción sin `103` ejecutado y verificado.
4. Los flags se encienden **en orden** y cada paso tiene checkpoint y reversión.
5. `01200` y `01100` son las **últimas** migraciones y solo reducen privilegios: no cambian el acceso del backend (que conecta como `postgres` por TCP), pero cierran la Data API.

---

## 1. Preflight de staging (checkpoint 0)

| # | Verificación | Comando / evidencia | Criterio |
|---|---|---|---|
| 1 | Rama y candidato | `git rev-parse --short HEAD`, `git status --short` | commit/tag congelado; sin cambios sin revisar |
| 2 | Manifiesto | `python database/saas/86_preflight_migracion_pg17_5bp.py --manifiesto database/saas/108_manifiesto_migracion_esquema_real_5bs.json --plan` | `VALIDACION ESTATICA: OK` |
| 3 | Simulacro offline | `python database/saas/106_simulacro_staging_5bs.py` | cero `bloqueos` |
| 4 | Motor | `psql --version`, `pg_dump --version` | 17.x |
| 5 | Proyecto separado | host/puerto distintos de producción | confirmado por el operador |
| 6 | Secretos | `91_guia_secretos_staging_5bp.md` | gestor externo, no `.env` versionado |
| 7 | Respaldo del destino | `103` §2–§4 | existe y fue restaurado en un cluster de prueba |
| 8 | Consumidores | `EVIDENCIA_CONSUMIDORES_5BS_*.json` | sin consumidor directo observable + decisión escrita |

---

## 2. Aprovisionamiento de roles (checkpoint 1)

Los roles **no** los crea la aplicación. Se crean una vez, con contraseñas generadas por el gestor de secretos:

| Rol | Atributos | Uso |
|---|---|---|
| `fs_saas_runtime` | LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT | `DATABASE_URL` de FastAPI (plano tenant) |
| `saas_admin_runtime` | LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT | `SAAS_ADMIN_DATABASE_URL` (plano global) |
| `saas_rls_owner` | NOLOGIN, NOSUPERUSER, NOBYPASSRLS | propietario de funciones de contexto/RLS |
| `saas_admin_owner` | NOLOGIN | propietario de funciones `saas_admin.*` |
| `saas_actor_sql_owner` | NOLOGIN | verificador de actor |
| `saas_personal_owner` | NOLOGIN | SP de personal/RBAC |

Verificación (debe devolver `0` filas para los tres roles de plataforma):

```sql
SELECT r.rolname, c.relname, a.privilege_type
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(rn)
  JOIN pg_roles r ON r.rolname = v.rn
  CROSS JOIN LATERAL aclexplode(c.relacl) a
 WHERE a.grantee = r.oid
   AND n.nspname IN ('public','saas','saas_admin','saas_migracion');
```

---

## 3. Migraciones (checkpoint 2)

Orden **exacto** del manifiesto `108` (nunca alfabético):

```
F00R  verificación del esquema histórico restaurado
F02R  semilla        (solo laboratorio; PROHIBIDA en staging real)
F03   secuencias históricas
F04   roles tenant
F05   roles globales
F06   00100–00300    núcleo portable
F07   00400          RLS principal restringido
F08   00500          compatibilidad 5BL
F08R  00550          compatibilidad esquema real (catálogos + ACL)
F09   00600          operativo 5BM
F10   00700          compras/devoluciones 5BN
F11   00800          personal/RBAC 5BO
F12   00900          superadmin hardening
F13   01000          invitaciones + rate limit
F15   01100          cierre ACL de plataforma
F16   01200          cierre ACL de superficies no públicas   ← Etapa J
F14   fixtures       (solo laboratorio; PROHIBIDA en staging real)
```

Reglas:
- `F02R` y `F14` **no se aplican** en staging con datos reales.
- Si `01100` falla, **no** se continúa: el estado queda a medias y se restaura el respaldo.
- `01200` incluye verificación fail-closed; si detecta una concesión residual, aborta y no deja cambios (transacción única).

---

## 4. Variables de entorno (checkpoint 3)

| Variable | Valor en staging | Nota |
|---|---|---|
| `APP_ENV` | `staging` | nunca `production` en staging |
| `SAAS_AUTH_ENABLED` | `false` → `true` en el paso 4 | arranque siempre en `false` |
| `SAAS_SUPERADMIN_ENABLED` | `false` → `true` | exige `SAAS_SUPERADMIN_KEY_SHA256` |
| `SAAS_SUPERADMIN_KEY_SHA256` | `kid:hex64[,kid:hex64]` | nunca la clave en claro |
| `SAAS_ADMIN_DATABASE_URL` | DSN `saas_admin_runtime` | sin él, CU01/CU02 → 503 |
| `SAAS_TENANT_ASSERTION_KEY` | 48+ bytes aleatorios | sin ella, RLS deniega todo |
| `SAAS_ACTOR_SQL_OWNER` | `saas_actor_sql_owner` | verificador de actor |
| `SAAS_RESERVAS_ENABLED` | `false` → `true` | tras smoke de catálogo |
| `SAAS_VENTAS_ENABLED` | `false` → `true` | tras reservas |
| `SAAS_COMPRAS_ENABLED` | `false` → `true` | tras ventas |
| `SAAS_DEVOLUCIONES_ENABLED` | `false` → `true` | al final |
| `SAAS_INVITATION_RATE_LIMIT_ENABLED` | `true` | exige admin DSN |
| `SAAS_SUPERADMIN_REQUIRE_HTTPS` | `true` | con proxy listado |

Ningún secreto se escribe en el repositorio, en `README`, en tickets ni en logs.

---

## 5. Secuencia de activación y smoke tests

**Paso 1 — Arranque cerrado.** Todos los flags SaaS en `false`. Verificar `/health` y rutas legacy. Checkpoint: sistema legacy responde igual que antes.

**Paso 2 — Login tenant.** `SAAS_AUTH_ENABLED=true`. Smoke:
- login empresa A → 200; `GET /auth/me` → empresa A;
- login empresa B → 200; catálogo A/B disjunto;
- intento de acceso cruzado → 404/403;
- revocación de membresía → 403 inmediato.

**Paso 3 — Operación comercial.** Encender reservas → ventas → compras → devoluciones, un flag por vez. En cada uno: una operación real de staging + replay idempotente.

**Paso 4 — Superadmin.** `SAAS_SUPERADMIN_ENABLED=true`, alta de empresa, invitación de un solo uso y aceptación (requiere rate limit operativo).

**Paso 5 — Data API.** Verificar el contrato elegido:

```bash
# Sin privilegios: debe devolver 401/403, NO 200
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/producto?select=id&limit=1" \
  -H "apikey: <ANON_KEY>"

# RPC de una función de `saas`: también debe ser 401/403
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<staging>.supabase.co/rest/v1/rpc/es_principal_runtime" \
  -H "apikey: <ANON_KEY>"
```

- Resultado esperado: **ningún 200** con datos de negocio.
- Si aparece un 200, **se detiene la activación** y se investiga qué GRANT lo permite.

**Paso 6 — Cierre.** Congelar flags, registrar versión desplegada, guardar evidencias y programar el corte de producción **solo** con B2 cerrado.

---

## 6. Reversión

| Escenario | Acción | Tiempo objetivo |
|---|---|---|
| Fallo en migración previa a `01100` | Restaurar respaldo del destino; volver la versión anterior de la app | ≤ 60 min |
| Fallo en `01100`/`01200` | La transacción revierte sola; restaurar respaldo si el estado quedó dudoso | ≤ 30 min |
| Fallo funcional con flags encendidos | Apagar flags en orden inverso (devoluciones → compras → ventas → reservas → superadmin → auth) | ≤ 5 min |
| Fallo de aislamiento A/B | Apagar `SAAS_AUTH_ENABLED`, restaurar respaldo, abrir incidente | inmediato |
| Fallo de Data API inesperado | `01200` no se revierte a mano: se restaura el respaldo previo a `01100` | ≤ 60 min |

**Nunca** se “arregla” un fallo de privilegios concediendo `GRANT` amplios a `anon`, `authenticated` o `service_role`.

---

## 7. Criterios de aceptación de staging

- [ ] Todos los flags encienden y apagan sin reiniciar datos.
- [ ] Smoke A/B completo (login, catálogo, venta, reserva, compra, devolución).
- [ ] 0 accesos cruzados entre empresas.
- [ ] 0 respuestas 200 de la Data API sobre tablas de negocio.
- [ ] 0 `SECURITY DEFINER` con propietario LOGIN o sin `search_path`.
- [ ] Restauración del respaldo verificada con invariantes iguales.
- [ ] Reversión probada al menos una vez (apagar flags y restaurar).
- [ ] Evidencias archivadas con hash.

## 8. Dependencias humanas para pasar a GO de staging

1. **B2**: autorización y ejecución del respaldo con datos reales (`104`).
2. **Consumidores**: decisión escrita de que la Data API se cierra (contrato A) o lista de excepciones mínimas (contrato B).
3. **Secretos**: aprovisionados en gestor externo.
4. **Proyecto staging**: creado, separado y con ventana aprobada.

Con las cuatro cerradas, el simulacro `106` debe pasar a `staging: GO`. Producción seguirá `NO-GO` hasta el ensayo real de staging, el restore verificado y la aprobación de despliegue.
