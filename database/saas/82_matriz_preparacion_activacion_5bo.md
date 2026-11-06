# 5BO / Etapa F — Matriz de preparación para activación (P3)

**Corte:** 31/10/2026 (UTC). **Ámbito:** backend SaaS `C:\SI2_Parcial1\fashionstore-api`.
**Este documento NO autoriza desplegar.** Distingue explícitamente:

| Nivel | Significado | Estado 5BO |
|---|---|---|
| Validado en laboratorio | Probado SQL+HTTP reales sobre PostgreSQL 18.6 desechable | **SÍ** (P0/P1/P2) |
| Apto para staging real | Requiere esquema histórico completo + backup/restore + revisión de migraciones | **NO** |
| Apto para producción | Requiere staging validado + aprobación explícita + Postgres 17 destino | **NO** |

---

## 1. Requisitos cumplidos en 5BO

| Requisito | Estado | Evidencia |
|---|---|---|
| Tenant = empresa, separación lógica, dos empresas independientes | Cumplido | `test_nucleo_saas_5bo.py` P0/P2; RLS `tenant_aislamiento` |
| CU03/CU04: alta de personal, rol, sucursal, revocación | Cumplido | `sp_alta_personal_tenant`, invitación de un solo uso, pruebas A/B |
| RBAC nominal por empresa (no solo nombre de rol) | Cumplido | `saas.permiso_default`, `saas.permiso_tenant`, `saas.tiene_permiso_tenant` |
| CU01/CU02: empresas, planes, suscripciones, cambio de plan solo superadmin | Cumplido | `/saas/superadmin/*`, `PATCH /tenant/empresa/plan` en 503 |
| Límites de plan parametrizables (2/10) con concurrencia | Cumplido | `plan_saas.max_sucursales_activas`, lock por empresa, 5 altas simultáneas → 1 |
| Superadmin: autenticación, rotación, revocación, rate limit, auditoría | Cumplido en laboratorio | multiclave `v1/v2`, `saas_admin.intento_auth`, `saas_admin.auditoria_operacion` |
| Separación de principales SQL (tenant / global / owner) | Cumplido | `saas_personal_owner` NOLOGIN, matriz EXECUTE verificada |
| Rutas legacy cerradas en modo SaaS | Cumplido | `/usuarios`, `/compras`, `/ventas`, `/pagos`, `/devoluciones` → 503 |
| Flags SaaS OFF por defecto | Cumplido | `config.py` (sin cambios de default) |

## 2. Bloqueos que impiden declarar staging/producción

| # | Bloqueo | Impacto | Requisito de cierre |
|---|---|---|---|
| B1 | **Esquema histórico incompleto**: 40/41 tablas, 14/27 triggers, 13/24 rutinas reconstruidas | Validaciones y auditoría reales no cubiertas | `pg_dump --schema-only` autorizado + catálogo (`80`/`81`), restaurar en clúster nuevo y repetir suites |
| B2 | **Backup/restore no probado** | Irrecuperable ante una migración fallida | Copia recuperable + restauración en destino aislado + inventario de objetos |
| B3 | **Migraciones DEMO** `20261023000400_cuatro_tenants_demo_local.sql` | Podría poblar producción con tenants ficticios | Separar migraciones portables de artefactos de laboratorio; ver `83_...json` |
| B4 | **PostgreSQL 17 de destino vs 18.6 del laboratorio** | Diferencias de planificador/privilegios | Repetir ACL/RLS/SP en staging compatible con el destino |
| B5 | **Secretos**: clave HMAC de aserción y huellas superadmin | Rotación y custodia fuera de Git | Aprovisionar por gestor de secretos; dos versiones durante rotación |
| B6 | **Frontend Angular** | `saas_route_gate` bloquea rutas legacy | Contrato mínimo de UI (login tenant, empresas, rol/sucursal, operaciones permitidas) |
| B7 | **Reembolso financiero** | Devolución con dinero “pendiente” | Proceso contable/manual definido (no bloquea la sección 9) |
| B8 | **Superficie de personal**: no hay alta de cuentas existentes por invitación aceptada por el propio titular | Solo cuentas nuevas | Diseñar aceptación autenticada para vincular cuentas preexistentes (fuera de alcance F) |

## 3. Ruta de activación propuesta (5BP)

1. Obtener el esquema real con `80_extraccion_esquema_autorizada_5bo.ps1` (autorización escrita, salida no versionada).
2. Ejecutar `81_metadatos_catalogo_solo_lectura_5bo.sql` y comparar con `75_esquema_historico_reconstruido_5bl.sql` (tablas/triggers/rutinas/ACL/search_path).
3. Restaurar el `schema-only` en un PostgreSQL **17** aislado; aplicar `20261030000100`…`00900` en orden; ejecutar el runner 78 adaptado y las suites.
4. Probar backup completo + restauración en un destino distinto; registrar RPO/RTO y plan de retorno.
5. Revisar la lista `83_matriz_migraciones_lab_vs_produccion_5bo.json`: excluir seeds demo y cualquier artefacto de laboratorio.
6. Aprovisionar secretos (HMAC, huellas superadmin con dos `kid`) por gestor externo; `SAAS_*_ENABLED=false` hasta la ventana de activación.
7. Simulacro de rollback: restaurar la copia previa y verificar login/tenant/operaciones.

## 4. Contrato mínimo para el frontend (posterior, no en F)

| Pantalla | Endpoint | Nota |
|---|---|---|
| Login tenant | `POST /auth/tenant/login` | requiere `empresa_slug` y `contexto` |
| Aceptar invitación | `POST /auth/tenant/invitaciones/aceptar` | token de un solo uso |
| Empresa/plan | `GET /tenant/empresa/me`, `/plan`, `/suscripcion` | límites y capacidades |
| Personal | `GET/POST /tenant/empresa/usuarios`, `PATCH .../{id}/rol|estado|sucursal` | permisos `PERSONAL/*` |
| Matriz RBAC | `GET /tenant/empresa/permisos`, `PATCH /tenant/empresa/roles/{id}/permisos` | ADMINISTRADOR no editable |
| Sucursales | `GET/POST /tenant/empresa/sucursales`, `PATCH .../estado` | cupo por plan |
| Superadmin | `/saas/superadmin/*` con `X-SaaS-Admin-Key` + `X-SaaS-Admin-Key-Id` | nunca JWT tenant |

## 5. Declaración

- **Laboratorio:** `VALIDADA` para el núcleo SaaS CU01–CU05 (P0/P1/P2).
- **Staging real:** `BLOQUEADA` por B1, B2, B3, B4, B5.
- **Producción:** `BLOQUEADA`. No se declara producción lista mientras falte
  validar el esquema histórico completo, los respaldos y la restauración.
