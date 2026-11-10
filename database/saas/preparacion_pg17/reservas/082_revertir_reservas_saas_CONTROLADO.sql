-- REVERSIÓN CONTROLADA. Solo si 080 creó estos objetos y FastAPI está detenida.
-- No revoca ACL de tablas preexistentes ni modifica datos comerciales.
\echo 'NO EJECUTAR SIN CONFIRMAR QUE NO EXISTIAN OBJETOS PREVIOS'
BEGIN;
DO $pre$
DECLARE instalado timestamptz; comentario text;
 reserva_seq bigint; detalle_seq bigint; movimiento_seq bigint;
BEGIN
 IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
  RAISE EXCEPTION 'La reversión requiere el instalador CREATEROLE autorizado';
 END IF;
 IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
   'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
   AND p.prosecdef
   AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
   AND pg_get_userbyid(p.proowner)=CASE WHEN p.prokind='p'
       THEN 'fashionstore_sp_owner' ELSE 'fashionstore_context_owner' END
   AND obj_description(p.oid,'pg_proc') LIKE
       'fashionstore.cu11.080.v1|body_md5=%'
   AND split_part(split_part(obj_description(p.oid,'pg_proc'),
       'body_md5=',2),'|',1)=md5(p.prosrc))<>4 THEN
  RAISE EXCEPTION 'Procedencia o integridad CU11 no verificable';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
   'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
  AND (has_function_privilege('public',p.oid,'EXECUTE')
       OR NOT has_function_privilege('fashionstore_api',p.oid,'EXECUTE'))) THEN
  RAISE EXCEPTION 'ACL CU11 no coincide con el paquete instalado';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname IN
   ('reserva','detalle_reserva','inventario','carrito','movimiento_inventario')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  RAISE EXCEPTION 'RLS/FORCE RLS CU11 incompleto';
 END IF;
 IF EXISTS (
  SELECT 1 FROM unnest(ARRAY[
   'carrito','detalle_carrito','inventario','reserva','detalle_reserva',
   'movimiento_inventario','venta']) t(tabla)
  LEFT JOIN pg_policies p ON p.schemaname='public' AND p.tablename=t.tabla
   AND p.policyname='p_sp_tenant_'||t.tabla AND p.cmd='ALL'
   AND 'fashionstore_sp_owner'=ANY(p.roles)
  WHERE p.policyname IS NULL
     OR position('actor_empresa_id' in coalesce(p.qual,''))=0
     OR position('actor_empresa_id' in coalesce(p.with_check,''))=0
 ) THEN
  RAISE EXCEPTION 'Policies empresariales CU11 incompletas';
 END IF;
 SELECT obj_description(p.oid,'pg_proc')
 INTO comentario FROM pg_proc p WHERE p.oid=to_regprocedure(
  'public.fs_verificar_actor_reserva(bigint,bigint)');
 instalado:=split_part(split_part(comentario,'installed_at=',2),'|',1)::timestamptz;
 reserva_seq:=split_part(split_part(comentario,'reserva_seq=',2),'|',1)::bigint;
 detalle_seq:=split_part(split_part(comentario,'detalle_seq=',2),'|',1)::bigint;
 movimiento_seq:=split_part(split_part(comentario,'movimiento_seq=',2),'|',1)::bigint;
 IF instalado IS NULL OR instalado>clock_timestamp() THEN
  RAISE EXCEPTION 'Timestamp de instalación CU11 inválido';
 END IF;
 IF EXISTS (SELECT 1 FROM public.reserva r WHERE r.fecha_reserva>=instalado)
 OR EXISTS (SELECT 1 FROM public.movimiento_inventario m
  WHERE m.fecha_hora>=instalado AND m.referencia_tipo='RESERVA'
    AND m.tipo IN ('RESERVA','LIBERACION_RESERVA')) THEN
  RAISE EXCEPTION 'Reversión bloqueada: existen operaciones CU11 posteriores a instalación';
 END IF;
 IF (SELECT last_value FROM public.reserva_id_seq)<>reserva_seq
 OR (SELECT last_value FROM public.detalle_reserva_id_seq)<>detalle_seq
 OR (SELECT last_value FROM public.movimiento_inventario_id_seq)<>movimiento_seq THEN
  RAISE EXCEPTION 'Reversión bloqueada: secuencias CU11 avanzaron después de instalación';
 END IF;
END $pre$;
GRANT fashionstore_sp_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT fashionstore_context_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
SET ROLE fashionstore_sp_owner;
REVOKE EXECUTE ON PROCEDURE
 public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint),
 public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text),
 public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)
 FROM fashionstore_api;
DROP PROCEDURE public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint);
DROP PROCEDURE public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text);
DROP PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text);
RESET ROLE;
SET ROLE fashionstore_context_owner;
DROP FUNCTION public.fs_verificar_actor_reserva(bigint,bigint);
RESET ROLE;
REVOKE fashionstore_sp_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;
REVOKE fashionstore_context_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;
COMMIT;
