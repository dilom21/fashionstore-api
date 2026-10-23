# Paso 7/7 — Validación técnica inicial de autenticación SaaS

**Fecha:** 2026-10-23. **Rama:** `feat/saas-multitenant-parcial2`.  
**Base:** Examen Parcial-2 – Sistemas 2 – S2-2026, apartados 9.2 y 9.6, RNF01, RNF02 y RNF11.  
**Importante:** Esto NO es una certificación del aislamiento completo: todavía no se aplicaron migraciones ni se probaron transacciones reales entre dos tenants.

## Acciones completadas

1. **Hallazgo de seguridad:** `get_current_user` aceptaba cualquier JWT firmado con `sub`, incluso el JWT `scope=tenant` de una empresa, y luego aplicaba `usuario.rol_id` global.
2. **Corrección (branch solamente):** en `app/core/dependencies.py` se agregó un guard. Si `scope='tenant'`, el token es rechazado en dependencias legacy (401), independientemente del feature flag. Cuando `SAAS_AUTH_ENABLED=true`, `get_current_user` bloquea todas las rutas legacy dependientes de él. Con flag false, los JWT originales continúan funcionando.
3. **Pruebas nuevas:** `tests_saas/test_auth_tenant_unit.py`. Se usa `DATABASE_URL=sqlite+pysqlite:///./saas_auth_unit_test.db` y un secreto de pruebas, no el secreto real. Las pruebas usan mocks/sesiones simuladas para membresía y permisos. No acceden a Supabase.
4. **Validación ejecutada localmente en arnés aislado:** `20 passed` (`pytest -q test_auth_saas_unit.py`, Python 3.13). Cuatro archivos nuevos principales fueron reproducidos byte a byte a partir del código revisado en GitHub y su SHA Git-blobs **coincidió exactamente**:
   - `app/core/tenant_context.py` — 49cf7cff990081cd64a0d34b988fb7a800aaf625
   - `app/core/tenant_dependencies.py` — 8fbcbab1c25f8b8f4cdeef13d4a6b4762ff8a0d4
   - `app/modules/autenticacion_seguridad/repositories/tenant_repository.py` — 3e3ec20c6f4d7e67cbed8669e0ba9fd9aa43a3d0
   - `app/modules/autenticacion_seguridad/api/tenant_router.py` — 48c74dccaa8dc5a19b93b854a59f552fc37a5ad4
5. **Límite de la prueba:** el arnés utiliza implementaciones simplificadas de `app.core.database`, `app.core.dependencies`, `AuthService` y `RolService`. Por tanto, los **20 PASS son del arnés aislado**, NO de todo el repositorio desplegable ni de la suite `tests_saas` ejecutada directamente desde GitHub. La prueba completa debe ejecutarse en el clone local antes del merge.
6. **Estado de Supabase:** 41 tablas de usuario en `public`, 0 tablas SaaS nuevas; 5 migraciones antiguas. No se aplicaron cambios SQL.
7. **Estado de GitHub:** solo la rama feature recibe cambios; `main` permanece original.

## Comando de ejecución reproducible en la computadora del desarrollador

En PowerShell, abrir el repositorio `fashionstore-api`:
```powershell
git fetch origin
git switch feat/saas-multitenant-parcial2
python -m pip install -r requirements.txt
python -m pytest -q tests_saas
```

**Advertencia:** esta suite aislada fuerza una URL de SQLite local y un secreto ficticio para sus imports; no utiliza credenciales productivas. No ejecutar todavía las pruebas generales `tests/` si apuntan a la BD real, salvo asegurar aislamiento transaccional y restauración probada.

## Pruebas del arnés aislado que pasaron

- JWT válido con empresa y sin rol global.
- Rechazo de JWT sin `scope`, con ID inválido, contexto inválido y falta de `jti`.
- Firma modificada y JWT legacy rechazados como tenant.
- Flag de SaaS desactivado: error 503.
- Token tenant rechazado por autorización legacy.
- Modo SaaS activado: rechazo de JWT legacy en `get_current_user`.
- Primer parcial: autenticación legacy funcional con flag desactivado.
- Consulta de membresía: bind de usuario, empresa y contexto; no seleccionar ambigüedades.
- Contexto de empresa proviene de membresía validada y se establece en sesión.
- Revocación de membresía: rechazo 403.
- Permisos basados en el rol del tenant, no rol global.
- Endpoints nuevos bloqueados en 503 sin consultar las tablas SaaS.

## Pruebas críticas PENDIENTES (requieren otra etapa/bloque)

- Base de pruebas restaurada desde el backup `.dump`.
- Crear tablas SaaS y migrar las 31 tablas empresariales con FKs y validaciones.
- Ejecución directa de `pytest -q tests_saas` sobre el repositorio completo (no se pudo clonar desde el entorno de ejecución actual por restricción de red).
- Probar conexión con usuario PostgreSQL de privilegios mínimos y **sin BYPASSRLS**.
- Dos tenants reales; IDs ajenos en endpoints de inventario, clientes, ventas, pagos, reportes y AR (ausencia de fugas).
- Probar procedimientos y triggers con empresas diferentes y concurrencia.
- Probar el frontend Angular, Flutter, sincronización offline y planes.
- Asegurar que todos los endpoints legacy estén migrados antes de poner `SAAS_AUTH_ENABLED=true`.

## Restricciones de activación

**Mantener `SAAS_AUTH_ENABLED=false`.** Si se activa ahora, el modo antiguo quedará bloqueado por seguridad, pero la base sigue sin las nuevas tablas/columnas y ninguna ruta comercial está completamente adaptada. No desplegar la rama como SaaS productivo hasta el cierre de la migración y pruebas integrales.

**Interpretación del paso 7:** la infraestructura de autenticación SaaS tiene pruebas unitarias iniciales y un bloqueo adicional para prevenir confusión de JWT. El **aislamiento integral del examen aún no está implementado ni demostrado**.
