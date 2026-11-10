-- CU12/CU13 SaaS - verificacion de solo lectura. NO EJECUTAR EN SUPABASE
-- sin autorizacion. Comprueba firmas, owners, ACL, RLS, membresias y CU11.
\echo 'CU12/CU13 VERIFICACION: solo lectura'
BEGIN;
SET LOCAL statement_timeout='60s';

DO $verificar$
DECLARE
    firma text;
    v_rol record;
BEGIN
    -- 1. Procedimientos CU12/CU13: firma, owner NOLOGIN y SECURITY DEFINER.
    FOREACH firma IN ARRAY ARRAY[
        'public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)',
        'public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)',
        'public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)'
    ] LOOP
        IF to_regprocedure(firma) IS NULL THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: falta %',firma;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_proc p
            JOIN pg_roles r ON r.oid=p.proowner
            WHERE p.oid=to_regprocedure(firma)
              AND p.prosecdef IS TRUE
              AND r.rolname='fashionstore_sp_owner'
              AND r.rolcanlogin IS FALSE
              AND r.rolsuper IS FALSE
              AND r.rolbypassrls IS FALSE
              AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
              AND p.proacl IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM aclexplode(p.proacl) acl
                WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
              )
        ) THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: SP inseguro %',firma;
        END IF;
        IF NOT has_function_privilege('fashionstore_api',firma,'EXECUTE') THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: API sin EXECUTE %',firma;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_proc p
            WHERE p.oid=to_regprocedure(firma)
              AND obj_description(p.oid,'pg_proc')
                  LIKE 'fashionstore.cu12_cu13.090.v2%'
        ) THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: marcador 090 v2 en %',firma;
        END IF;
    END LOOP;

    -- 2. Funcion auxiliar de sucursal.
    IF to_regprocedure('fashionstore_private.actor_sucursal_id()') IS NULL THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: falta actor_sucursal_id';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
        WHERE p.oid=to_regprocedure('fashionstore_private.actor_sucursal_id()')
          AND r.rolname='fashionstore_context_owner'
          AND p.prosecdef IS TRUE
          AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: actor_sucursal_id inseguro';
    END IF;
    IF NOT has_function_privilege(
        'fashionstore_api',
        'fashionstore_private.actor_sucursal_id()','EXECUTE') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: API sin actor_sucursal_id';
    END IF;

    -- 3. CU11 sigue instalado y con sus owners/ACL.
    FOREACH firma IN ARRAY ARRAY[
        'public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)',
        'public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)',
        'public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)'
    ] LOOP
        IF to_regprocedure(firma) IS NULL THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: CU11 perdido %',firma;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
            WHERE p.oid=to_regprocedure(firma)
              AND p.prosecdef IS TRUE
              AND r.rolname='fashionstore_sp_owner'
              AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
        ) THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: CU11 alterado %',firma;
        END IF;
        IF NOT has_function_privilege('fashionstore_api',firma,'EXECUTE') THEN
            RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: API sin CU11 %',firma;
        END IF;
    END LOOP;
    IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NULL THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: verificador CU11 ausente';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
        WHERE p.oid=to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)')
          AND p.prosecdef IS TRUE
          AND r.rolname='fashionstore_context_owner'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: verificador CU11 alterado';
    END IF;

    -- 4. Policies nuevas y preexistentes.
    IF EXISTS (
        SELECT 1 FROM (VALUES
          ('cliente','p_api_select_staff_cliente'),
          ('reserva','p_api_select_staff_reserva'),
          ('detalle_reserva','p_api_select_staff_detalle_reserva'),
          ('venta','p_api_select_staff_venta'),
          ('detalle_venta','p_api_select_staff_detalle_venta'),
          ('pago','p_api_select_staff_pago'),
          ('detalle_venta','p_sp_tenant_detalle_venta'),
          ('pago','p_sp_tenant_pago'),
          ('reserva','p_sp_tenant_reserva'),
          ('detalle_reserva','p_sp_tenant_detalle_reserva'),
          ('inventario','p_sp_tenant_inventario'),
          ('movimiento_inventario','p_sp_tenant_movimiento_inventario'),
          ('venta','p_sp_tenant_venta')
        ) v(tabla,politica)
        LEFT JOIN pg_policies p ON p.schemaname='public'
          AND p.tablename=v.tabla AND p.policyname=v.politica
        WHERE p.policyname IS NULL
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: policies ausentes';
    END IF;

    -- 5. RLS y FORCE RLS en el nucleo comercial.
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public'
          AND c.relname IN
            ('reserva','detalle_reserva','inventario','venta','detalle_venta','pago')
          AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: RLS/FORCE incompleto';
    END IF;

    -- 6. ACL del owner NOLOGIN y de la API.
    IF NOT has_table_privilege('fashionstore_sp_owner','public.venta','INSERT')
       OR NOT has_table_privilege('fashionstore_sp_owner','public.venta','UPDATE')
       OR NOT has_table_privilege('fashionstore_sp_owner','public.detalle_venta','INSERT')
       OR NOT has_table_privilege('fashionstore_sp_owner','public.pago','INSERT')
       OR NOT has_table_privilege('fashionstore_sp_owner','public.inventario','UPDATE')
       OR NOT has_table_privilege('fashionstore_sp_owner','public.reserva','UPDATE') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: ACL DML incompleta';
    END IF;
    IF NOT has_table_privilege('fashionstore_api','public.venta','SELECT')
       OR NOT has_table_privilege('fashionstore_api','public.detalle_venta','SELECT')
       OR NOT has_table_privilege('fashionstore_api','public.pago','SELECT')
       OR NOT has_table_privilege('fashionstore_api','public.reserva','SELECT')
       OR NOT has_table_privilege('fashionstore_api','public.detalle_reserva','SELECT') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: API sin SELECT comercial';
    END IF;
    IF has_table_privilege('fashionstore_api','public.venta','INSERT')
       OR has_table_privilege('fashionstore_api','public.venta','UPDATE')
       OR has_table_privilege('fashionstore_api','public.detalle_venta','INSERT')
       OR has_table_privilege('fashionstore_api','public.pago','INSERT') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: API con DML comercial';
    END IF;

    -- 7. Sin EXECUTE PUBLIC en los SP nuevos.
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN (
            'sp_confirmar_reserva_tenant','sp_crear_venta_presencial_tenant',
            'sp_registrar_pago_presencial_tenant')
          AND p.proacl IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM aclexplode(p.proacl) acl
            WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
          )
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: EXECUTE PUBLIC presente';
    END IF;

    -- 8. Sin CREATE residual en los esquemas de los owners.
    IF has_schema_privilege(
        'fashionstore_context_owner','fashionstore_private','CREATE') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: CREATE residual context_owner';
    END IF;
    IF has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: CREATE residual sp_owner';
    END IF;

    -- 9. Membresias administrativas preexistentes intactas y sin residuos.
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
        FOR v_rol IN
            SELECT oid,rolname FROM pg_roles
            WHERE rolname IN ('fashionstore_context_owner','fashionstore_sp_owner')
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_auth_members m
                WHERE m.member=CURRENT_USER::regrole
                  AND m.roleid=v_rol.oid
                  AND m.admin_option IS TRUE
                  AND m.grantor<>CURRENT_USER::regrole
            ) THEN
                RAISE EXCEPTION
                  'CU12_CU13_POSTFLIGHT=FAIL: membresia admin ausente %',
                  v_rol.rolname;
            END IF;
            -- Puede existir una concesion preexistente de CURRENT_USER con
            -- SET FALSE; lo prohibido es dejar un SET TRUE temporal.
            IF EXISTS (
                SELECT 1 FROM pg_auth_members m
                WHERE m.member=CURRENT_USER::regrole
                  AND m.roleid=v_rol.oid
                  AND m.set_option IS TRUE
            ) THEN
                RAISE EXCEPTION
                  'CU12_CU13_POSTFLIGHT=FAIL: SET temporal residual %',
                  v_rol.rolname;
            END IF;
        END LOOP;
    END IF;

    -- 10. Dependencias de lectura del verificador de sucursal.
    IF NOT has_schema_privilege(
        'fashionstore_context_owner','fashionstore_private','USAGE')
       OR NOT has_table_privilege(
        'fashionstore_context_owner','public.empleado','SELECT')
       OR NOT has_table_privilege(
        'fashionstore_context_owner','public.sucursal','SELECT') THEN
        RAISE EXCEPTION 'CU12_CU13_POSTFLIGHT=FAIL: contexto de sucursal incompleto';
    END IF;

    RAISE NOTICE 'CU12_CU13_POSTFLIGHT=OK';
END $verificar$;

COMMIT;
