# FashionStore – Contexto Maestro para IA en VS Code
## CU03 – Gestionar Usuarios | Backend + Frontend

> Documento de contexto para continuar el desarrollo del primer parcial de Sistemas de Información II.
> Proyecto: **FashionStore – Plataforma inteligente de comercio electrónico para tienda de ropa masculina**.

---

# 1. Contexto general del proyecto

FashionStore es una plataforma de comercio electrónico para una cadena de tiendas de ropa masculina.

La solución contempla:

- Aplicación web.
- Aplicación móvil.
- Gestión de clientes.
- Gestión de usuarios internos.
- Roles y permisos.
- Bitácora.
- Catálogo de prendas.
- Categorías.
- Tallas.
- Colores.
- Variantes.
- Temporadas.
- Colecciones.
- Sucursales.
- Proveedores.
- Inventario.
- Carrito.
- Reservas.
- Ventas presenciales.
- Compras digitales.
- Gestión de pagos.
- Devoluciones.
- Reportes.
- Inteligencia artificial.
- Vestidor virtual mediante realidad aumentada.

El proyecto corresponde al **primer parcial de Sistemas de Información II** y se trabaja utilizando **PUDS / Proceso Unificado de Desarrollo de Software**.

Tecnologías principales:

- Backend: **Python + FastAPI**
- ORM: **SQLAlchemy 2**
- Driver PostgreSQL: **Psycopg 3**
- Base de datos: **PostgreSQL en Supabase**
- Frontend web: **Angular**
- Aplicación móvil: **Flutter + Dart**
- Autenticación: **JWT HS256**
- Contraseñas: **Argon2id**
- Modelado: **UML 2.5+**
- Control de versiones: **Git + GitHub**

---

# 2. Repositorios

Repositorios principales:

## Backend

```text
fashionstore-api
```

Ruta local usada actualmente:

```text
C:\SI2_Parcial1\fashionstore-api
```

## Frontend

```text
fashionstore-web
```

Ruta local esperada:

```text
C:\SI2_Parcial1\fashionstore-web
```

## Mobile

```text
fashionstore-mobile
```

Ramas del equipo:

```text
main
pruebas
dev-harold
dev-josias
```

Reglas:

- Trabajar sobre la rama actual.
- No hacer commit salvo solicitud explícita.
- No hacer push salvo solicitud explícita.
- No modificar `main` directamente sin indicación.
- No borrar archivos importantes.
- No sobrescribir trabajo existente sin inspeccionarlo primero.

---

# 3. Base de datos

La base de datos ya existe en Supabase.

Proyecto:

```text
fashionstore
```

Project Ref:

```text
zyitwlxfkphthlvlmuej
```

Estado actual aproximado:

- 38 tablas.
- 1 vista.
- 7 procedimientos almacenados.
- 24 triggers.
- PK, FK, UNIQUE y CHECK configurados.
- Índices creados.
- RLS habilitado.
- Datos de prueba cargados.

IMPORTANTE:

- NO usar `Base.metadata.create_all()`.
- NO recrear tablas.
- NO crear migraciones DDL sin autorización.
- NO modificar Supabase para implementar CU03.
- El frontend NO se conecta directamente a Supabase.
- Todo acceso debe pasar por FastAPI.

Arquitectura:

```text
Angular / Flutter
       ↓
     FastAPI
       ↓
SQLAlchemy + Psycopg
       ↓
Supabase PostgreSQL
```

---

# 4. Backend – estado actual

El backend ya tiene conexión funcional con Supabase.

Endpoint comprobado:

```http
GET /health/db
```

Respuesta validada:

```json
{
  "status": "ok",
  "database": "postgres",
  "usuario": "postgres",
  "total_productos": 8
}
```

Arquitectura obligatoria:

```text
Router
  ↓
Service
  ↓
Repository
  ↓
SQLAlchemy
  ↓
PostgreSQL
```

No colocar consultas de base de datos directamente en routers.

---

# 5. Funcionalidades backend ya implementadas

Actualmente se encuentran funcionando, entre otras:

## Salud

```http
GET /
GET /health
GET /health/db
```

## Catálogo

```http
GET /productos
GET /productos/{producto_id}
GET /productos/{producto_id}/disponibilidad
```

`GET /productos` devuelve 8 productos.

El detalle de producto incluye:

- Categoría.
- Variantes.
- Talla.
- Color.
- Inventario.
- Sucursal.
- Temporada.
- `stock_disponible` calculado.

## Autenticación

```http
POST /auth/login
GET /auth/me
```

Implementado con:

- Argon2id.
- PyJWT.
- JWT HS256.
- Bearer Token.

Usuario validado:

```text
admin@fashionstore.test
```

Existe también un usuario cliente:

```text
cliente@fashionstore.test
```

Contraseña temporal de prueba:

```text
Fashion123*
```

Nunca exponer `password_hash`.

## Categorías

```http
GET /categorias
GET /categorias/{categoria_id}
```

Datos comprobados:

```text
9 categorías
```

## Sucursales

```http
GET /sucursales
GET /sucursales/{sucursal_id}
```

Datos comprobados:

```text
4 sucursales
```

## Inventario

```http
GET /inventario
GET /inventario/{inventario_id}
GET /inventario/sucursal/{sucursal_id}
GET /inventario/producto/{producto_id}
```

Filtros implementados:

- `sucursal_id`
- `producto_id`
- `categoria_id`
- `talla_id`
- `color_id`
- `temporada_id`

Datos comprobados:

```text
72 registros de inventario
```

Los endpoints de inventario están protegidos con JWT.

---

# 6. Estructura backend esperada

Estructura general:

```text
app/
├── core/
│   ├── config.py
│   ├── database.py
│   └── security.py
│
├── models/
│   ├── __init__.py
│   ├── seguridad.py
│   ├── personas.py
│   ├── catalogo.py
│   ├── inventario.py
│   ├── ubicacion.py
│   └── temporadas.py
│
├── schemas/
├── repositories/
├── services/
├── routers/
└── main.py
```

La IA debe inspeccionar la estructura real antes de crear nuevos archivos.

No duplicar clases, routers, schemas ni servicios existentes.

---

# 7. Tablas principales para CU03

## Tabla `usuario`

Campos relevantes:

```text
id
rol_id
correo
password_hash
fecha_creacion
estado
```

Relación:

```text
usuario.rol_id → rol.id
```

## Tabla `rol`

Campos relevantes:

```text
id
nombre
descripcion
estado
```

## Tabla `empleado`

Campos relevantes:

```text
id
usuario_id
sucursal_id
nombres
apellidos
ci
telefono
fecha_contratacion
estado
```

Relaciones principales:

```text
empleado.usuario_id → usuario.id
empleado.sucursal_id → sucursal.id
```

## Tabla `bitacora`

Existe y ya se encuentra asociada al sistema mediante triggers.

Las operaciones administrativas relevantes sobre entidades críticas deben quedar registradas.

Existe un mecanismo de contexto de usuario basado en:

```sql
SELECT set_config('app.usuario_id', '<ID_USUARIO>', true);
```

Cuando se hagan escrituras autenticadas que deban registrar correctamente el autor, utilizar este mecanismo dentro de la misma transacción, respetando la implementación real del proyecto.

---

# 8. CU03 – Gestionar Usuarios

## 8.1 Identificación

```text
ID: CU03
Nombre: Gestionar usuarios
Actor principal: Administrador
Plataforma: Web
```

## 8.2 Descripción

Permite al Administrador registrar, consultar, modificar, habilitar y deshabilitar las cuentas de usuarios internos de FashionStore.

Los usuarios internos principales son:

- Administrador.
- Encargado de sucursal.
- Cajero.

El registro autónomo de clientes NO forma parte de este CU.

El cliente se administra mediante el CU relacionado con su propia cuenta.

## 8.3 Propósito

Mantener actualizadas y controladas las cuentas del personal autorizado para acceder al sistema.

## 8.4 Precondiciones

- El Administrador debe haber iniciado sesión.
- Debe poseer autorización para gestionar usuarios.
- Los roles asignables deben existir y estar activos.

## 8.5 Postcondiciones

- El usuario queda registrado o actualizado.
- El rol queda correctamente asociado.
- La cuenta queda habilitada o deshabilitada según la operación.
- La operación administrativa debe quedar reflejada en bitácora cuando corresponda.

---

# 9. Alcance funcional del CU03

CU03 debe permitir:

```text
Consultar usuarios
Buscar usuarios
Filtrar usuarios
Registrar usuario
Consultar detalle
Modificar usuario
Asignar/cambiar rol existente
Habilitar usuario
Deshabilitar usuario
```

CU03 NO debe:

```text
Eliminar físicamente usuarios con historial
Crear roles
Editar roles
Crear permisos
Editar permisos
Modificar estructura de permisos
```

La creación y administración de roles y permisos corresponde al CU04.

---

# 10. Flujo principal CU03

1. El Administrador inicia sesión.
2. Accede al módulo de Administración.
3. Selecciona la opción Usuarios.
4. El sistema muestra los usuarios registrados.
5. El Administrador puede buscar, filtrar o seleccionar un usuario.
6. Puede elegir:
   - Registrar.
   - Consultar.
   - Modificar.
   - Habilitar.
   - Deshabilitar.
7. Para registrar un usuario interno se solicitan los datos necesarios.
8. El sistema valida los datos.
9. Verifica que el correo sea único.
10. Verifica que el rol exista y esté activo.
11. La contraseña temporal se almacena usando Argon2id.
12. Se registra la cuenta.
13. Si corresponde, se vincula con un registro de empleado.
14. Se asocia la sucursal correspondiente.
15. Se registra la operación en bitácora.
16. El sistema devuelve confirmación.
17. El listado se actualiza.

---

# 11. Flujos alternativos

## FA01 – Correo duplicado

- El sistema detecta que el correo ya existe.
- No registra al usuario.
- Devuelve error de conflicto.
- El Administrador debe corregir los datos.

HTTP sugerido:

```text
409 Conflict
```

## FA02 – Datos incompletos

- El sistema detecta campos obligatorios inválidos o vacíos.
- No registra los cambios.

HTTP sugerido:

```text
422 Unprocessable Entity
```

## FA03 – Rol inexistente o inactivo

- No se permite registrar ni actualizar el usuario con ese rol.
- Informar claramente la causa.

## FA04 – Deshabilitar usuario

- Cambiar `estado` a `false`.
- No borrar el registro.
- El usuario deja de poder autenticarse.
- Registrar la acción en bitácora.

## FA05 – Habilitar usuario

- Cambiar `estado` a `true`.
- Registrar la acción.

## FA06 – Usuario inexistente

HTTP sugerido:

```text
404 Not Found
```

## FA07 – Usuario sin permisos

HTTP sugerido:

```text
403 Forbidden
```

---

# 12. Reglas de negocio CU03

## RN-CU03-01

Solo un Administrador autorizado puede gestionar usuarios internos.

## RN-CU03-02

El correo de cada usuario debe ser único.

## RN-CU03-03

Las contraseñas deben almacenarse con Argon2id.

## RN-CU03-04

Todo usuario interno debe estar asociado a un rol existente y activo.

## RN-CU03-05

Un usuario deshabilitado no puede iniciar sesión.

## RN-CU03-06

No se debe eliminar físicamente un usuario con información histórica.

Debe utilizarse deshabilitación lógica:

```text
estado = false
```

## RN-CU03-07

Las acciones administrativas importantes deben quedar registradas en bitácora.

## RN-CU03-08

La definición y modificación de roles y permisos corresponde al CU04.

---

# 13. Backend requerido para CU03

Endpoints sugeridos:

```http
GET    /usuarios
GET    /usuarios/{usuario_id}
POST   /usuarios
PATCH  /usuarios/{usuario_id}
PATCH  /usuarios/{usuario_id}/estado
PATCH  /usuarios/{usuario_id}/rol
```

Si la arquitectura actual integra cambio de rol dentro del PATCH general, no crear un endpoint redundante.

Todos estos endpoints deben estar protegidos.

Requerimientos:

```text
JWT válido
+
Usuario autenticado
+
Rol/permiso administrativo
```

La IA debe reutilizar `get_current_user` y la estrategia de autorización ya existente.

No duplicar autenticación.

---

# 14. Backend – archivos sugeridos

Antes de crear, inspeccionar si existen.

Posibles archivos:

```text
app/schemas/usuarios.py
app/repositories/usuarios_repository.py
app/services/usuarios_service.py
app/routers/usuarios.py
```

Modelos:

```text
app/models/seguridad.py
app/models/personas.py
```

No crear nuevos modelos si `Usuario`, `Rol` y `Empleado` ya existen.

---

# 15. Backend – responsabilidades por capa

## Router

Responsable de:

- Recibir HTTP.
- Validar parámetros mediante Pydantic.
- Aplicar dependencies.
- Devolver response models.
- Convertir excepciones de negocio en respuestas HTTP.

NO debe contener lógica SQL.

## Service

Responsable de:

- Reglas de negocio.
- Validar rol.
- Validar estado.
- Validar duplicidad.
- Coordinar creación de usuario y empleado.
- Hash de contraseña.
- Cambiar estado.
- Preparar contexto de bitácora.
- Gestionar transacciones coherentes.

## Repository

Responsable de:

- Consultas SQLAlchemy.
- Obtener usuario.
- Buscar por correo.
- Listar.
- Crear.
- Actualizar.
- Obtener rol.
- Obtener empleado.
- Obtener sucursal.

## Models

Representan tablas existentes.

No recrear BD.

---

# 16. Response sugerida para listado de usuarios

Ejemplo conceptual:

```json
{
  "items": [
    {
      "id": 1,
      "correo": "admin@fashionstore.test",
      "estado": true,
      "fecha_creacion": "2026-09-01T12:00:00",
      "rol": {
        "id": 1,
        "nombre": "ADMINISTRADOR"
      },
      "empleado": {
        "id": 1,
        "nombres": "Administrador",
        "apellidos": "FashionStore",
        "ci": "123456",
        "telefono": "70000000",
        "sucursal": {
          "id": 1,
          "nombre": "Sucursal Centro"
        }
      }
    }
  ]
}
```

No exponer:

```text
password_hash
```

---

# 17. Body sugerido para registrar usuario

Conceptualmente:

```json
{
  "correo": "nuevo@fashionstore.test",
  "password": "Temporal123*",
  "rol_id": 2,
  "empleado": {
    "nombres": "Juan",
    "apellidos": "Pérez",
    "ci": "9876543",
    "telefono": "70000001",
    "sucursal_id": 1,
    "fecha_contratacion": "2026-09-05"
  }
}
```

Ajustar al diseño real del backend y a las restricciones reales de la BD.

No hardcodear IDs.

---

# 18. Frontend web – objetivo

Implementar el módulo administrativo de Gestión de Usuarios en Angular.

Debe integrarse con:

```text
FastAPI
JWT existente
Auth interceptor existente o equivalente
API REST existente
```

No conectarse directamente a Supabase.

---

# 19. Frontend – flujo esperado

```text
Administrador inicia sesión
        ↓
Dashboard administrativo
        ↓
Administración
        ↓
Usuarios
        ↓
Listado
        ↓
Buscar / filtrar
        ↓
Nuevo / editar / habilitar / deshabilitar
```

---

# 20. Frontend – pantalla principal

Ruta sugerida:

```text
/admin/usuarios
```

La pantalla debe incluir:

- Título: Gestión de usuarios.
- Botón: Nuevo usuario.
- Buscador.
- Filtro por rol.
- Filtro por estado.
- Tabla de usuarios.
- Estado visual.
- Acciones.

Columnas sugeridas:

```text
ID
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

Evitar eliminación física.

---

# 21. Frontend – formulario de usuario

Debe permitir:

## Cuenta

```text
Correo
Contraseña temporal (solo al registrar)
Rol
Estado cuando corresponda
```

## Empleado

```text
Nombres
Apellidos
CI
Teléfono
Sucursal
Fecha de contratación
```

El rol debe seleccionarse de roles existentes.

La sucursal debe seleccionarse de sucursales existentes.

No hardcodear listas.

---

# 22. Frontend – comportamiento UX

Requisitos:

- Formulario reactivo.
- Validaciones visuales.
- Mensajes claros.
- Indicador de carga.
- Confirmación antes de deshabilitar/habilitar si corresponde.
- Manejo de errores HTTP.
- No mostrar mensajes técnicos del backend directamente.
- No mostrar stack traces.
- No recargar toda la aplicación innecesariamente.
- Refrescar listado después de guardar.
- Responsive para escritorio y tablet.

---

# 23. Frontend – posibles archivos

La IA debe inspeccionar el proyecto real antes de decidir nombres.

Una estructura posible:

```text
src/app/features/administracion/usuarios/
├── pages/
│   └── usuarios-page.component.*
├── components/
│   ├── usuarios-table/
│   └── usuario-form/
├── services/
│   └── usuarios.service.ts
└── models/
    └── usuario.model.ts
```

Si el proyecto usa otra estructura, respetarla.

No crear una segunda arquitectura paralela.

---

# 24. Frontend – servicios API

Métodos sugeridos:

```text
listarUsuarios()
obtenerUsuario(id)
crearUsuario(payload)
actualizarUsuario(id, payload)
cambiarEstadoUsuario(id, estado)
cambiarRolUsuario(id, rolId)
```

Reutilizar:

```text
environment
HttpClient
Interceptor JWT
Guards existentes
```

---

# 25. Seguridad frontend

La seguridad real debe estar en backend.

El frontend puede:

- Ocultar opciones no autorizadas.
- Aplicar guard.
- Validar rol.

Pero esto NO reemplaza:

```text
autorización backend
```

Nunca confiar solo en Angular.

---

# 26. Relación con CU04 y CU05

## CU04 – Gestionar roles y permisos

CU03 puede asignar un rol existente.

CU03 NO administra:

- Definición del rol.
- Funciones.
- Acciones.
- Permisos.

Eso se hará en CU04.

## CU05 – Consultar bitácora

CU03 genera eventos que posteriormente podrán consultarse mediante CU05.

Ejemplos:

```text
CREAR usuario
MODIFICAR usuario
CAMBIAR ROL usuario
HABILITAR usuario
DESHABILITAR usuario
```

---

# 27. Pruebas backend mínimas

La IA debe probar como mínimo:

## Importaciones

```powershell
python -c "import app.models; print('MODELOS OK')"
```

```powershell
python -c "from app.main import app; print('APP OK')"
```

## Regresión

Verificar que sigan funcionando:

```http
GET /productos
POST /auth/login
GET /auth/me
GET /categorias
GET /sucursales
```

## CU03

Probar:

```http
GET /usuarios
GET /usuarios/{id}
POST /usuarios
PATCH /usuarios/{id}
PATCH /usuarios/{id}/estado
```

Casos:

- Administrador válido.
- Sin token.
- Usuario sin permiso.
- Correo duplicado.
- Rol inexistente.
- Usuario inexistente.
- Deshabilitación.
- Intento de login de usuario deshabilitado.
- Rehabilitación.

---

# 28. Pruebas frontend mínimas

Comprobar:

- Build exitoso.
- No errores TypeScript.
- Ruta de usuarios accesible.
- Guard funcionando.
- Listado carga desde FastAPI.
- Crear usuario.
- Editar usuario.
- Deshabilitar.
- Habilitar.
- Filtros.
- Mensajes de error.
- Token enviado automáticamente.

Ejecutar, según configuración actual:

```powershell
npm run build
```

y/o:

```powershell
ng build
```

---

# 29. Restricciones importantes

NO hacer:

- `Base.metadata.create_all()`
- DDL en Supabase.
- Nuevas migraciones.
- Modificar `.env`.
- Eliminar usuarios físicamente.
- Hardcodear contraseñas.
- Hardcodear SECRET_KEY.
- Exponer `password_hash`.
- Conectar Angular directamente a Supabase.
- SQL desde router.
- Lógica de negocio en componentes Angular.
- Commit.
- Push.

---

# 30. Criterios de terminado del CU03

CU03 puede considerarse terminado cuando:

- Administrador puede listar usuarios.
- Puede consultar detalle.
- Puede crear usuario interno.
- Puede asignar rol existente.
- Puede editar datos.
- Puede habilitar/deshabilitar.
- Las contraseñas se almacenan con Argon2id.
- Usuario deshabilitado no puede iniciar sesión.
- Operaciones relevantes quedan trazables en bitácora.
- Frontend y backend están integrados.
- No se rompieron endpoints anteriores.
- Build del frontend pasa.
- Imports del backend pasan.
- API devuelve errores coherentes.
- No se expone información sensible.

---

# 31. Resultado funcional esperado

```text
Administrador
      ↓
Login
      ↓
Gestión de Usuarios
      ↓
┌───────────────────────────────┐
│ Consultar                     │
│ Registrar                     │
│ Editar                        │
│ Asignar rol                   │
│ Habilitar                     │
│ Deshabilitar                  │
└───────────────────────────────┘
      ↓
FastAPI
      ↓
Service
      ↓
Repository
      ↓
PostgreSQL
      ↓
Bitácora
```

---

# 32. Próximos CU del mismo desarrollador

Después de CU03 se trabajará uno por uno:

```text
CU04 – Gestionar roles y permisos
CU05 – Consultar bitácora del sistema
```

No adelantar implementaciones grandes de CU04/CU05 salvo las dependencias mínimas necesarias para CU03.

