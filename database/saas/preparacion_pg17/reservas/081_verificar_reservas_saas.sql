-- SOLO LECTURA. Imprime PASS/FAIL y, con psql ON_ERROR_STOP, FAIL termina != 0.
WITH verificacion AS (
SELECT (
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN
  ('sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
   'sp_finalizar_reserva_sin_compra_tenant') AND p.prokind='p'
   AND p.prosecdef AND pg_get_userbyid(p.proowner)='fashionstore_sp_owner'
   AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[])=3
 AND EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(
  'public.fs_verificar_actor_reserva(bigint,bigint)') AND p.prosecdef
  AND pg_get_userbyid(p.proowner)='fashionstore_context_owner'
  AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[])
 AND NOT EXISTS (SELECT 1 FROM pg_roles r
  WHERE r.rolname IN ('fashionstore_sp_owner','fashionstore_context_owner')
    AND (r.rolcanlogin OR r.rolsuper OR r.rolbypassrls OR r.rolcreaterole
         OR r.rolcreatedb OR r.rolinherit))
 AND (SELECT count(*) FROM pg_proc p WHERE p.oid IN (
   to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)'),
   to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)'),
   to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)'))
   AND has_function_privilege('fashionstore_api',p.oid,'EXECUTE'))=3
 AND has_function_privilege('fashionstore_api',
   'public.fs_verificar_actor_reserva(bigint,bigint)','EXECUTE')
 AND has_function_privilege('fashionstore_sp_owner',
   'public.fs_verificar_actor_reserva(bigint,bigint)','EXECUTE')
 AND NOT EXISTS (SELECT 1 FROM pg_proc p
  WHERE p.oid IN (
   to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)'),
   to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)'),
   to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)'),
   to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)'))
  AND EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
              WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
 AND NOT has_schema_privilege('fashionstore_sp_owner','public','CREATE')
 AND NOT has_schema_privilege('fashionstore_context_owner','public','CREATE')
 AND NOT has_table_privilege('fashionstore_api','public.reserva','INSERT')
 AND NOT has_table_privilege('fashionstore_api','public.detalle_reserva','INSERT')
 AND NOT has_table_privilege('fashionstore_api','public.inventario','UPDATE')
 AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname IN
   ('reserva','detalle_reserva','inventario','carrito')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity))
 AND NOT pg_has_role('fashionstore_api','fashionstore_sp_owner','MEMBER')
 AND NOT pg_has_role('fashionstore_api','fashionstore_context_owner','MEMBER')
 AND NOT EXISTS (
  SELECT 1 FROM pg_auth_members m
  JOIN pg_roles granted ON granted.oid=m.roleid
  JOIN pg_roles member ON member.oid=m.member
  WHERE member.rolname=current_user
    AND granted.rolname IN ('fashionstore_sp_owner','fashionstore_context_owner')
    AND m.set_option)
 AND NOT EXISTS (
  SELECT 1 FROM pg_auth_members m
  JOIN pg_roles granted ON granted.oid=m.roleid
  JOIN pg_roles member ON member.oid=m.member
  JOIN pg_roles grantor ON grantor.oid=m.grantor
  WHERE member.rolname=current_user AND grantor.rolname=current_user
    AND granted.rolname IN ('fashionstore_sp_owner','fashionstore_context_owner'))
 AND (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
   'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
   AND obj_description(p.oid,'pg_proc') LIKE 'fashionstore.cu11.080.v1|body_md5=%'
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'body_md5=',2),'|',1)=md5(p.prosrc))=4
 AND EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(
   'public.fs_verificar_actor_reserva(bigint,bigint)')
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'installed_at=',2),'|',1)::timestamptz
       <= clock_timestamp()
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'reserva_seq=',2),'|',1)::bigint>=0
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'detalle_seq=',2),'|',1)::bigint>=0
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'movimiento_seq=',2),'|',1)::bigint>=0)
 AND NOT EXISTS (
  WITH requeridas(tabla,politica,comando,tenant) AS (VALUES
   ('carrito','p_sp_tenant_carrito','ALL',true),
   ('detalle_carrito','p_sp_tenant_detalle_carrito','ALL',true),
   ('inventario','p_sp_tenant_inventario','ALL',true),
   ('reserva','p_sp_tenant_reserva','ALL',true),
   ('detalle_reserva','p_sp_tenant_detalle_reserva','ALL',true),
   ('movimiento_inventario','p_sp_tenant_movimiento_inventario','ALL',true),
   ('venta','p_sp_tenant_venta','ALL',true),
   ('cliente','p_sp_select_cliente','SELECT',true),
   ('empleado','p_sp_select_empleado','SELECT',true),
   ('sucursal','p_sp_select_sucursal','SELECT',true),
   ('categoria','p_sp_select_categoria','SELECT',true),
   ('producto','p_sp_select_producto','SELECT',true),
   ('variante_producto','p_sp_select_variante_producto','SELECT',true),
   ('temporada','p_sp_select_temporada','SELECT',true),
   ('empresa','p_sp_empresa_select','SELECT',true),
   ('usuario_empresa','p_sp_membresia_select','SELECT',true),
   ('suscripcion','p_sp_suscripcion_select','SELECT',true),
   ('usuario','p_sp_select_global_usuario','SELECT',false),
   ('rol','p_sp_select_global_rol','SELECT',false),
   ('plan_saas','p_sp_select_global_plan_saas','SELECT',false),
   ('talla','p_sp_select_global_talla','SELECT',false),
   ('color','p_sp_select_global_color','SELECT',false),
   ('funcion','p_sp_select_global_funcion','SELECT',false),
   ('accion','p_sp_select_global_accion','SELECT',false),
   ('rol_funcion','p_sp_select_global_rol_funcion','SELECT',false)
  )
  SELECT 1 FROM requeridas r
  LEFT JOIN pg_policies p ON p.schemaname='public'
   AND p.tablename=r.tabla AND p.policyname=r.politica
   AND p.cmd=r.comando AND 'fashionstore_sp_owner'=ANY(p.roles)
  WHERE p.policyname IS NULL
     OR (r.tenant AND position('actor_empresa_id' in coalesce(p.qual,''))=0)
     OR (r.comando='ALL' AND position(
          'actor_empresa_id' in coalesce(p.with_check,''))=0)
 )
) AS ok
)
SELECT CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS estado, ok
FROM verificacion \gset cu11_
\echo CU11_POSTFLIGHT=:cu11_estado
\if :cu11_ok
\else
DO $fallo$ BEGIN
 RAISE EXCEPTION 'CU11_POSTFLIGHT=FAIL';
END $fallo$;
\endif
