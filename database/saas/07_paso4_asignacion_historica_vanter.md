# FashionStore — Bloque 2 / Paso 4: asignación histórica a Vanter Men

**Estado:** SQL y script de ejecución local PREPARADOS; **no se han ejecutado** en la computadora del usuario ni en Supabase. El usuario debe comprobar el resultado de PowerShell antes de dar el paso por completado.

**Fuentes:** `Examen Parcial-2 - Sistemas 2 - S2-2026.pdf`, sección 9, especialmente 9.1, 9.2, 9.3 y 9.6, y RNF02; `database/saas/02_plan_tenancy_y_datos.md` del Bloque 1. El PDF exige pertenencia por empresa y aislamiento, pero no prescribe esta técnica SQL.

## Base y artefactos
- PostgreSQL LOCAL: `127.0.0.1:5432`, base `fashionstore_restore_20261023_193554_594`.
- Migración preparada: `supabase/migrations/20261023000200_vanter_tenancy_historica.sql`. Está **guardada como migración propuesta**, con guardas de **prueba local**. NO ejecutar como `supabase db push`, ni aplicarla al servidor remoto en su forma actual. Deberá adaptarse/promoverse al diseñar despliegue definitivo.
- Comando seguro para esta etapa: `database/saas/03_asignar_vanter_historico_local.ps1`.
- Precondición verificada previamente por el estudiante: 46 tablas y cinco SaaS nuevas vacías.

## Cambios previstos si el usuario ejecuta el script local
1. Crear plan `BASICO`: máximo 2 sucursales activas, productos/ventas/reservas/offline/reportes convencionales disponibles, sin IA generativa.
2. Crear plan `PROFESIONAL`: máximo 10 sucursales activas y con IA generativa.
3. Crear empresa `vanter-men`, nombre comercial y razón social académica provisionales `Vanter Men`; crear `empresa_configuracion` inicial.
4. Crear suscripción `ACTIVA` a Profesional con vigencia de 365 días desde la fecha **local** de aplicación (`America/La_Paz`), solo con propósito académico. No requiere pago SaaS recurrente.
5. Crear exactamente 9 filas `usuario_empresa`, copiando el `rol_id` y `estado` de los 9 usuarios existentes. No elegir un superadmin ni cambiar `usuario.rol_id`.
6. Añadir columna `empresa_id BIGINT` a las 31 tablas empresariales preexistentes; usar `DEFAULT` constante al añadir la columna y retirarlo inmediatamente. Es una operación DDL para propagar titularidad histórica, **sin un `UPDATE` masivo** que active los triggers de ventas, compras, inventario y auditoría.
7. Añadir FK individual `empresa_id REFERENCES public.empresa(id)` en cada tabla. No confundirla con la FK compuesta pendiente: la FK individual comprueba que la empresa exista, **pero no que un recurso referenciado pertenezca a la misma empresa**.
8. Conservar `empresa_id NOT NULL` en 30 tablas y permitir `NULL` únicamente en `bitacora` por los futuros eventos propios de la plataforma. Todos los registros de bitácora históricos se asocian a Vanter Men.
9. Verificar en transacción el conteo anterior/posterior de cada una de las 31 tablas, que todos sus registros históricos pertenezcan a Vanter y que los 27 triggers sigan registrados; ante un error SQL, revertir la transacción completa.

## Controles incluidos
- El PowerShell usa nombre fijo de la base y host `127.0.0.1`; comprueba que sean exactamente esos antes de pedir `ASIGNAR`.
- La migración SQL verifica también `current_database()`, los 46 objetos de tabla iniciales, las 5 SaaS vacías, conteos históricos, 27 triggers y ausencia previa de `empresa_id` en las 31 tablas. No es reutilizable tal cual en producción debido a sus guardas de copia local.
- La contraseña se solicita por consola y se elimina de variables de entorno al finalizar.
- El script comprueba después: 1 empresa, 2 planes, 1 suscripción, 1 configuración, 9 membresías, 31 columnas históricas de empresa, 30 `NOT NULL`, 31 FK simples a empresa, 4 sucursales / 40 ventas / 30 pagos de Vanter, 27 triggers y datos históricos sin cambio en conteos.
- La restauración `.dump` original se conserva. No modificar ni borrar el backup.

## Advertencias para siguientes pasos (NO resueltas aún)
- Todavía no existen FK **compuestas** entre recursos empresariales. Se requieren en el Paso 5 para impedir cruzar pedidos, inventarios, usuarios/empleados, reservas, ventas, pagos, AR, etc., y para permitir tenants distintos.
- Las tablas heredadas pueden seguir con permisos inseguros hasta que se audite el rol de FastAPI y se aplique aislamiento real en backend/RLS. Tres tablas de vestidor AR tenían RLS desactivado en el inventario original.
- `bitacora.empresa_id NULL` **no significa acceso libre**. Deben distinguirse eventos de plataforma de eventos empresariales, idealmente mediante nuevos triggers y una ruta administrativa separada.
- El login SaaS de Bloque 1 consulta `cliente.empresa_id`/`empleado.empresa_id`; aun con estas columnas presentes, se requiere auditoría completa antes de activar el feature flag.
- Las restricciones `cliente.usuario_id UNIQUE` y `empleado.usuario_id UNIQUE` del primer parcial siguen globales. Adaptarlas después para usuarios multiempresa.
- La migración de titularidad no altera los roles de las nueve cuentas ni designa por defecto administrador de plataforma.
- No ejecutar las pruebas legacy de escritura contra este esquema modificado sin revisar primero que incluyan contexto de empresa. Las nuevas inserciones empresariales requieren `empresa_id`.
- **Mantener `SAAS_AUTH_ENABLED=false` y no exponer tráfico multiempresa.**
