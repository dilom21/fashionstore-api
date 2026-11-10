-- COPIA DE LABORATORIO 5CB de 050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql
-- Cambio 1: se retiró la guarda 'BLOQUEADO' de la primera línea
-- para poder ensayarla en un clúster desechable.
-- No se alteró nada más.
-- sha256 del original: ce7bb0126c08e48e86d72c780e3d96cab4ca612190e564cbbfecb0caeb68c47f
-- PROPUESTA LOCAL. NO EJECUTAR. ACL de corte: rompe rutas legacy deliberadamente.
-- REVISIÓN 5CA (2026-11-06), sin ejecutar. Originales en
-- respaldo_050_originales_5ca/. Diferencias respecto a la propuesta original:
--   HUECO 2/6: SELECT sobre movimiento_inventario y UPDATE explícito de carrito
--              para fashionstore_sp_owner (los SP de CU16/17/18 los necesitan).
--   HUECO 3  : GRANT SELECT sobre public.usuario a fashionstore_api, sin el cual
--              p_api_usuario_select (050b) es inaplicable y el login falla.
--   HUECO 5  : el contrato de instalación de los SP se corrige: el guardián usa
--              actor_coincide() y NO bloqueos FOR SHARE (exigirían UPDATE sobre
--              usuario_empresa/suscripcion, que el owner de SP no tiene).
-- El GRANT de bitácora para fashionstore_audit_owner ya existía aquí (hueco 6 de
-- 5CA), se mantiene tal cual.
BEGIN;

-- El ejecutor Supabase tiene CREATEROLE pero no es superusuario. La membresia
-- creada por 050a conserva ADMIN con SET FALSE; habilitar SET solo durante la
-- transferencia de fn_registrar_bitacora() y retirarlo antes del COMMIT.
DO $temporary_audit_owner_membership$
DECLARE executor name:=current_user;
BEGIN
 EXECUTE format(
  'GRANT fashionstore_audit_owner TO %I WITH SET TRUE, INHERIT FALSE',executor);
 IF NOT pg_has_role(executor,'fashionstore_audit_owner','SET') THEN
  RAISE EXCEPTION 'No se pudo habilitar SET temporal para audit_owner';
 END IF;
END $temporary_audit_owner_membership$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC,fashionstore_api,fashionstore_bootstrap;
DO $optional_supabase_roles$
DECLARE r text;
BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I',r);
   EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I',r);
   EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I',r);
   EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I',r);
  END IF;
 END LOOP;
END $optional_supabase_roles$;
GRANT USAGE ON SCHEMA public TO fashionstore_api;
GRANT USAGE ON SCHEMA public TO fashionstore_platform_admin;

-- Lectura sólo donde existe una policy actor-aware en 050b.
GRANT SELECT ON public.empresa,public.empresa_configuracion,public.plan_saas,
 public.suscripcion,public.usuario_empresa,public.categoria,public.coleccion,
 public.configuracion_vestidor_ar,public.inventario,public.producto,
 public.producto_coleccion,public.promocion,public.promocion_producto,
 public.recurso_producto,public.sucursal,public.temporada,public.variante_producto,
 public.bitacora,public.cliente,public.empleado,public.movimiento_inventario,
 public.orden_compra,public.detalle_orden_compra,public.proveedor,
 public.proveedor_producto,public.carrito,public.detalle_carrito,public.reserva,
 public.detalle_reserva,public.venta,public.detalle_venta,public.pago,
 public.devolucion,public.detalle_devolucion,public.sesion_vestidor_ar,
 public.prueba_vestidor_ar TO fashionstore_api;
GRANT SELECT ON public.ciudad,public.talla,public.color,public.rol,public.modulo,
 public.funcion,public.accion,public.rol_funcion TO fashionstore_api;
-- 5CA HUECO 3 (bloqueante): sin este privilegio la policy p_api_usuario_select
-- de 050b es inaplicable y toda ruta que une public.usuario (login y
-- TenantAuthRepository.obtener_membresia_vigente) falla con
-- "permission denied for table usuario". Sólo SELECT: las filas las limita la
-- policy y las escrituras siguen prohibidas por ACL y triggers.
GRANT SELECT ON public.usuario TO fashionstore_api;

GRANT INSERT,UPDATE ON public.categoria,public.producto,public.variante_producto,
 public.sucursal TO fashionstore_api;
GRANT UPDATE ON public.empresa,public.empresa_configuracion,public.suscripcion,
 public.usuario_empresa TO fashionstore_api;
GRANT INSERT,UPDATE ON public.carrito TO fashionstore_api;
GRANT INSERT,UPDATE,DELETE ON public.detalle_carrito TO fashionstore_api;
GRANT USAGE,SELECT ON SEQUENCE public.categoria_id_seq,public.producto_id_seq,
 public.variante_producto_id_seq,public.sucursal_id_seq,public.carrito_id_seq,
 public.detalle_carrito_id_seq TO fashionstore_api;

GRANT SELECT,INSERT,UPDATE ON public.empresa,public.empresa_configuracion,
 public.plan_saas,public.suscripcion,public.usuario,public.usuario_empresa,
 public.sucursal,public.empleado TO fashionstore_platform_admin;
GRANT SELECT ON public.ciudad,public.rol TO fashionstore_platform_admin;
GRANT USAGE,SELECT ON SEQUENCE public.empresa_id_seq,public.plan_saas_id_seq,
 public.suscripcion_id_seq,public.usuario_id_seq,public.usuario_empresa_id_seq,
 public.sucursal_id_seq,public.empleado_id_seq TO fashionstore_platform_admin;

-- La auditoría se escribe por trigger con owner NOLOGIN; la API no recibe
-- INSERT directo, por lo que el repository debe dejar de insertar bitácora.
GRANT INSERT ON public.bitacora TO fashionstore_audit_owner;
GRANT USAGE,SELECT ON SEQUENCE public.bitacora_id_seq TO fashionstore_audit_owner;
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $fn$
DECLARE
 v_usuario_id bigint; v_registro_id text; v_accion text;
 v_empresa_id bigint; v_fila jsonb;
BEGIN
 IF nullif(current_setting('app.actor_token',true),'') IS NOT NULL THEN
  v_usuario_id:=fashionstore_private.actor_usuario_id();
 END IF;
 IF TG_OP='DELETE' THEN v_fila:=to_jsonb(OLD); ELSE v_fila:=to_jsonb(NEW); END IF;
 IF v_fila ? 'empresa_id' AND nullif(v_fila->>'empresa_id','') IS NOT NULL THEN
  v_empresa_id:=(v_fila->>'empresa_id')::bigint;
 END IF;
 CASE TG_OP
  WHEN 'INSERT' THEN v_accion:='CREAR'; v_registro_id:=NEW.id::text;
  WHEN 'UPDATE' THEN v_accion:='MODIFICAR'; v_registro_id:=NEW.id::text;
  WHEN 'DELETE' THEN v_accion:='ELIMINAR'; v_registro_id:=OLD.id::text;
  ELSE v_accion:=TG_OP;
 END CASE;
 INSERT INTO public.bitacora(empresa_id,usuario_id,fecha_hora,accion,entidad_afectada,descripcion)
 VALUES(v_empresa_id,v_usuario_id,current_timestamp,v_accion,TG_TABLE_NAME,
        format('Operación %s sobre registro ID %s',v_accion,v_registro_id));
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $fn$;
-- Completar atributos y ACL mientras el ejecutor todavia es owner. Despues de
-- ALTER OWNER no se vuelve a modificar la funcion desde el rol administrativo.
ALTER FUNCTION public.fn_registrar_bitacora() SECURITY DEFINER;
ALTER FUNCTION public.fn_registrar_bitacora() SET search_path=public,pg_temp;
REVOKE ALL ON FUNCTION public.fn_registrar_bitacora() FROM PUBLIC,fashionstore_api;
GRANT CREATE ON SCHEMA public TO fashionstore_audit_owner;
ALTER FUNCTION public.fn_registrar_bitacora() OWNER TO fashionstore_audit_owner;
REVOKE CREATE ON SCHEMA public FROM fashionstore_audit_owner;

DO $revoke_temporary_audit_owner_membership$
DECLARE executor name:=current_user;
BEGIN
 -- Retira solamente la concesion temporal cuyo grantor es el ejecutor; la
 -- membresia administrativa automatica queda intacta y con SET FALSE.
 EXECUTE format(
  'REVOKE fashionstore_audit_owner FROM %I GRANTED BY %I',executor,executor);
 IF pg_has_role(executor,'fashionstore_audit_owner','SET') THEN
  RAISE EXCEPTION 'Persistio SET temporal para audit_owner';
 END IF;
 IF NOT EXISTS(
  SELECT 1 FROM pg_auth_members m
  JOIN pg_roles granted ON granted.oid=m.roleid
  JOIN pg_roles member ON member.oid=m.member
  WHERE granted.rolname='fashionstore_audit_owner'
    AND member.rolname=executor
    AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option
 ) THEN
  RAISE EXCEPTION 'Membresia administrativa final inesperada para audit_owner';
 END IF;
 IF EXISTS(
  SELECT 1 FROM pg_auth_members m
  JOIN pg_roles granted ON granted.oid=m.roleid
  JOIN pg_roles member ON member.oid=m.member
  JOIN pg_roles grantor ON grantor.oid=m.grantor
  WHERE granted.rolname='fashionstore_audit_owner'
    AND member.rolname=executor AND grantor.rolname=executor
 ) THEN
  RAISE EXCEPTION 'Persistio la concesion temporal para audit_owner';
 END IF;
END $revoke_temporary_audit_owner_membership$;

-- Owner de los tres SP: privilegios restringidos al flujo CU16/17/18.
GRANT SELECT ON public.usuario,public.usuario_empresa,public.empresa,public.rol,
 public.suscripcion,public.plan_saas,public.cliente,public.empleado,public.sucursal,
 public.carrito,public.detalle_carrito,public.categoria,public.producto,
 public.variante_producto,public.temporada,public.talla,public.color,
 public.inventario,public.reserva,public.detalle_reserva,public.venta,
 public.rol_funcion,public.funcion,public.accion TO fashionstore_sp_owner;
-- 5CA HUECO 2: los reportes del SP leen movimiento_inventario; 050c sólo le
-- daba INSERT/UPDATE sobre esa tabla y el SELECT faltaba.
GRANT SELECT ON public.movimiento_inventario TO fashionstore_sp_owner;
GRANT INSERT,UPDATE ON public.reserva,public.detalle_reserva,public.inventario,
 public.movimiento_inventario,public.carrito TO fashionstore_sp_owner;
-- 5CA HUECO 2: el cierre de reserva marca el carrito como CONVERTIDO. El
-- privilegio ya estaba incluido arriba; esta línea lo hace explícito para que
-- una refactorización futura no lo pierda de vista.
GRANT UPDATE ON public.carrito TO fashionstore_sp_owner;
GRANT USAGE,SELECT ON SEQUENCE public.reserva_id_seq,public.detalle_reserva_id_seq,
 public.movimiento_inventario_id_seq TO fashionstore_sp_owner;

-- Los SP tenant deben instalarse desde borradores 12-14 después de insertar
-- como primera instrucción:
-- IF NOT fashionstore_private.actor_coincide(p_empresa_id,p_usuario_id)
-- THEN RAISE EXCEPTION ... ERRCODE='42501'; END IF;
-- y deben quedar SECURITY DEFINER, owner fashionstore_sp_owner, search_path
-- pg_catalog,pg_temp, PUBLIC revocado. No se conceden aquí porque todavía no
-- existe una versión productiva revisada de sus cuerpos.
-- 5CA HUECO 5: el guardián se limita a actor_coincide(). La versión de borrador
-- usaba además FOR SHARE OF ue, ss sobre usuario_empresa/suscripcion, lo que
-- exige UPDATE sobre esas tablas y rompía el SP en el pool restringido. En el
-- laboratorio se retiró el bloqueo de filas y se conservó la guarda de
-- identidad; la vigencia de membresía y suscripción ya la verifica
-- actor_claims() en cada llamada. actor_coincide() se crea en 050a.

-- Ningún LOGIN se crea ni se vincula aquí. Operación posterior y explícita:
-- GRANT fashionstore_api TO <login_dedicado_fastapi_no_bypassrls>;
-- GRANT fashionstore_bootstrap TO <login_dedicado_bootstrap_no_bypassrls>;
-- GRANT fashionstore_platform_admin TO <login_dedicado_superadmin_no_bypassrls>;
-- Los nombres y contraseñas reales nunca pertenecen a una migración.
COMMIT;

