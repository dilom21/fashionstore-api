# Bloque 3 — Paso 3/7: lectura empresarial del catálogo

**Estado:** PASO 3 COMPLETADO Y VERIFICADO mediante pruebas HTTP unitarias y consulta SQL de solo lectura ejecutadas localmente por el estudiante.  
**Rama:** `feat/saas-multitenant-parcial2`.  
**Seguridad:** `SAAS_AUTH_ENABLED=false`, base de Supabase intacta, sin `supabase db push`, `main` intacta.  
**Contexto:** Bloque 2 validó 5 tenants, 48 FK compuestas y 18 pruebas; Bloque 3 paso 2 obtuvo 79 pruebas unitarias superadas, no garantiza aislamiento de consultas.

## Objetivo
Proveer rutas de lectura de categorías, productos y variantes que sean **obligatoriamente tenant-scoped**. La empresa efectiva procede del JWT empresarial y de la consulta de membresía vigente de `get_current_tenant_context`. No se acepta `empresa_id` desde el cuerpo, URL o query string para determinar el alcance. Una consulta por ID que pertenece a otro tenant se trata como recurso inexistente (404), sin revelar su titularidad.

## Cambios nuevos
1. `app/modules/catalogo/repositories/tenant_repository.py`: clase `TenantCatalogoRepository`, proyecciones SQLAlchemy Core por `empresa_id`, `JOIN` de productos/categorías y variantes/productos con `empresa_id` consistente, filtrado de registros inactivos y límite de página. Las consultas solo devuelven columnas explícitas, sin relaciones ORM cargadas por defecto.
2. `app/modules/catalogo/api/tenant_router.py`: seis rutas GET con `Depends(get_current_tenant_context)`:
   - `GET /tenant/catalogo/categorias`
   - `GET /tenant/catalogo/categorias/{categoria_id}`
   - `GET /tenant/catalogo/productos?categoria_id=&limit=&offset=`
   - `GET /tenant/catalogo/productos/{producto_id}`
   - `GET /tenant/catalogo/productos/{producto_id}/variantes`
   - `GET /tenant/catalogo/variantes/{variante_id}`
3. `app/main.py`: se registra el nuevo router sin modificar los handlers previos (`/productos`, `/categorias`).
4. `app/core/saas_route_gate.py`: allowlist de métodos y rutas **exacta**; se autorizan solo los seis GET nuevos cuando SaaS está habilitado, además de las cuatro rutas permitidas del paso 1. No se libera ninguna ruta comercial heredada ni métodos POST/PUT/PATCH/DELETE del nuevo catálogo. La coincidencia de IDs tiene un patrón explícito, no `startswith`.
5. `tests_saas/test_tenant_catalogo_lecturas.py`: pruebas HTTP contra app real, con base SQLite temporal y dos empresas ficticias. El token/membresía se sustituyen por un contexto falso solo dentro del test (la validación JWT tiene otros tests). Se comprueban filtrado de listas, categoría/variante/producto ajenos -> 404, filtros manipulados `?empresa_id=`, registros inactivos, paginación y bloqueo legacy, además de modo SaaS desactivado y token ausente.
6. `database/saas/07_verificar_lectura_catalogo_local.sql` y `database/saas/07_verificar_lectura_catalogo_local.ps1`: comprobación sobre la base local de las cinco empresas. SQL `BEGIN TRANSACTION READ ONLY` + `ROLLBACK`, valida 14 productos de Vanter Men y 1 de cada una de las demás empresas, 120 variantes históricas y aislamiento referencial.

## Ejecución de pruebas

Desde PowerShell de la rama SaaS y conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado observado:** 94 pruebas aprobadas (79 anteriores + 15 nuevas), 1 advertencia `StarletteDeprecationWarning`, 2.11 s; sin fallos.

Solo si ese comando pasa, validar la copia local PostgreSQL:

```powershell
.\database\saas\07_verificar_lectura_catalogo_local.ps1
```

Indicar contraseña PostgreSQL **local**, no la de Supabase. Esta segunda prueba es **de solo lectura** y finalizará en `ROLLBACK`.

## Cobertura y límites
- La base SQLite contiene solo datos inventados, nunca datos reales. Las pruebas HTTP sustituyen la verificación de membresía por `TenantContext` ficticio para probar exclusivamente la lógica de catálogo; **no son pruebas de login SaaS real contra PostgreSQL**.
- SQL PostgreSQL de cinco empresas demuestra conteos, consistencia y filtro por ID; **no prueba políticas RLS ni peticiones HTTP reales con administradores SaaS**. Las cuatro cuentas demo continúan desactivadas.
- Los endpoints del primer parcial todavía NO fuerzan `empresa_id`; su acceso permanece bloqueado en modo SaaS por el middleware. Con el flag apagado siguen funcionando los contratos legacy solamente en un entorno aislado del primer parcial.
- No se incluyen imágenes de recursos, inventarios, promociones ni reportes de otros tenants; estos componentes se aislarán con sus módulos en fases posteriores.
- No modificar aún `SAAS_AUTH_ENABLED` ni compartir la base SaaS con un backend legacy habilitado.
- El siguiente paso 4/7 tratará las **escrituras** y límites del catálogo, pero no debe iniciarse sin confirmación `siguiente`.

## Evidencia real de ejecución local (23/10/2026)
- Rama sincronizada mediante `git pull --ff-only origin feat/saas-multitenant-parcial2`, actualización hasta `8b00a5f`.
- `python -m pytest -q tests_saas`: **94 passed, 1 warning in 2.11s**. Advertencia deprecatoria Starlette/httpx; ningún test falló.
- `database/saas/07_verificar_lectura_catalogo_local.ps1`: conectado **solo** a `127.0.0.1:5432`, base `fashionstore_restore_20261023_193554_594`.
- PostgreSQL imprimió `LECTURA OK` para cinco empresas y `LECTURA AISLADA OK: 5 tenants; sin modificaciones`. Conteos:
  - `vanter-men`: 14 productos, 120 variantes.
  - `urbanwear`, `elegancia-sur`, `denim-house`, `sportline`: 1 producto y 1 variante por empresa.
- Resultado SQL final `ROLLBACK`. El script informó `LECTURAS CATALOGO LOCAL SUPERADAS (SIN ESCRITURAS)`.
- La sesión SQL fue READ ONLY. No se realizaron escrituras remotas ni se activó `SAAS_AUTH_ENABLED`.
- **Interpretación prudente**: se validaron las pruebas HTTP con pertenencia simulada y los datos locales mediante SQL de solo lectura; todavía no se ha demostrado autenticación real entre empresas ni RLS con rol sin BYPASSRLS.

**Criterio de salida alcanzado:** Paso 3/7 completado; esperar `siguiente` para el Paso 4.
