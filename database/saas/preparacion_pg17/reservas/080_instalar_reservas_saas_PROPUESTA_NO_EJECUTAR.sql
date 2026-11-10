-- CU11 RESERVAS SAAS - PROPUESTA NO EJECUTAR SIN AUTORIZACION
-- PostgreSQL 17. Instalacion atomica; no habilita SAAS_RESERVAS_ENABLED.
\echo 'NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA'
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';

DO $pre$
BEGIN
 IF current_setting('server_version_num')::int < 170000 THEN
  RAISE EXCEPTION 'Requiere PostgreSQL 17'; END IF;
 IF NOT (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) THEN
  RAISE EXCEPTION 'Instalador sin CREATEROLE'; END IF;
 IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)') IS NOT NULL
 OR to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)') IS NOT NULL THEN
  RAISE EXCEPTION 'Objetos CU11 ya existen; detener y auditar'; END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner')
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole
        OR rolcreatedb OR rolinherit))
 OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api'
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb))
 OR (SELECT count(*) FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner','fashionstore_api'))<>3 THEN
  RAISE EXCEPTION 'Roles base ausentes o inseguros'; END IF;
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
   ('reserva','detalle_reserva','inventario','carrito')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  RAISE EXCEPTION 'RLS/FORCE RLS incompleto'; END IF;
 IF NOT has_table_privilege('fashionstore_sp_owner','public.reserva','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.reserva','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.detalle_reserva','INSERT')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.inventario','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.carrito','UPDATE')
 OR NOT has_table_privilege('fashionstore_sp_owner','public.movimiento_inventario','INSERT') THEN
  RAISE EXCEPTION 'Owner SP sin ACL minima ya aprobada'; END IF;
 IF EXISTS (
  SELECT 1 FROM unnest(ARRAY[
   'usuario_empresa','usuario','empresa','rol','suscripcion','plan_saas',
   'carrito','cliente','sucursal','detalle_carrito','inventario',
   'variante_producto','producto','categoria','temporada','talla','color',
   'reserva','detalle_reserva','empleado','rol_funcion','funcion','accion','venta'
  ]) t(nombre)
  WHERE NOT has_table_privilege(
   'fashionstore_sp_owner',format('public.%I',nombre),'SELECT')) THEN
  RAISE EXCEPTION 'Owner SP sin SELECT requerido por CU11'; END IF;
 IF EXISTS (
  SELECT 1 FROM unnest(ARRAY[
   'reserva_id_seq','detalle_reserva_id_seq','movimiento_inventario_id_seq'
  ]) s(nombre)
  WHERE NOT has_sequence_privilege(
   'fashionstore_sp_owner',format('public.%I',nombre),'USAGE')) THEN
  RAISE EXCEPTION 'Owner SP sin USAGE de secuencias CU11'; END IF;
 IF EXISTS (
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
 ) THEN
  RAISE EXCEPTION 'Policies RLS CU11 ausentes o incompatibles'; END IF;
END $pre$;

CREATE FUNCTION public.fs_verificar_actor_reserva(
 p_empresa_id bigint,p_usuario_id bigint
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $actor$
 SELECT p_empresa_id= fashionstore_private.actor_empresa_id()
    AND p_usuario_id= fashionstore_private.actor_usuario_id()
$actor$;
REVOKE ALL ON FUNCTION public.fs_verificar_actor_reserva(bigint,bigint) FROM PUBLIC;

CREATE OR REPLACE PROCEDURE public.sp_crear_reserva_tenant(
    IN p_empresa_id bigint,
    IN p_carrito_id bigint,
    IN p_usuario_id bigint,
    IN p_fecha_atencion timestamptz,
    IN p_observacion text,
    INOUT p_reserva_id bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_reserva_tenant$
DECLARE
    v_carrito record;
    v_detalle record;
    v_inventario record;
    v_total integer;
    v_validas integer;
    v_rol_id bigint;
BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id <= 0
       OR p_usuario_id IS NULL OR p_usuario_id <= 0
       OR p_carrito_id IS NULL OR p_carrito_id <= 0 THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_fecha_atencion IS NULL OR p_fecha_atencion <= clock_timestamp() THEN
        RAISE EXCEPTION 'Fecha de atencion invalida' USING ERRCODE='22007';
    END IF;

    -- Fijar membresia y suscripcion durante la operacion, no confiar en JSON.
    SELECT ue.rol_id INTO v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
    JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
    WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
      AND ue.estado IS TRUE AND r.nombre='CLIENTE'
      AND pl.estado IS TRUE AND pl.permite_reservas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento;
    IF v_rol_id IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    -- El bloqueo de carrito serializa conversiones del mismo carrito.
    SELECT ca.id,ca.cliente_id,ca.sucursal_id,ca.estado,ca.fecha_actualizacion
    INTO v_carrito
    FROM public.carrito ca
    JOIN public.cliente cl ON cl.id=ca.cliente_id
       AND cl.empresa_id=p_empresa_id AND cl.usuario_id=p_usuario_id
       AND cl.estado IS TRUE
    JOIN public.sucursal su ON su.id=ca.sucursal_id
       AND su.empresa_id=p_empresa_id AND su.estado IS TRUE
    WHERE ca.id=p_carrito_id AND ca.empresa_id=p_empresa_id
    FOR UPDATE OF ca;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Carrito no encontrado' USING ERRCODE='P0002';
    END IF;
    IF v_carrito.estado <> 'ACTIVO'
       OR v_carrito.fecha_actualizacion IS NULL
       OR v_carrito.fecha_actualizacion <= clock_timestamp()-interval '2 hours' THEN
        RAISE EXCEPTION 'Carrito vencido o no activo' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.reserva re WHERE re.carrito_id=p_carrito_id) THEN
        RAISE EXCEPTION 'Carrito ya reservado' USING ERRCODE='23505';
    END IF;

    SELECT count(*) INTO v_total FROM public.detalle_carrito dc
       WHERE dc.carrito_id=p_carrito_id;
    IF v_total=0 THEN
        RAISE EXCEPTION 'Carrito sin prendas' USING ERRCODE='23514';
    END IF;
    -- No se aceptan lineas rotas ni relaciones entre empresas: contar TODAS.
    SELECT count(*) INTO v_validas
    FROM public.detalle_carrito dc
    JOIN public.inventario i ON i.id=dc.inventario_id
       AND i.empresa_id=p_empresa_id AND i.sucursal_id=v_carrito.sucursal_id
    JOIN public.variante_producto vp ON vp.id=i.variante_producto_id
       AND vp.empresa_id=p_empresa_id AND vp.estado IS TRUE
    JOIN public.producto pr ON pr.id=vp.producto_id
       AND pr.empresa_id=p_empresa_id AND pr.estado IS TRUE
    JOIN public.categoria cat ON cat.id=pr.categoria_id
       AND cat.empresa_id=p_empresa_id AND cat.estado IS TRUE
    JOIN public.temporada tem ON tem.id=i.temporada_id AND tem.empresa_id=p_empresa_id AND tem.estado IS TRUE
    JOIN public.talla ta ON ta.id=vp.talla_id AND ta.estado IS TRUE
    JOIN public.color co ON co.id=vp.color_id AND co.estado IS TRUE
    WHERE dc.carrito_id=p_carrito_id AND dc.empresa_id=p_empresa_id
      AND dc.cantidad > 0;
    IF v_total<>v_validas THEN
        RAISE EXCEPTION 'Lineas empresariales inconsistentes' USING ERRCODE='23514';
    END IF;

    -- Sin COMMIT interno. Un fallo posterior revierte TODA la reserva.
    INSERT INTO public.reserva (
        empresa_id, cliente_id, sucursal_id, carrito_id,
        fecha_reserva, fecha_atencion, estado, observacion
    ) VALUES (
        p_empresa_id, v_carrito.cliente_id, v_carrito.sucursal_id,
        p_carrito_id, clock_timestamp(), p_fecha_atencion, 'PENDIENTE', p_observacion
    ) RETURNING id INTO p_reserva_id;

    -- Orden estable de locks contra competencia por distintos carritos.
    FOR v_detalle IN
        SELECT dc.inventario_id,dc.cantidad
        FROM public.detalle_carrito dc
        WHERE dc.carrito_id=p_carrito_id AND dc.empresa_id=p_empresa_id
        ORDER BY dc.inventario_id
    LOOP
        SELECT i.stock_actual,i.stock_reservado
        INTO v_inventario
        FROM public.inventario i
        WHERE i.id=v_detalle.inventario_id
          AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_carrito.sucursal_id
        FOR UPDATE OF i;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario no disponible' USING ERRCODE='23514';
        END IF;
        IF v_detalle.cantidad <= 0 OR v_detalle.cantidad IS NULL
           OR v_detalle.cantidad > v_inventario.stock_actual-v_inventario.stock_reservado THEN
            RAISE EXCEPTION 'Stock insuficiente' USING ERRCODE='23514';
        END IF;

        UPDATE public.inventario i
        SET stock_reservado=i.stock_reservado+v_detalle.cantidad,
            fecha_actualizacion=clock_timestamp()
        WHERE i.id=v_detalle.inventario_id AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_carrito.sucursal_id
          AND i.stock_actual-i.stock_reservado>=v_detalle.cantidad;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Stock cambiado concurrentemente' USING ERRCODE='23514';
        END IF;

        INSERT INTO public.detalle_reserva (
            empresa_id, reserva_id, inventario_id, cantidad
        ) VALUES (
            p_empresa_id, p_reserva_id, v_detalle.inventario_id, v_detalle.cantidad
        );
        INSERT INTO public.movimiento_inventario (
            empresa_id, inventario_id, usuario_id, tipo, cantidad,
            fecha_hora, observaciones, referencia_tipo, referencia_id
        ) VALUES (
            p_empresa_id,v_detalle.inventario_id,p_usuario_id,'RESERVA',
            v_detalle.cantidad,clock_timestamp(),'Stock reservado para cliente',
            'RESERVA',p_reserva_id
        );
    END LOOP;

    UPDATE public.carrito ca
    SET estado='CONVERTIDO',fecha_actualizacion=clock_timestamp()
    WHERE ca.id=p_carrito_id AND ca.empresa_id=p_empresa_id
      AND ca.cliente_id=v_carrito.cliente_id AND ca.estado='ACTIVO';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Carrito modificado concurrentemente' USING ERRCODE='23514';
    END IF;
END;
$sp_reserva_tenant$;

CREATE OR REPLACE PROCEDURE public.sp_cancelar_reserva_tenant(
    IN p_empresa_id bigint,
    IN p_reserva_id bigint,
    IN p_usuario_id bigint,
    IN p_observacion text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_cancelar_tenant$
DECLARE
    v_reserva record;
    v_linea record;
    v_rol text;
    v_rol_id bigint;
    v_stock integer;
    v_total integer;
    v_validas integer;
BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id<=0
       OR p_reserva_id IS NULL OR p_reserva_id<=0
       OR p_usuario_id IS NULL OR p_usuario_id<=0 THEN
       RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT r.nombre,ue.rol_id INTO v_rol,v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
    JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
    WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
      AND ue.estado IS TRUE AND pl.estado IS TRUE AND pl.permite_reservas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento;
    IF v_rol IS NULL THEN
       RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT re.id,re.cliente_id,re.sucursal_id,re.estado,re.observacion
    INTO v_reserva FROM public.reserva re
    JOIN public.sucursal su ON su.id=re.sucursal_id AND su.empresa_id=p_empresa_id
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
    FOR UPDATE OF re;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Reserva no encontrada' USING ERRCODE='P0002';
    END IF;

    IF v_rol='CLIENTE' THEN
        IF NOT EXISTS(
            SELECT 1 FROM public.cliente cl
            WHERE cl.id=v_reserva.cliente_id
              AND cl.empresa_id=p_empresa_id
              AND cl.usuario_id=p_usuario_id AND cl.estado IS TRUE
        ) THEN
            RAISE EXCEPTION 'Reserva no encontrada' USING ERRCODE='P0002';
        END IF;
    ELSIF v_rol IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL') THEN
        IF NOT EXISTS(
            SELECT 1 FROM public.empleado em
            WHERE em.usuario_id=p_usuario_id AND em.empresa_id=p_empresa_id
              AND em.estado IS TRUE
              AND (v_rol='ADMINISTRADOR'
                   OR em.sucursal_id=v_reserva.sucursal_id)
        ) OR NOT EXISTS(
            SELECT 1 FROM public.rol_funcion rf
            JOIN public.funcion f ON f.id=rf.funcion_id AND f.estado IS TRUE
            JOIN public.accion a ON a.id=rf.accion_id AND a.estado IS TRUE
            WHERE rf.rol_id=v_rol_id AND f.nombre='GESTIONAR_RESERVAS'
              AND a.nombre='EJECUTAR'
        ) THEN
            RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
        END IF;
    ELSE
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    IF v_reserva.estado NOT IN ('PENDIENTE','CONFIRMADA') THEN
        RAISE EXCEPTION 'Estado de reserva no cancelable' USING ERRCODE='23514';
    END IF;
    -- Conservador: una venta vinculada podría comprometer el stock reservado.
    IF EXISTS (
        SELECT 1 FROM public.venta ve WHERE ve.reserva_id=p_reserva_id
           AND ve.empresa_id=p_empresa_id
    ) THEN
        RAISE EXCEPTION 'Reserva con venta asociada' USING ERRCODE='23514';
    END IF;

    SELECT count(*) INTO v_total
    FROM public.detalle_reserva dr WHERE dr.reserva_id=p_reserva_id;
    SELECT count(*) INTO v_validas
    FROM public.detalle_reserva dr
    JOIN public.inventario i ON i.id=dr.inventario_id
       AND i.empresa_id=p_empresa_id
       AND i.sucursal_id=v_reserva.sucursal_id
    WHERE dr.reserva_id=p_reserva_id AND dr.empresa_id=p_empresa_id
      AND dr.cantidad>0;
    IF v_total=0 OR v_validas<>v_total THEN
        RAISE EXCEPTION 'Lineas de reserva inconsistentes' USING ERRCODE='23514';
    END IF;

    FOR v_linea IN
        SELECT dr.inventario_id,dr.cantidad FROM public.detalle_reserva dr
        WHERE dr.reserva_id=p_reserva_id AND dr.empresa_id=p_empresa_id
        ORDER BY dr.inventario_id
    LOOP
        SELECT i.stock_reservado INTO v_stock
        FROM public.inventario i
        WHERE i.id=v_linea.inventario_id AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_reserva.sucursal_id
        FOR UPDATE OF i;
        IF NOT FOUND OR v_stock<v_linea.cantidad THEN
            RAISE EXCEPTION 'Stock reservado inconsistente' USING ERRCODE='23514';
        END IF;

        UPDATE public.inventario i
        SET stock_reservado=i.stock_reservado-v_linea.cantidad,
            fecha_actualizacion=clock_timestamp()
        WHERE i.id=v_linea.inventario_id AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_reserva.sucursal_id
          AND i.stock_reservado>=v_linea.cantidad;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'No se pudo liberar stock' USING ERRCODE='23514';
        END IF;
        INSERT INTO public.movimiento_inventario (
            empresa_id,inventario_id,usuario_id,tipo,cantidad,fecha_hora,
            observaciones,referencia_tipo,referencia_id
        ) VALUES (
            p_empresa_id,v_linea.inventario_id,p_usuario_id,
            'LIBERACION_RESERVA',v_linea.cantidad,clock_timestamp(),
            COALESCE(p_observacion,'Reserva cancelada'),'RESERVA',p_reserva_id
        );
    END LOOP;

    UPDATE public.reserva re
    SET estado='CANCELADA',
        observacion=CASE
            WHEN p_observacion IS NULL THEN re.observacion
            WHEN re.observacion IS NULL THEN p_observacion
            ELSE re.observacion || ' | ' || p_observacion
        END
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
      AND re.estado IN ('PENDIENTE','CONFIRMADA');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Estado de reserva cambiado' USING ERRCODE='23514';
    END IF;
    -- El carrito queda CONVERTIDO; nunca se reactiva.
END;
$sp_cancelar_tenant$;

CREATE OR REPLACE PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(
    IN p_empresa_id bigint,
    IN p_reserva_id bigint,
    IN p_usuario_id bigint,
    IN p_observacion text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_finalizar_tenant$
DECLARE
    v_reserva record;
    v_linea record;
    v_rol text;
    v_rol_id bigint;
    v_stock integer;
    v_total integer;
    v_validas integer;
BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id<=0
       OR p_reserva_id IS NULL OR p_reserva_id<=0
       OR p_usuario_id IS NULL OR p_usuario_id<=0 THEN
       RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT r.nombre,ue.rol_id INTO v_rol,v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
    JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
    WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
      AND ue.estado IS TRUE AND pl.estado IS TRUE AND pl.permite_reservas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento
      AND r.nombre IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO');
    IF v_rol IS NULL THEN
       RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT re.id,re.sucursal_id,re.estado,re.observacion
    INTO v_reserva
    FROM public.reserva re
    JOIN public.sucursal su ON su.id=re.sucursal_id AND su.empresa_id=p_empresa_id
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
    FOR UPDATE OF re;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Reserva no encontrada' USING ERRCODE='P0002';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.empleado em
        WHERE em.empresa_id=p_empresa_id AND em.usuario_id=p_usuario_id
          AND em.estado IS TRUE
          AND (v_rol='ADMINISTRADOR' OR em.sucursal_id=v_reserva.sucursal_id)
    ) OR NOT EXISTS (
        SELECT 1 FROM public.rol_funcion rf
        JOIN public.funcion f ON f.id=rf.funcion_id AND f.estado IS TRUE
        JOIN public.accion a ON a.id=rf.accion_id AND a.estado IS TRUE
        WHERE rf.rol_id=v_rol_id AND f.nombre='GESTIONAR_RESERVAS'
          AND a.nombre='EJECUTAR'
    ) THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    IF v_reserva.estado<>'CONFIRMADA' THEN
        RAISE EXCEPTION 'La reserva no puede finalizarse' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.venta ve
        WHERE ve.empresa_id=p_empresa_id AND ve.reserva_id=p_reserva_id
    ) THEN
        RAISE EXCEPTION 'La reserva tiene venta vinculada' USING ERRCODE='23514';
    END IF;

    SELECT count(*) INTO v_total
    FROM public.detalle_reserva dr WHERE dr.reserva_id=p_reserva_id;
    SELECT count(*) INTO v_validas
    FROM public.detalle_reserva dr
    JOIN public.inventario i ON i.id=dr.inventario_id
       AND i.empresa_id=p_empresa_id
       AND i.sucursal_id=v_reserva.sucursal_id
    WHERE dr.reserva_id=p_reserva_id AND dr.empresa_id=p_empresa_id
      AND dr.cantidad>0;
    IF v_total=0 OR v_validas<>v_total THEN
       RAISE EXCEPTION 'Lineas de reserva inconsistentes' USING ERRCODE='23514';
    END IF;

    FOR v_linea IN
        SELECT dr.inventario_id,dr.cantidad
        FROM public.detalle_reserva dr
        WHERE dr.reserva_id=p_reserva_id AND dr.empresa_id=p_empresa_id
        ORDER BY dr.inventario_id
    LOOP
        SELECT i.stock_reservado INTO v_stock
        FROM public.inventario i
        WHERE i.id=v_linea.inventario_id AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_reserva.sucursal_id
        FOR UPDATE OF i;
        IF NOT FOUND OR v_stock<v_linea.cantidad THEN
            RAISE EXCEPTION 'Stock reservado inconsistente' USING ERRCODE='23514';
        END IF;

        UPDATE public.inventario i
        SET stock_reservado=i.stock_reservado-v_linea.cantidad,
            fecha_actualizacion=clock_timestamp()
        WHERE i.id=v_linea.inventario_id AND i.empresa_id=p_empresa_id
          AND i.sucursal_id=v_reserva.sucursal_id
          AND i.stock_reservado>=v_linea.cantidad;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Stock no disponible para liberar' USING ERRCODE='23514';
        END IF;

        INSERT INTO public.movimiento_inventario (
            empresa_id,inventario_id,usuario_id,tipo,cantidad,fecha_hora,
            observaciones,referencia_tipo,referencia_id
        ) VALUES (
            p_empresa_id,v_linea.inventario_id,p_usuario_id,
            'LIBERACION_RESERVA',v_linea.cantidad,clock_timestamp(),
            COALESCE(NULLIF(TRIM(p_observacion),''),
                     'Atencion finalizada sin compra'),
            'RESERVA',p_reserva_id
        );
    END LOOP;

    UPDATE public.reserva re
    SET estado='ATENDIDA',
        observacion=CASE
            WHEN p_observacion IS NULL OR TRIM(p_observacion)=''
                THEN re.observacion
            WHEN re.observacion IS NULL OR TRIM(re.observacion)=''
                THEN p_observacion
            ELSE re.observacion || ' | ' || p_observacion
        END
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
      AND re.estado='CONFIRMADA';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Estado de reserva cambiado' USING ERRCODE='23514';
    END IF;
    -- stock_actual se conserva. No hay COMMIT interno.
END;
$sp_finalizar_tenant$;

-- Marcador verificable: CREATE OR REPLACE conserva comentarios, por lo que un
-- cambio posterior del cuerpo rompe la comparación md5(prosrc) de 081/082.
DO $marcar$
DECLARE r record; marca text:='fashionstore.cu11.080.v1';
 reserva_seq bigint; detalle_seq bigint; movimiento_seq bigint;
BEGIN
 SELECT last_value INTO reserva_seq FROM public.reserva_id_seq;
 SELECT last_value INTO detalle_seq FROM public.detalle_reserva_id_seq;
 SELECT last_value INTO movimiento_seq FROM public.movimiento_inventario_id_seq;
 FOR r IN SELECT p.oid,p.prokind,p.oid::regprocedure firma,md5(p.prosrc) hash
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'fs_verificar_actor_reserva','sp_crear_reserva_tenant',
   'sp_cancelar_reserva_tenant','sp_finalizar_reserva_sin_compra_tenant')
 LOOP
  EXECUTE format('COMMENT ON %s %s IS %L',
   CASE WHEN r.prokind='p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
   r.firma,
   marca||'|body_md5='||r.hash||CASE WHEN r.prokind='f'
    THEN '|installed_at='||transaction_timestamp()::text
      ||'|reserva_seq='||reserva_seq::text
      ||'|detalle_seq='||detalle_seq::text
      ||'|movimiento_seq='||movimiento_seq::text
    ELSE '' END);
 END LOOP;
END $marcar$;

-- Transferencias: SET temporal, CREATE temporal y retirada en la misma TX.
GRANT fashionstore_context_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON SCHEMA public TO fashionstore_context_owner;
GRANT EXECUTE ON FUNCTION public.fs_verificar_actor_reserva(bigint,bigint)
 TO fashionstore_api,fashionstore_sp_owner;
ALTER FUNCTION public.fs_verificar_actor_reserva(bigint,bigint)
 OWNER TO fashionstore_context_owner;
REVOKE CREATE ON SCHEMA public FROM fashionstore_context_owner;
REVOKE fashionstore_context_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;

GRANT fashionstore_sp_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON SCHEMA public TO fashionstore_sp_owner;
REVOKE ALL ON PROCEDURE public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint) TO fashionstore_api;
ALTER PROCEDURE public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint) OWNER TO fashionstore_sp_owner;
REVOKE ALL ON PROCEDURE public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text) TO fashionstore_api;
ALTER PROCEDURE public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text) OWNER TO fashionstore_sp_owner;
REVOKE ALL ON PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text) TO fashionstore_api;
ALTER PROCEDURE public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text) OWNER TO fashionstore_sp_owner;
REVOKE CREATE ON SCHEMA public FROM fashionstore_sp_owner;
REVOKE fashionstore_sp_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;

COMMIT;
