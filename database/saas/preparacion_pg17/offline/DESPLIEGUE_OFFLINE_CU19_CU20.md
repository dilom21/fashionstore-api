# CU19/CU20 — despliegue controlado del ledger offline

**NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA.**

## Requisitos previos

- PostgreSQL 17.
- CU11, CU12 y CU13 instalados y verificados.
- Roles `fashionstore_api` y `fashionstore_sp_owner` existentes y restringidos.
- Funciones `fashionstore_private.actor_empresa_id()` y
  `actor_usuario_id()` disponibles para `fashionstore_sp_owner`.
- Índice `public.uq_carrito_activo_cliente_sucursal` existente.
- Instalador con `CREATEROLE`/superusuario y `ADMIN OPTION` sobre
  `fashionstore_sp_owner`. En Supabase es `postgres`.

## 1. Validar en PostgreSQL 17 aislado

```powershell
python database/saas/preparacion_pg17/offline/validar_106_pg17_aislado.py
```

Resultado exigido:

```text
CU19_CU20_LAB=PASS ledger,idempotencia,conflicto,rechazo,concurrencia,rollback,servicio_reserva,venta_admin,venta_cajero,actor,acl,rls,carrito_origen,cu11_cu13_intactos
```

El laboratorio crea un clúster temporal, instala CU11/CU13 y 106, prueba
ledger, replay, conflicto, rechazo con efectivo, actor ajeno, acceso directo
denegado, RLS, índice de carrito y conservación de CU11/CU13.

## 2. Instalar 106

Ejecutar con la API detenida o en una ventana controlada: el cambio de
`public.carrito` y su índice único bloquea brevemente esa tabla. No ejecutar
con sincronización offline activa.

Ejecutar como administrador autorizado:

```powershell
psql -X -v ON_ERROR_STOP=1 `
  -f database/saas/preparacion_pg17/offline/106_offline_cu19_cu20_PROPUESTA_NO_EJECUTAR.sql
```

Salida esperada:

```text
CU19_CU20_OFFLINE_INSTALL=PASS NO_EJECUTADO_EN_SUPABASE
```

La transacción:

- agrega `public.carrito.origen` con default `ONLINE`;
- recrea el índice único incluyendo `origen`, conservando el nombre que usa
  CU15;
- crea `fashionstore_private.offline_operacion` con RLS/FORCE RLS;
- crea las cuatro funciones de ledger con owner `fashionstore_sp_owner`,
  `SECURITY DEFINER`, `search_path=pg_catalog, pg_temp` y EXECUTE solo para
  `fashionstore_api`;
- conserva y restaura exactamente las membresías del instalador;
- no modifica procedimientos CU11/CU13 ni datos comerciales.

## 3. Verificación posterior

```sql
SELECT to_regclass('fashionstore_private.offline_operacion');
SELECT column_name, column_default
FROM information_schema.columns
WHERE table_schema='public' AND table_name='carrito' AND column_name='origen';
SELECT indexdef FROM pg_indexes
WHERE schemaname='public' AND indexname='uq_carrito_activo_cliente_sucursal';
SELECT proname, pg_get_userbyid(proowner), prosecdef
FROM pg_proc
WHERE pronamespace='fashionstore_private'::regnamespace
  AND proname LIKE 'fn_offline_%'
ORDER BY proname;
SELECT relrowsecurity, relforcerowsecurity
FROM pg_class WHERE oid='fashionstore_private.offline_operacion'::regclass;
```

Condiciones mínimas:

- `fashionstore_api` **no** tiene DML directo sobre
  `fashionstore_private.offline_operacion`.
- Las cuatro funciones son `SECURITY DEFINER` del owner NOLOGIN.
- `PUBLIC` no tiene EXECUTE.
- `fashionstore_sp_owner` no conserva `CREATE` temporal.
- CU11/CU13 siguen presentes.

## 4. Activar el flag

Solo después de 106 instalado y verificado:

```text
SAAS_AUTH_ENABLED=true
SAAS_OFFLINE_ENABLED=true
```

No desactivar `SAAS_VENTAS_ENABLED`: CU13 lo utiliza. No guardar secretos ni
el flag en el repositorio; usar el gestor de secretos/entorno.

Mientras `SAAS_OFFLINE_ENABLED=false` o falte 106, ambos endpoints responden
503 y no se procesa ninguna operación comercial offline.

## 5. Contrato para Angular

La referencia obligatoria es:

```text
docs/saas/contratos_offline_cu19_cu20.md
```

No crear endpoints alternativos por variante. Usar:

- `POST /tenant/offline/operaciones`
- `GET /tenant/offline/operaciones/{operacion_uuid}`

## 6. Reversión manual controlada

No se entrega reversión automática. Antes de revertir:

1. Detener la sincronización y poner `SAAS_OFFLINE_ENABLED=false`.
2. Verificar que `offline_operacion` no tenga operaciones `PENDIENTE`,
   `ENVIANDO` o `ERROR_REINTENTABLE`.
3. Revisar el impacto de eliminar la columna `origen` y el ledger; conservar
   evidencia si existen operaciones aceptadas.
4. Ejecutar cualquier reversión solo con autorización y respaldo, como
   `postgres`, sin tocar CU11/CU13 ni datos comerciales.
