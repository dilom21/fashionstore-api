# Despliegue estructural temporal

Estado: preparado; no ejecutado. Esta variante mantiene exclusivamente a
Vanter Men y no habilita SaaS multiempresa.

## Orden

Con la API detenida, respaldo nuevo y una sola conexion administrativa directa:

1. `001_ventana_previa.sql`
2. `000_preflight.sql`
3. `010_saas_core_vanter.sql`
4. `020_tenant_expand_backfill.sql`
5. `030_tenant_integrity.sql`
6. `040_routines_views_hardening.sql`
7. `045_compatibilidad_legacy_vanter_temporal.sql`
8. `060_postflight.sql`

Usar `ON_ERROR_STOP=1` y no continuar despues de ningun error.
Si falla cualquier archivo o `060`, mantener la API detenida: no reabrirla
sobre un estado parcial ni intentar el archivo siguiente.

## Alcance temporal

- Los triggers temporales asignan Vanter Men cuando un INSERT legacy omite
  `empresa_id` o cuando SQLAlchemy envia `NULL`. No se instala ningun DEFAULT
  global. Un `empresa_id` distinto de Vanter Men se rechaza.
- Las altas y cambios de rol/estado de `usuario` sincronizan temporalmente su
  membresia de Vanter Men, necesaria para las FK de `cliente` y `empleado`.
- La base rechaza crear otra empresa, eliminar Vanter Men, cambiar su id/slug o
  desactivarla. Por ello los triggers no pueden asignar datos a un tenant incorrecto
  mientras esta compatibilidad permanezca instalada.
- Las FK compuestas, unicidades tenant e inmutabilidad de `empresa_id` de
  `030`/`040` permanecen activas.
- `040` conserva cerrados los procedimientos para `PUBLIC`, `anon` y
  `authenticated`. El owner PostgreSQL mantiene su ejecucion; si FastAPI no usa
  el owner, sus operaciones legacy basadas en procedimientos quedaran bloqueadas
  hasta conocer y autorizar explicitamente ese rol.
- Las cinco tablas SaaS quedan sin acceso para `PUBLIC`, `anon` y
  `authenticated`. No se ejecutan `050a`, `050b` ni `050c`.

## Retiro obligatorio

Antes de crear una segunda empresa se deben, en una misma ventana con la API
detenida: retirar los 31 triggers de asignacion y la sincronizacion de membresia,
retirar la guarda monocompania, adaptar los CU
para enviar `empresa_id` validado y aplicar la seguridad `050a`-`050c`. No se
debe retirar la guarda ni habilitar otra empresa por separado.
