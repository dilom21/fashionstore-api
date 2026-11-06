# 121 — Inventario de release 5C0 (candidato, SIN publicar)

**Sello:** 2026-10-31 (America/La_Paz) · **Etapa:** 5C0
**Autorización vigente:** edición LOCAL de ambos repos + pruebas contra el staging LOCAL sintético.
**NO autorizado y NO ejecutado:** `git commit`, `push`, `merge`, `rebase`, `reset`, `checkout`, `stash`, `clean`, creación de recursos cloud, despliegue, costos, Supabase/Render/Vercel productivos, `.env` productivos, paquete B2.

> Este documento es un **inventario verificable**, no una publicación. El remoto
> `origin` contiene solo código anterior: **no** debe usarse un “deploy from GitHub”
> de la rama remota para el staging SaaS.

---

## 1. Repositorios, ramas y punto de partida

| Repo | Ruta local | Rama | HEAD local | Remoto |
|---|---|---|---|---|
| Backend | `C:\SI2_Parcial1\fashionstore-api` | `feat/saas-multitenant-parcial2` | `cc1be74` | `origin` GitHub `dilom21/fashionstore-api` |
| Frontend | `C:\SI2_Parcial1\fashionstore-web` | `main` | `7bc66b4` | `origin` GitHub `dilom21/fashionstore-web` |

Todo el trabajo SaaS de 5BK–5C0 está **sin commit**. El remoto no representa el
estado local y no puede desplegarse como candidato.

---

## 2. Backend `fashionstore-api`

### 2.1 Estado Git (solo lectura)

```text
git status --short              -> 149 entradas
git diff --name-only (tracked)  ->  21 archivos modificados
git ls-files --others           -> 463 archivos no rastreados
```

Las 149 entradas incluyen 128 entradas no rastreadas (Git agrupa directorios) que
representan 463 archivos; la mayor parte es evidencia histórica en
`database/saas/evidencias/` (327 archivos) y documentación de etapas.

### 2.2 Archivos rastreados modificados (21) — obligatorios para el release

```text
app/core/config.py
app/core/database.py
app/core/saas_route_gate.py
app/core/saas_superadmin_auth.py
app/core/tenant_dependencies.py
app/main.py
app/modules/autenticacion_seguridad/api/tenant_router.py
app/modules/autenticacion_seguridad/repositories/tenant_repository.py
app/modules/catalogo/api/tenant_router.py
app/modules/catalogo/api/tenant_write_router.py
app/modules/catalogo/repositories/tenant_repository.py
app/modules/catalogo/repositories/tenant_write_repository.py
app/modules/reservas/api/tenant_reserva_write_router.py
app/modules/sucursales/api/saas_superadmin_router.py
app/modules/sucursales/repositories/saas_initial_admin_repository.py
app/modules/sucursales/repositories/saas_superadmin_repository.py
app/modules/usuarios/api/tenant_membership_router.py
requirements.txt
tests_saas/test_cu04_membresias_empresa_sqlite_offline.py
tests_saas/test_reserva_transaccional_inerte_5al.py
tests_saas/test_tenant_empresa_sucursales_planes_http.py
```

Delta rastreado: **21 archivos, 1 336 inserciones, 84 eliminaciones**
(`git diff --stat`). Incluye 5BZ (guardia, gate, contratos) y 5C0 (gate de
proveedores).

### 2.3 Archivos NUEVOS obligatorios (código, migraciones, pruebas, docs)

**5C0 (esta etapa)**

```text
supabase/migrations/20261031001400_saas_proveedores_tenant_5c0.sql
app/modules/proveedores/repositories/tenant_proveedor_repository.py
app/modules/proveedores/api/tenant_proveedor_router.py
tests_saas/test_tenant_proveedores_5c0.py
tests_saas/test_guardia_despliegue_5c0.py
database/saas/124_proveedores_tenant_5c0.py
database/saas/125_preflight_estatico_staging_5c0.py
database/saas/125_env_staging_5c0.example
database/saas/126_manifiesto_migracion_staging_5c0.json
database/saas/121_release_5c0.md          (este documento)
database/saas/122_cloud_gate_5c0.md
odd/tasks/saas-5c0.md
```

**5BK–5BZ (preexistentes, igualmente sin commit y necesarios)**

- Código: `app/core/launch_guard.py`, `app/core/saas_rls_assertion.py`,
  `app/core/saas_admin_db.py`, `app/core/saas_invitation_rate_limit.py`,
  `app/core/saas_superadmin_guard.py`, `app/modules/inventario/api/tenant_inventario_router.py`,
  `app/modules/inventario/repositories/tenant_inventario_repository.py`,
  routers/repositorios tenant de compras, devoluciones, ventas, carrito, reservas,
  catálogo, usuarios y sucursales (los listados completos están en
  `REP_5BZ_20261031_202746.md` §2).
- Pruebas: `tests_saas/test_guardia_arranque_5bz.py`,
  `test_tenant_contratos_5bz.py`, `test_compras_devoluciones_5bn.py`,
  `test_nucleo_saas_5bo.py`, `test_operativo_saas_5bm.py`, entre otras.
- Herramientas/evidencias: `database/saas/115_staging_local_5bx.py`,
  `119_servidor_saas_local_5by.py`, `120_cloud_preflight_5bz.md`,
  `121_guardia_arranque_5bz.py`, `122_contratos_tenant_5bz.py`,
  `123_e2e_navegador_5bz.py`, `113_manifiesto_migracion_staging_5bx.json`,
  `117_checklist_staging_cloud_5bx.md`.

### 2.4 Secuencia exacta de migraciones para staging

El manifiesto **`126_manifiesto_migracion_staging_5c0.json`** deriva de 113 y
agrega la fase **F18**. Fases = **19**; migraciones SQL aplicables = **14**:

| # | Archivo | Fase |
|---|---|---|
| 1 | `20261030000100_saas_core_portable.sql` | F06 |
| 2 | `20261030000200_saas_empresa_id_portable.sql` | F06 |
| 3 | `20261030000300_saas_integridad_tenant_portable.sql` | F06 |
| 4 | `20261030000400_saas_rls_principal_restringido_portable.sql` | F07 |
| 5 | `20261030000500_saas_compatibilidad_5bl.sql` | F08 |
| 6 | `20261030000550_saas_compatibilidad_esquema_real_5br.sql` | F08R |
| 7 | `20261030000600_saas_operativo_5bm.sql` | F09 |
| 8 | `20261030000700_saas_compras_devoluciones_5bn.sql` | F10 |
| 9 | `20261030000800_saas_personal_rbac_5bo.sql` | F11 |
| 10 | `20261030000900_saas_superadmin_hardening_5bo.sql` | F12 |
| 11 | `20261030001000_saas_invitaciones_rate_limit_5bp.sql` | F13 |
| 12 | `20261030001100_saas_acl_plataforma_esquema_real_5br.sql` | F15 |
| 13 | `20261030001200_saas_acl_superficies_5bs.sql` | F16 |
| 14 | `20261030001300_saas_rbac_catalogos_personal_owner_5bx.sql` | F17 |
| **15** | **`20261031001400_saas_proveedores_tenant_5c0.sql`** | **F18** |

**Excluidas de staging y producción** (manifiesto 126, `exclusiones`):

```text
supabase/migrations/20261023000100_saas_base_tables.sql
supabase/migrations/20261023000200_vanter_tenancy_historica.sql
supabase/migrations/20261023000300_integridad_tenant_local.sql
supabase/migrations/20261023000400_cuatro_tenants_demo_local.sql   <-- SEED DEMO
database/saas/75_esquema_historico_reconstruido_5bl.sql
database/saas/74/76/77/78_laboratorio_*.py (herramientas de laboratorio)
```

La migración 5C0 ya fue aplicada **solo** al staging local
`fashionstore_lab_5bx_staging`; su SHA-256 es
`4e7334b1f9bc96635ededc7c96ba59d29aaf4fd94aa5233311b7faee81ece9cb`.

### 2.5 Dependencias backend

`requirements.txt` (versiones fijas, sin rangos): FastAPI `0.141.1`,
Pydantic `2.13.5`, SQLAlchemy `2.0.52`, psycopg `3.3.4`, alembic `1.19.1`,
PyJWT `2.13.0`, argon2-cffi `25.1.0`, `email-validator 2.3.0`, uvicorn `0.52.4`.
No se requieren paquetes nuevos para 5C0 (el DTO usa validación propia, sin
`EmailStr`). `requirements.txt` está modificado respecto de HEAD y debe publicarse.

### 2.6 Revisión mecánica de secretos/versionado (sin leer contenido)

| Comprobación | Resultado |
|---|---|
| `.env` rastreado | **No** (`git check-ignore`: `.gitignore:7`) |
| Archivos `.env`, `secrets*.json`, `.dump`, `.fsb2`, `.pem`, `.key` rastreados | **0** |
| Archivos con esos nombres no rastreados dentro del repo | **0** |
| Literales DSN con usuario:contraseña en `app/ database/ tests*/ supabase/` | **0 archivos** |
| `secrets_5bx.json` | Fuera del repo (`%LOCALAPPDATA%\Temp\opencode\fs_staging_5bx\`), nunca versionado |
| Paquete B2 `base.dump.fsb2` | Fuera del repo, no abierto, no copiado, no usado en 5C0 |

### 2.7 Qué NO debe publicarse ni empaquetarse

```text
.env  (raíz del backend)                        -> secreto local
%LOCALAPPDATA%\Temp\opencode\fs_staging_5bx\   -> laboratorio, secretos, pgdata
%LOCALAPPDATA%\Temp\opencode\fs_b2_real_*\     -> paquete B2 del propietario
database/saas/evidencias/                      -> evidencias internas (327 archivos)
__pycache__/  .pytest_cache/  .angular/        -> artefactos de build
*.dump  *.fsb2  *.log  *.zip                    -> datos/paquetes locales
CTX_*.md  PROMPT_*.md                          -> insumos del propietario (decisión: no publicar salvo que se quiera)
```

Recomendación: publicar **código, migraciones, pruebas y documentación técnica
121/122/125/126**; mantener `evidencias/`, `CTX_*`, `PROMPT_*` y `odd/` fuera del
repositorio público o en un commit privado separado.

---

## 3. Frontend `fashionstore-web`

### 3.1 Estado Git (solo lectura)

```text
git status --short              -> 15 entradas (10 modificadas + 5 no rastreadas de directorio/archivo)
git diff --stat (tracked)       -> 10 archivos, 206 inserciones, 8 eliminaciones
git ls-files --others           -> 129 archivos no rastreados
```

### 3.2 Archivos rastreados modificados (10) — obligatorios

```text
.gitignore                                   (e2e-results.json, test-results/, playwright-report/)
angular.json                                 (build/serve saas-local)
package.json                                 (@playwright/test + scripts e2e)
package-lock.json
src/app/app.config.ts
src/app/app.routes.server.ts                 (panel SaaS en RenderMode.Client)
src/app/app.routes.ts
src/app/app.ts
src/app/core/interceptors/auth.interceptor.ts
src/environments/environment.ts
```

### 3.3 Nuevos obligatorios (129 archivos no rastreados)

| Grupo | Archivos | Nota |
|---|---|---|
| `src/app/saas/pages/**` | 67 | 24 pantallas + specs |
| `src/app/saas/services/**` | 27 | incluye `saas-proveedores.service.ts` + spec 5C0 |
| `src/app/saas/utils/**` | 5 | montos, fechas, operación idempotente |
| `src/app/saas/layout/**` | 4 | shell del panel |
| `src/app/saas/guards/**` | 3 | sesión/permisos |
| `src/app/saas/interceptors/**` | 2 | errores tenant |
| `src/app/saas/models/tenant.models.ts` | 1 | incluye DTOs de proveedor 5C0 |
| `src/app/saas/saas.routes.ts`, `saas.config.ts` | 2 | rutas/DI |
| `src/environments/environment.saas-local.ts` | 1 | configuración SaaS local |
| `src/app/app.routes.server.spec.ts` | 1 | regresión D1 |
| `e2e/**` | 15 | Playwright (saas + legacy + soporte) |
| `playwright.config.ts` | 1 | Edge, sin descargar binarios |

**5C0 específico**

```text
src/app/saas/pages/compras/nuevo-proveedor/nuevo-proveedor.ts
src/app/saas/pages/compras/nuevo-proveedor/nuevo-proveedor.html
src/app/saas/pages/compras/nuevo-proveedor/nuevo-proveedor.css
src/app/saas/pages/compras/nuevo-proveedor/nuevo-proveedor.spec.ts
src/app/saas/services/saas-proveedores.service.ts
src/app/saas/services/saas-proveedores.service.spec.ts
```

Modificados 5C0 dentro de archivos ya tocados: `saas-compras.page.ts`,
`saas-compras.page.html`, `saas-compras.page.spec.ts`,
`src/app/saas/models/tenant.models.ts`, `e2e/saas/07-compras.spec.ts`.

### 3.4 Dependencias frontend

Angular `^22.1.0` (+ CLI/build `^22.1.6`), RxJS `~7.8.0`, TypeScript `~6.0.2`,
Vitest `^4.0.8`, Playwright `^1.64.1` (dev), `@stripe/stripe-js ^9.16.0`,
`jspdf ^4.2.1`, `chart.js ^4.5.1`. `package-lock.json` está modificado y debe
publicarse junto con `package.json`; la instalación reproducible es `npm ci`.

### 3.5 Qué NO publicar del frontend

```text
node_modules/  dist/  .angular/  coverage/
e2e-results.json  test-results/  playwright-report/
.env*  (no existen en el repo)
```

---

## 4. Artefacto reproducible (plan, no ejecutado)

```powershell
# Backend: archivo de release sin evidencias ni secretos
$api = "C:\SI2_Parcial1\fashionstore-api"
$destino = "C:\SI2_Parcial1\_release_5c0"
New-Item -ItemType Directory -Force $destino | Out-Null
robocopy $api "$destino\fashionstore-api" /E `
  /XD .git __pycache__ .pytest_cache database\saas\evidencias `
  /XF .env *.dump *.fsb2 *.log CTX_*.md PROMPT_*.md
# Frontend
robocopy "C:\SI2_Parcial1\fashionstore-web" "$destino\fashionstore-web" /E `
  /XD .git node_modules dist .angular coverage test-results playwright-report `
  /XF e2e-results.json

# Verificación del artefacto
Get-ChildItem $destino -Recurse -File | Measure-Object
Select-String -Path "$destino\**\*" -Pattern "postgres(ql)?://[^<""]*:[^@""]*@" -List
```

Criterio de aceptación del artefacto:

1. `npm ci && npm run build:saas` exit 0 con el `package-lock.json` publicado.
2. `python -m compileall -q app` exit 0; `pytest` focalizado sin fallos.
3. **0** archivos `.env`, `*.dump`, `*.fsb2`, `secrets*.json`, `evidencias/`.
4. Manifiesto 126 con checksums; 14 migraciones SQL en orden; seed demo excluido.

---

## 5. Plan de publicación (requiere autorización; NO ejecutado)

Secuencia mínima propuesta, una vez aprobada:

1. **Backend**: crear rama de release desde `cc1be74`; agregar migraciones
   20261030*/20261031* + código + pruebas + docs 121/122/125/126. Excluir
   evidencias/CTX/PROMPT/odd. Commit atómico por área (migraciones, API, pruebas,
   docs). No reescribir historia.
2. **Frontend**: agregar `e2e/`, `playwright.config.ts`, `src/app/saas/**`,
   `environment.saas-local.ts` y los 10 archivos modificados; commit atómico.
3. Verificar en CI local `npm ci`, tests, builds y (si aplica) Playwright antes
   de publicar.
4. `push` de ambas ramas y apertura de PR **solo con autorización explícita**.
5. El despliegue de staging debe construirse desde el **commit publicado**, nunca
   desde la rama remota obsoleta ni desde el árbol local sin commit.

Autorizaciones humanas necesarias para este apartado: ver `122_cloud_gate_5c0.md` §7.

---

## 6. Dictamen del candidato de release

| Ámbito | Dictamen | Fundamento |
|---|---|---|
| **Inventory completeness** | **GO** | Todo cambio requerido identificado y clasificado; 21+10 rastreados, 129+463 no rastreados, migraciones y manifiesto 126 |
| **Secret hygiene** | **GO** | 0 DSN con credenciales, 0 archivos sensibles rastreados, `.env` ignorado, B2 fuera del repo |
| **Reproducibility** | **GO (local)** | Lockfiles presentes, manifiesto con checksums, secuencia de 14 migraciones, seed demo excluido |
| **Publication** | **NO-GO** | Sin autorización de commit/push; el remoto no contiene el trabajo local |
| **Cloud deploy from current remote** | **NO-GO** | La rama remota está obsoleta; desplegarla produciría una versión sin 5BK–5C0 |
