/* Pruebas de aceptación CU15 para una base AISLADA donde la migración ya fue
   instalada. No contiene fixtures comerciales ni apunta a Supabase. Ejecutar
   cada bloque dentro de BEGIN; ... ROLLBACK; con JWT sintético de laboratorio.
   Un PASS requiere que la transacción revierta todas sus filas al fallar. */

BEGIN;
SET LOCAL search_path = pg_catalog, public, pg_temp;
-- 1) contrato DDL y políticas: no existen privilegios públicos ni doble identity.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname='cu15_nota_numero_seq') THEN RAISE EXCEPTION 'FAIL sequence nota'; END IF;
  IF EXISTS (SELECT 1 FROM information_schema.role_table_grants WHERE table_name LIKE 'cu15_%' AND grantee IN ('public','anon','authenticated')) THEN RAISE EXCEPTION 'FAIL ACL público'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='cu15_tenant' AND tablename='cu15_operacion') THEN RAISE EXCEPTION 'FAIL RLS'; END IF;
END $$;
ROLLBACK;

-- 2) Los siguientes casos se ejecutan en un laboratorio con venta/cliente/
-- inventario/pago sintéticos y deben terminar con ROLLBACK en cada bloque:
--    a) crear dos veces con la misma idempotency_key => mismo operacion_id,
--       una sola operación y cero CU15_PRENDA_RECIBIDA en SOLICITADA.
--    b) M -> L APTO con L disponible => stock M +1, L -1, dos movimientos,
--       una nota y un outbox de recepción + uno de completado.
--    c) L sin stock / NO_APTO / CUARENTENA => excepción 23514 y ningún cambio
--       en inventario, movimientos, operación, nota ni outbox de completado.
--    d) diferencia cotizada Bs 60 sin pago aprobado con referencia => 55000;
--       con pago aprobado CU13/CU14 de referencia exacta => PAGADO y cierre.
--    e) SIN_CAMBIO parcial => importe_elegible usa pago APROBADO prorrateado,
--       50% ROUND_HALF_UP, y repetición de completar no duplica nada.
--    f) JWT A/B, cliente cruzado, empleado sin GESTIONAR_DEVOLUCIONES o fuera
--       de sucursal => SQLSTATE 42501 sin revelar ni modificar la operación.
--    g) timestamps now()=fecha_hora+interval '7 days' => permitido; un segundo
--       posterior => rechazado al crear, manteniendo venta intacta.
