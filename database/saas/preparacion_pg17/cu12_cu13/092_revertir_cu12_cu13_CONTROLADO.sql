-- CU12/CU13 SaaS - reversion controlada de objetos 090.
-- NO EJECUTAR EN SUPABASE sin autorizacion. No toca tablas, datos,
-- secuencias, triggers ni el paquete CU11.
\echo 'CU12/CU13 REVERSION CONTROLADA: no toca datos'
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';

DO $pre$
BEGIN
    IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
        RAISE EXCEPTION 'Instalador sin CREATEROLE';
    END IF;
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
       AND NOT (
         SELECT count(DISTINCT roleid)=2
         FROM pg_auth_members
         WHERE member=CURRENT_USER::regrole
           AND roleid IN ('fashionstore_context_owner'::regrole,
                          'fashionstore_sp_owner'::regrole)
           AND admin_option
       ) THEN
        RAISE EXCEPTION 'Instalador sin ADMIN OPTION sobre owners CU12/CU13';
    END IF;
END $pre$;

-- Retirar policies nuevas (idempotente).
DROP POLICY IF EXISTS p_api_select_staff_cliente ON public.cliente;
DROP POLICY IF EXISTS p_api_select_staff_reserva ON public.reserva;
DROP POLICY IF EXISTS p_api_select_staff_detalle_reserva ON public.detalle_reserva;
DROP POLICY IF EXISTS p_api_select_staff_venta ON public.venta;
DROP POLICY IF EXISTS p_api_select_staff_detalle_venta ON public.detalle_venta;
DROP POLICY IF EXISTS p_api_select_staff_pago ON public.pago;
DROP POLICY IF EXISTS p_sp_tenant_detalle_venta ON public.detalle_venta;
DROP POLICY IF EXISTS p_sp_tenant_pago ON public.pago;

-- Retirar ACL minima del owner NOLOGIN (no revierte SELECT preexistente
-- de venta; 090 no lo concedio).
REVOKE INSERT,UPDATE ON public.venta FROM fashionstore_sp_owner;
REVOKE SELECT,INSERT ON public.detalle_venta FROM fashionstore_sp_owner;
REVOKE SELECT,INSERT ON public.pago FROM fashionstore_sp_owner;
REVOKE USAGE,SELECT ON SEQUENCE
    public.venta_id_seq,public.detalle_venta_id_seq,public.pago_id_seq
    FROM fashionstore_sp_owner;

-- Procedimientos: se retiran como su owner NOLOGIN, sin privilegios extra.
-- Las membresias preexistentes (ADMIN OPTION via supabase_admin, SET FALSE)
-- se fotografian y se verifican; solo se revoca la concesion temporal.
CREATE TEMP TABLE cu12_cu13_revert_membresias_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid IN ('fashionstore_context_owner'::regrole,
                 'fashionstore_sp_owner'::regrole);
CREATE TEMP TABLE cu12_cu13_revert_set_temporal (
    rol name PRIMARY KEY,
    existente boolean NOT NULL,
    admin_previo boolean,
    inherit_previo boolean,
    set_previo boolean
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu12_cu13_revert_preparar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v_admin boolean;
    v_inherit boolean;
    v_set boolean;
BEGIN
    SELECT m.admin_option,m.inherit_option,m.set_option
    INTO v_admin,v_inherit,v_set
    FROM pg_auth_members m
    WHERE m.member=CURRENT_USER::regrole
      AND m.roleid=p_rol::regrole
      AND m.grantor=CURRENT_USER::regrole;

    IF FOUND THEN
        IF v_set IS NOT TRUE THEN
            EXECUTE format(
              'GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE',
              p_rol,
              CASE WHEN v_admin THEN 'ADMIN OPTION,' ELSE '' END,
              CASE WHEN v_inherit THEN 'TRUE' ELSE 'FALSE' END
            );
        END IF;
        INSERT INTO pg_temp.cu12_cu13_revert_set_temporal(
            rol,existente,admin_previo,inherit_previo,set_previo
        ) VALUES (p_rol,true,v_admin,v_inherit,v_set)
        ON CONFLICT (rol) DO UPDATE SET
            existente=EXCLUDED.existente,
            admin_previo=EXCLUDED.admin_previo,
            inherit_previo=EXCLUDED.inherit_previo,
            set_previo=EXCLUDED.set_previo;
    ELSE
        EXECUTE format(
          'GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE', p_rol
        );
        INSERT INTO pg_temp.cu12_cu13_revert_set_temporal(
            rol,existente,admin_previo,inherit_previo,set_previo
        ) VALUES (p_rol,false,NULL,NULL,NULL)
        ON CONFLICT (rol) DO UPDATE SET
            existente=EXCLUDED.existente,
            admin_previo=EXCLUDED.admin_previo,
            inherit_previo=EXCLUDED.inherit_previo,
            set_previo=EXCLUDED.set_previo;
    END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu12_cu13_revert_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v record;
BEGIN
    SELECT * INTO v FROM pg_temp.cu12_cu13_revert_set_temporal WHERE rol=p_rol;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF v.existente THEN
        EXECUTE format(
          'GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s',
          p_rol,
          CASE WHEN v.admin_previo THEN 'ADMIN OPTION,' ELSE '' END,
          CASE WHEN v.inherit_previo THEN 'TRUE' ELSE 'FALSE' END,
          CASE WHEN v.set_previo THEN 'TRUE' ELSE 'FALSE' END
        );
    ELSE
        EXECUTE format(
          'REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER', p_rol
        );
    END IF;
END $fn$;

DO $temp_set_sp_owner$
BEGIN
    IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
        PERFORM pg_temp.cu12_cu13_revert_preparar_set('fashionstore_sp_owner');
    END IF;
END $temp_set_sp_owner$;
SET ROLE fashionstore_sp_owner;
DROP PROCEDURE IF EXISTS public.sp_confirmar_reserva_tenant(bigint,bigint,bigint);
DROP PROCEDURE IF EXISTS public.sp_crear_venta_presencial_tenant(
    bigint,bigint,bigint,bigint,jsonb,bigint);
DROP PROCEDURE IF EXISTS public.sp_registrar_pago_presencial_tenant(
    bigint,bigint,bigint,text,bigint);
RESET ROLE;
DO $restore_set_sp_owner$
BEGIN
    PERFORM pg_temp.cu12_cu13_revert_restaurar_set('fashionstore_sp_owner');
END $restore_set_sp_owner$;

DO $temp_set_context_owner$
BEGIN
    IF NOT pg_has_role(CURRENT_USER,'fashionstore_context_owner','SET') THEN
        PERFORM pg_temp.cu12_cu13_revert_preparar_set('fashionstore_context_owner');
    END IF;
END $temp_set_context_owner$;
SET ROLE fashionstore_context_owner;
DROP FUNCTION IF EXISTS fashionstore_private.actor_sucursal_id();
RESET ROLE;
DO $restore_set_context_owner$
BEGIN
    PERFORM pg_temp.cu12_cu13_revert_restaurar_set('fashionstore_context_owner');
END $restore_set_context_owner$;

DO $post$
BEGIN
    IF to_regprocedure('public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)') IS NOT NULL
       OR to_regprocedure('public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)') IS NOT NULL
       OR to_regprocedure('public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)') IS NOT NULL
       OR to_regprocedure('fashionstore_private.actor_sucursal_id()') IS NOT NULL THEN
        RAISE EXCEPTION 'CU12_CU13_REVERT=FAIL: quedaron objetos 090';
    END IF;

    IF EXISTS (
        SELECT 1 FROM cu12_cu13_revert_membresias_previas p
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_auth_members m
            WHERE m.roleid=p.roleid AND m.member=p.member
              AND m.grantor=p.grantor
              AND m.admin_option=p.admin_option
              AND m.inherit_option=p.inherit_option
              AND m.set_option=p.set_option
        )
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_REVERT=FAIL: membresia preexistente alterada';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_auth_members m
        WHERE m.member=CURRENT_USER::regrole
          AND m.roleid IN ('fashionstore_context_owner'::regrole,
                           'fashionstore_sp_owner'::regrole)
          AND NOT EXISTS (
              SELECT 1 FROM cu12_cu13_revert_membresias_previas p
              WHERE p.roleid=m.roleid AND p.member=m.member
                AND p.grantor=m.grantor
          )
    ) THEN
        RAISE EXCEPTION 'CU12_CU13_REVERT=FAIL: membresia temporal residual';
    END IF;
    RAISE NOTICE 'CU12_CU13_REVERT=OK';
END $post$;

COMMIT;
