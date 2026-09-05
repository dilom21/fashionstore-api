# Prompt Maestro — CU04 Gestionar Roles y Permisos

Lee primero el contexto del repositorio abierto:

Backend:
`FASHIONSTORE_CU04_BACKEND_CONTEXT.md`

Frontend:
`FASHIONSTORE_CU04_FRONTEND_CONTEXT.md`

Trabaja directamente sobre los archivos reales.

NO entregues solo snippets.

Si `apply_patch` falla:
- lee el archivo
- usa Python/pathlib, PowerShell u otro mecanismo
- verifica lo escrito
- continúa

No hagas commit.
No hagas push.

## Modo de ejecución

Si el workspace actual es `fashionstore-api`, realiza SOLO BACKEND.
Si el workspace actual es `fashionstore-web`, realiza SOLO FRONTEND.

---

# BACKEND

Inspecciona primero:
- app/modules/roles/
- app/modules/usuarios/
- app/modules/autenticacion_seguridad/
- app/core/dependencies.py
- app/main.py

Inspecciona modelos reales:
- Rol
- Modulo
- Funcion
- Accion
- RolFuncion
- Usuario
- Bitacora

No redefinas tablas.

## 1. Extender roles

Mantener:
- GET /roles
- GET /roles?asignable_interno=true

Agregar:
- GET /roles/{rol_id}
- POST /roles
- PATCH /roles/{rol_id}
- PATCH /roles/{rol_id}/estado

Reglas:
- nombre único
- no IDs hardcodeados
- ADMINISTRADOR protegido
- roles base no renombrables si el código depende de esos nombres
- roles con usuarios activos no se deshabilitan
- roles personalizados sí se crean/editan/desactivan cuando corresponda

## 2. Catálogo permisos

Agregar:
- GET /roles/catalogo-permisos

Devolver jerarquía real:
Modulo → Funcion → Acciones

Solo registros activos.

## 3. Permisos por rol

Agregar:
- GET /roles/{rol_id}/permisos
- PUT /roles/{rol_id}/permisos

PUT reemplaza matriz completa atómicamente.

Payload conceptual:
{
  "permisos": [
    {"funcion_id": 3, "accion_id": 2},
    {"funcion_id": 3, "accion_id": 3}
  ]
}

Validar referencias, activos, duplicados e idempotencia.

ADMINISTRADOR no debe perder acceso total.

## 4. Autorización granular

Implementar helper/dependency reusable equivalente a:
`require_permission(funcion, accion)`

Basarse en BD:
Usuario → Rol → RolFuncion → Funcion → Modulo → Accion

Sin permiso → 403.

Usar en CU04 según acción:
- consultar → GESTIONAR_ROLES + CONSULTAR
- crear → GESTIONAR_ROLES + CREAR
- editar/permisos → GESTIONAR_ROLES + EDITAR
- desactivar → GESTIONAR_ROLES + ELIMINAR o convención consistente

Si es seguro, migrar CU03 a:
GESTIONAR_USUARIOS + CONSULTAR/CREAR/EDITAR

No romper CU03.

## 5. Bitácora

Antes de escrituras:
`SELECT set_config('app.usuario_id', '<usuario_id>', true);`

Si `rol_funcion` no tiene trigger, registra un único evento resumen usando infraestructura existente de bitácora.
No crear triggers ni DDL.

## 6. Errores

- 400 regla inválida
- 401 sin auth
- 403 sin permiso
- 404 inexistente
- 409 duplicado / rol en uso
- 422 payload inválido

## 7. Pruebas backend

Imports:
- python -c "import app.models; print('MODELOS OK')"
- python -c "from app.main import app; print('APP OK')"

Probar:
- GET /roles
- GET /roles?asignable_interno=true
- GET /roles/{id}
- POST nuevo rol
- duplicado
- PATCH
- estado
- ADMINISTRADOR protegido
- GET catálogo
- GET permisos
- PUT permisos
- PUT idempotente
- referencia inválida
- 401
- 403
- 200 admin

Regresión:
- CU03 usuarios completo
- login
- auth/me
- productos
- categorías
- sucursales

Limpiar datos temporales.
No Supabase manual.
No migraciones.
No .env.

Al terminar reporta todo y DETENTE.

---

# FRONTEND

Inspecciona primero:
- features/administracion/
- navigation/
- components/
- usuarios/
- shared/components/confirm-dialog/
- core/guards/
- auth/

No crear otro shell/sidebar.

## 1. Consumir API real

Usar contrato real:
- GET /roles
- GET /roles/{id}
- POST /roles
- PATCH /roles/{id}
- PATCH /roles/{id}/estado
- GET /roles/catalogo-permisos
- GET /roles/{id}/permisos
- PUT /roles/{id}/permisos

Reutilizar/refactorizar `roles.service.ts` sin romper CU03.

## 2. Página CU04

Reemplazar pantalla en construcción de:
`/admin/seguridad/roles-permisos`

Desktop:
- roles a la izquierda
- matriz a la derecha

Móvil:
- selector de rol
- módulos acordeón

## 3. Matriz

Construir desde backend:
módulo → función → acciones

Columnas:
Crear, Consultar, Editar, Eliminar, Ejecutar

Permitir marcar/desmarcar, seleccionar función completa, detectar cambios, guardar, descartar.

Guardar todo con un solo PUT.

## 4. Protecciones

ADMINISTRADOR:
- read-only
- no renombrar
- no deshabilitar
- badge rol sistema

No hardcodear IDs.

## 5. CRUD

Crear rol, editar rol, estado con confirm-dialog.
Manejar 409 rol en uso.

## 6. Sidebar

`Gestionar Roles y Permisos` deja de ser Próx.
Ruta activa:
`/admin/seguridad/roles-permisos`

CU03 sigue funcionando.
Bitácora sigue en construcción.

## 7. UX

Reutilizar manejo de errores CU03.
Loading, saving, dirty state, confirmación si cambia de rol con cambios sin guardar.
Responsive y accesible.

## 8. Pruebas frontend

Probar:
- abrir desde sidebar
- listar
- seleccionar
- matriz
- crear
- editar
- guardar permisos
- descartar
- admin read-only
- 409
- 401/403
- responsive
- logout

Regresión:
- /admin/usuarios
- /admin/inicio
- sidebar
- login personal

Ejecutar:
`npm run build`

No backend.
No Supabase.
No git.

Al terminar reporta todo y DETENTE.

---

NO comenzar CU05.
Completar únicamente CU04.
