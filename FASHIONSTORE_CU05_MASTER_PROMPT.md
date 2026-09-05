# Prompt Maestro — CU05 Consultar Bitácora

Antes de cambiar archivos identifica el repositorio abierto.

Si estás en `fashionstore-api`:
- lee `FASHIONSTORE_CU05_BACKEND_CONTEXT.md`
- realiza SOLO backend

Si estás en `fashionstore-web`:
- lee `FASHIONSTORE_CU05_FRONTEND_CONTEXT.md`
- realiza SOLO frontend

Trabaja directamente sobre archivos reales.
No entregues solo snippets.
Si `apply_patch` falla, usa pathlib/PowerShell/u otro mecanismo, verifica y continúa.
No hagas commit ni push.

## BACKEND
Implementa CU05 como consulta read-only.

Inspecciona modelo Bitacora existente, Usuario/Rol, dependencies y estructura de módulos.
No redefinas tablas.

Crear módulo `app/modules/bitacora/` si no existe.

Implementar:
- GET /bitacora
- GET /bitacora/catalogos
- GET /bitacora/{bitacora_id}

GET /bitacora:
- paginación
- orden fecha_hora DESC, id DESC
- filtros buscar, usuario_id, accion, entidad_afectada, fecha_desde, fecha_hasta, limit, offset
- response items + total + limit + offset

GET /bitacora/catalogos:
- acciones DISTINCT reales
- entidades DISTINCT reales
- usuarios seguros para filtro

GET /bitacora/{id}:
- detalle
- 404 si no existe

Seguridad para TODOS:
`require_permission("CONSULTAR_BITACORA", "CONSULTAR")`

No POST/PATCH/DELETE.
No auditar GETs.
No modificar bitácora.

Usuario NULL: permitido.
IP NULL: permitido.
Descripción: texto plano.
Rango desde > hasta → 400.
Evitar N+1.

Pruebas:
- imports
- listado
- paginación
- filtros individuales y combinados
- rango inválido
- detalle/404
- catálogos
- 401
- 403
- 200 con permiso
- nulls
- comprobar que consultar no crea bitácora
- regresión CU03/CU04

No Supabase manual, DDL, migraciones o .env.
Reporta archivos, endpoints, filtros, seguridad, paginación, pruebas y regresión. DETENTE.

## FRONTEND
Implementa CU05 en `/admin/seguridad/bitacora`.

Inspecciona admin shell/sidebar, patterns CU03/CU04, error utils y estilos.
No crear otro layout.

Consumir API REAL:
- GET /bitacora
- GET /bitacora/catalogos
- GET /bitacora/{id}

Crear feature `features/administracion/bitacora/`.

Implementar:
- listado paginado
- búsqueda
- filtros usuario/acción/entidad/fecha
- limpiar filtros
- total
- detalle read-only
- usuario null → Sistema
- IP null → —
- humanización centralizada
- fecha local
- responsive
- accesibilidad

Desktop: tabla `Fecha | Usuario | Acción | Entidad | IP | Descripción | Ver`.
Móvil: cards/apilado.

No botones Crear/Editar/Eliminar.
No hardcodear catálogos; usar GET /bitacora/catalogos.
Paginación con total/limit/offset.

Sidebar: `Consultar Bitácora` pasa a funcional.
CU03/CU04 intactos.

Reutilizar errores existentes:
401 → logout/login
403 → mensaje coherente

Ejecutar `npm run build` y pruebas de regresión CU03/CU04.
No backend/Supabase/git.
Reporta archivos, ruta, servicio/modelos, filtros, paginación, detalle, sidebar, build y regresión. DETENTE.

NO comenzar otros CU.
