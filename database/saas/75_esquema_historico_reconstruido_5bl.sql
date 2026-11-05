-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA D (5BL)
-- RECONSTRUCCION DEL ESQUEMA HISTORICO DEL PRIMER PARCIAL: TRIGGERS, SP Y VISTA
--
-- Procedencia (SOLO LECTURA, auditorias locales ya ejecutadas en el primer parcial)
-- -----------------------------------------------------------------------------
-- * Cuerpos de las seis funciones de trigger y catalogo de disparadores:
--   auditoria 5K sobre 127.0.0.1:5432 (export pg_get_functiondef, ROLLBACK).
-- * Cuerpo de fn_completar_ip_bitacora: SQL_Correccion_Bitacora_IP.sql del repo.
-- * Cuerpos completos de los tres procedimientos CU16/CU18: extraccion 5F
--   (pg_get_functiondef, ROLLBACK).
-- * Inventario de estructura/restricciones/triggers: auditoria 5AD.
--
-- FIDELIDAD
-- ---------
-- El origen NO es un respaldo ni un pg_dump del esquema real: no existe respaldo
-- local accesible en este equipo. Esta reconstruccion reproduce LITERALMENTE los
-- cuerpos y disparadores documentados (9 funciones, 14 disparadores de 27
-- declarados, 3 de 24 rutinas) y RECONSTRUYE por comportamiento documentado las
-- dos funciones de proteccion de detalles y la vista. Las tablas provienen del
-- mapeo ORM (40 de 41 tablas historicas), que es lo mismo que uso la Etapa B.
-- Por tanto la validacion derivada es PARCIAL respecto del esquema real.
--
-- USO
-- ---
-- Exclusivamente en el cluster desechable del laboratorio 5BL. El preflight
-- aborta si no se declara el marcador de sesion y una base descartable.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $preflight$
BEGIN
  IF COALESCE(current_setting('saas.lab_5bl', TRUE), '') <> 'true' THEN
    RAISE EXCEPTION
      '5BL: reconstruccion historica solo para laboratorio. Ejecute SET saas.lab_5bl = ''true'' en la misma sesion';
  END IF;
  IF current_database() !~ '(lab|staging)' THEN
    RAISE EXCEPTION
      '5BL: la base % no parece descartable (nombre con lab/staging)', current_database();
  END IF;
  IF to_regclass('public.usuario') IS NULL
     OR to_regclass('public.bitacora') IS NULL
     OR to_regclass('public.inventario') IS NULL THEN
    RAISE EXCEPTION '5BL: faltan las tablas historicas base; aplique esto sobre la reconstruccion, no sobre una base vacia';
  END IF;
END;
$preflight$;

-- ===========================================================================
-- 1. Funciones de trigger del primer parcial (cuerpos auditados)
-- ===========================================================================

CREATE OR REPLACE FUNCTION public.fn_actualizar_actividad_carrito()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_carrito_id bigint;
BEGIN
    v_carrito_id := COALESCE(NEW.carrito_id, OLD.carrito_id);

    UPDATE public.carrito
    SET fecha_actualizacion = NOW()
    WHERE id = v_carrito_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_controlar_actualizacion_inventario()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
    IF NEW.sucursal_id IS DISTINCT FROM OLD.sucursal_id
       OR NEW.variante_producto_id IS DISTINCT FROM OLD.variante_producto_id
       OR NEW.temporada_id IS DISTINCT FROM OLD.temporada_id THEN
        RAISE EXCEPTION 'No se puede cambiar la sucursal, variante o temporada de un inventario existente';
    END IF;
    IF NEW.stock_actual IS DISTINCT FROM OLD.stock_actual
       OR NEW.stock_reservado IS DISTINCT FROM OLD.stock_reservado THEN
        NEW.fecha_actualizacion := CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_normalizar_correo_usuario()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
    IF NEW.correo IS NULL OR BTRIM(NEW.correo) = '' THEN
        RAISE EXCEPTION 'El correo del usuario no puede estar vacio';
    END IF;
    NEW.correo := LOWER(BTRIM(NEW.correo));
    RETURN NEW;
END;
$function$;

-- Cuerpo historico del primer parcial, ANTERIOR a la columna empresa_id.
-- Reconstruido a partir del cuerpo auditado en 5K (que ya incluia la proyeccion
-- de tenant del sandbox migrado) retirando esa proyeccion, que no existia antes
-- de la migracion 20261030000200. La version tenant-aware se instala en 00500.
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
 v_usuario_text TEXT;
 v_usuario_id BIGINT;
 v_registro_id TEXT;
 v_accion TEXT;
BEGIN
 v_usuario_text := current_setting('app.usuario_id',TRUE);
 IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN
   v_usuario_id := v_usuario_text::BIGINT;
 ELSE
   v_usuario_id := NULL;
 END IF;
 CASE TG_OP
   WHEN 'INSERT' THEN v_accion:='CREAR'; v_registro_id:=NEW.id::TEXT;
   WHEN 'UPDATE' THEN v_accion:='MODIFICAR'; v_registro_id:=NEW.id::TEXT;
   WHEN 'DELETE' THEN v_accion:='ELIMINAR'; v_registro_id:=OLD.id::TEXT;
   ELSE v_accion:=TG_OP;
 END CASE;
 INSERT INTO public.bitacora
   (usuario_id,fecha_hora,accion,entidad_afectada,descripcion)
 VALUES
   (v_usuario_id,CURRENT_TIMESTAMP,v_accion,TG_TABLE_NAME,
    format('Operacion %s sobre registro ID %s',v_accion,v_registro_id));
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_validar_detalle_reserva_sucursal()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_sucursal_reserva BIGINT;
    v_sucursal_inventario BIGINT;
BEGIN
    SELECT sucursal_id INTO v_sucursal_reserva FROM reserva WHERE id = NEW.reserva_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'La reserva % no existe', NEW.reserva_id;
    END IF;
    SELECT sucursal_id INTO v_sucursal_inventario FROM inventario WHERE id = NEW.inventario_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'El inventario % no existe', NEW.inventario_id;
    END IF;
    IF v_sucursal_reserva <> v_sucursal_inventario THEN
        RAISE EXCEPTION 'El inventario % no pertenece a la sucursal de la reserva %', NEW.inventario_id, NEW.reserva_id;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_validar_sucursal_detalle_carrito()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_sucursal_carrito bigint;
    v_sucursal_inventario bigint;
BEGIN
    SELECT sucursal_id
    INTO v_sucursal_carrito
    FROM public.carrito
    WHERE id = NEW.carrito_id;

    SELECT sucursal_id
    INTO v_sucursal_inventario
    FROM public.inventario
    WHERE id = NEW.inventario_id;

    IF v_sucursal_carrito IS NULL THEN
        RAISE EXCEPTION 'Carrito inexistente';
    END IF;

    IF v_sucursal_inventario IS NULL THEN
        RAISE EXCEPTION 'Inventario inexistente';
    END IF;

    IF v_sucursal_carrito <> v_sucursal_inventario THEN
        RAISE EXCEPTION
            'El inventario seleccionado no pertenece a la sucursal del carrito';
    END IF;

    RETURN NEW;
END;
$function$;

-- Version versionada en el repositorio (SQL_Correccion_Bitacora_IP.sql).
CREATE OR REPLACE FUNCTION public.fn_completar_ip_bitacora()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_ip TEXT;
BEGIN
    IF NEW.ip IS NULL OR btrim(NEW.ip) = '' THEN
        v_ip := NULLIF(current_setting('app.ip', TRUE), '');
        IF v_ip IS NOT NULL THEN
            NEW.ip := v_ip;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

-- Cuerpos NO capturados en las auditorias previas: se reconstruye el
-- comportamiento documentado en 02_plan_tenancy_y_datos.md (protegen detalles de
-- documentos comerciales ya cerrados). Se declaran como reconstruccion.
CREATE OR REPLACE FUNCTION public.fn_proteger_detalle_venta()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_estado TEXT;
BEGIN
    SELECT estado INTO v_estado
      FROM public.venta
     WHERE id = COALESCE(NEW.venta_id, OLD.venta_id);
    IF v_estado IN ('COMPLETADA', 'CANCELADA', 'REEMBOLSADA') THEN
        RAISE EXCEPTION
            'No se puede modificar el detalle de una venta en estado %', v_estado;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_proteger_detalle_orden_compra()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_estado TEXT;
BEGIN
    SELECT estado INTO v_estado
      FROM public.orden_compra
     WHERE id = COALESCE(NEW.orden_compra_id, OLD.orden_compra_id);
    IF v_estado IN ('RECIBIDA', 'CANCELADA') THEN
        RAISE EXCEPTION
            'No se puede modificar el detalle de una orden de compra en estado %', v_estado;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$function$;

-- ===========================================================================
-- 2. Disparadores documentados (14 de los 27 declarados en el esquema real)
-- ===========================================================================

DROP TRIGGER IF EXISTS trg_bitacora_cliente ON public.cliente;
CREATE TRIGGER trg_bitacora_cliente
AFTER INSERT OR DELETE OR UPDATE ON public.cliente
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_actualizar_actividad_carrito ON public.detalle_carrito;
CREATE TRIGGER trg_actualizar_actividad_carrito
AFTER INSERT OR DELETE OR UPDATE ON public.detalle_carrito
FOR EACH ROW EXECUTE FUNCTION public.fn_actualizar_actividad_carrito();

DROP TRIGGER IF EXISTS trg_validar_sucursal_detalle_carrito ON public.detalle_carrito;
CREATE TRIGGER trg_validar_sucursal_detalle_carrito
BEFORE INSERT OR UPDATE OF carrito_id, inventario_id ON public.detalle_carrito
FOR EACH ROW EXECUTE FUNCTION public.fn_validar_sucursal_detalle_carrito();

DROP TRIGGER IF EXISTS trg_validar_detalle_reserva_sucursal ON public.detalle_reserva;
CREATE TRIGGER trg_validar_detalle_reserva_sucursal
BEFORE INSERT OR UPDATE ON public.detalle_reserva
FOR EACH ROW EXECUTE FUNCTION public.fn_validar_detalle_reserva_sucursal();

DROP TRIGGER IF EXISTS trg_bitacora_inventario ON public.inventario;
CREATE TRIGGER trg_bitacora_inventario
AFTER INSERT OR DELETE OR UPDATE ON public.inventario
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_controlar_actualizacion_inventario ON public.inventario;
CREATE TRIGGER trg_controlar_actualizacion_inventario
BEFORE UPDATE ON public.inventario
FOR EACH ROW EXECUTE FUNCTION public.fn_controlar_actualizacion_inventario();

DROP TRIGGER IF EXISTS trg_bitacora_producto ON public.producto;
CREATE TRIGGER trg_bitacora_producto
AFTER INSERT OR DELETE OR UPDATE ON public.producto
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_bitacora_reserva ON public.reserva;
CREATE TRIGGER trg_bitacora_reserva
AFTER INSERT OR DELETE OR UPDATE ON public.reserva
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_bitacora_sucursal ON public.sucursal;
CREATE TRIGGER trg_bitacora_sucursal
AFTER INSERT OR DELETE OR UPDATE ON public.sucursal
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_bitacora_usuario ON public.usuario;
CREATE TRIGGER trg_bitacora_usuario
AFTER INSERT OR DELETE OR UPDATE ON public.usuario
FOR EACH ROW EXECUTE FUNCTION public.fn_registrar_bitacora();

DROP TRIGGER IF EXISTS trg_normalizar_correo_usuario ON public.usuario;
CREATE TRIGGER trg_normalizar_correo_usuario
BEFORE INSERT OR UPDATE ON public.usuario
FOR EACH ROW EXECUTE FUNCTION public.fn_normalizar_correo_usuario();

DROP TRIGGER IF EXISTS trg_bitacora_completar_ip ON public.bitacora;
CREATE TRIGGER trg_bitacora_completar_ip
BEFORE INSERT ON public.bitacora
FOR EACH ROW EXECUTE FUNCTION public.fn_completar_ip_bitacora();

DROP TRIGGER IF EXISTS trg_proteger_detalle_venta ON public.detalle_venta;
CREATE TRIGGER trg_proteger_detalle_venta
BEFORE UPDATE OR DELETE ON public.detalle_venta
FOR EACH ROW EXECUTE FUNCTION public.fn_proteger_detalle_venta();

DROP TRIGGER IF EXISTS trg_proteger_detalle_orden_compra ON public.detalle_orden_compra;
CREATE TRIGGER trg_proteger_detalle_orden_compra
BEFORE UPDATE OR DELETE ON public.detalle_orden_compra
FOR EACH ROW EXECUTE FUNCTION public.fn_proteger_detalle_orden_compra();

-- ===========================================================================
-- 3. Vista historica (definicion NO capturada; reconstruida por comportamiento)
--    security_invoker=true segun auditoria del primer parcial.
-- ===========================================================================

CREATE OR REPLACE VIEW public.vw_inventario_stock
WITH (security_invoker = true)
AS
SELECT i.id                         AS inventario_id,
       i.sucursal_id,
       s.nombre                     AS sucursal,
       i.variante_producto_id,
       vp.sku,
       vp.producto_id,
       p.nombre                     AS producto,
       i.temporada_id,
       i.stock_actual,
       i.stock_reservado,
       (i.stock_actual - i.stock_reservado) AS stock_disponible
  FROM public.inventario i
  JOIN public.sucursal s          ON s.id = i.sucursal_id
  JOIN public.variante_producto vp ON vp.id = i.variante_producto_id
  JOIN public.producto p          ON p.id = vp.producto_id;

-- ===========================================================================
-- 4. Procedimientos historicos CU16/CU18 (cuerpos auditados en 5F)
--    Se insertan desde la extraccion literal en el marcador siguiente.
-- ===========================================================================

CREATE OR REPLACE PROCEDURE public.sp_cancelar_reserva(IN p_reserva_id bigint, IN p_usuario_id bigint, IN p_observacion text)
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $procedure$
DECLARE
    v_reserva RECORD;
    v_detalle RECORD;
BEGIN
    SELECT * INTO v_reserva
    FROM reserva
    WHERE id = p_reserva_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'La reserva % no existe', p_reserva_id;
    END IF;

    IF v_reserva.estado NOT IN ('PENDIENTE', 'CONFIRMADA') THEN
        RAISE EXCEPTION 'La reserva % no puede cancelarse porque se encuentra en estado %', p_reserva_id, v_reserva.estado;
    END IF;

    FOR v_detalle IN
        SELECT dr.inventario_id, dr.cantidad
        FROM detalle_reserva dr
        WHERE dr.reserva_id = p_reserva_id
        ORDER BY dr.inventario_id
    LOOP
        PERFORM 1 FROM inventario WHERE id = v_detalle.inventario_id FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario % de la reserva no encontrado', v_detalle.inventario_id;
        END IF;

        IF (SELECT stock_reservado FROM inventario WHERE id = v_detalle.inventario_id) < v_detalle.cantidad THEN
            RAISE EXCEPTION 'Inconsistencia en stock reservado del inventario %', v_detalle.inventario_id;
        END IF;

        UPDATE inventario
        SET stock_reservado = stock_reservado - v_detalle.cantidad,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = v_detalle.inventario_id;

        INSERT INTO movimiento_inventario (
            inventario_id, usuario_id, tipo, cantidad, fecha_hora,
            observaciones, referencia_tipo, referencia_id
        ) VALUES (
            v_detalle.inventario_id, p_usuario_id, 'LIBERACION_RESERVA',
            v_detalle.cantidad, CURRENT_TIMESTAMP,
            COALESCE(p_observacion, 'Reserva cancelada'), 'RESERVA', p_reserva_id
        );
    END LOOP;

    UPDATE reserva
    SET estado = 'CANCELADA',
        observacion = CASE
            WHEN p_observacion IS NULL THEN observacion
            WHEN observacion IS NULL THEN p_observacion
            ELSE observacion || ' | ' || p_observacion
        END
    WHERE id = p_reserva_id;
END;
$procedure$;

CREATE OR REPLACE PROCEDURE public.sp_crear_reserva_desde_carrito(IN p_carrito_id bigint, IN p_fecha_atencion timestamp with time zone, IN p_observacion text, IN p_usuario_id bigint, INOUT p_reserva_id bigint)
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $procedure$

DECLARE

    v_carrito RECORD;

    v_detalle RECORD;

    v_inventario RECORD;

    v_stock_disponible INTEGER;

BEGIN

    ------------------------------------------------------------------

    -- 1. Validaciones b├ísicas

    ------------------------------------------------------------------



    IF p_fecha_atencion <= CURRENT_TIMESTAMP THEN

        RAISE EXCEPTION

            'La fecha de atenci├│n debe ser posterior a la fecha actual';

    END IF;



    ------------------------------------------------------------------

    -- 2. Bloquear carrito para impedir doble conversi├│n concurrente

    ------------------------------------------------------------------



    SELECT

        c.id,

        c.cliente_id,

        c.sucursal_id,

        c.estado,

        c.fecha_actualizacion

    INTO v_carrito

    FROM carrito c

    WHERE c.id = p_carrito_id

    FOR UPDATE;



    IF NOT FOUND THEN

        RAISE EXCEPTION 'El carrito % no existe', p_carrito_id;

    END IF;



    IF v_carrito.estado <> 'ACTIVO' THEN

        RAISE EXCEPTION

            'El carrito % no se encuentra activo', p_carrito_id;

    END IF;



    ------------------------------------------------------------------

    -- 3. Validar cliente y sucursal

    ------------------------------------------------------------------



    IF NOT EXISTS (

        SELECT 1

        FROM cliente

        WHERE id = v_carrito.cliente_id

          AND estado = TRUE

    ) THEN

        RAISE EXCEPTION

            'El cliente asociado al carrito no existe o est├í inactivo';

    END IF;



    IF NOT EXISTS (

        SELECT 1

        FROM sucursal

        WHERE id = v_carrito.sucursal_id

          AND estado = TRUE

    ) THEN

        RAISE EXCEPTION

            'La sucursal asociada al carrito no existe o est├í inactiva';

    END IF;



    ------------------------------------------------------------------

    -- 4. Validar que tenga prendas

    ------------------------------------------------------------------



    IF NOT EXISTS (

        SELECT 1

        FROM detalle_carrito

        WHERE carrito_id = p_carrito_id

    ) THEN

        RAISE EXCEPTION

            'El carrito no contiene prendas para reservar';

    END IF;



    ------------------------------------------------------------------

    -- 5. Crear reserva vinculada al carrito

    ------------------------------------------------------------------



    INSERT INTO reserva (

        cliente_id,

        sucursal_id,

        carrito_id,

        fecha_reserva,

        fecha_atencion,

        estado,

        observacion

    )

    VALUES (

        v_carrito.cliente_id,

        v_carrito.sucursal_id,

        p_carrito_id,

        CURRENT_TIMESTAMP,

        p_fecha_atencion,

        'PENDIENTE',

        p_observacion

    )

    RETURNING id INTO p_reserva_id;



    ------------------------------------------------------------------

    -- 6. Procesar las prendas reales del carrito

    ------------------------------------------------------------------



    FOR v_detalle IN

        SELECT

            dc.inventario_id,

            dc.cantidad

        FROM detalle_carrito dc

        WHERE dc.carrito_id = p_carrito_id

        ORDER BY dc.inventario_id

    LOOP



        IF v_detalle.cantidad IS NULL OR v_detalle.cantidad <= 0 THEN

            RAISE EXCEPTION

                'Existe una cantidad inv├ílida en el carrito';

        END IF;



        --------------------------------------------------------------

        -- Bloqueo de inventario para concurrencia

        --------------------------------------------------------------



        SELECT

            i.id,

            i.sucursal_id,

            i.stock_actual,

            i.stock_reservado

        INTO v_inventario

        FROM inventario i

        WHERE i.id = v_detalle.inventario_id

        FOR UPDATE;



        IF NOT FOUND THEN

            RAISE EXCEPTION

                'No existe el inventario %',

                v_detalle.inventario_id;

        END IF;



        IF v_inventario.sucursal_id <> v_carrito.sucursal_id THEN

            RAISE EXCEPTION

                'El inventario % no pertenece a la sucursal del carrito',

                v_detalle.inventario_id;

        END IF;



        v_stock_disponible :=

            v_inventario.stock_actual

            - v_inventario.stock_reservado;



        IF v_stock_disponible < v_detalle.cantidad THEN

            RAISE EXCEPTION

                'Stock insuficiente para inventario %. Disponible: %, solicitado: %',

                v_detalle.inventario_id,

                v_stock_disponible,

                v_detalle.cantidad;

        END IF;



        --------------------------------------------------------------

        -- Reservar stock

        --------------------------------------------------------------



        UPDATE inventario

        SET

            stock_reservado =

                stock_reservado + v_detalle.cantidad,

            fecha_actualizacion = CURRENT_TIMESTAMP

        WHERE id = v_detalle.inventario_id;



        --------------------------------------------------------------

        -- Detalle de reserva

        --------------------------------------------------------------



        INSERT INTO detalle_reserva (

            reserva_id,

            inventario_id,

            cantidad

        )

        VALUES (

            p_reserva_id,

            v_detalle.inventario_id,

            v_detalle.cantidad

        );



        --------------------------------------------------------------

        -- Kardex

        --------------------------------------------------------------



        INSERT INTO movimiento_inventario (

            inventario_id,

            usuario_id,

            tipo,

            cantidad,

            fecha_hora,

            observaciones,

            referencia_tipo,

            referencia_id

        )

        VALUES (

            v_detalle.inventario_id,

            p_usuario_id,

            'RESERVA',

            v_detalle.cantidad,

            CURRENT_TIMESTAMP,

            'Stock reservado para cliente',

            'RESERVA',

            p_reserva_id

        );



    END LOOP;



    ------------------------------------------------------------------

    -- 7. Carrito consumido por la reserva

    ------------------------------------------------------------------



    UPDATE carrito

    SET

        estado = 'CONVERTIDO',

        fecha_actualizacion = CURRENT_TIMESTAMP

    WHERE id = p_carrito_id;



END;

$procedure$;

CREATE OR REPLACE PROCEDURE public.sp_finalizar_reserva_sin_compra(IN p_reserva_id bigint, IN p_usuario_id bigint, IN p_observacion text)
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $procedure$

DECLARE

    v_reserva RECORD;

    v_detalle RECORD;

    v_stock_reservado INTEGER;

BEGIN

    -- 1. Bloquear la reserva para evitar que otro proceso

    --    la modifique al mismo tiempo.

    SELECT *

    INTO v_reserva

    FROM reserva

    WHERE id = p_reserva_id

    FOR UPDATE;



    IF NOT FOUND THEN

        RAISE EXCEPTION 'La reserva % no existe', p_reserva_id;

    END IF;



    -- 2. CU18 solo puede finalizar sin compra

    --    una reserva previamente CONFIRMADA.

    IF v_reserva.estado <> 'CONFIRMADA' THEN

        RAISE EXCEPTION

            'La reserva % no puede finalizarse sin compra porque se encuentra en estado %',

            p_reserva_id,

            v_reserva.estado;

    END IF;



    -- 3. La reserva debe tener prendas asociadas.

    IF NOT EXISTS (

        SELECT 1

        FROM detalle_reserva

        WHERE reserva_id = p_reserva_id

    ) THEN

        RAISE EXCEPTION

            'La reserva % no contiene prendas reservadas',

            p_reserva_id;

    END IF;



    -- 4. Recorrer todas las prendas reservadas.

    --    El ORDER BY mantiene siempre el mismo orden de bloqueo.

    FOR v_detalle IN

        SELECT

            dr.inventario_id,

            dr.cantidad

        FROM detalle_reserva dr

        WHERE dr.reserva_id = p_reserva_id

        ORDER BY dr.inventario_id

    LOOP

        -- Bloquear inventario.

        SELECT stock_reservado

        INTO v_stock_reservado

        FROM inventario

        WHERE id = v_detalle.inventario_id

        FOR UPDATE;



        IF NOT FOUND THEN

            RAISE EXCEPTION

                'Inventario % de la reserva no encontrado',

                v_detalle.inventario_id;

        END IF;



        -- Validar consistencia antes de liberar.

        IF v_stock_reservado < v_detalle.cantidad THEN

            RAISE EXCEPTION

                'Inconsistencia en stock reservado del inventario %. Reservado: %, requerido para liberar: %',

                v_detalle.inventario_id,

                v_stock_reservado,

                v_detalle.cantidad;

        END IF;



        -- 5. Liberar ├║nicamente stock_reservado.

        --    stock_actual NO se modifica.

        UPDATE inventario

        SET

            stock_reservado = stock_reservado - v_detalle.cantidad,

            fecha_actualizacion = CURRENT_TIMESTAMP

        WHERE id = v_detalle.inventario_id;



        -- 6. Registrar trazabilidad.

        INSERT INTO movimiento_inventario (

            inventario_id,

            usuario_id,

            tipo,

            cantidad,

            fecha_hora,

            observaciones,

            referencia_tipo,

            referencia_id

        )

        VALUES (

            v_detalle.inventario_id,

            p_usuario_id,

            'LIBERACION_RESERVA',

            v_detalle.cantidad,

            CURRENT_TIMESTAMP,

            COALESCE(

                NULLIF(TRIM(p_observacion), ''),

                'Atenci├│n finalizada sin compra'

            ),

            'RESERVA',

            p_reserva_id

        );

    END LOOP;



    -- 7. La reserva s├¡ fue atendida, aunque el cliente no compr├│.

    UPDATE reserva

    SET

        estado = 'ATENDIDA',



        -- No sobrescribimos la observaci├│n original del cliente.

        -- Solo agregamos la nueva si realmente se recibi├│ una.

        observacion = CASE

            WHEN p_observacion IS NULL

                 OR TRIM(p_observacion) = ''

                THEN observacion



            WHEN observacion IS NULL

                 OR TRIM(observacion) = ''

                THEN p_observacion



            ELSE observacion || ' | ' || p_observacion

        END

    WHERE id = p_reserva_id;

END;

$procedure$;

-- El esquema real concede EXECUTE a PUBLIC en las rutinas examinadas. Se
-- reproduce para que la Etapa D demuestre que debe revocarse.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO PUBLIC;
GRANT EXECUTE ON ALL PROCEDURES IN SCHEMA public TO PUBLIC;

COMMIT;
