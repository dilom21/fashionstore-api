-- Corrección aplicada sólo al laboratorio después del hallazgo de la primera corrida.
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=public,pg_temp AS $$
DECLARE
 v_usuario_text text; v_usuario_id bigint; v_registro_id text;
 v_accion text; v_empresa_id bigint; v_fila jsonb;
BEGIN
 v_usuario_text:=current_setting('app.usuario_id',true);
 IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN v_usuario_id:=v_usuario_text::bigint; END IF;
 IF TG_OP='DELETE' THEN v_fila:=to_jsonb(OLD); ELSE v_fila:=to_jsonb(NEW); END IF;
 IF v_fila ? 'empresa_id' AND nullif(v_fila->>'empresa_id','') IS NOT NULL THEN v_empresa_id:=(v_fila->>'empresa_id')::bigint; END IF;
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
ALTER FUNCTION public.fn_actualizar_actividad_carrito() SET search_path=public,pg_temp;
ALTER FUNCTION public.fn_validar_sucursal_detalle_carrito() SET search_path=public,pg_temp;
