-- CU14: verificación de solo lectura del login del webhook.
-- Ejecutar autenticado como fashionstore_webhook_login, por ejemplo:
--   psql "$DATABASE_WEBHOOK_URL" -X -v ON_ERROR_STOP=1 -f 105_verificar_webhook_login.sql
-- No escribe datos ni modifica privilegios.
\set ON_ERROR_STOP on

DO $verify$
DECLARE
  v record;
  v_priv text;
BEGIN
  IF session_user <> 'fashionstore_webhook_login'
     OR current_user <> 'fashionstore_webhook_login' THEN
    RAISE EXCEPTION 'Ejecutar como fashionstore_webhook_login (actual=%)',
      current_user;
  END IF;

  SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit
    INTO v
  FROM pg_roles WHERE rolname=current_user;
  IF NOT FOUND OR NOT v.rolcanlogin OR v.rolsuper OR v.rolbypassrls
     OR v.rolcreatedb OR v.rolcreaterole OR NOT v.rolinherit THEN
    RAISE EXCEPTION 'Atributos del login incorrectos';
  END IF;

  IF (SELECT count(*) FROM pg_auth_members
      WHERE member=current_user::regrole) <> 1
     OR NOT EXISTS (
       SELECT 1 FROM pg_auth_members
       WHERE member=current_user::regrole
         AND roleid='fashionstore_webhook'::regrole
         AND admin_option IS FALSE
         AND inherit_option IS TRUE
         AND set_option IS FALSE) THEN
    RAISE EXCEPTION 'Membresia del login incorrecta';
  END IF;

  IF NOT pg_has_role(current_user,'fashionstore_webhook','USAGE')
     OR pg_has_role(current_user,'fashionstore_webhook','SET') THEN
    RAISE EXCEPTION 'Herencia/SET de fashionstore_webhook incorrectos';
  END IF;

  IF pg_has_role(current_user,'fashionstore_api','USAGE')
     OR pg_has_role(current_user,'fashionstore_sp_owner','USAGE')
     OR pg_has_role(current_user,'fashionstore_webhook_owner','USAGE') THEN
    RAISE EXCEPTION 'El login hereda un rol no autorizado';
  END IF;

  -- Contrato exacto que evalúa PagoStripeTenantService.webhook antes de
  -- ejecutar el procedimiento privado.
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles r
    WHERE r.rolname=session_user
      AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
      AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND r.rolinherit
      AND pg_has_role(r.rolname,'fashionstore_webhook','USAGE')
      AND has_function_privilege(
        r.rolname,
        'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
        'EXECUTE')
  ) THEN
    RAISE EXCEPTION 'El principal no cumple el contrato del pool webhook';
  END IF;

  IF NOT has_schema_privilege(current_user,
                              'fashionstore_private','USAGE') THEN
    RAISE EXCEPTION 'Falta USAGE en fashionstore_private';
  END IF;
  IF NOT has_function_privilege(
       current_user,
       'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
       'EXECUTE') THEN
    RAISE EXCEPTION 'Falta EXECUTE del procedimiento privado';
  END IF;
  IF has_function_privilege(
       current_user,
       'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)',
       'EXECUTE')
     OR has_function_privilege(
       current_user,
       'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)',
       'EXECUTE') THEN
    RAISE EXCEPTION 'El login puede ejecutar procedimientos publicos CU14';
  END IF;

  IF has_schema_privilege(current_user,'public','CREATE')
     OR has_schema_privilege(current_user,'fashionstore_private','CREATE')
     OR EXISTS (
       SELECT 1 FROM pg_namespace n
       WHERE has_schema_privilege(current_user,n.oid,'CREATE')
     ) THEN
    RAISE EXCEPTION 'El login conserva CREATE en algun esquema';
  END IF;

  FOREACH v_priv IN ARRAY
    ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']
  LOOP
    IF has_table_privilege(current_user,'public.venta',v_priv)
       OR has_table_privilege(current_user,'public.pago',v_priv)
       OR has_table_privilege(current_user,'public.inventario',v_priv)
       OR has_table_privilege(current_user,'public.movimiento_inventario',v_priv) THEN
      RAISE EXCEPTION 'El login tiene DML directo: %', v_priv;
    END IF;
  END LOOP;

  IF has_sequence_privilege(current_user,
                            'public.movimiento_inventario_id_seq','USAGE')
     OR has_sequence_privilege(current_user,
                               'public.movimiento_inventario_id_seq','SELECT') THEN
    RAISE EXCEPTION 'El login tiene privilegios sobre la secuencia';
  END IF;
END $verify$;

SELECT 'CU14_WEBHOOK_LOGIN_CONNECTION=PASS' AS resultado;
