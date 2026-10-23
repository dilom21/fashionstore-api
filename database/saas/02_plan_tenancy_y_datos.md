# FashionStore — Segundo parcial — Bloque 1, Paso 4 de 7
## Plan de pertenencia empresarial, datos históricos y aislamiento multitenant

**Estado:** DISEÑO PARA REVISIÓN; NO APLICADO A SUPABASE.  
**Rama:** `feat/saas-multitenant-parcial2`  
**Documento rector:** `Examen Parcial-2 - Sistemas 2 - S2-2026.pdf`, páginas 7-8, apartados 9.1–9.6 y RNF02.  
**Fuente técnica:** Esquema en vivo del proyecto Supabase `zyitwlxfkphthlvlmuej` (consultado el 23/10/2026).

## 1. Requisito obligatorio (apartados 9.2 y 9.6)
- Una sola base PostgreSQL compartida; cada **empresa** es un tenant y puede disponer de varias sucursales.
- El aislamiento debe comprobarse en **FastAPI**; no basta ocultar botones en Angular o Flutter.
- Un usuario de una empresa no puede consultar, modificar ni borrar datos de otra.
- Para la demostración se prepararán cinco empresas (supera el mínimo de dos exigido).
- Plan BÁSICO: 2 sucursales activas; PROFESIONAL: 10. IA generativa exclusiva del Profesional.
- El PDF no determina el detalle exacto de las columnas o índices: las decisiones siguientes son el **diseño propuesto del proyecto**.

## 2. Inventario de las 41 tablas actuales: 10 globales y 31 empresariales

### Globales (10)
`accion`, `ciudad`, `color`, `funcion`, `modulo`, `rol`, `rol_funcion`, `talla`, `token_recuperacion`, `usuario`.

**Justificación:** `usuario` representa identidad única con correo global. `usuario_empresa` establece el rol y la pertenencia a cada empresa. `rol` y `rol_funcion` son plantillas globales de permisos, no una autorización por sí solos. `ciudad`, `talla` y `color` se comparten como catálogos de referencia en esta versión académica; requerirían otro diseño si cada empresa pudiera crear sus propios valores. `token_recuperacion` recupera credenciales de la identidad global.

### Propias de cada empresa (31)
- **Auditoría:** `bitacora` (puede requerir `empresa_id NULL` para eventos exclusivos de la plataforma).
- **Operación y personas:** `sucursal`, `empleado`, `cliente`.
- **Catálogo:** `categoria`, `producto`, `variante_producto`, `recurso_producto`, `temporada`, `coleccion`, `producto_coleccion`, `promocion`, `promocion_producto`.
- **Proveedores y compras:** `proveedor`, `proveedor_producto`, `orden_compra`, `detalle_orden_compra`.
- **Inventario:** `inventario`, `movimiento_inventario`.
- **Carrito, reserva, venta y pagos:** `carrito`, `detalle_carrito`, `reserva`, `detalle_reserva`, `venta`, `detalle_venta`, `pago`, `devolucion`, `detalle_devolucion`.
- **Vestidor virtual:** `configuracion_vestidor_ar`, `sesion_vestidor_ar`, `prueba_vestidor_ar`.

**Verificación:** 10 + 31 = 41. El esquema `public` vigente aún no contiene ninguna columna `empresa_id`.

## 3. Estrategia para los registros anteriores al segundo parcial
1. Conservar intactos los ID originales de tablas y sus claves foráneas.
2. Crear una empresa cuyo `slug = 'vanter-men'`; recuperar su ID mediante `SELECT id FROM public.empresa WHERE slug='vanter-men'`, **no asumir ID=1**.
3. Registrar los planes `BASICO` (2 sucursales, sin IA) y `PROFESIONAL` (10 sucursales, con IA) y sus suscripciones.
4. Vanter Men tendrá sus **4 sucursales existentes** y el plan **PROFESIONAL**.
5. Asociar a Vanter Men todos los registros comerciales históricos preexistentes de las 31 tablas; **no clonarlos** hacia otros tenants.
6. Preservar los **9 usuarios globales**; generar una membresía en `usuario_empresa` por cada usuario con su `rol_id` histórico. El usuario podrá tener otras membresías futuras con roles distintos.
7. Hay **2 usuarios ADMINISTRADOR**; mantener su acceso a Vanter Men, pero definir explícitamente después quién será administrador principal. **No nombrar a uno arbitrariamente**.
8. `cliente.usuario_id` está poblado en los **5 clientes** actuales; los **4 empleados** apuntan a usuarios existentes. No hay usuarios sin perfil empleado/cliente en la revisión actual.
9. Las cuatro empresas de demostración adicionales (nombres tentativos: UrbanWear, Elegancia Sur, Denim House, SportLine) iniciarán con **registros comerciales propios y separados**, nunca con copias de ventas o clientes originales. Nombres y sucursales se pueden ajustar antes de la carga.

## 4. Estrategia de migración sin romper las tablas vigentes

### Fase A: expandir el esquema (aún no ejecutar)
- Crear primero las 5 tablas base del Paso 3.
- Añadir `empresa_id BIGINT` a las 31 tablas empresariales. Se recomienda **secuencia controlada** para migrar datos existentes, no añadir un `NOT NULL` masivo de inmediato.
- En `bitacora`, mantener `empresa_id` anulable para auditoría exclusiva de plataforma, con una regla diferenciada y comprobable.
- Revisar la interacción con **27 triggers existentes** antes de emitir `UPDATE` de pertenencia.
- **No desactivar todos los triggers indiscriminadamente.** En particular, hay protecciones sobre detalle de venta y detalle de orden de compra que bloquean actualizaciones según el estado.
- Considerar un procedimiento de reconstrucción en base de prueba o una ruta de actualización explícitamente segura, sin alterar cantidades, precios, estados ni totales.

### Fase B: asignar propietario a datos legados
- Transacción supervisada; localizar `empresa.id` por `slug`, no por número fijo.
- Asignar filas históricas de **todas** las tablas empresariales a Vanter Men, verificando antes y después los conteos.
- Comprobar que la pertenencia de cada registro coincida con la de todas sus entidades empresariales referenciadas.
- Generar membresías de los 9 usuarios originales solo para Vanter Men y preservar `usuario.rol_id` transitoriamente por compatibilidad.
- No exponer el nuevo esquema a clientes hasta que FastAPI valide el tenant y los procedimientos estén adaptados.

### Fase C: reforzar integridad
- Para las 28 tablas empresariales con ID propio: añadir `UNIQUE(empresa_id, id)` para permitir referencias compuestas; **su PK `id` original no se modifica**.
- Para cada FK entre entidades empresariales: migrar o complementar la FK simple con `FOREIGN KEY (empresa_id, columna_id) REFERENCES padre(empresa_id, id)`. Las FKs a catálogos globales conservan su forma original.
- Para enlaces de dos padres empresariales (`detalle_carrito` y su inventario, `reserva` con cliente y sucursal, `pago` con venta, `inventario` con sucursal/variante/temporada, `prueba_vestidor_ar` con sesión/configuración/variante, etc.), validar **ambos lados** y no solo el registro principal.
- Para las 30 tablas empresariales diferentes de `bitacora`: establecer `empresa_id NOT NULL` **únicamente después** de comprobar que todas las filas están asignadas.
- Establecer unicidad empresarial: `categoria(empresa_id,nombre)`, `temporada(empresa_id,nombre)`, `variante_producto(empresa_id,sku)`, `sucursal(empresa_id,ciudad_id,nombre)`, `proveedor(empresa_id,nit)`, y evaluar `cliente(empresa_id,ci)`, `empleado(empresa_id,ci)`, `cliente(empresa_id,usuario_id)`, `empleado(empresa_id,usuario_id)`.
- Para `pago.referencia_transaccion`, decidir el ámbito de unicidad **según pasarela**; no relajar automáticamente una referencia que la pasarela exige globalmente única.
- La exclusión de `bitacora` de `NOT NULL` **no** permite abrir un bypass de aislamiento; sus registros empresariales deben llevar propietario y sus eventos de plataforma necesitan ruta administrativa separada.

### Fase D: corte controlado
- Adaptar procedimientos almacenados, 27 triggers, vista `vw_inventario_stock`, repositorios FastAPI y JWT/contexto de empresa.
- Configurar seguridad RLS según el modelo real (JWT propio FastAPI + PostgreSQL), sin asumir que `auth.uid()` corresponde a nuestros usuarios.
- En producción, **no habilitar tráfico de múltiples tenants** hasta que estén activas todas las defensas y pasen las pruebas cruzadas.

## 5. Relación con las suscripciones
- BÁSICO: máximo 2 sucursales **activas**; PROFESIONAL: máximo 10.
- Las restricciones de sucursales y generación de IA se validarán en **FastAPI** utilizando `empresa` + `suscripcion` + `plan_saas`. La UI solo refleja el resultado.
- El respaldo histórico de las cuatro sucursales de Vanter Men requiere PROFESIONAL; no imponer BÁSICO sobre sus cuatro sucursales durante la migración.
- Los planes configurables del prototipo no implican facturación recurrente real (no obligatoria en el PDF).

## 6. Riesgos confirmados de datos y lógica actuales
- 41 tablas, 60 FK, 27 triggers, 24 rutinas y 1 vista.
- 806 registros de `bitacora`, 40 ventas (21 completadas, 1 cancelada, 18 pendientes), 4 órdenes (1 recibida, 1 cancelada, 1 enviada y 1 borrador).
- `fn_proteger_detalle_venta` prohíbe modificar detalles de ventas COMPLETADAS, CANCELADAS o REEMBOLSADAS.
- `fn_proteger_detalle_orden_compra` prohíbe modificar detalles de órdenes RECIBIDAS o CANCELADAS.
- `fn_registrar_bitacora` inserta nuevos eventos ante actualizaciones de entidades; la migración debe preservar trazabilidad sin fabricar cambios comerciales.
- `configuracion_vestidor_ar`, `sesion_vestidor_ar` y `prueba_vestidor_ar` tienen RLS desactivado. Corregirlo con una política de acceso explícita, no activar RLS a ciegas durante la operación normal.
- La vista `vw_inventario_stock` usa `security_invoker=true`, pero debe filtrar tenant en su consulta o mediante las defensas de las tablas fuente.
- Aún no se ha probado la restauración del archivo `.dump` en una base aislada: requisito previo a la aplicación de cambios sobre los datos reales.

## 7. Condiciones de aceptación (para revisión y pruebas futuras)
- `COUNT(*)` de filas históricas por tabla empresarial no disminuye y sus PK se mantienen.
- Las 4 sucursales, 9 usuarios, 5 clientes, 4 empleados, 14 productos, 120 variantes, 265 registros de inventario, 11 reservas, 40 ventas y 30 pagos originales siguen disponibles para Vanter Men.
- Cada fila empresarial pertenece a **exactamente un tenant**, salvo eventos de bitácora explícitamente de plataforma.
- Las otras empresas no reciben datos personales ni ventas del primer parcial.
- El usuario solo entra en una empresa si existe membresía activa.
- Una venta/reserva/inventario/ítem no puede referenciar registros empresariales de otro tenant.
- A solicita `/ventas/{id}` de B -> respuesta sin datos sensibles (normalmente 404) aun si conoce el ID.
- Los reportes y procesos de inventario filtran por tenant; no hay sumas entre empresas.
- Los límites del plan Básico y del Profesional se verifican desde backend.

## 8. Archivos relacionados
- `database/saas/01_esquema_base_borrador.sql` — Paso 3 (borrador DDL de cinco tablas).
- `database/saas/02_verificacion_previa_lectura.sql` — Paso 4 (consultas de comprobación, exclusivamente lectura).
- **Siguientes pasos:** formalizar la migración versionada, restauración de prueba, modificación segura del esquema y código FastAPI, pruebas de aislamiento. No se ejecutó ninguna migración durante este Paso 4.
