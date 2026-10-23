# FashionStore — Segundo parcial — Paso 5: matriz de seguridad multitenant

**Estado:** casos de prueba propuestos, NO ejecutados.  
**Referencia normativa:** PDF segundo parcial, pp. 7–9: desafío VI (9.6), RNF01, RNF02 y RNF11.  
**Contexto:** Empresa A = Vanter Men; empresa B = empresa demo independiente; usuario U con o sin membresía. Preparar fixtures en entorno de pruebas; no escribir datos de prueba en Supabase productivo.

| ID | Caso de aceptación | Resultado requerido |
|---|---|---|
| AUTH-01 | Credenciales válidas, miembro activo de A, una sola empresa | Token tenant `empresa_id=A`, sesión autenticada |
| AUTH-02 | Credenciales válidas, dos empresas asignadas | Selección explícita; token previo no sirve para recursos |
| AUTH-03 | Cliente modifica `empresa_id` o `empresa_slug` para elegir B sin membresía | Rechazo 403, sin token de B |
| AUTH-04 | JWT firmado de A utilizado con header/body `empresa_id=B` | Backend conserva A; no cambia tenant ni revela B |
| AUTH-05 | JWT antiguo sin scope/empresa | 401 en todas las rutas comerciales tenant al activar SaaS |
| AUTH-06 | Membresía revocada después de emitir JWT | 403 sin esperar expiración |
| AUTH-07 | Usuario desactivado o empresa desactivada | Rechazo aunque el token no haya expirado |
| AUTH-08 | Rol `CAJERO` de A y `ADMINISTRADOR` de B | Autorización según rol específico de tenant; admin A no implícito |
| AUTH-09 | `ADMINISTRADOR` de A intenta gestionar otra empresa | 403 |
| AUTH-10 | Venta `id` de B solicitada con token válido de A | 404 genérico, sin detalles |
| AUTH-11 | GET listado/reportes con usuario de A | Solo agregados y filas de A |
| AUTH-12 | Venta A intenta usar `inventario_id` de B | Fallo, rollback sin cambio de stock |
| AUTH-13 | Reserva A usa cliente A con sucursal B | Fallo, rollback |
| AUTH-14 | Dos solicitudes simultáneas crean sucursales en plan BÁSICO con una activa | A lo sumo una nueva activa; máximo 2 |
| AUTH-15 | Solicitud IA generativa con plan BÁSICO | 403 sin invocación al proveedor de IA |
| AUTH-16 | Solicitud IA generativa con plan PROFESIONAL y suscripción vigente | Autorización para ejecutar servicio (si está configurado) |
| AUTH-17 | Suscripción vencida o suspendida | Bloqueo de operaciones comerciales protegidas |
| AUTH-18 | `GET /auth/me` con token tenant B | Rol y empresa B obtenidos de BD; no del claim `rol` |
| AUTH-19 | Recuperación de contraseña/login sin tenant seleccionado | No permite leer datos empresariales |
| AUTH-20 | Cambio de empresa válido para U miembro de A y B | Emite nuevo token de B; token de A conserva alcance A |
| AUTH-21 | Conexiones SQLAlchemy reutilizadas de A a B | `app.empresa_id` transaccional no se filtra entre requests |
| AUTH-22 | Venta/reserva offline reintentada con idempotency UUID de tenant A contra B | Rechazar conflicto y nunca reprocesar en B |
| AUTH-23 | `Empleado.usuario_id` o `Cliente.usuario_id` en dos empresas | Soportado tras revisar índices `UNIQUE` y consultas por empresa |
| AUTH-24 | Procedimiento PG llamado con ID de registro de B desde A | Niega la operación; sin alteración transaccional |
| AUTH-25 | Consultar tabla del vestidor AR por API pública sin autorización | Sin acceso a filas u operaciones de otro tenant |
| AUTH-26 | `postgres`/rol BYPASSRLS usado por backend | Fallo en chequeo de seguridad de despliegue; no declararlo aislado |
| AUTH-27 | Usuario sin membresía intenta iniciar sesión bajo slug público A | Rechazo de acceso al recurso empresarial |
| AUTH-28 | Administración de plataforma vs administración de tenant | No se concede superadmin al rol ADMINISTRADOR histórico |

## Evidencias esperadas para el Paso 7
- Pruebas FastAPI `pytest` con consultas reales `id + empresa_id` y repositorios.
- Test de REST de cada módulo: catalogo, clientes, sucursales, inventario, compras, carritos, reservas, ventas, pagos, devoluciones, reportes, vestidor AR y operaciones offline.
- Test de procedimientos/triggers bajo rol de aplicación **sin BYPASSRLS** en base temporal de prueba.
- Comparación de conteos históricos antes/después y verificación de rollback de operaciones inválidas.
- Informe de endpoint/código/línea cubiertos, resultados PASS/FAIL y cambios pendientes.

**Nota:** redactar esta matriz no significa que los 28 escenarios hayan pasado. Ninguna prueba se ejecutó en este Paso 5.
