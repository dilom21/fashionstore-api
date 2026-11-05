-- T03: extend the existing tenant idempotency ledger to reservations.
-- The ledger is scoped by empresa_id and usuario_id, so replay cannot cross tenants.
BEGIN;
ALTER TABLE public.operacion_cliente_idempotente
  DROP CONSTRAINT IF EXISTS ck_operacion_cliente_tipo;
ALTER TABLE public.operacion_cliente_idempotente
  ADD CONSTRAINT ck_operacion_cliente_tipo
  CHECK (tipo IN ('VENTA_DIGITAL', 'RESERVA'));
COMMIT;
