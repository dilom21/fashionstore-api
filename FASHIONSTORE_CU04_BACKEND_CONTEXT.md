# FashionStore — Contexto Backend CU04
## Gestionar Roles y Permisos

## 1. Proyecto
FashionStore es una plataforma web y móvil de comercio electrónico para una cadena de tiendas de ropa masculina.

Stack backend:
- Python
- FastAPI
- SQLAlchemy 2
- Psycopg 3
- PostgreSQL en Supabase
- JWT HS256
- Argon2id

Arquitectura obligatoria:
Router → Service → Repository → SQLAlchemy → PostgreSQL/Supabase

No usar `Base.metadata.create_all()`.
No recrear tablas.
No hacer migraciones ni DDL para CU04.
No modificar `.env`.
No hacer commit ni push.

## 2. Estado actual
CU03 — Gestionar Usuarios está terminado en backend.

Endpoints actuales relevantes:
- POST /auth/login
- GET /auth/me
- GET /usuarios
- GET /usuarios/{usuario_id}
- POST /usuarios
- PATCH /usuarios/{usuario_id}
- PATCH /usuarios/{usuario_id}/estado
- GET /roles
- GET /roles?asignable_interno=true

Seguridad actual:
- get_current_user
- get_current_admin

Bitácora:
- las escrituras relevantes usan `set_config('app.usuario_id', ..., true)`
- existen triggers de auditoría
- CU03 comprobó auditoría de usuario/empleado

## 3. Roles existentes
Tabla `rol`:
- id
- nombre
- descripcion
- estado

Roles base actuales:
- ADMINISTRADOR
- ENCARGADO_SUCURSAL
- CAJERO
- CLIENTE

Reglas ya vigentes:
- CLIENTE no puede asignarse a un usuario interno.
- La validación se hace por nombre/categoría de negocio, no por ID fijo.
- Existe `RolService.validar_rol_para_usuario_interno`.

## 4. Módulos existentes
Tabla `modulo`:
- SEGURIDAD
- CATALOGO
- INVENTARIO
- RESERVAS
- VENTAS
- COMPRAS_PROVEEDORES
- REPORTES

## 5. Funciones existentes
SEGURIDAD:
- CONSULTAR_BITACORA
- GESTIONAR_ROLES
- GESTIONAR_USUARIOS

CATALOGO:
- CONSULTAR_CATALOGO
- GESTIONAR_PRODUCTOS

INVENTARIO:
- CONSULTAR_INVENTARIO
- GESTIONAR_INVENTARIO

RESERVAS:
- GESTIONAR_RESERVAS

VENTAS:
- GESTIONAR_DEVOLUCIONES
- GESTIONAR_PAGOS
- GESTIONAR_VENTAS

COMPRAS_PROVEEDORES:
- GESTIONAR_ORDENES_COMPRA
- GESTIONAR_PROVEEDORES

REPORTES:
- CONSULTAR_REPORTES

## 6. Acciones existentes
Tabla `accion`:
- CREAR
- CONSULTAR
- EDITAR
- ELIMINAR
- EJECUTAR

## 7. Relación de permisos
Tabla `rol_funcion` relaciona conceptualmente:
ROL → FUNCION → ACCION

Campos esperados:
- rol_id
- funcion_id
- accion_id
- descripcion

Antes de mapear, inspeccionar modelo/tabla real y constraints.
La tabla ya contiene permisos iniciales. No reemplazar silenciosamente el seed actual.

## 8. CU04 — Gestionar Roles y Permisos
Actor principal: Administrador
Plataforma: Web

Debe permitir:
- Consultar roles
- Consultar detalle de rol
- Crear rol
- Editar rol
- Habilitar/deshabilitar rol
- Consultar catálogo de permisos
- Consultar permisos de un rol
- Asignar/revocar permisos

No eliminar físicamente información histórica.

## 9. Roles base protegidos
ADMINISTRADOR:
- no renombrar
- no deshabilitar
- no eliminar
- acceso total protegido
- matriz en solo lectura

ENCARGADO_SUCURSAL:
- no renombrar
- no eliminar físicamente
- descripción editable
- matriz editable
- no deshabilitar si hay usuarios activos asignados

CAJERO:
- mismas reglas que ENCARGADO_SUCURSAL

CLIENTE:
- no renombrar
- no eliminar físicamente
- matriz editable si el alcance lo requiere
- no asignable a empleado interno
- no deshabilitar si afecta cuentas activas

Roles personalizados:
- se pueden crear
- se pueden editar
- se pueden desactivar si no tienen usuarios activos

Identificar roles base por nombre, nunca por ID fijo.

## 10. Reglas de negocio
1. Solo usuarios con permiso GESTIONAR_ROLES pueden acceder.
2. Nombre de rol único, normalizado y no vacío.
3. No eliminar físicamente roles.
4. No desactivar un rol con usuarios activos asignados → 409.
5. No duplicar rol_id + funcion_id + accion_id.
6. Solo asignar módulos, funciones y acciones activos.
7. La matriz se guarda de forma atómica.
8. PUT de permisos debe ser idempotente.
9. ADMINISTRADOR no puede quedar bloqueado.
10. Cambios deben ser auditables.
11. Backend valida todo; no confiar en Angular.

## 11. Autorización granular
Implementar helper/dependency reusable equivalente a:
`require_permission(funcion, accion)`

Debe validar:
Usuario → Rol → RolFuncion → Funcion → Modulo → Accion
considerando estados activos.

Sin permiso → 403.

Preferir permisos reales de BD; el ADMINISTRADOR ya tiene permisos completos en seed.

## 12. Migración progresiva CU03
Si es seguro, migrar CU03 a permisos:
- GET usuarios → GESTIONAR_USUARIOS + CONSULTAR
- POST usuarios → GESTIONAR_USUARIOS + CREAR
- PATCH usuarios → GESTIONAR_USUARIOS + EDITAR
- cambio de estado → convención coherente EDITAR/ELIMINAR

No romper pruebas CU03.

## 13. API propuesta
Mantener:
- GET /roles
- GET /roles?asignable_interno=true

Agregar:
- GET /roles/{rol_id}
- POST /roles
- PATCH /roles/{rol_id}
- PATCH /roles/{rol_id}/estado
- GET /roles/catalogo-permisos
- GET /roles/{rol_id}/permisos
- PUT /roles/{rol_id}/permisos

PUT permisos body conceptual:
{
  "permisos": [
    {"funcion_id": 3, "accion_id": 2},
    {"funcion_id": 3, "accion_id": 3}
  ]
}

PUT reemplaza la matriz completa del rol de manera atómica.

## 14. Schemas sugeridos
Extender `app/modules/roles/schemas/schemas.py` con:
- RolResponse
- RolDetalleResponse
- RolCreate
- RolUpdate
- RolEstadoUpdate
- AccionResponse
- FuncionPermisosResponse
- ModuloPermisosResponse
- PermisoAsignadoResponse
- RolPermisosResponse
- PermisoItemRequest
- RolPermisosUpdate

## 15. Repository
Extender `app/modules/roles/repositories/repository.py` para:
- listar roles
- obtener rol
- buscar por nombre
- contar usuarios activos por rol
- crear/actualizar/cambiar estado
- listar módulos/funciones/acciones activos
- listar/reemplazar permisos de rol
- verificar permiso de un rol

Evitar N+1.

## 16. Service
Extender `app/modules/roles/services/service.py` para:
- reglas de roles base
- normalización
- duplicados
- validaciones
- matriz atómica
- auditoría
- anti-lockout

## 17. Core dependencies
Extender `app/core/dependencies.py` con helper/dependency reusable de permisos.
No duplicar JWT ni get_current_user.

## 18. Errores
- 400 regla inválida
- 401 sin auth
- 403 sin permiso
- 404 recurso inexistente
- 409 duplicado / rol en uso
- 422 payload inválido

## 19. Pruebas mínimas
Imports:
- `python -c "import app.models; print('MODELOS OK')"`
- `python -c "from app.main import app; print('APP OK')"`

Roles:
- listar
- detalle
- crear personalizado
- duplicado 409
- editar
- deshabilitar sin usuarios
- deshabilitar con usuarios → 409
- ADMINISTRADOR protegido

Permisos:
- catálogo
- permisos por rol
- PUT
- PUT repetido idempotente
- referencia inválida
- sin token 401
- sin permiso 403
- admin 200

Regresión:
- CU03 completo
- login
- auth/me
- productos
- categorías
- sucursales

No dejar datos temporales.

## 20. Resultado esperado
CU04 BACKEND:
- CRUD lógico de roles
- catálogo de módulos/funciones/acciones
- lectura de permisos
- reemplazo atómico de permisos
- autorización granular reusable
- anti-lockout
- auditoría
- regresión CU03
