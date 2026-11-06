# Checklist de despliegue — versión `5BO-F.1` (NO EJECUTAR AÚN)

**Estado:** PREPARADO, **BLOQUEADO** hasta cerrar B1/B2/B3/B4/B5.
**Uso:** marcar cada casilla con evidencia verificable; una casilla sin evidencia
se considera **NO cumplida**. No es una autorización de despliegue.

## 0. Precondiciones documentales

- [ ] Esquema histórico completo obtenido con autorización (`80_...ps1`) y catálogo (`81_...sql`) revisado.
- [ ] Comparación 5BL vs esquema real: tablas, triggers, rutinas, propietarios, ACL y `search_path`.
- [ ] Backup completo del destino y **restauración probada** en instancia aislada.
- [ ] Lista `83_matriz_migraciones_lab_vs_produccion_5bo.json` revisada y firmada.
- [ ] Secretos aprovisionados fuera del repositorio (HMAC de aserción, dos `kid` superadmin).

## 1. Staging (PostgreSQL 17 compatible con destino)

- [ ] Clúster/instancia de staging creada; versión y extensión `pgcrypto` (si aplica) registradas.
- [ ] Roles: `fs_saas_runtime`, `saas_admin_runtime`, `saas_admin_owner`, `saas_rls_owner`, `saas_actor_sql_owner`, `saas_personal_owner` (NOLOGIN, sin SUPERUSER/BYPASSRLS).
- [ ] Migraciones `20261030000100` … `00900` aplicadas en orden, **una sola vez** y con registro.
- [ ] `20261023000400_cuatro_tenants_demo_local.sql` **NO** aplicada.
- [ ] `database/saas/72_verificacion_post_migracion_saas_solo_lectura.sql` sin hallazgos.
- [ ] Runner 78 adaptado a staging: verificación de RLS/ACL/ejecución en verde.
- [ ] `pytest tests_saas/test_nucleo_saas_5bo.py` en verde contra staging.

## 2. Seguridad

- [ ] `SAAS_AUTH_ENABLED=false`, `SAAS_SUPERADMIN_ENABLED=false`, `SAAS_VENTAS_ENABLED=false`, `SAAS_COMPRAS_ENABLED=false`, `SAAS_DEVOLUCIONES_ENABLED=false`, `SAAS_RESERVAS_ENABLED=false` al desplegar.
- [ ] `SAAS_SUPERADMIN_REQUIRE_HTTPS=true` y `SAAS_SUPERADMIN_TRUSTED_PROXIES` limitado al balanceador.
- [ ] Rate limit verificado entre **dos procesos** (`saas_admin.intento_auth` compartido).
- [ ] Auditoría global revisada: sin claves, sin tokens, sin IP en claro.
- [ ] `PATCH /tenant/empresa/plan` responde 503.
- [ ] Rutas legacy (`/usuarios`, `/empleados`, `/compras`, `/ventas`, `/pagos`, `/devoluciones`) responden 503.

## 3. Activación controlada

- [ ] Ventana de cambio aprobada por el responsable.
- [ ] Copia de seguridad inmediatamente anterior a la ventana.
- [ ] Activación por empresa piloto: `SAAS_AUTH_ENABLED=true` + flag del módulo necesario.
- [ ] Verificación post-activación: login tenant, alta de personal, matriz RBAC, sucursales, plan/suscripción.
- [ ] Monitoreo 24 h: errores 5xx, 429 del plano global, latencia de `saas.tenant_actual()`.

## 4. Rollback

- [ ] Criterio de aborto definido (p. ej. 5xx > 1 %, login tenant roto, fuga entre empresas).
- [ ] Procedimiento: restaurar copia previa + reiniciar con flags OFF.
- [ ] Simulacro de rollback ejecutado en staging y tiempo medido.
- [ ] Comunicación a usuarios y registro del incidente.

## 5. Firma

| Rol | Nombre | Fecha | Resultado |
|---|---|---|---|
| Responsable técnico | | | |
| Responsable de datos | | | |
| Aprobación de cambios | | | |

> **Producción permanece BLOQUEADA** mientras cualquier casilla de las secciones
> 0–1 esté sin evidencia. Este checklist no sustituye la aprobación explícita.
