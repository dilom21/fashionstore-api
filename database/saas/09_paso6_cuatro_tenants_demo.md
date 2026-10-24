# Bloque 2 / Paso 6 — Cuatro tenants independientes de demostración

**Estado:** artefactos preparados en GitHub, **no ejecutados** todavía en la PC del estudiante ni en Supabase.  
**Rama:** `feat/saas-multitenant-parcial2`.  
**Requisito académico:** PDF `Examen Parcial-2 - Sistemas 2 - S2-2026.pdf`, Desafío VI, secciones 9.1–9.6: base PostgreSQL compartida, tenant = empresa y mínimo dos empresas de prueba, planes Básico y Profesional, administrador por empresa, sucursales y recursos propios.  
**Destino de esta versión del seed:** exclusivamente `127.0.0.1:5432`, BD `fashionstore_restore_20261023_193554_594`.

## Archivos
- SQL: `supabase/migrations/20261023000400_cuatro_tenants_demo_local.sql`. **NO USAR DB PUSH**: por seguridad contiene un nombre de base local fijo; no es una migración remota lista para producción.
- Script PowerShell: `database/saas/05_crear_cuatro_tenants_demo_local.ps1`.
- El archivo exige 46 tablas, 48 FK tenant, 28 UNIQUE tenant, 27 triggers y los conteos del Paso 5. Una segunda ejecución no es admisible.
- El script solo ejecuta después de introducir `DEMO` manualmente.

## Tenants ficticios que se crearán

| Empresa | Slug | Plan | Sucursales activas nuevas | Productos | Variantes | Inventarios | Clientes ficticios | Usuarios admin desactivados |
|---|---|---|---:|---:|---:|---:|---:|---:|
| UrbanWear | urbanwear | BASICO | 1 | 1 | 1 | 1 | 1 | 1 |
| Elegancia Sur | elegancia-sur | PROFESIONAL | 3 | 1 | 1 | 3 | 1 | 1 |
| Denim House | denim-house | BASICO | 2 | 1 | 1 | 2 | 1 | 1 |
| SportLine | sportline | PROFESIONAL | 3 | 1 | 1 | 3 | 1 | 1 |

Para cada una se crea además configuración, suscripción `ACTIVA`, una categoría y temporada exclusivas (pueden compartir nombres con otros tenants), una membresía de admin, y un perfil de empleado demo inactivo ligado a su primera sucursal. Los productos y direcciones son ficticios; no se crean ventas, pagos ni reservas adicionales.

## Seguridad de identidades de demostración

Las cuatro cuentas administrativas se registran con correos bajo el dominio reservado `demo.invalid`, con `usuario.estado=false` y `usuario_empresa.estado=false`. Los hashes bcrypt son de cuatro secretos aleatorios **no guardados**; nadie dispone de contraseñas funcionales. En otro paso deberán emitirse contraseñas por canal seguro y habilitar solo cuando el backend filtre correctamente por tenant. No cambiar el feature flag `SAAS_AUTH_ENABLED` en este paso.

## Auditoría

El trigger original `fn_registrar_bitacora` no copiaba el `empresa_id` al registrar eventos. Se reemplaza únicamente su definición, **manteniendo todos los triggers**, para extraer `empresa_id` de la fila afectada (`NEW`/`OLD`) y grabar eventos empresariales con dueño. Eventos de identidades globales `usuario` carecen de empresa y quedan como auditoría de plataforma (`NULL`).

En esta siembra se esperan 34 nuevos eventos (4 registros de usuarios globales + 9 sucursales + 4 productos + 4 variantes + 9 inventarios + 4 empleados). De los 34, 30 llevan `empresa_id` y 4 son eventos globales. Se conservan los 806 eventos históricos de Vanter Men.

## Conteos globales esperados después de ejecutarse con éxito

| Elemento | Antes | Después |
|---|---:|---:|
| Empresas | 1 | 5 |
| Planes | 2 | 2 |
| Suscripciones | 1 | 5 |
| Membresías | 9 | 13 |
| Usuarios globales | 9 | 13 |
| Clientes | 5 | 9 |
| Empleados | 4 | 8 |
| Sucursales | 4 | 13 |
| Productos | 14 | 18 |
| Variantes | 120 | 124 |
| Inventarios | 265 | 274 |
| Ventas | 40 | 40 |
| Pagos | 30 | 30 |
| Reservas | 11 | 11 |
| Bitácora | 806 | 840 |
| FK empresariales nuevas | 48 | 48 |
| Triggers | 27 | 27 |

## Advertencias sobre el alcance

- Los límites de sucursales están **respetados por los datos de demostración**, pero sigue pendiente bloquear desde FastAPI la creación de nuevas sucursales por encima del plan.
- Las FK compuestas del Paso 5 impiden relaciones cruzadas, pero todavía no impiden `SELECT` de otros tenants si una API consulta sin filtro. Esto sigue pendiente y NO se considera cumplido RNF02.
- Debe probarse en el Paso 7 un intento real de inserción cruzada, el acceso por ID de otro tenant, login SaaS y estados de suscripción.
- Los usuarios demo están inactivos y no pueden utilizarse para autenticar aún. Será necesario un flujo seguro de aprovisionamiento y habilitación.
- No exponer el esquema SaaS ni activar `SAAS_AUTH_ENABLED`; Supabase de producción y la rama main quedan sin modificar.

**Próxima acción:** ejecutar el script PowerShell en Windows y enviar el resultado para comprobar si el Paso 6 se completó. No pasar al Paso 7 sin esa evidencia.
