\set ON_ERROR_STOP on
DO $verify$
DECLARE fallos text[]:=ARRAY[]::text[]; firma text; rol text; r record;
BEGIN
 -- 1. Procedimientos CU14: firmas, owners y SECURITY DEFINER/search_path.
 FOREACH firma IN ARRAY ARRAY[
  'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)',
  'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)',
  'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)'
 ] LOOP
  IF to_regprocedure(firma) IS NULL THEN
   fallos:=fallos||('falta:'||firma); CONTINUE; END IF;
  IF NOT EXISTS (
   SELECT 1 FROM pg_proc p JOIN pg_roles o ON o.oid=p.proowner
   WHERE p.oid=to_regprocedure(firma)
     AND p.prosecdef IS TRUE
     AND o.rolname=CASE WHEN p.proname='sp_evento_stripe_tenant'
        THEN 'fashionstore_webhook_owner' ELSE 'fashionstore_sp_owner' END
     AND o.rolcanlogin IS FALSE AND o.rolsuper IS FALSE
     AND o.rolbypassrls IS FALSE AND o.rolcreaterole IS FALSE
     AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
     AND p.proacl IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
      WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
  ) THEN fallos:=fallos||('sp_inseguro:'||firma); END IF;
  IF NOT EXISTS (
   SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(firma)
     AND obj_description(p.oid,'pg_proc') LIKE 'fashionstore-cu14-v2:%'
  ) THEN fallos:=fallos||('marcador:'||firma); END IF;
 END LOOP;

 -- 2. Roles webhook y API/owners sin atributos peligrosos.
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_webhook','fashionstore_webhook_owner','fashionstore_sp_owner','fashionstore_api')
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb)) THEN
  fallos:=fallos||'roles'; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_webhook' AND rolinherit IS FALSE)
 OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_webhook_owner' AND rolinherit IS FALSE) THEN
  fallos:=fallos||'roles_inherit'; END IF;

 -- 3. EXECUTE minimo. PUBLIC ya se verifica con aclexplode en el paso 1.
 IF NOT has_function_privilege('fashionstore_api',
   'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)','EXECUTE')
 OR NOT has_function_privilege('fashionstore_api',
   'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)','EXECUTE') THEN
  fallos:=fallos||'api_execute'; END IF;
 IF has_function_privilege('fashionstore_api',
   'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)','EXECUTE') THEN
  fallos:=fallos||'api_private_execute'; END IF;
 IF NOT has_function_privilege('fashionstore_webhook',
   'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)','EXECUTE') THEN
  fallos:=fallos||'webhook_execute'; END IF;
 IF has_function_privilege('fashionstore_webhook',
   'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)','EXECUTE')
 OR has_function_privilege('fashionstore_webhook',
   'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)','EXECUTE') THEN
  fallos:=fallos||'webhook_public_execute'; END IF;
 FOREACH rol IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF has_function_privilege(rol,
    'public.sp_crear_venta_digital_tenant(bigint,bigint,bigint,bigint,text,bigint)','EXECUTE')
  OR has_function_privilege(rol,
    'public.sp_registrar_intencion_stripe_tenant(bigint,bigint,bigint,text,text,bigint)','EXECUTE') THEN
   fallos:=fallos||('execute_excesivo:'||rol); END IF;
 END LOOP;

 -- 4. CREATE residual.
 IF has_schema_privilege('fashionstore_sp_owner','public','CREATE')
 OR has_schema_privilege('fashionstore_webhook_owner','fashionstore_private','CREATE')
 OR has_schema_privilege('fashionstore_webhook','public','CREATE')
 OR has_schema_privilege('fashionstore_webhook','fashionstore_private','CREATE') THEN
  fallos:=fallos||'create_residual'; END IF;

 -- 5. Policies CU14: tabla, comando y rol exactos.
 IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='venta' AND policyname='p_api_select_client_venta'
   AND cmd='SELECT' AND roles @> ARRAY['fashionstore_api']::name[]
   AND coalesce(qual,'') LIKE '%actor_posee_cliente%'
   AND coalesce(qual,'') LIKE '%actor_empresa_id%')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='detalle_venta' AND policyname='p_api_select_client_detalle_venta'
   AND cmd='SELECT' AND roles @> ARRAY['fashionstore_api']::name[]
   AND coalesce(qual,'') LIKE '%actor_posee_cliente%'
   AND coalesce(qual,'') LIKE '%actor_empresa_id%')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='pago' AND policyname='p_api_select_client_pago'
   AND cmd='SELECT' AND roles @> ARRAY['fashionstore_api']::name[]
   AND coalesce(qual,'') LIKE '%actor_posee_cliente%'
   AND coalesce(qual,'') LIKE '%actor_empresa_id%') THEN
  fallos:=fallos||'policies_cliente'; END IF;

 IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='pago' AND policyname='p_webhook_pago'
   AND cmd='ALL' AND roles=ARRAY['fashionstore_webhook_owner']::name[])
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='venta' AND policyname='p_webhook_venta'
   AND cmd='ALL' AND roles=ARRAY['fashionstore_webhook_owner']::name[])
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='detalle_venta' AND policyname='p_webhook_detalle_venta'
   AND cmd='SELECT' AND roles=ARRAY['fashionstore_webhook_owner']::name[])
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='inventario' AND policyname='p_webhook_inventario'
   AND cmd='ALL' AND roles=ARRAY['fashionstore_webhook_owner']::name[])
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='movimiento_inventario' AND policyname='p_webhook_movimiento'
   AND cmd='INSERT' AND roles=ARRAY['fashionstore_webhook_owner']::name[]) THEN
  fallos:=fallos||'policies_webhook'; END IF;

 -- 6. RLS/FORCE.
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
    ('carrito','detalle_carrito','venta','detalle_venta','pago','inventario','movimiento_inventario')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  fallos:=fallos||'rls'; END IF;

 -- 7. ACL de owners y API.
 IF NOT has_table_privilege('fashionstore_sp_owner','public.venta','SELECT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.venta','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.venta','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.detalle_venta','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.pago','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.carrito','UPDATE') THEN
  fallos:=fallos||'acl_sp_owner'; END IF;
 IF NOT has_table_privilege('fashionstore_webhook_owner','public.pago','SELECT')
 OR NOT has_table_privilege('fashionstore_webhook_owner','public.venta','UPDATE')
 OR NOT has_table_privilege('fashionstore_webhook_owner','public.inventario','UPDATE')
 OR NOT has_table_privilege('fashionstore_webhook_owner','public.detalle_venta','SELECT')
 OR NOT has_table_privilege('fashionstore_webhook_owner','public.movimiento_inventario','INSERT') THEN
  fallos:=fallos||'acl_webhook_owner'; END IF;
 IF has_table_privilege('fashionstore_webhook_owner','public.pago','DELETE')
 OR has_table_privilege('fashionstore_webhook_owner','public.venta','DELETE')
 OR has_table_privilege('fashionstore_webhook_owner','public.inventario','DELETE')
 OR has_table_privilege('fashionstore_webhook_owner','public.movimiento_inventario','UPDATE') THEN
  fallos:=fallos||'acl_webhook_excesiva'; END IF;
 IF EXISTS (SELECT 1 FROM information_schema.role_table_grants
   WHERE grantee='fashionstore_webhook' AND table_schema='public') THEN
  fallos:=fallos||'dml_webhook'; END IF;
 IF has_table_privilege('fashionstore_api','public.venta','INSERT')
 OR has_table_privilege('fashionstore_api','public.venta','UPDATE')
 OR has_table_privilege('fashionstore_api','public.detalle_venta','INSERT')
 OR has_table_privilege('fashionstore_api','public.pago','INSERT')
 OR has_table_privilege('fashionstore_api','public.inventario','UPDATE')
 OR has_table_privilege('fashionstore_api','public.movimiento_inventario','INSERT') THEN
  fallos:=fallos||'dml_api'; END IF;

 -- 8. Membresias: admin presente y sin SET temporal residual.
 IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
  FOR r IN SELECT oid,rolname FROM pg_roles
   WHERE rolname IN ('fashionstore_sp_owner','fashionstore_webhook_owner')
  LOOP
   IF NOT EXISTS (SELECT 1 FROM pg_auth_members m
     WHERE m.member=CURRENT_USER::regrole AND m.roleid=r.oid AND m.admin_option) THEN
    fallos:=fallos||('membresia_admin:'||r.rolname); END IF;
   IF EXISTS (SELECT 1 FROM pg_auth_members m
     WHERE m.member=CURRENT_USER::regrole AND m.roleid=r.oid AND m.set_option) THEN
    fallos:=fallos||('set_residual:'||r.rolname); END IF;
  END LOOP;
  -- La concesión externa de sp_owner (supabase_admin) debe seguir existiendo.
  IF NOT EXISTS (SELECT 1 FROM pg_auth_members m
    WHERE m.member=CURRENT_USER::regrole
      AND m.roleid='fashionstore_sp_owner'::regrole
      AND m.admin_option AND m.grantor<>CURRENT_USER::regrole) THEN
   fallos:=fallos||'membresia_externa_sp_owner'; END IF;
 END IF;

 -- 9. CU11/CU12/CU13 preservados.
 FOREACH firma IN ARRAY ARRAY[
  'public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)',
  'public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)',
  'public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)',
  'public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)',
  'public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)',
  'public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)'
 ] LOOP
  IF to_regprocedure(firma) IS NULL THEN
   fallos:=fallos||('cu_previo_ausente:'||firma); CONTINUE; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_roles o ON o.oid=p.proowner
    WHERE p.oid=to_regprocedure(firma) AND p.prosecdef IS TRUE
      AND o.rolname='fashionstore_sp_owner') THEN
   fallos:=fallos||('cu_previo_alterado:'||firma); END IF;
  IF NOT has_function_privilege('fashionstore_api',firma,'EXECUTE') THEN
   fallos:=fallos||('cu_previo_api:'||firma); END IF;
 END LOOP;
 IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NULL
 OR NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_roles o ON o.oid=p.proowner
   WHERE p.oid=to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)')
     AND p.prosecdef IS TRUE AND o.rolname='fashionstore_context_owner') THEN
  fallos:=fallos||'verificador_cu11'; END IF;

 IF array_length(fallos,1) IS NOT NULL THEN
  RAISE EXCEPTION 'CU14_POSTFLIGHT=FAIL %',array_to_string(fallos,',');
 END IF;
 RAISE NOTICE 'CU14_POSTFLIGHT=PASS';
END $verify$;
SELECT 'CU14_POSTFLIGHT=PASS' AS resultado;
