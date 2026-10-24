-- B3/P5I. VERIFICACION NO DESTRUCTIVA del NUEVO sandbox local.
-- No instala ni llama procedimientos. Ejecutar solo en la base sandbox.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';

DO $sandbox$
DECLARE
    v_rel record;
    v_conteo bigint;
    v_tablas integer := 0;
    v_fk integer;
    v_rls integer;
    v_sp integer;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino NO es el sandbox local esperado: % / %',
            current_database(), host(inet_server_addr());
    END IF;

    -- La copia del esquema debe incluir las tablas de CU15 a CU22.
    IF to_regclass('public.empresa') IS NULL
       OR to_regclass('public.usuario') IS NULL
       OR to_regclass('public.cliente') IS NULL
       OR to_regclass('public.carrito') IS NULL
       OR to_regclass('public.detalle_carrito') IS NULL
       OR to_regclass('public.reserva') IS NULL
       OR to_regclass('public.detalle_reserva') IS NULL
       OR to_regclass('public.inventario') IS NULL
       OR to_regclass('public.movimiento_inventario') IS NULL
       OR to_regclass('public.venta') IS NULL
       OR to_regclass('public.pago') IS NULL THEN
        RAISE EXCEPTION 'Faltan tablas comerciales en el sandbox';
    END IF;

    -- Extensiones pueden crear tablas propias. Verificamos que TODAS las
    -- tablas normales de public esten vacias; nada historico fue importado.
    FOR v_rel IN
        SELECT c.oid, n.nspname, c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
        ORDER BY c.relname
    LOOP
        EXECUTE format(
          'SELECT count(*) FROM %I.%I', v_rel.nspname, v_rel.relname
        ) INTO v_conteo;
        IF v_conteo <> 0 THEN
            RAISE EXCEPTION 'El sandbox NO esta vacio: %.% tiene % filas',
                v_rel.nspname, v_rel.relname, v_conteo;
        END IF;
        v_tablas := v_tablas + 1;
    END LOOP;
    IF v_tablas < 40 THEN
        RAISE EXCEPTION 'Faltan tablas del esquema FashionStore: %', v_tablas;
    END IF;

    SELECT count(*) INTO v_fk
    FROM pg_constraint co
    JOIN pg_class c ON c.oid=co.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public'
      AND co.contype='f' AND co.convalidated
      AND co.conname LIKE 'fk_tenant_%';
    IF v_fk<>48 THEN
        RAISE EXCEPTION 'No se restauraron las 48 FK tenant (% encontradas)', v_fk;
    END IF;

    SELECT count(*) INTO v_rls
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relrowsecurity
      AND c.relname IN (
        'carrito','detalle_carrito','reserva','detalle_reserva',
        'inventario','movimiento_inventario'
      );
    IF v_rls<>6 THEN
        RAISE EXCEPTION 'Faltan flags RLS en tablas comerciales (%/6)',v_rls;
    END IF;

    -- Los SP inertes del Paso 5G NO pueden haber sido instalados.
    SELECT count(*) INTO v_sp
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind='p'
      AND p.proname IN (
        'sp_crear_reserva_tenant',
        'sp_cancelar_reserva_tenant',
        'sp_finalizar_reserva_sin_compra_tenant'
      );
    IF v_sp<>0 THEN
        RAISE EXCEPTION 'Hay procedimientos tenant instalados sin autorizar (%).',v_sp;
    END IF;

    RAISE NOTICE 'SANDBOX PG OK: % tablas vacias, 48 FK tenant, 6 flags RLS, 0 SP tenant.',v_tablas;
    RAISE NOTICE 'No hay datos historicos. RLS NO ha sido probado con rol limitado.';
END
$sandbox$;

-- Resultado no sensible: solo propiedades del rol de verificacion.
SELECT current_database() AS base_verificada,
       current_user AS usuario_actual,
       rol.rolsuper, rol.rolbypassrls
FROM pg_roles rol WHERE rol.rolname=current_user;

ROLLBACK;
