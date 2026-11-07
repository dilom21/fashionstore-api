-- COPIA DE LABORATORIO 5CA de 050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql
-- Cambio único: se retiró la guarda 'BLOQUEADO' de la primera línea
-- para poder ensayarla en un clúster desechable. Nada más se alteró.
-- sha256 del original: 2cb36497c5136a036e2130c0fcfc97b5e947eccf9bce608fa5f5d529ed7296ff
-- PROPUESTA LOCAL. NO EJECUTAR. ACL de corte: rompe rutas legacy deliberadamente.
BEGIN;

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
GRANT CREATE ON SCHEMA public TO fashionstore_audit_owner;
ALTER FUNCTION public.fn_registrar_bitacora() OWNER TO fashionstore_audit_owner;
REVOKE CREATE ON SCHEMA public FROM fashionstore_audit_owner;
ALTER FUNCTION public.fn_registrar_bitacora() SECURITY DEFINER;
ALTER FUNCTION public.fn_registrar_bitacora() SET search_path=public,pg_temp;
REVOKE ALL ON FUNCTION public.fn_registrar_bitacora() FROM PUBLIC,fashionstore_api;

-- Owner de los tres SP: privilegios restringidos al flujo CU16/17/18.
GRANT SELECT ON public.usuario,public.usuario_empresa,public.empresa,public.rol,
 public.suscripcion,public.plan_saas,public.cliente,public.empleado,public.sucursal,
 public.carrito,public.detalle_carrito,public.categoria,public.producto,
 public.variante_producto,public.temporada,public.talla,public.color,
 public.inventario,public.reserva,public.detalle_reserva,public.venta,
 public.rol_funcion,public.funcion,public.accion TO fashionstore_sp_owner;
GRANT INSERT,UPDATE ON public.reserva,public.detalle_reserva,public.inventario,
 public.movimiento_inventario,public.carrito TO fashionstore_sp_owner;
GRANT USAGE,SELECT ON SEQUENCE public.reserva_id_seq,public.detalle_reserva_id_seq,
 public.movimiento_inventario_id_seq TO fashionstore_sp_owner;

-- Los SP tenant deben instalarse desde borradores 12-14 después de insertar
-- como primera instrucción:
-- IF NOT fashionstore_private.actor_coincide(p_empresa_id,p_usuario_id)
-- THEN RAISE EXCEPTION ... ERRCODE='42501'; END IF;
-- y deben quedar SECURITY DEFINER, owner fashionstore_sp_owner, search_path
-- pg_catalog,pg_temp, PUBLIC revocado. No se conceden aquí porque todavía no
-- existe una versión productiva revisada de sus cuerpos.

-- Ningún LOGIN se crea ni se vincula aquí. Operación posterior y explícita:
-- GRANT fashionstore_api TO <login_dedicado_fastapi_no_bypassrls>;
-- GRANT fashionstore_bootstrap TO <login_dedicado_bootstrap_no_bypassrls>;
-- GRANT fashionstore_platform_admin TO <login_dedicado_superadmin_no_bypassrls>;
-- Los nombres y contraseñas reales nunca pertenecen a una migración.
COMMIT;

