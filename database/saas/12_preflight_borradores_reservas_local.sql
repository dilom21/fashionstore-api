-- B3/P5G: preflight metadatos para borradores SP, SIN EJECUTARLOS.
-- PostgreSQL 18 SOLO LOCAL, lectura obligatoria y ROLLBACK.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $guard$
DECLARE v_fk integer;
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino no autorizado para 5G';
    END IF;
    IF (SELECT count(*) FROM public.empresa) <> 5
       OR (SELECT count(*) FROM public.carrito) <> 48
       OR (SELECT count(*) FROM public.reserva) <> 11
       OR (SELECT count(*) FROM public.inventario) <> 274 THEN
        RAISE EXCEPTION 'Datos historicos esperados no coinciden';
    END IF;
    SELECT count(*) INTO v_fk FROM pg_constraint c
    JOIN pg_class t ON t.oid=c.conrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname='public' AND c.contype='f' AND c.convalidated
      AND c.conname LIKE 'fk_tenant_%';
    IF v_fk<>48 THEN
        RAISE EXCEPTION 'Se esperaban 48 FK tenant, se hallaron %',v_fk;
    END IF;
    RAISE NOTICE 'PRECHECK 5G OK: local, cinco empresas y 48 FK tenant';
END $guard$;

-- Solo metadatos de columnas realmente existentes, sin datos de clientes.
SELECT table_name,column_name,data_type,is_nullable,column_default,
       is_identity
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN (
    'carrito','detalle_carrito','reserva','detalle_reserva',
    'inventario','movimiento_inventario','venta',
    'usuario_empresa','suscripcion','plan_saas','empleado'
  )
  AND column_name IN (
    'id','empresa_id','cliente_id','usuario_id','sucursal_id','carrito_id',
    'reserva_id','inventario_id','estado','fecha_actualizacion',
    'stock_actual','stock_reservado','fecha_atencion','fecha_inicio',
    'fecha_vencimiento','rol_id','permite_reservas'
  )
ORDER BY table_name,column_name;

-- Las FK, CHECK y UNIQUE relevantes deben respetar la migracion B2.
SELECT t.relname AS tabla,c.conname AS restriccion,
       c.contype AS tipo,pg_get_constraintdef(c.oid) AS definicion
FROM pg_constraint c
JOIN pg_class t ON t.oid=c.conrelid
JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public'
  AND t.relname IN ('carrito','detalle_carrito','reserva',
    'detalle_reserva','inventario','movimiento_inventario','venta')
  AND c.contype IN ('f','c','u','p')
ORDER BY t.relname,c.conname;

SELECT t.relname AS tabla,idx.indexname,idx.indexdef
FROM pg_indexes idx
JOIN pg_class t ON t.relname=idx.tablename
JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public' AND idx.schemaname='public'
  AND t.relname IN ('carrito','reserva','detalle_reserva','inventario')
ORDER BY t.relname,idx.indexname;

-- Triggers de negocio y FK de auditoria; no se invocan.
SELECT t.relname AS tabla,tr.tgname AS trigger,
       tr.tgenabled AS habilitado,
       pg_get_triggerdef(tr.oid) AS definicion
FROM pg_trigger tr
JOIN pg_class t ON t.oid=tr.tgrelid
JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public' AND NOT tr.tgisinternal
  AND t.relname IN ('carrito','detalle_carrito','reserva',
    'detalle_reserva','inventario','movimiento_inventario','venta')
ORDER BY t.relname,tr.tgname;

-- Revisar rol real y barreras RLS. SUPERUSER/BYPASSRLS no prueba aislamiento.
SELECT current_user AS usuario_pg,
       r.rolsuper,r.rolbypassrls,r.rolinherit
FROM pg_roles r WHERE r.rolname=current_user;
SELECT t.relname AS tabla,t.relrowsecurity,t.relforcerowsecurity,
       pg_get_userbyid(t.relowner) AS owner
FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public' AND t.relname IN (
  'carrito','detalle_carrito','reserva','detalle_reserva',
  'inventario','movimiento_inventario'
) ORDER BY t.relname;

ROLLBACK;
