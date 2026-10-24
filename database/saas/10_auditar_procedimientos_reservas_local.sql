-- B3/P5E: AUDITORIA METADATOS DE PROCEDIMIENTOS CU16/CU18.
-- SOLO PostgreSQL 18 LOCAL RESTAURADO. NO EJECUTA PROCEDIMIENTOS.
-- Las marcas heuristicas de contenido NO prueban autorizacion tenant.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $auditar$
DECLARE v_count BIGINT;
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino PostgreSQL no autorizado: % / %',
            current_database(), host(inet_server_addr());
    END IF;

    IF (SELECT count(*) FROM public.empresa) <> 5
       OR (SELECT count(*) FROM public.reserva) <> 11
       OR (SELECT count(*) FROM public.inventario) <> 274
       OR (SELECT count(*) FROM public.carrito) <> 48
       OR (SELECT count(*) FROM public.venta) <> 40
       OR (SELECT count(*) FROM public.pago) <> 30 THEN
        RAISE EXCEPTION 'Conteos comerciales locales no coinciden con 5B';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_constraint con
    JOIN pg_class tab ON tab.oid=con.conrelid
    JOIN pg_namespace ns ON ns.oid=tab.relnamespace
    WHERE ns.nspname='public'
      AND con.contype='f' AND con.convalidated
      AND con.conname LIKE 'fk_tenant_%';
    IF v_count <> 48 THEN
        RAISE EXCEPTION 'FK compuestas tenant invalidas: % de 48',v_count;
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_proc p
    JOIN pg_namespace ns ON ns.oid=p.pronamespace
    WHERE ns.nspname='public' AND p.prokind='p'
      AND p.proname IN (
          'sp_crear_reserva_desde_carrito',
          'sp_cancelar_reserva',
          'sp_finalizar_reserva_sin_compra'
      );
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'Se esperan exactamente 3 procedimientos CU16/CU18: % encontrados',v_count;
    END IF;
    RAISE NOTICE 'AUDITORIA OK: 5 empresas, 48 FK tenant, 3 procedimientos existentes';
    RAISE NOTICE 'ADVERTENCIA: detectar texto empresa_id NO certifica comprobacion de propiedad';
END;
$auditar$;

SELECT
    p.proname AS procedimiento,
    pg_get_function_identity_arguments(p.oid) AS argumentos,
    (lower(pg_get_functiondef(p.oid)) LIKE '%empresa_id%') AS menciona_empresa_id,
    (lower(pg_get_functiondef(p.oid)) LIKE '%app.empresa_id%') AS menciona_contexto_rls,
    (lower(pg_get_functiondef(p.oid)) LIKE '%for update%') AS menciona_for_update,
    (lower(pg_get_functiondef(p.oid)) LIKE '%stock_reservado%') AS menciona_stock_reservado
FROM pg_proc p
JOIN pg_namespace ns ON ns.oid=p.pronamespace
WHERE ns.nspname='public' AND p.prokind='p'
  AND p.proname IN (
      'sp_crear_reserva_desde_carrito',
      'sp_cancelar_reserva',
      'sp_finalizar_reserva_sin_compra'
  )
ORDER BY p.proname;

-- Sin leer datos personales; solo estadisticas agrupadas.
SELECT e.slug,
       (SELECT count(*) FROM public.reserva r WHERE r.empresa_id=e.id) AS reservas,
       (SELECT count(*) FROM public.detalle_reserva dr WHERE dr.empresa_id=e.id) AS lineas_reservadas,
       (SELECT count(*) FROM public.inventario i WHERE i.empresa_id=e.id) AS inventarios,
       (SELECT coalesce(sum(i.stock_reservado),0) FROM public.inventario i
        WHERE i.empresa_id=e.id) AS unidades_stock_reservado
FROM public.empresa e ORDER BY e.slug;

ROLLBACK;
