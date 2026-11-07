-- LABORATORIO 5CA — SP tenant con guardia de actor firmado.
-- Derivado del borrador database/saas/borradores/12_sp_crear_reserva_tenant.sql.txt.
-- DESVIACIÓN DECLARADA: el borrador declara un PROCEDURE con INOUT; aquí se
-- instala como FUNCTION que devuelve la reserva creada para poder invocarlo con
-- parámetros desde psycopg y medir concurrencia real. La guardia, el owner
-- NOLOGIN y la SECURITY DEFINER son los mismos que exige la propuesta 050c.
BEGIN;

CREATE OR REPLACE FUNCTION public.sp_crear_reserva_tenant(
    p_empresa_id bigint,
    p_carrito_id bigint,
    p_usuario_id bigint,
    p_fecha_atencion timestamptz,
    p_observacion text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $sp$
DECLARE
    v_carrito record;
    v_detalle record;
    v_inventario record;
    v_total integer;
    v_validas integer;
    v_rol_id bigint;
    v_reserva_id bigint;
BEGIN
    -- Guardia obligatoria: los parámetros deben coincidir con el token firmado.
    IF NOT fashionstore_private.actor_coincide(p_empresa_id, p_usuario_id) THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_carrito_id IS NULL OR p_carrito_id <= 0 THEN
        RAISE EXCEPTION 'Carrito inválido' USING ERRCODE='22023';
    END IF;
    IF p_fecha_atencion IS NULL
       OR p_fecha_atencion <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'Fecha de atención inválida' USING ERRCODE='22007';
    END IF;

    SELECT ue.rol_id INTO v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id = ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id = ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id = ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id = e.id AND ss.estado = 'ACTIVA'
    JOIN public.plan_saas pl ON pl.id = ss.plan_saas_id
    WHERE ue.empresa_id = p_empresa_id AND ue.usuario_id = p_usuario_id
      AND ue.estado IS TRUE AND r.nombre = 'CLIENTE'
      AND pl.estado IS TRUE AND pl.permite_reservas IS TRUE
      AND (pg_catalog.clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento;
    -- HALLAZGO 5CA: el borrador usa "FOR SHARE OF ue, ss". PostgreSQL exige
    -- privilegio UPDATE para bloquear filas, así que el SP fallaba con
    -- "permission denied for table usuario_empresa" y obligaba a dar UPDATE
    -- sobre la membresía al owner del SP, violando mínimo privilegio.
    -- Se mantiene el control por identidad firmada sin bloqueo de fila.
    IF v_rol_id IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT ca.id, ca.cliente_id, ca.sucursal_id, ca.estado,
           ca.fecha_actualizacion
    INTO v_carrito
    FROM public.carrito ca
    JOIN public.cliente cl ON cl.id = ca.cliente_id
       AND cl.empresa_id = p_empresa_id AND cl.usuario_id = p_usuario_id
       AND cl.estado IS TRUE
    JOIN public.sucursal su ON su.id = ca.sucursal_id
       AND su.empresa_id = p_empresa_id AND su.estado IS TRUE
    WHERE ca.id = p_carrito_id AND ca.empresa_id = p_empresa_id
    FOR UPDATE OF ca;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Carrito no encontrado' USING ERRCODE='P0002';
    END IF;
    IF v_carrito.estado <> 'ACTIVO'
       OR v_carrito.fecha_actualizacion IS NULL
       OR v_carrito.fecha_actualizacion
          <= pg_catalog.clock_timestamp() - interval '2 hours' THEN
        RAISE EXCEPTION 'Carrito vencido o no activo' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.reserva re
               WHERE re.carrito_id = p_carrito_id) THEN
        RAISE EXCEPTION 'Carrito ya reservado' USING ERRCODE='23505';
    END IF;

    SELECT count(*) INTO v_total FROM public.detalle_carrito dc
    WHERE dc.carrito_id = p_carrito_id;
    IF v_total = 0 THEN
        RAISE EXCEPTION 'Carrito sin prendas' USING ERRCODE='23514';
    END IF;
    SELECT count(*) INTO v_validas
    FROM public.detalle_carrito dc
    JOIN public.inventario i ON i.id = dc.inventario_id
       AND i.empresa_id = p_empresa_id
    JOIN public.sucursal su ON su.id = i.sucursal_id
       AND su.empresa_id = p_empresa_id
       AND su.id = v_carrito.sucursal_id
    WHERE dc.carrito_id = p_carrito_id AND dc.empresa_id = p_empresa_id
      AND dc.cantidad > 0;
    IF v_total <> v_validas THEN
        RAISE EXCEPTION 'Líneas empresariales inconsistentes' USING ERRCODE='23514';
    END IF;

    INSERT INTO public.reserva (empresa_id, cliente_id, sucursal_id, carrito_id,
                                fecha_reserva, fecha_atencion, estado, observacion)
    VALUES (p_empresa_id, v_carrito.cliente_id, v_carrito.sucursal_id,
            p_carrito_id, pg_catalog.clock_timestamp(), p_fecha_atencion,
            'PENDIENTE', p_observacion)
    RETURNING id INTO v_reserva_id;

    FOR v_detalle IN
        SELECT dc.inventario_id, dc.cantidad
        FROM public.detalle_carrito dc
        WHERE dc.carrito_id = p_carrito_id AND dc.empresa_id = p_empresa_id
        ORDER BY dc.inventario_id
    LOOP
        SELECT i.stock_actual, i.stock_reservado
        INTO v_inventario
        FROM public.inventario i
        WHERE i.id = v_detalle.inventario_id
          AND i.empresa_id = p_empresa_id
          AND i.sucursal_id = v_carrito.sucursal_id
        FOR UPDATE OF i;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario no disponible' USING ERRCODE='23514';
        END IF;
        IF v_inventario.stock_actual - v_inventario.stock_reservado
           < v_detalle.cantidad THEN
            RAISE EXCEPTION 'Stock insuficiente' USING ERRCODE='23514';
        END IF;
        UPDATE public.inventario i
        SET stock_reservado = i.stock_reservado + v_detalle.cantidad,
            fecha_actualizacion = pg_catalog.clock_timestamp()
        WHERE i.id = v_detalle.inventario_id AND i.empresa_id = p_empresa_id
          AND i.sucursal_id = v_carrito.sucursal_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Stock cambiado concurrentemente' USING ERRCODE='23514';
        END IF;
        INSERT INTO public.detalle_reserva (empresa_id, reserva_id,
                                            inventario_id, cantidad)
        VALUES (p_empresa_id, v_reserva_id, v_detalle.inventario_id,
                v_detalle.cantidad);
        INSERT INTO public.movimiento_inventario (empresa_id, inventario_id,
                usuario_id, tipo, cantidad, fecha_hora, observaciones,
                referencia_tipo, referencia_id)
        VALUES (p_empresa_id, v_detalle.inventario_id, p_usuario_id, 'RESERVA',
                v_detalle.cantidad, pg_catalog.clock_timestamp(),
                'Stock reservado para cliente', 'RESERVA', v_reserva_id);
    END LOOP;

    UPDATE public.carrito ca
    SET estado = 'CONVERTIDO', fecha_actualizacion = pg_catalog.clock_timestamp()
    WHERE ca.id = p_carrito_id AND ca.empresa_id = p_empresa_id
      AND ca.cliente_id = v_carrito.cliente_id AND ca.estado = 'ACTIVO';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Carrito modificado concurrentemente' USING ERRCODE='23514';
    END IF;
    RETURN v_reserva_id;
END;
$sp$;

REVOKE ALL ON FUNCTION public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text)
  FROM PUBLIC;
ALTER FUNCTION public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text)
  OWNER TO fashionstore_sp_owner;
GRANT EXECUTE ON FUNCTION public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text)
  TO fashionstore_api;

COMMIT;
