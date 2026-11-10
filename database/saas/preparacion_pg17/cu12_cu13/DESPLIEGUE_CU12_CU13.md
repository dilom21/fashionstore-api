# CU12/CU13 SaaS - paquete SQL

## Prerrequisitos

- Paquete CU11 (`database/saas/preparacion_pg17/reservas/080...`) instalado:
  `fs_verificar_actor_reserva`, `sp_cancelar_reserva_tenant` y
  `sp_finalizar_reserva_sin_compra_tenant`.
- RLS/FORCE RLS y policies `p_sp_tenant_*` de 050b/050c.
- Roles `fashionstore_api`, `fashionstore_context_owner`,
  `fashionstore_sp_owner` restringidos.
- `fashionstore_private.actor_empresa_id()` y `actor_usuario_id()`.

## Instalacion

1. Revisar y aprobar el SQL.
2. Ejecutar `090_instalar_cu12_cu13_saas_PROPUESTA_NO_EJECUTAR.sql`.
3. Ejecutar `091_verificar_cu12_cu13_saas.sql` (solo lectura).
4. Habilitar en el backend:
   - `SAAS_AUTH_ENABLED=true`
   - `SAAS_RESERVAS_ENABLED=true` (CU12)
   - `SAAS_VENTAS_ENABLED=true` (CU13)
   - `SAAS_ACTOR_SQL_OWNER` y `SAAS_SP_SQL_OWNER` ya usados por CU11.

## Que instala

- `sp_confirmar_reserva_tenant(empresa,reserva,usuario)`:
  PENDIENTE -> CONFIRMADA sin tocar inventario.
- `sp_crear_venta_presencial_tenant(empresa,usuario,reserva,cliente,items,venta_id)`:
  crea venta PRESENCIAL PENDIENTE; no registra pago ni descuenta stock.
- `sp_registrar_pago_presencial_tenant(empresa,venta,usuario,metodo,pago_id)`:
  inserta pago APROBADO y confirma la venta; consume/libera reservado,
  descuenta stock, registra movimientos y marca la reserva ATENDIDA.
- `fashionstore_private.actor_sucursal_id()`.
- Policies de lectura para personal (cliente, reserva, detalle_reserva,
  venta, detalle_venta, pago) y policies del owner NOLOGIN.
- ACL minima para `fashionstore_sp_owner`; **no** se concede DML a
  `fashionstore_api`.

## Reversion

`092_revertir_cu12_cu13_CONTROLADO.sql` retira procedimientos, funcion y
policies nuevas. No toca tablas, datos, secuencias ni el paquete CU11.

## Validacion local

`validar_090_pg17_aislado.py` crea un cluster PostgreSQL 17 descartable en
`127.0.0.1:55434`, instala el SQL, prueba CU12/CU13 (stock, empresa,
sucursal, reserva, pago, RLS y concurrencia) y ejecuta verificacion y
reversion. Nunca conecta a Supabase ni al PostgreSQL compartido.
