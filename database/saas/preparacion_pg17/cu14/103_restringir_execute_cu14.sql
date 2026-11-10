\set ON_ERROR_STOP on

-- Correccion posterior a la instalacion CU14.
-- No modifica datos, owners, RLS, policies ni el procedimiento privado webhook.
BEGIN;

CREATE TEMP TABLE cu14_acl_membresias_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid='fashionstore_sp_owner'::regrole;

CREATE TEMP TABLE cu14_acl_set_temporal (
  rol name PRIMARY KEY,
  existente boolean NOT NULL,
  admin_previo boolean,
  inherit_previo boolean,
  set_previo boolean
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu14_acl_preparar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v_admin boolean; v_inherit boolean; v_set boolean;
BEGIN
  SELECT m.admin_option,m.inherit_option,m.set_option
    INTO v_admin,v_inherit,v_set
  FROM pg_auth_members m
  WHERE m.member=CURRENT_USER::regrole
    AND m.roleid=p_rol::regrole
    AND m.grantor=CURRENT_USER::regrole;

  IF FOUND THEN
    IF v_set IS NOT TRUE THEN
      EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE',
        p_rol,
        CASE WHEN v_admin THEN 'ADMIN OPTION,' ELSE '' END,
        CASE WHEN v_inherit THEN 'TRUE' ELSE 'FALSE' END);
    END IF;
    INSERT INTO cu14_acl_set_temporal VALUES(
      p_rol,true,v_admin,v_inherit,v_set);
  ELSE
    EXECUTE format(
      'GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE', p_rol);
    INSERT INTO cu14_acl_set_temporal VALUES(
      p_rol,false,NULL,NULL,NULL);
  END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu14_acl_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v record;
BEGIN
  SELECT * INTO v FROM cu14_acl_set_temporal WHERE rol=p_rol;
  IF NOT FOUND THEN RETURN; END IF;
  IF v.existente THEN
    EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s',
      p_rol,
      CASE WHEN v.admin_previo THEN 'ADMIN OPTION,' ELSE '' END,
      CASE WHEN v.inherit_previo THEN 'TRUE' ELSE 'FALSE' END,
      CASE WHEN v.set_previo THEN 'TRUE' ELSE 'FALSE' END);
  ELSE
    EXECUTE format(
      'REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER', p_rol);
  END IF;
END $fn$;

DO $prepare_set$
BEGIN
  IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
    PERFORM pg_temp.cu14_acl_preparar_set('fashionstore_sp_owner');
  END IF;
END $prepare_set$;

SET ROLE fashionstore_sp_owner;

REVOKE EXECUTE ON PROCEDURE
  public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)
FROM anon, authenticated, service_role;

REVOKE EXECUTE ON PROCEDURE
  public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)
FROM anon, authenticated, service_role;

RESET ROLE;

DO $restore_set$
BEGIN
  PERFORM pg_temp.cu14_acl_restaurar_set('fashionstore_sp_owner');
END $restore_set$;

DO $verify_acl$
DECLARE
  rol text;
  firma text;
BEGIN
  FOREACH rol IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
    FOREACH firma IN ARRAY ARRAY[
      'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)',
      'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)'
    ] LOOP
      IF has_function_privilege(rol, firma, 'EXECUTE') THEN
        RAISE EXCEPTION 'CU14_EXECUTE_HARDENING=FAIL rol=% firma=%', rol, firma;
      END IF;
    END LOOP;
  END LOOP;

  FOREACH rol IN ARRAY ARRAY['fashionstore_api','fashionstore_sp_owner'] LOOP
    FOREACH firma IN ARRAY ARRAY[
      'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)',
      'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)'
    ] LOOP
      IF NOT has_function_privilege(rol, firma, 'EXECUTE') THEN
        RAISE EXCEPTION 'CU14_EXECUTE_HARDENING=FAIL falta rol=% firma=%', rol, firma;
      END IF;
    END LOOP;
  END LOOP;

  IF NOT has_function_privilege(
    'fashionstore_webhook',
    'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
    'EXECUTE'
  ) OR has_function_privilege(
    'fashionstore_api',
    'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
    'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'CU14_EXECUTE_HARDENING=FAIL ACL webhook alterada';
  END IF;

  IF EXISTS (
    SELECT 1 FROM cu14_acl_membresias_previas p
    WHERE NOT EXISTS (
      SELECT 1 FROM pg_auth_members m
      WHERE m.roleid=p.roleid AND m.member=p.member AND m.grantor=p.grantor
        AND m.admin_option=p.admin_option
        AND m.inherit_option=p.inherit_option
        AND m.set_option=p.set_option
    )
  ) OR EXISTS (
    SELECT 1 FROM pg_auth_members m
    WHERE m.member=CURRENT_USER::regrole
      AND m.roleid='fashionstore_sp_owner'::regrole
      AND NOT EXISTS (
        SELECT 1 FROM cu14_acl_membresias_previas p
        WHERE p.roleid=m.roleid AND p.member=m.member AND p.grantor=m.grantor
      )
  ) THEN
    RAISE EXCEPTION 'CU14_EXECUTE_HARDENING=FAIL membresia temporal residual';
  END IF;
END $verify_acl$;

COMMIT;

SELECT 'CU14_EXECUTE_HARDENING=PASS' AS resultado;
