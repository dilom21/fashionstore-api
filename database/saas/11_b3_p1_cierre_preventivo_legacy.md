# Bloque 3 — Backend FastAPI multitenant
## Paso 1 de 7: impedir uso accidental de rutas legacy en modo SaaS

**Estado:** código y pruebas en GitHub, pendientes de ejecutar por el estudiante.  
**Rama:** `feat/saas-multitenant-parcial2`. No se modificó `main` ni Supabase.  
**Precondición:** Bloque 2 (7/7) superado sobre PostgreSQL local con cinco empresas, 48 FK tenant y 18 pruebas unitarias previas; el backend multitenant aún **no es apto para producción**.

## Hallazgo y problema
La autenticación SaaS nueva (`/auth/tenant/login`, `/auth/tenant/me`) valida empresa, membresía y suscripción, pero las rutas heredadas del primer parcial no filtran todavía por `empresa_id`.

Se inspeccionaron los routers de catálogo, inventario, ventas, reservas, reportes, sucursales, compras y pagos; esos archivos no consumen `get_current_tenant_context` ni muestran filtrado por `empresa_id`. Si solo se activara `SAAS_AUTH_ENABLED` sin más cambios, esos endpoints podrían mostrar datos de otros tenants o fallar por restricciones nuevas del esquema.

## Implementación preventiva de este paso
- `app/core/saas_route_gate.py`: middleware ASGI con lista exacta de rutas/métodos permitidos en modo SaaS:
  - GET `/`: respuesta estática.
  - GET `/health`: salud básica sin datos empresariales.
  - POST `/auth/tenant/login`: entrada SaaS, sujeta a las validaciones del router.
  - GET `/auth/tenant/me`: identidad SaaS, requiere JWT y membresía.
- **Toda otra ruta HTTP** devuelve `503`, incluidas `/productos`, `/ventas`, `/pagos`, `/auth/clientes/login`, `/health/db`, `/docs`, `/openapi.json` y rutas no previstas. Comparación exacta: no se habilitan rutas mediante coincidencias de prefijo.
- Las conexiones **WebSocket** se rechazan en modo SaaS con código `1008` (aún no se han migrado canales).
- `app/main.py` registra el middleware antes de CORS, para que las respuestas HTTP sigan el tratamiento CORS.
- Con `SAAS_AUTH_ENABLED=false` el middleware pasa las peticiones sin cambiar el comportamiento del primer parcial.
- No habilitar `SAAS_AUTH_ENABLED` todavía. La lista de rutas permitidas se ampliará únicamente junto con sus pruebas de empresa, permisos y filtrado.

## Test añadido
`tests_saas/test_saas_route_gate.py` valida:
- Rutas/métodos no migrados rechazados (sin tocar base real).
- Rutas permitidas exactas en modo SaaS.
- Compatibilidad en modo legado, incluidos docs y rutas HTTP existentes.
- WebSocket rechazado en modo SaaS y conservado en modo legado.
- Middleware **registrado en la aplicación FastAPI real**; rutas legacy de la aplicación real deben devolver 503 sin ejecutar SQL.

**Comando local (Conda):**
```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado esperado si el entorno de dependencias es el mismo y el código pasa:** 36 pruebas unitarias (18 anteriores y 18 nuevas), sin fallos; la advertencia existente de Starlette puede continuar. No reportar aprobación sin ver la salida de PowerShell.

## Límites estrictos
- Esto es una **barrera temporal**, no autorización granular ni solución RNF02 definitiva.
- Con `SAAS_AUTH_ENABLED=false` las rutas heredadas seguirán activas. **Nunca compartir una base multitenant remota con este modo legacy abierto**; antes de cualquier despliegue deben migrarse los módulos o aplicarse un bloqueo global independiente del flag.
- No hay pruebas de RLS real ni de acceso cruzado HTTP autenticado.
- No se modifican modelos, servicios o repositorios comerciales en este paso.
- El proyecto mantiene los administradores demo desactivados y no modifica Supabase.
- **Prohibido desplegar a Supabase en este estado**.

## Hoja de ruta propuesta para el Bloque 3 (solo Paso 1 activo)
1. **Protección temporal fail-closed de rutas legacy:** este paso.
2. Actualizar modelos ORM empresariales con `empresa_id` y servicios comunes de alcance tenant.
3. Adaptar consultas de lectura del catálogo y otros listados al contexto tenant.
4. Adaptar creación, edición y validaciones de catálogo, sucursales e inventario.
5. Migrar flujos comerciales: carrito, reservas, ventas, pagos, compras, devoluciones.
6. Aplicar límites de planes, suscripciones, roles empresariales e IA por plan.
7. Pruebas HTTP inter-tenant, RLS con usuario sin `BYPASSRLS` y revisión final de despliegue.

**Trabajo secuencial:** no iniciar Paso 2 sin que el usuario indique `siguiente` después de verificar Paso 1.
