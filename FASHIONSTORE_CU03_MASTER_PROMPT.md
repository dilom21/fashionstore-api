# Prompt Maestro – Implementar CU03 Gestionar Usuarios
## FashionStore | Backend FastAPI + Frontend Angular

Trabaja directamente sobre los repositorios reales de FashionStore abiertos en VS Code.

Antes de tocar archivos, lee el documento:

```text
FASHIONSTORE_CU03_CONTEXT.md
```

y úsalo como fuente principal de contexto.

El objetivo de esta tarea es implementar de extremo a extremo:

```text
CU03 – Gestionar usuarios
```

Actor principal:

```text
Administrador
```

Plataforma:

```text
Web
```

---

# REGLA PRINCIPAL

NO me entregues solamente snippets.

MODIFICA LOS ARCHIVOS REALES DEL PROYECTO.

Si una herramienta de parche falla:

- inspecciona primero el archivo;
- usa otro método de escritura;
- puedes utilizar Python + pathlib;
- puedes utilizar PowerShell;
- verifica después el contenido escrito;
- continúa la tarea.

No te detengas únicamente porque `apply_patch` falle.

---

# REPOSITORIOS

Backend esperado:

```text
C:\SI2_Parcial1\fashionstore-api
```

Frontend esperado:

```text
C:\SI2_Parcial1\fashionstore-web
```

Si ambos están abiertos en un workspace multi-root, trabaja sobre ambos.

Si solo uno está abierto, completa primero ese repositorio y reporta claramente qué falta del otro.

---

# ANTES DE IMPLEMENTAR

Inspecciona:

BACKEND:

```text
app/
app/core/
app/models/
app/schemas/
app/repositories/
app/services/
app/routers/
app/main.py
requirements.txt
```

FRONTEND:

```text
src/app/
src/environments/
package.json
angular.json
```

Identifica:

- arquitectura existente;
- patrones de nombres;
- auth actual;
- interceptor;
- guards;
- layouts;
- módulos/features;
- servicios;
- componentes reutilizables.

NO construyas una arquitectura paralela.

---

# BACKEND – OBJETIVO

Implementar o completar:

```http
GET    /usuarios
GET    /usuarios/{usuario_id}
POST   /usuarios
PATCH  /usuarios/{usuario_id}
PATCH  /usuarios/{usuario_id}/estado
```

Puedes usar:

```http
PATCH /usuarios/{usuario_id}/rol
```

solo si es coherente con la arquitectura actual y no duplica responsabilidad del PATCH general.

---

# BACKEND – ARQUITECTURA OBLIGATORIA

```text
Router
  ↓
Service
  ↓
Repository
  ↓
SQLAlchemy
  ↓
PostgreSQL Supabase
```

NO poner SQL en routers.

NO usar `Base.metadata.create_all()`.

NO modificar Supabase.

NO crear migraciones.

---

# BACKEND – ENTIDADES

Reutiliza los modelos reales existentes.

Principalmente:

```text
Usuario
Rol
Empleado
Sucursal
Bitacora
```

No redefinir tablas.

No crear clases duplicadas.

---

# BACKEND – SEGURIDAD

Todos los endpoints de Gestión de Usuarios deben requerir:

```text
JWT válido
+
usuario autenticado
+
autorización administrativa
```

Reutiliza:

```text
get_current_user
```

y cualquier helper de roles/permisos ya existente.

Si no existe todavía autorización por rol suficientemente limpia, crea una dependency reusable sin romper auth.

No hardcodear el ID del rol.

Resolver autorización por nombre/permiso real.

---

# BACKEND – REGLAS DE NEGOCIO

Implementa:

1. correo único;
2. contraseña con Argon2id;
3. rol debe existir;
4. rol debe estar activo;
5. sucursal debe existir cuando corresponda;
6. no exponer password_hash;
7. usuario inactivo no puede autenticarse;
8. no borrar físicamente usuario;
9. habilitar/deshabilitar mediante `estado`;
10. registrar contexto de usuario autenticado para bitácora cuando corresponda.

Para escrituras auditables utiliza, dentro de la misma transacción y si aplica con la implementación existente:

```sql
SELECT set_config('app.usuario_id', '<ID_USUARIO>', true);
```

No alterar triggers.

---

# BACKEND – CREACIÓN DE USUARIO

La creación debe ser transaccional.

Flujo conceptual:

```text
validar admin
↓
validar correo
↓
validar rol
↓
validar sucursal
↓
hash Argon2id
↓
crear usuario
↓
crear/vincular empleado
↓
commit
↓
respuesta segura
```

Si falla empleado después de crear usuario:

```text
rollback completo
```

No dejar datos huérfanos.

---

# BACKEND – LISTADO

GET /usuarios debe devolver información útil.

Debe permitir filtros opcionales razonables si la arquitectura lo soporta:

```text
buscar
rol_id
estado
sucursal_id
```

No es obligatorio paginar si el proyecto aún no utiliza paginación.

Evita N+1 queries.

Cargar:

```text
Usuario
→ Rol
→ Empleado
→ Sucursal
```

con `joinedload` / `selectinload` cuando corresponda.

---

# BACKEND – SCHEMAS

Crear o completar:

```text
app/schemas/usuarios.py
```

Schemas conceptuales:

```text
UsuarioCreate
UsuarioUpdate
UsuarioEstadoUpdate
UsuarioResponse
UsuarioDetalleResponse
```

No aceptar:

```text
password_hash
```

desde cliente.

Para creación aceptar:

```text
password
```

---

# BACKEND – REPOSITORY

Crear o completar:

```text
app/repositories/usuarios_repository.py
```

Responsabilidades:

- listar;
- obtener por id;
- obtener por correo;
- crear;
- actualizar;
- obtener rol;
- obtener sucursal;
- consultar empleado.

---

# BACKEND – SERVICE

Crear o completar:

```text
app/services/usuarios_service.py
```

Responsabilidades:

- reglas de negocio;
- validaciones;
- hash;
- coordinación transaccional;
- contexto bitácora;
- cambio de estado;
- modificación;
- asignación de rol existente.

---

# BACKEND – ROUTER

Crear o completar:

```text
app/routers/usuarios.py
```

Registrar en:

```text
app/main.py
```

Usar:

```text
APIRouter
Depends
HTTPException
status
response_model
```

---

# FRONTEND – OBJETIVO

Implementar Gestión de Usuarios para el Administrador.

No conectarse a Supabase.

Usar únicamente FastAPI.

---

# FRONTEND – RUTA

Usar una ruta coherente con el proyecto.

Sugerida:

```text
/admin/usuarios
```

Si existe una convención actual diferente:

```text
RESPETARLA
```

---

# FRONTEND – PANTALLA PRINCIPAL

Debe incluir:

```text
Gestión de usuarios
```

Elementos:

- botón Nuevo usuario;
- buscador;
- filtro de rol;
- filtro de estado;
- opcional filtro sucursal;
- tabla.

Columnas:

```text
Correo
Empleado
Rol
Sucursal
Estado
Acciones
```

Acciones:

```text
Ver
Editar
Habilitar
Deshabilitar
```

No ofrecer eliminación física.

---

# FRONTEND – FORMULARIO

Usar Angular Reactive Forms.

Campos:

CUENTA:

```text
correo
password temporal al registrar
rol
```

EMPLEADO:

```text
nombres
apellidos
ci
telefono
sucursal
fecha_contratacion
```

Validaciones:

- required;
- email;
- contraseña mínima razonable;
- CI requerido si la BD lo exige;
- rol requerido;
- sucursal requerida cuando corresponda.

No hardcodear roles.

No hardcodear sucursales.

Consumir endpoints reales.

---

# FRONTEND – SERVICIO

Crear o completar un servicio equivalente a:

```text
usuarios.service.ts
```

Métodos:

```text
listarUsuarios
obtenerUsuario
crearUsuario
actualizarUsuario
cambiarEstado
```

Reutilizar:

```text
HttpClient
environment
interceptor JWT
```

No añadir manualmente Bearer Token en cada método si ya existe interceptor.

---

# FRONTEND – MODELOS

Crear interfaces/types coherentes.

No utilizar `any` salvo necesidad excepcional.

Ejemplos conceptuales:

```text
Usuario
UsuarioDetalle
UsuarioCreateRequest
UsuarioUpdateRequest
RolResumen
SucursalResumen
```

---

# FRONTEND – AUTORIZACIÓN

Reutilizar guards existentes.

La pantalla solo debe estar disponible para Administrador.

Pero recordar:

```text
el backend debe seguir validando autorización
```

No confiar únicamente en Angular.

---

# FRONTEND – UX

Implementar:

- loading;
- empty state;
- mensajes de éxito;
- mensajes de error;
- confirmación para habilitar/deshabilitar;
- formularios claros;
- feedback de validación;
- responsive;
- actualización del listado sin recargar toda la SPA.

Mantener el estilo visual existente de FashionStore.

No rediseñar toda la aplicación.

---

# MANEJO DE ERRORES

Backend:

```text
401 token inválido
403 sin autorización
404 usuario/rol/sucursal inexistente
409 correo duplicado
422 validación
500 solo inesperado
```

Frontend:

traducir estos errores a mensajes comprensibles.

No mostrar stack traces.

---

# PRUEBAS BACKEND

Ejecutar:

```powershell
python -c "import app.models; print('MODELOS OK')"
```

```powershell
python -c "from app.main import app; print('APP OK')"
```

Levantar:

```powershell
python -m uvicorn app.main:app --reload
```

Probar:

```http
POST /auth/login
GET /auth/me
GET /usuarios
GET /usuarios/{id}
POST /usuarios
PATCH /usuarios/{id}
PATCH /usuarios/{id}/estado
```

Casos negativos:

- sin token;
- usuario no administrador;
- correo duplicado;
- rol inexistente;
- sucursal inexistente;
- usuario inexistente;
- deshabilitar;
- intentar login deshabilitado;
- habilitar nuevamente.

---

# PRUEBAS FRONTEND

Ejecutar:

```powershell
npm install
```

solo si dependencias no están instaladas.

Después:

```powershell
npm run build
```

o:

```powershell
ng build
```

según scripts reales.

Comprobar:

- build;
- imports;
- routing;
- guard;
- listado;
- filtros;
- crear;
- editar;
- deshabilitar;
- habilitar;
- manejo de errores.

---

# REGRESIÓN

Verificar que sigan funcionando:

```http
GET /productos
GET /productos/1
GET /productos/1/disponibilidad
GET /categorias
GET /sucursales
POST /auth/login
GET /auth/me
```

No romper inventario.

---

# NO HACER

NO:

```text
modificar Supabase
modificar .env
crear migraciones
usar Base.metadata.create_all()
hardcodear SECRET_KEY
exponer password_hash
eliminar usuarios físicamente
hacer commit
hacer push
instalar paquetes innecesarios
crear arquitectura paralela
```

---

# ENTREGA FINAL

Cuando termines, reporta exactamente:

## Backend

- archivos creados;
- archivos modificados;
- endpoints;
- reglas implementadas;
- seguridad;
- bitácora;
- pruebas;
- resultados.

## Frontend

- archivos creados;
- archivos modificados;
- ruta;
- componentes;
- servicios;
- formularios;
- validaciones;
- pruebas/build.

## Errores

- errores encontrados;
- causa;
- solución aplicada.

## Estado del CU03

Indicar:

```text
COMPLETO
PARCIAL
BLOQUEADO
```

y justificar.

NO hacer commit ni push.

Detente después de finalizar CU03.

NO comiences CU04 ni CU05 salvo dependencias estrictamente necesarias.
