BEGIN;
SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='120s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_pg17',0));

CREATE OR REPLACE FUNCTION public.fn_empresa_id_inmutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.empresa_id IS DISTINCT FROM OLD.empresa_id THEN
  RAISE EXCEPTION 'empresa_id es inmutable' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.fn_empresa_id_inmutable() FROM PUBLIC;

-- La bitácora es empresarial cuando el trigger proviene de una tabla que ya
-- posee empresa_id. Usuario/rol siguen produciendo eventos globales (NULL).
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public,pg_temp AS $$
DECLARE
 v_usuario_text text; v_usuario_id bigint; v_registro_id text;
 v_accion text; v_empresa_id bigint; v_fila jsonb;
BEGIN
 v_usuario_text:=current_setting('app.usuario_id',true);
 IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN
  v_usuario_id:=v_usuario_text::bigint;
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
END $$;
REVOKE ALL ON FUNCTION public.fn_registrar_bitacora() FROM PUBLIC;

-- Dos funciones trigger históricas eran las únicas sin search_path fijo.
ALTER FUNCTION public.fn_actualizar_actividad_carrito() SET search_path=public,pg_temp;
ALTER FUNCTION public.fn_validar_sucursal_detalle_carrito() SET search_path=public,pg_temp;

DO $triggers$
DECLARE t text;
 tables constant text[]:=ARRAY['bitacora','carrito','categoria','cliente','coleccion','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion','detalle_orden_compra','detalle_reserva','detalle_venta','devolucion','empleado','inventario','movimiento_inventario','orden_compra','pago','producto','producto_coleccion','promocion','promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva','sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 FOREACH t IN ARRAY tables LOOP
  EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OF empresa_id ON public.%I FOR EACH ROW EXECUTE FUNCTION public.fn_empresa_id_inmutable()','trg_'||t||'_empresa_inmutable',t);
 END LOOP;
END $triggers$;

-- 5CB (HALLAZGO): el histórico 20260903183744_add_fashionstore_triggers.sql sólo
-- instaló trg_bitacora_* en usuario, rol, producto, variante_producto, sucursal,
-- empleado, cliente, inventario, orden_compra, reserva, venta, pago y devolucion.
-- public.categoria quedó SIN auditoría por trigger aunque 050c sí concede
-- INSERT/UPDATE de categoría al administrador del tenant: los eventos sólo
-- existían si la aplicación los insertaba a mano. Se añade el trigger que falta
-- con la MISMA función fn_registrar_bitacora(), de modo que:
--   * el evento se escribe en la MISMA transacción del cambio (atómico);
--   * empresa_id se toma de la propia fila afectada (NEW/OLD), nunca de un GUC
--     ni de un dato de la petición HTTP, así que un evento jamás puede quedar
--     asignado a otra empresa;
--   * usuario_id sigue siendo app.usuario_id, fijado sólo tras validar JWT y
--     membresía (app/core/tenant_dependencies.py);
--   * INSERT, UPDATE y DELETE se auditan con el mismo formato del histórico.
-- Idempotente: re-ejecutar 040 no duplica el trigger. La duplicación de eventos
-- (trigger + insert manual del API) se evita en la aplicación: con el trigger
-- presente, app/core/bitacora_trigger.py hace que el repositorio NO inserte.
DO $bitacora_categoria$
BEGIN
 IF NOT EXISTS(
   SELECT 1 FROM pg_catalog.pg_trigger t
   WHERE t.tgrelid='public.categoria'::regclass
     AND t.tgname='trg_bitacora_categoria' AND NOT t.tgisinternal
 ) THEN
  CREATE TRIGGER trg_bitacora_categoria
   AFTER INSERT OR UPDATE OR DELETE ON public.categoria
   FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();
 END IF;
END $bitacora_categoria$;

CREATE OR REPLACE VIEW public.vw_inventario_stock WITH(security_invoker=true) AS
SELECT i.id inventario_id,i.sucursal_id,s.nombre sucursal,
 i.variante_producto_id,vp.sku,p.id producto_id,p.nombre producto,
 t.id talla_id,t.nombre talla,c.id color_id,c.nombre color,
 i.temporada_id,te.nombre temporada,i.stock_actual,i.stock_reservado,
 i.stock_actual-i.stock_reservado stock_disponible,i.fecha_actualizacion,
 i.empresa_id
FROM public.inventario i
JOIN public.sucursal s ON (s.empresa_id,s.id)=(i.empresa_id,i.sucursal_id)
JOIN public.variante_producto vp ON (vp.empresa_id,vp.id)=(i.empresa_id,i.variante_producto_id)
JOIN public.producto p ON (p.empresa_id,p.id)=(vp.empresa_id,vp.producto_id)
JOIN public.talla t ON t.id=vp.talla_id JOIN public.color c ON c.id=vp.color_id
JOIN public.temporada te ON (te.empresa_id,te.id)=(i.empresa_id,i.temporada_id);

DO $legacy$
DECLARE p record; role_name text;
 legacy constant text[]:=ARRAY['sp_ajustar_inventario','sp_cancelar_reserva','sp_confirmar_venta','sp_crear_reserva_desde_carrito','sp_crear_reserva','sp_finalizar_reserva_sin_compra','sp_recalcular_total_venta','sp_recibir_orden_compra','sp_registrar_devolucion'];
BEGIN
 FOR p IN SELECT x.oid::regprocedure signature FROM pg_proc x JOIN pg_namespace n ON n.oid=x.pronamespace WHERE n.nspname='public' AND x.prokind='p' AND x.proname=ANY(legacy) LOOP
  EXECUTE format('REVOKE ALL ON PROCEDURE %s FROM PUBLIC',p.signature);
  FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
   IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN EXECUTE format('REVOKE ALL ON PROCEDURE %s FROM %I',p.signature,role_name); END IF;
  END LOOP;
 END LOOP;
END $legacy$;

-- Los tres SP *_tenant NO se crean aquí. FastAPI exige session_user individual,
-- fs_verificar_actor_reserva() SECDEF con owner NOLOGIN y owner SP separado.
-- Instalarlos antes de provisionar ese contrato produciría suplantación dentro del tenant.
COMMIT;

