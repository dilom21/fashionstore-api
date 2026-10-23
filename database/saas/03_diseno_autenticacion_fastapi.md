# FashionStore — Segundo parcial — Bloque 1, Paso 5 de 7
## Diseño técnico de autenticación, autorización y contexto multitenant en FastAPI

**Estado:** propuesta de arquitectura y contratos API **para revisión**. No hay código funcional ni cambios SQL ejecutados.  
**Repositorio:** `dilom21/fashionstore-api`, rama `feat/saas-multitenant-parcial2`.  
**Documento rector:** `Examen Parcial-2 - Sistemas 2 - S2-2026.pdf`, pp. 7–9, apartados **9.1–9.6** y **RNF01, RNF02, RNF04–RNF06, RNF10, RNF11**.  
**Nota de trazabilidad:** El PDF exige aislamiento efectivo en backend, empresas/roles, planes y suscripciones, pero **no impone un formato particular de JWT ni rutas REST**. El flujo y los endpoints siguientes son decisiones técnicas propuestas, verificables contra el código actual.

## 1. Hallazgos contrastados con el código de la rama

| Fuente real | Estado actual | Riesgo multitenant |
|---|---|---|
| `app/core/security.py` | JWT con HS256, expiración, decode; sin `iss`/`aud` obligatorios | No vincula sesión a una empresa |
| `app/modules/autenticacion_seguridad/services/service.py` | Token `{sub, correo, rol, contexto}` tras autenticar Argon2 | `rol` refleja permiso **global**, no de empresa |
| `app/modules/autenticacion_seguridad/api/router.py` | POST `/auth/clientes/login`, `/auth/personal/login`, `/auth/clientes/registro`, GET `/auth/me` | No solicita/valida tenant |
| `app/core/dependencies.py` | `get_current_user`, `get_current_admin` usa `usuario.rol.nombre`, `require_permission` usa `usuario.rol_id`; `get_current_cliente` global | Riesgo de admin de empresa A administrando B |
| `app/core/database.py` | `SessionLocal`, `get_db`, `after_begin` para `app.ip`; `app.usuario_id` se establece en servicios | Todavía no existe `app.empresa_id` |
| `app/modules/autenticacion_seguridad/repositories/repository.py` | Usuario por correo global, perfiles `Cliente`/`Empleado` por usuario sin empresa | Selecciona perfil incorrecto en cuentas multiempresa |
| `app/modules/usuarios/repositories/repository.py` | Listado general de `Usuario`, sin empresa | Fuga de identidades/empleados |
| `app/modules/ventas/repositories/repository.py` | Venta por `id`/carrito/reserva sin tenant; llama `sp_recalcular_total_venta` por id | IDOR y operaciones cruzadas |
| `tests/conftest.py` | Fixtures con JWT antiguos `sub, correo, rol, contexto`; transacción/savepoint revertida | Las pruebas deben migrarse o añadir fixtures tenant-scoped |

### Advertencia crítica específica de Supabase
Una consulta administrativa de solo lectura devolvió `current_user = postgres` y `rolbypassrls = true`. **Esto solo verifica el rol de esa conexión administrativa: NO demuestra qué rol usa el servicio FastAPI**, cuya `DATABASE_URL` real no se ha inspeccionado. Si FastAPI conecta con `postgres` (o con otro rol BYPASSRLS/propietario no forzado), activar RLS **no basta** para aislar tenants. Antes de producción confirmar el rol efectivo de FastAPI y usar una cuenta con privilegios mínimos, sin BYPASSRLS, política RLS real o un mecanismo equivalente fuerte. Nunca basar la seguridad solo en una variable `app.empresa_id` establecida por una conexión privilegiada. El JWT propio de FastAPI no equivale al JWT de Supabase Auth: `auth.uid()` no representa automáticamente nuestros `usuario.id`.

## 2. Identidad y autorización: separar dos conceptos
- **Identidad global:** `usuario.id`, correo global, contraseña Argon2, estado y recuperación; `usuario.rol_id` queda temporalmente por compatibilidad histórica, pero **no autoriza recursos de empresa**.
- **Membresía empresarial:** `usuario_empresa(usuario_id, empresa_id, rol_id, estado)` determina acceso y rol **dentro del tenant**; también comprobar `empresa.estado`, `rol.estado` y suscripción.
- **Administrador de empresa:** membresía activa con `rol.nombre='ADMINISTRADOR'`, habilitada únicamente dentro de esa empresa. Los **dos** administradores históricos se migran a membresías de Vanter Men: no se les asigna privilegio global por defecto.
- **Administrador de la plataforma SaaS:** necesita autoridad **explícita y separada** (por ejemplo, una tabla/rol técnico de operador SaaS validado server-side). No usar `usuario.rol_id=ADMINISTRADOR` como superadministrador. El paso 3 solo creó cinco tablas: esta distinción exige decisión e implementación adicional antes de habilitar administración global.
- **Sucursales:** un usuario empleado debe operar únicamente sobre sucursales de su `empresa_id` y con el alcance autorizado; tener rol de admin de empresa no garantiza operaciones SaaS globales.

## 3. Contrato JWT propuesto (no implementado)
Proponer JWT de acceso **limitado a una empresa**:

```json
{
  "sub": "<usuario.id>",
  "scope": "tenant",
  "empresa_id": "<empresa.id>",
  "contexto": "personal",
  "iat": 0,
  "exp": 0,
  "jti": "<uuid>"
}
```

Reglas:
1. Verificar firma, algoritmo permitido, `sub`, expiración, `scope='tenant'` y tipo/validez de `empresa_id`. Recomendar añadir `iss`/`aud` de forma coordinada al emisor y decodificador.
2. **Nunca confiar en un claim de rol para otorgar privilegios.** Consultar `usuario_empresa.rol_id` y `rol_funcion` vigentes en BD en cada autorización sensible. El claim `empresa_id` indica el tenant solicitado, pero el servidor **revalida membresía y estado**.
3. Un token sin `empresa_id` / `scope='tenant'`, incluidos los JWT anteriores, **no puede acceder a recursos empresariales** después de activar el modo multitenant (fail closed). Coordinar el despliegue web/móvil, actualización de pruebas e invalidación/expiración natural de tokens anteriores.
4. No permitir un cambio de tenant cambiando un header o sustituyendo el valor del cuerpo: se requiere un token nuevo emitido tras comprobación de membresía.
5. La empresa no deriva de una sucursal arbitraria, del precio de un plan ni de un ID enviado directamente por Angular/Flutter.

## 4. Flujo de autenticación propuesto

### A. Login actual adaptado (sin romper innecesariamente el primer parcial)
- Mantener como puntos de entrada `POST /auth/clientes/login` y `POST /auth/personal/login`, con credenciales por correo/password. Cuando se implemente, ampliar el contrato para aceptar **opcionalmente** `empresa_slug` si el flujo de interfaz ya conoce la tienda.
- Tras verificar Argon2 y `usuario.estado`, obtener **solo** empresas con membresía activa válida para el contexto cliente/personal.
- Si se suministra `empresa_slug`, comprobar que la cuenta pertenece al tenant indicado; no confiar en la elección por sí misma.
- Si hay **una sola empresa autorizada**, emitir JWT de tenant y conservar la forma principal de respuesta (`access_token`, `usuario`) con campos empresariales adicionales de manera controlada.
- Si existen **varias empresas autorizadas** y no se ha seleccionado una: respuesta de selección, con token de corta duración restringido a `scope='seleccion_empresa'` que **no** accede a catálogo administrativo, inventario o ventas. El formato final de la respuesta puede requerir discriminación de schema Pydantic y adaptación de Angular/Flutter.
- Propuesta: `POST /auth/empresa/seleccionar` recibe `empresa_slug` y token corto de selección; revalida membresía/estado y emite JWT `scope='tenant'`. Ningún tenant arbitrario concede acceso.
- `GET /auth/me` mostrará `empresa_id`, `empresa_slug`, nombre de empresa, rol efectivo y contexto, calculados sobre la membresía vigente. La respuesta debe actualizarse en Pydantic.

### B. Cambio de empresa después del login
- Ruta propuesta `POST /auth/empresa/cambiar` con el JWT actual y `empresa_slug`; verificar membresía en la nueva empresa y emitir token **nuevo**.
- No inferir permisos de una empresa desde otra; el frontend debe borrar cachés de datos empresariales al cambiar tenant.

### C. Registro público de clientes
- `POST /auth/clientes/registro` hoy crea `Usuario + Cliente` en una transacción con rol global CLIENTE.
- En SaaS, el cliente debe indicar el **slug público de empresa** mediante ruta/contexto confiable para resolver tenant; comprobar estado antes de crear.
- Cuenta ya existente: no crear otro `usuario` con el mismo correo, ni incorporar a una empresa sin comprobación suficiente de titularidad/consentimiento; definir invitación/unión autenticada con validación.
- Registrar `Usuario` (cuando sea nuevo), `Cliente` (perfil de la empresa) y `UsuarioEmpresa` en una única transacción; rollback total ante fallo.
- Nota de esquema pendiente: `cliente.usuario_id` y `empleado.usuario_id` son **UNIQUE globales** en la BD original. Para que una cuenta tenga distintos perfiles en varias empresas, revisar restricción por `(empresa_id,usuario_id)` y consultas de perfil; solo `usuario_empresa` no resuelve ese punto.

## 5. Nuevo contexto tipado de tenant
Archivo sugerido (para implementación del Paso 6): `app/core/tenant_context.py`.

```python
@dataclass(frozen=True)
class TenantContext:
    usuario_id: int
    empresa_id: int
    empresa_slug: str
    rol_id: int
    rol_nombre: str
    contexto: Literal["cliente", "personal"]
    # La suscripcion y permisos efectivos se verifican en dependencias
```

Dependencia sugerida: `get_current_tenant_context`.
1. Autenticar token y leer `sub`, `empresa_id`, `scope`.
2. Obtener usuario activo.
3. Cargar `usuario_empresa` activa + `empresa` activa + `rol` activo en una consulta con filtros explícitos por `usuario_id` y `empresa_id`.
4. Verificar contexto (cliente o personal), perfil perteneciente a esa empresa y reglas de negocio.
5. Verificar suscripción **activa y dentro de vigencia** para operaciones sujetas al plan. Para administrar vencimientos, conservar rutas SaaS restringidas específicamente, sin abrir recursos comerciales de un tenant suspendido.
6. Devolver `TenantContext` inmutable. Debe estar disponible en las dependencias de autorización, servicios, repositorios, procedimientos, reportes y sincronización.
7. En casos de autenticación global (login, recuperación, listado de membresías), usar rutas separadas que nunca exponen datos comerciales.

`get_current_admin` y `require_permission` deben ser reemplazados/adaptados en endpoints empresariales para comprobar `TenantContext.rol_id`, no `Usuario.rol_id`. Evitar mezclar `TenantContext` con `Usuario` en funciones que suponían permisos globales.

## 6. Aislamiento SQL y transacciones
- Firma conceptual de repositorio: `obtener_venta(db, venta_id, tenant: TenantContext)`, filtrando **simultáneamente** `Venta.id == venta_id` y `Venta.empresa_id == tenant.empresa_id`.
- `INSERT`: `empresa_id` se establece desde el **contexto validado** del servidor y no desde un payload arbitrario.
- `UPDATE`/`DELETE`: localizar/modificar por `id + empresa_id`, comprobar `rowcount` cuando corresponda y preservar el comportamiento transaccional.
- FKs compuestas en BD (`empresa_id, id`) deben garantizar que ventas, detalles, inventarios, sucursales, productos, reservas, clientes y AR no se crucen.
- Agregaciones/reportes SIEMPRE `WHERE empresa_id = ...`; la cláusula JOIN también debe preservar la pertenencia de ambos lados.
- Procedimientos `sp_crear_reserva`, `sp_crear_reserva_desde_carrito`, `sp_confirmar_venta`, `sp_recibir_orden_compra`, `sp_ajustar_inventario`, etc. no deben ejecutarse únicamente con ID global. Adaptar firmas/validación y revisar TODOS los 24 objetos de rutina actuales.
- `app/core/database.py` hoy usa `Session.info['request_ip']` y `after_begin` para `app.ip`. Si se agrega `set_config('app.empresa_id', ..., true)`, hacerlo **después de validar la membresía**, en la transacción activa, y reestablecerlo en transacciones posteriores; una sesión puede iniciar transacción antes de validar login, por lo que depender exclusivamente de `after_begin` no es seguro. Valores `true` son locales a la transacción y no se transmiten de un request a otro al reutilizar conexiones.
- **La variable `app.empresa_id` NO ES un control de acceso por sí sola**, menos aún con un rol BYPASSRLS. La seguridad requiere permisos de BD/RLS reales + repositorios con filtros y comprobación de recursos; los triggers y rutinas también deben preservar el tenant.
- Auditar `vw_inventario_stock` (`security_invoker=true`), servicios de Stripe/pagos y subida de assets; no confiar en que la UI filtra por empresa.

## 7. Suscripción y funcionalidades
- `plan_saas.max_sucursales_activas`: BÁSICO=2; PROFESIONAL=10, configurables en BD.
- La creación/reactivación de sucursal se autoriza en backend en transacción coherente, contando activas de la empresa y protegiendo contra dos altas concurrentes (bloquear fila de empresa o equivalente).
- `permite_reportes_ia` se verifica en backend para IA generativa, sin confiar en un botón oculto.
- Plan vencido/suspendido: denegar operaciones comerciales protegidas (403), salvo rutas autorizadas explícitamente para gestión/renovación por responsables.
- El estado efectivo combina `suscripcion.estado='ACTIVA'` y rango de fechas vigente. Para evitar confusión en el prototipo usar el criterio de fecha de negocio documentado y zona horaria `America/La_Paz`, no comparar una `DATE` con UTC sin decisión.

## 8. Contrato mínimo de errores
| Caso | HTTP propuesto | Regla |
|---|---|---|
| Sin token, JWT inválido/expirado o legacy en ruta tenant | 401 | No hay sesión tenant válida |
| Usuario sin membresía, rol/empresa desactivado | 403 | No autorizar aunque el JWT esté firmado |
| Empresa suspendida / plan no vigente | 403 | Excepción solo en rutas de renovación/gestión autorizadas |
| Recurso de otro tenant (aunque se conozca el ID) | 404 | No revelar existencia ni datos |
| Límite de sucursales activas agotado | 409 | Validación atómica en backend |
| Tenant ambiguo en login | Respuesta de selección tipada | No emitir token tenant antes de selección |

**Regla anti-IDOR:** no retornar `403` con información de que el recurso de otra empresa existe cuando `404` es adecuado; usar error genérico.

## 9. Cambios sugeridos para el Paso 6 (solo implementación inicial)
1. Crear modelos SQLAlchemy de `Empresa`, `UsuarioEmpresa`, `Suscripcion`, `PlanSaas` conforme al borrador SQL una vez probado en base aislada (para las entidades aún inexistentes en vivo no ejecutar modelos que disparen consultas en producción).
2. `app/core/tenant_context.py`: estructura inmutable y errores explícitos.
3. `app/core/dependencies.py`: crear `get_current_tenant_context` y `require_tenant_permission`, manteniendo las dependencias anteriores temporalmente solo para rutas ya identificadas, sin permitirles servir endpoints multitenant.
4. `app/modules/autenticacion_seguridad/{repositories,services,schemas,api}`: validar membresía/rol, contrato de login/selección; coordinar con clientes web y móvil, sin ruptura silenciosa.
5. `app/core/security.py`: separar token de selección de empresa del token tenant y validar claims estrictamente.
6. Preparar pruebas unitarias aisladas con mocks y comprobaciones reales sobre una base de pruebas; no confiar en la BD de producción como laboratorio.
7. Solo desplegar multiempresa cuando las 31 tablas y todos los endpoints/servicios/SQL tengan guardias efectivas. Los cambios de autenticación aislados **NO** completan el aislamiento del sistema.

## 10. Decisiones que debemos dejar explícitas para la implementación
- Identidad global `usuario` sí; rol empresarial desde `usuario_empresa`.
- La selección de empresa se autentica y valida en servidor, nunca únicamente desde el frontend.
- Cuentas antiguas pertenecen a Vanter Men, pero **no** se nombra superadmin global automáticamente.
- Dos perfiles de empleado/cliente de un mismo usuario requerirán ajustar la unicidad original.
- Admin de plataforma y admin de empresa son poderes diferentes.
- No hay todavía migración DDL aplicada: el borrador del Paso 3 **no existe en la base real**.
- Proteger el código de arranque/dependencias para evitar referenciar nuevas tablas antes de que estén desplegadas, o implementar cambios bajo una activación controlada.
- Las pruebas y clientes legados que fabriquen JWT sin empresa deben adaptarse al activarse el nuevo flujo.

**Criterio de cierre del Paso 5:** diseño documentado en GitHub, referido a código real y al PDF, sin edición del backend funcional, sin migración ejecutada y con matriz de pruebas para el siguiente turno.
