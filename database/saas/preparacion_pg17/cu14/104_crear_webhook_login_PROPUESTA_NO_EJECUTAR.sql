-- CU14: aprovisionamiento mínimo del login del webhook Stripe.
-- PROPUESTA LOCAL: NO EJECUTAR SIN AUTORIZACION ADMINISTRATIVA.
-- No contiene contraseña. Después de este script, establecerla en una sesión
-- psql interactiva con: \password fashionstore_webhook_login
\echo 'NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA'
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';

DO $pre$
DECLARE
  v_rol record;
BEGIN
  IF NOT (SELECT rolsuper OR rolcreaterole FROM pg_roles
          WHERE rolname=current_user) THEN
    RAISE EXCEPTION 'Se requiere SUPERUSER o CREATEROLE';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles
             WHERE rolname='fashionstore_webhook_login') THEN
    RAISE EXCEPTION 'fashionstore_webhook_login ya existe; detener y auditar';
  END IF;

  SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole
    INTO v_rol
  FROM pg_roles WHERE rolname='fashionstore_webhook';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Falta el rol fashionstore_webhook';
  END IF;
  IF v_rol.rolcanlogin OR v_rol.rolsuper OR v_rol.rolbypassrls
     OR v_rol.rolcreatedb OR v_rol.rolcreaterole THEN
    RAISE EXCEPTION 'fashionstore_webhook tiene atributos inseguros';
  END IF;

  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
     AND NOT EXISTS (
       SELECT 1 FROM pg_auth_members
       WHERE member=current_user::regrole
         AND roleid='fashionstore_webhook'::regrole
         AND admin_option) THEN
    RAISE EXCEPTION 'El instalador no tiene ADMIN OPTION sobre fashionstore_webhook';
  END IF;
END $pre$;

-- Sin contraseña: se establece después con \password, que no la escribe en
-- el historial, el SQL ni los logs.
CREATE ROLE fashionstore_webhook_login
  LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  PASSWORD NULL;

-- Única membresía autorizada. No se concede ADMIN.
GRANT fashionstore_webhook TO fashionstore_webhook_login
  WITH INHERIT TRUE, SET FALSE;

DO $verify$
DECLARE
  v record;
  v_priv text;
BEGIN
  SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit
    INTO v
  FROM pg_roles WHERE rolname='fashionstore_webhook_login';
  IF NOT FOUND OR NOT v.rolcanlogin OR v.rolsuper OR v.rolbypassrls
     OR v.rolcreatedb OR v.rolcreaterole OR NOT v.rolinherit THEN
    RAISE EXCEPTION 'Atributos del login incorrectos';
  END IF;

  -- Exactamente una membresía: fashionstore_webhook, sin ADMIN,
  -- INHERIT TRUE y SET FALSE. Ninguna otra membresía.
  IF (SELECT count(*) FROM pg_auth_members
      WHERE member='fashionstore_webhook_login'::regrole) <> 1
     OR NOT EXISTS (
       SELECT 1 FROM pg_auth_members
       WHERE member='fashionstore_webhook_login'::regrole
         AND roleid='fashionstore_webhook'::regrole
         AND admin_option IS FALSE
         AND inherit_option IS TRUE
         AND set_option IS FALSE) THEN
    RAISE EXCEPTION 'Membresia del login incorrecta';
  END IF;

  IF NOT pg_has_role('fashionstore_webhook_login',
                     'fashionstore_webhook','USAGE')
     OR pg_has_role('fashionstore_webhook_login',
                    'fashionstore_webhook','SET') THEN
    RAISE EXCEPTION 'Herencia/SET de fashionstore_webhook incorrectos';
  END IF;

  IF pg_has_role('fashionstore_webhook_login','fashionstore_api','USAGE')
     OR pg_has_role('fashionstore_webhook_login','fashionstore_sp_owner','USAGE')
     OR pg_has_role('fashionstore_webhook_login',
                    'fashionstore_webhook_owner','USAGE') THEN
    RAISE EXCEPTION 'El login hereda un rol no autorizado';
  END IF;

  IF NOT has_schema_privilege('fashionstore_webhook_login',
                              'fashionstore_private','USAGE') THEN
    RAISE EXCEPTION 'Falta USAGE en fashionstore_private';
  END IF;
  IF NOT has_function_privilege(
       'fashionstore_webhook_login',
       'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
       'EXECUTE') THEN
    RAISE EXCEPTION 'Falta EXECUTE del procedimiento privado';
  END IF;
  IF has_function_privilege(
       'fashionstore_webhook_login',
       'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)',
       'EXECUTE')
     OR has_function_privilege(
       'fashionstore_webhook_login',
       'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)',
       'EXECUTE') THEN
    RAISE EXCEPTION 'El login puede ejecutar procedimientos publicos CU14';
  END IF;

  IF has_schema_privilege('fashionstore_webhook_login','public','CREATE')
     OR has_schema_privilege('fashionstore_webhook_login',
                             'fashionstore_private','CREATE')
     OR EXISTS (
       SELECT 1 FROM pg_namespace n
       WHERE has_schema_privilege('fashionstore_webhook_login',n.oid,'CREATE')
     ) THEN
    RAISE EXCEPTION 'El login conserva CREATE en algun esquema';
  END IF;

  FOREACH v_priv IN ARRAY
    ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']
  LOOP
    IF has_table_privilege('fashionstore_webhook_login','public.venta',v_priv)
       OR has_table_privilege('fashionstore_webhook_login','public.pago',v_priv)
       OR has_table_privilege('fashionstore_webhook_login',
                              'public.inventario',v_priv)
       OR has_table_privilege('fashionstore_webhook_login',
                              'public.movimiento_inventario',v_priv) THEN
      RAISE EXCEPTION 'El login tiene DML directo: %', v_priv;
    END IF;
  END LOOP;

  IF has_sequence_privilege('fashionstore_webhook_login',
                            'public.movimiento_inventario_id_seq','USAGE')
     OR has_sequence_privilege('fashionstore_webhook_login',
                               'public.movimiento_inventario_id_seq','SELECT') THEN
    RAISE EXCEPTION 'El login tiene privilegios sobre la secuencia';
  END IF;
END $verify$;

COMMIT;

SELECT 'CU14_WEBHOOK_LOGIN=PASS PENDIENTE_PASSWORD' AS resultado;
