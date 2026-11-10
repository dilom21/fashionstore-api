-- CU12 (gestion/atencion de reservas) y CU13 (venta presencial + pago) SAAS.
-- PROPUESTA LOCAL. NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA.
-- PostgreSQL 17. Instalacion atomica; no habilita SAAS_RESERVAS_ENABLED ni
-- SAAS_VENTAS_ENABLED. Depende del paquete CU11 (080 reservas SaaS) ya
-- instalado y auditado.
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

 -- El paquete CU11 es prerrequisito: verificador de actor y SP de reservas.
 IF to_regprocedure('public.fs_verificar_actor_reserva(bigint,bigint)') IS NULL THEN
  RAISE EXCEPTION 'Falta fs_verificar_actor_reserva de CU11'; END IF;
 IF to_regprocedure('public.sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)') IS NULL
 OR to_regprocedure('public.sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)') IS NULL THEN
  RAISE EXCEPTION 'Faltan procedimientos CU11 de cancelacion/finalizacion'; END IF;

 IF to_regprocedure('public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)') IS NOT NULL
 OR to_regprocedure('public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)') IS NOT NULL
 OR to_regprocedure('fashionstore_private.actor_sucursal_id()') IS NOT NULL THEN
  RAISE EXCEPTION 'Objetos CU12/CU13 ya existen; detener y auditar'; END IF;

 IF EXISTS (
   SELECT 1 FROM pg_policies
   WHERE schemaname='public' AND policyname IN (
     'p_api_select_staff_cliente','p_api_select_staff_reserva',
     'p_api_select_staff_detalle_reserva','p_api_select_staff_venta',
     'p_api_select_staff_detalle_venta','p_api_select_staff_pago',
     'p_sp_tenant_detalle_venta','p_sp_tenant_pago')
 ) THEN
  RAISE EXCEPTION 'Policies CU12/CU13 ya existen; detener y auditar'; END IF;

 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner')
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole
        OR rolcreatedb OR rolinherit))
 OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api'
   AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb))
 OR (SELECT count(*) FROM pg_roles WHERE rolname IN
   ('fashionstore_context_owner','fashionstore_sp_owner','fashionstore_api'))<>3 THEN
  RAISE EXCEPTION 'Roles base ausentes o inseguros'; END IF;

 -- El instalador debe poder administrar ambos owners. En Supabase postgres
 -- NO es superusuario: recibe ADMIN OPTION de supabase_admin con SET FALSE.
 IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
    AND NOT (
      SELECT count(DISTINCT roleid)=2
      FROM pg_auth_members
      WHERE member=CURRENT_USER::regrole
        AND roleid IN ('fashionstore_context_owner'::regrole,
                       'fashionstore_sp_owner'::regrole)
        AND admin_option
    ) THEN
  RAISE EXCEPTION 'Instalador sin ADMIN OPTION sobre owners CU12/CU13'; END IF;

 -- Los CREATE de owner son estrictamente temporales. Si ya existieran, no
 -- deben retirarse a ciegas: detener y auditar.
 IF has_schema_privilege(
      'fashionstore_context_owner','fashionstore_private','CREATE')
 OR has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
  RAISE EXCEPTION 'Owners con CREATE preexistente; detener y auditar'; END IF;

 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN
   ('reserva','detalle_reserva','inventario','venta','detalle_venta','pago')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN
  RAISE EXCEPTION 'RLS/FORCE RLS incompleto en tablas CU12/CU13'; END IF;

 IF NOT EXISTS (
   SELECT 1 FROM pg_attribute a
   JOIN pg_class c ON c.oid=a.attrelid
   JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname='plan_saas'
     AND a.attname='permite_ventas' AND a.attnum>0 AND NOT a.attisdropped)
 OR NOT EXISTS (
   SELECT 1 FROM pg_attribute a
   JOIN pg_class c ON c.oid=a.attrelid
   JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname='plan_saas'
     AND a.attname='permite_reservas' AND a.attnum>0 AND NOT a.attisdropped) THEN
  RAISE EXCEPTION 'plan_saas sin permite_ventas/permite_reservas'; END IF;

 IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='venta' AND policyname='p_sp_tenant_venta')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='reserva' AND policyname='p_sp_tenant_reserva')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='inventario' AND policyname='p_sp_tenant_inventario')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='detalle_reserva' AND policyname='p_sp_tenant_detalle_reserva')
 OR NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
   AND tablename='movimiento_inventario' AND policyname='p_sp_tenant_movimiento_inventario') THEN
  RAISE EXCEPTION 'Policies RLS CU11 ausentes o incompatibles'; END IF;
END $pre$;

-- Sucursal efectiva del personal autenticado. Sólo la usa la API para
-- reforzar el alcance en lecturas; las escrituras siguen validando en cada SP.
CREATE OR REPLACE FUNCTION fashionstore_private.actor_sucursal_id()
RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT em.sucursal_id
 FROM public.empleado em
 JOIN public.sucursal su
   ON su.id=em.sucursal_id AND su.empresa_id=em.empresa_id
 WHERE em.empresa_id=fashionstore_private.actor_empresa_id()
   AND em.usuario_id=fashionstore_private.actor_usuario_id()
   AND em.estado IS TRUE AND su.estado IS TRUE
 LIMIT 1
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.actor_sucursal_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fashionstore_private.actor_sucursal_id()
 TO fashionstore_api,fashionstore_sp_owner;

-- CU12: PENDIENTE -> CONFIRMADA. No toca inventario ni detalle_reserva.
CREATE OR REPLACE PROCEDURE public.sp_confirmar_reserva_tenant(
    IN p_empresa_id bigint,
    IN p_reserva_id bigint,
    IN p_usuario_id bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_confirmar_reserva$
DECLARE
    v_rol text;
    v_rol_id bigint;
    v_reserva record;
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
      AND ue.estado IS TRUE AND pl.estado IS TRUE
      AND pl.permite_reservas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento
      AND r.nombre IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL');
    IF v_rol IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT re.estado,re.sucursal_id INTO v_reserva
    FROM public.reserva re
    JOIN public.sucursal su
      ON su.id=re.sucursal_id AND su.empresa_id=p_empresa_id
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
    FOR UPDATE OF re;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Reserva no encontrada' USING ERRCODE='P0002';
    END IF;

    -- RBAC empresarial obligatorio para ambos roles de gestion.
    IF NOT EXISTS (
        SELECT 1 FROM public.rol_funcion rf
        JOIN public.funcion f ON f.id=rf.funcion_id AND f.estado IS TRUE
        JOIN public.accion a ON a.id=rf.accion_id AND a.estado IS TRUE
        WHERE rf.rol_id=v_rol_id AND f.nombre='GESTIONAR_RESERVAS'
          AND a.nombre='EJECUTAR'
    ) THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    -- Compatibilidad con el primer parcial: ADMINISTRADOR tiene alcance
    -- empresarial aunque no tenga empleado. ENCARGADO_SUCURSAL sigue exigiendo
    -- empleado activo en la sucursal de la reserva.
    IF v_rol<>'ADMINISTRADOR' AND NOT EXISTS (
        SELECT 1 FROM public.empleado em
        WHERE em.empresa_id=p_empresa_id AND em.usuario_id=p_usuario_id
          AND em.estado IS TRUE
          AND em.sucursal_id=v_reserva.sucursal_id
    ) THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    IF v_reserva.estado<>'PENDIENTE' THEN
        RAISE EXCEPTION 'La reserva no puede confirmarse'
            USING ERRCODE='23514';
    END IF;

    UPDATE public.reserva re
    SET estado='CONFIRMADA'
    WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
      AND re.estado='PENDIENTE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Estado de reserva cambiado' USING ERRCODE='23514';
    END IF;
    -- Sin COMMIT interno. No modifica inventario ni stock_reservado.
END;
$sp_confirmar_reserva$;

-- CU13: crea venta PRESENCIAL PENDIENTE, directa o desde reserva CONFIRMADA.
-- No registra pago, no descuenta stock_actual y no libera stock_reservado.
CREATE OR REPLACE PROCEDURE public.sp_crear_venta_presencial_tenant(
    IN p_empresa_id bigint,
    IN p_usuario_id bigint,
    IN p_reserva_id bigint,
    IN p_cliente_id bigint,
    IN p_items jsonb,
    INOUT p_venta_id bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_crear_venta$
DECLARE
    v_rol text;
    v_rol_id bigint;
    v_empleado_id bigint;
    v_sucursal_empleado bigint;
    v_sucursal_id bigint;
    v_sucursales integer;
    v_cliente_id bigint;
    v_reserva_estado text;
    v_item record;
    v_inv record;
    v_cantidad_reservada integer;
    v_precio numeric(12,2);
    v_total numeric(12,2):=0;
    v_lineas integer:=0;
BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id<=0
       OR p_usuario_id IS NULL OR p_usuario_id<=0 THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_reserva_id IS NOT NULL AND p_reserva_id<=0 THEN
        RAISE EXCEPTION 'Reserva invalida' USING ERRCODE='22023';
    END IF;
    IF p_cliente_id IS NOT NULL AND p_cliente_id<=0 THEN
        RAISE EXCEPTION 'Cliente invalido' USING ERRCODE='22023';
    END IF;
    IF p_items IS NULL OR jsonb_typeof(p_items)<>'array' THEN
        RAISE EXCEPTION 'La venta debe incluir al menos una prenda'
            USING ERRCODE='22023';
    END IF;
    IF jsonb_array_length(p_items)=0 THEN
        RAISE EXCEPTION 'La venta debe incluir al menos una prenda'
            USING ERRCODE='22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) e
        WHERE jsonb_typeof(e)<>'object'
           OR (e->>'inventario_id') IS NULL
           OR (e->>'inventario_id') !~ '^[0-9]+$'
           OR (e->>'cantidad') IS NULL
           OR (e->>'cantidad') !~ '^[0-9]+$'
           OR (e->>'inventario_id')::bigint<=0
           OR (e->>'cantidad')::integer<=0
    ) THEN
        RAISE EXCEPTION 'Seleccion de venta invalida' USING ERRCODE='22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_items) e
        GROUP BY (e->>'inventario_id') HAVING count(*)>1
    ) THEN
        RAISE EXCEPTION 'Inventario duplicado en la seleccion'
            USING ERRCODE='22023';
    END IF;

    SELECT r.nombre,ue.rol_id INTO v_rol,v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
    JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
    WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
      AND ue.estado IS TRUE AND pl.estado IS TRUE
      AND pl.permite_ventas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento
      AND r.nombre IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO');
    IF v_rol IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT em.id,em.sucursal_id INTO v_empleado_id,v_sucursal_empleado
    FROM public.empleado em
    JOIN public.sucursal su
      ON su.id=em.sucursal_id AND su.empresa_id=em.empresa_id
    WHERE em.empresa_id=p_empresa_id AND em.usuario_id=p_usuario_id
      AND em.estado IS TRUE AND su.estado IS TRUE
    LIMIT 1;
    -- ADMINISTRADOR conserva alcance empresarial sin empleado (primer parcial).
    -- ENCARGADO_SUCURSAL y CAJERO siguen exigiendo empleado activo.
    IF v_rol<>'ADMINISTRADOR' AND v_empleado_id IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    IF p_reserva_id IS NOT NULL THEN
        IF p_cliente_id IS NOT NULL THEN
            RAISE EXCEPTION 'cliente_id no aplica a una venta desde reserva'
                USING ERRCODE='22023';
        END IF;
        SELECT re.cliente_id,re.sucursal_id,re.estado
        INTO v_cliente_id,v_sucursal_id,v_reserva_estado
        FROM public.reserva re
        JOIN public.sucursal su
          ON su.id=re.sucursal_id AND su.empresa_id=p_empresa_id
         AND su.estado IS TRUE
        WHERE re.id=p_reserva_id AND re.empresa_id=p_empresa_id
        FOR UPDATE OF re;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Reserva no encontrada' USING ERRCODE='P0002';
        END IF;
        IF v_reserva_estado<>'CONFIRMADA' THEN
            RAISE EXCEPTION 'La reserva no esta CONFIRMADA'
                USING ERRCODE='23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.venta ve
            WHERE ve.empresa_id=p_empresa_id AND ve.reserva_id=p_reserva_id
        ) THEN
            RAISE EXCEPTION 'La reserva ya tiene una venta'
                USING ERRCODE='23505';
        END IF;
        IF v_rol<>'ADMINISTRADOR'
           AND v_sucursal_id<>v_sucursal_empleado THEN
            RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
        END IF;
    ELSE
        v_cliente_id:=p_cliente_id;
        IF v_cliente_id IS NOT NULL THEN
            PERFORM 1 FROM public.cliente cl
            WHERE cl.id=v_cliente_id AND cl.empresa_id=p_empresa_id
              AND cl.estado IS TRUE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Cliente no encontrado' USING ERRCODE='P0002';
            END IF;
        END IF;
        SELECT count(DISTINCT i.sucursal_id),min(i.sucursal_id)
        INTO v_sucursales,v_sucursal_id
        FROM jsonb_array_elements(p_items) e
        JOIN public.inventario i
          ON i.id=(e->>'inventario_id')::bigint
         AND i.empresa_id=p_empresa_id;
        IF v_sucursales<>1 THEN
            RAISE EXCEPTION 'Inventario no disponible o de varias sucursales'
                USING ERRCODE='23514';
        END IF;
        IF v_rol<>'ADMINISTRADOR'
           AND v_sucursal_id<>v_sucursal_empleado THEN
            RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
        END IF;
    END IF;

    INSERT INTO public.venta (
        empresa_id,cliente_id,empleado_id,sucursal_id,reserva_id,
        fecha_hora,canal,estado,total,carrito_id
    ) VALUES (
        p_empresa_id,v_cliente_id,v_empleado_id,v_sucursal_id,p_reserva_id,
        clock_timestamp(),'PRESENCIAL','PENDIENTE',0,NULL
    ) RETURNING id INTO p_venta_id;

    FOR v_item IN
        SELECT (e->>'inventario_id')::bigint AS inventario_id,
               (e->>'cantidad')::integer AS cantidad
        FROM jsonb_array_elements(p_items) e
        ORDER BY (e->>'inventario_id')::bigint
    LOOP
        SELECT i.stock_actual,i.stock_reservado,i.sucursal_id
        INTO v_inv
        FROM public.inventario i
        JOIN public.variante_producto vp
          ON vp.id=i.variante_producto_id
         AND vp.empresa_id=p_empresa_id AND vp.estado IS TRUE
        JOIN public.producto pr
          ON pr.id=vp.producto_id
         AND pr.empresa_id=p_empresa_id AND pr.estado IS TRUE
        JOIN public.temporada te
          ON te.id=i.temporada_id
         AND te.empresa_id=p_empresa_id AND te.estado IS TRUE
        JOIN public.sucursal su
          ON su.id=i.sucursal_id
         AND su.empresa_id=p_empresa_id AND su.estado IS TRUE
        WHERE i.id=v_item.inventario_id AND i.empresa_id=p_empresa_id
        FOR UPDATE OF i;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario no disponible' USING ERRCODE='23514';
        END IF;
        IF v_inv.sucursal_id<>v_sucursal_id THEN
            RAISE EXCEPTION 'Inventario de otra sucursal' USING ERRCODE='23514';
        END IF;

        IF p_reserva_id IS NOT NULL THEN
            SELECT COALESCE((
                SELECT dr.cantidad FROM public.detalle_reserva dr
                WHERE dr.empresa_id=p_empresa_id
                  AND dr.reserva_id=p_reserva_id
                  AND dr.inventario_id=v_item.inventario_id
            ),0) INTO v_cantidad_reservada;
            IF v_cantidad_reservada<v_item.cantidad THEN
                RAISE EXCEPTION 'La seleccion excede lo reservado'
                    USING ERRCODE='23514';
            END IF;
            IF v_inv.stock_reservado<v_item.cantidad THEN
                RAISE EXCEPTION 'Stock reservado inconsistente'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            IF v_item.cantidad>v_inv.stock_actual-v_inv.stock_reservado THEN
                RAISE EXCEPTION 'Stock insuficiente' USING ERRCODE='23514';
            END IF;
        END IF;

        SELECT pr.precio INTO v_precio
        FROM public.variante_producto vp
        JOIN public.producto pr
          ON pr.id=vp.producto_id AND pr.empresa_id=p_empresa_id
        WHERE vp.id=(
            SELECT i2.variante_producto_id FROM public.inventario i2
            WHERE i2.id=v_item.inventario_id AND i2.empresa_id=p_empresa_id
        );
        IF v_precio IS NULL OR v_precio<0 THEN
            RAISE EXCEPTION 'Precio invalido' USING ERRCODE='23514';
        END IF;

        INSERT INTO public.detalle_venta (
            empresa_id,venta_id,inventario_id,cantidad,precio_unitario
        ) VALUES (
            p_empresa_id,p_venta_id,v_item.inventario_id,
            v_item.cantidad,v_precio
        );
        v_total:=v_total+(v_precio*v_item.cantidad);
        v_lineas:=v_lineas+1;
    END LOOP;

    IF v_lineas=0 THEN
        RAISE EXCEPTION 'La venta debe incluir al menos una prenda'
            USING ERRCODE='22023';
    END IF;

    UPDATE public.venta ve
    SET total=v_total
    WHERE ve.id=p_venta_id AND ve.empresa_id=p_empresa_id
      AND ve.estado='PENDIENTE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No se pudo registrar la venta' USING ERRCODE='23514';
    END IF;
    -- Sin COMMIT interno. La venta queda PENDIENTE y sin pago.
END;
$sp_crear_venta$;

-- CU13: registra pago APROBADO y confirma la venta en una sola transaccion.
-- Consume/libera stock_reservado, descuenta stock_actual, marca la reserva
-- ATENDIDA y deja la venta COMPLETADA. Sin COMMIT interno.
CREATE OR REPLACE PROCEDURE public.sp_registrar_pago_presencial_tenant(
    IN p_empresa_id bigint,
    IN p_venta_id bigint,
    IN p_usuario_id bigint,
    IN p_metodo text,
    INOUT p_pago_id bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $sp_pago$
DECLARE
    v_rol text;
    v_rol_id bigint;
    v_empleado_id bigint;
    v_sucursal_empleado bigint;
    v_metodo text;
    v_venta record;
    v_reserva record;
    v_detalle record;
    v_inv record;
    v_reservada integer;
    v_consumo integer;
    v_directa integer;
    v_vendido integer;
    v_liberar integer;
    v_total numeric(12,2);
BEGIN
    IF public.fs_verificar_actor_reserva(
        p_empresa_id, p_usuario_id
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id<=0
       OR p_venta_id IS NULL OR p_venta_id<=0
       OR p_usuario_id IS NULL OR p_usuario_id<=0 THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    v_metodo:=upper(btrim(coalesce(p_metodo,'')));
    IF v_metodo NOT IN ('EFECTIVO','TARJETA','TRANSFERENCIA','QR','OTRO') THEN
        RAISE EXCEPTION 'Metodo de pago no permitido' USING ERRCODE='22023';
    END IF;

    SELECT r.nombre,ue.rol_id INTO v_rol,v_rol_id
    FROM public.usuario_empresa ue
    JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
    JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
    JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
    JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
    JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
    WHERE ue.empresa_id=p_empresa_id AND ue.usuario_id=p_usuario_id
      AND ue.estado IS TRUE AND pl.estado IS TRUE
      AND pl.permite_ventas IS TRUE
      AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
          BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento
      AND r.nombre IN ('ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO');
    IF v_rol IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT em.id,em.sucursal_id INTO v_empleado_id,v_sucursal_empleado
    FROM public.empleado em
    JOIN public.sucursal su
      ON su.id=em.sucursal_id AND su.empresa_id=em.empresa_id
    WHERE em.empresa_id=p_empresa_id AND em.usuario_id=p_usuario_id
      AND em.estado IS TRUE AND su.estado IS TRUE
    LIMIT 1;
    -- ADMINISTRADOR conserva alcance empresarial sin empleado (primer parcial).
    -- ENCARGADO_SUCURSAL y CAJERO siguen exigiendo empleado activo.
    IF v_rol<>'ADMINISTRADOR' AND v_empleado_id IS NULL THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;

    SELECT ve.id,ve.sucursal_id,ve.reserva_id,ve.canal,ve.estado,ve.total
    INTO v_venta
    FROM public.venta ve
    JOIN public.sucursal su
      ON su.id=ve.sucursal_id AND su.empresa_id=p_empresa_id
     AND su.estado IS TRUE
    WHERE ve.id=p_venta_id AND ve.empresa_id=p_empresa_id
    FOR UPDATE OF ve;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Venta no encontrada' USING ERRCODE='P0002';
    END IF;
    IF v_venta.canal<>'PRESENCIAL' THEN
        RAISE EXCEPTION 'La venta no es presencial' USING ERRCODE='23514';
    END IF;
    IF v_venta.estado<>'PENDIENTE' THEN
        RAISE EXCEPTION 'La venta no esta pendiente de pago'
            USING ERRCODE='23514';
    END IF;
    IF v_rol<>'ADMINISTRADOR'
       AND v_venta.sucursal_id<>v_sucursal_empleado THEN
        RAISE EXCEPTION 'Acceso no autorizado' USING ERRCODE='42501';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.pago pa
        WHERE pa.empresa_id=p_empresa_id AND pa.venta_id=p_venta_id
          AND pa.estado='APROBADO'
    ) THEN
        RAISE EXCEPTION 'La venta ya tiene un pago aprobado'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.detalle_venta dv
        WHERE dv.empresa_id=p_empresa_id AND dv.venta_id=p_venta_id
    ) THEN
        RAISE EXCEPTION 'La venta no contiene productos' USING ERRCODE='23514';
    END IF;
    IF v_venta.total IS NULL OR v_venta.total<=0 THEN
        RAISE EXCEPTION 'La venta no tiene total valido' USING ERRCODE='23514';
    END IF;

    IF v_venta.reserva_id IS NOT NULL THEN
        SELECT re.id,re.estado,re.sucursal_id INTO v_reserva
        FROM public.reserva re
        WHERE re.id=v_venta.reserva_id AND re.empresa_id=p_empresa_id
        FOR UPDATE OF re;
        IF NOT FOUND
           OR v_reserva.estado NOT IN ('PENDIENTE','CONFIRMADA')
           OR v_reserva.sucursal_id<>v_venta.sucursal_id THEN
            RAISE EXCEPTION 'La reserva asociada a la venta no es valida'
                USING ERRCODE='23514';
        END IF;
    END IF;

    INSERT INTO public.pago (
        empresa_id,venta_id,fecha_hora,monto,metodo,estado
    ) VALUES (
        p_empresa_id,p_venta_id,clock_timestamp(),v_venta.total,
        v_metodo,'APROBADO'
    ) RETURNING id INTO p_pago_id;

    FOR v_detalle IN
        SELECT dv.inventario_id,dv.cantidad
        FROM public.detalle_venta dv
        WHERE dv.empresa_id=p_empresa_id AND dv.venta_id=p_venta_id
        ORDER BY dv.inventario_id
    LOOP
        SELECT i.stock_actual,i.stock_reservado,i.sucursal_id INTO v_inv
        FROM public.inventario i
        WHERE i.id=v_detalle.inventario_id AND i.empresa_id=p_empresa_id
        FOR UPDATE OF i;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Inventario no encontrado' USING ERRCODE='23514';
        END IF;
        IF v_inv.sucursal_id<>v_venta.sucursal_id THEN
            RAISE EXCEPTION 'Inventario de otra sucursal' USING ERRCODE='23514';
        END IF;

        v_reservada:=0;
        IF v_venta.reserva_id IS NOT NULL THEN
            SELECT COALESCE((
                SELECT dr.cantidad FROM public.detalle_reserva dr
                WHERE dr.empresa_id=p_empresa_id
                  AND dr.reserva_id=v_venta.reserva_id
                  AND dr.inventario_id=v_detalle.inventario_id
            ),0) INTO v_reservada;
        END IF;
        v_consumo:=LEAST(v_reservada,v_detalle.cantidad);
        v_directa:=v_detalle.cantidad-v_consumo;

        IF v_inv.stock_actual<v_detalle.cantidad THEN
            RAISE EXCEPTION 'Stock fisico insuficiente' USING ERRCODE='23514';
        END IF;
        IF (v_inv.stock_actual-v_inv.stock_reservado)<v_directa THEN
            RAISE EXCEPTION 'Stock disponible insuficiente'
                USING ERRCODE='23514';
        END IF;
        IF v_inv.stock_reservado<v_consumo THEN
            RAISE EXCEPTION 'Stock reservado inconsistente'
                USING ERRCODE='23514';
        END IF;

        UPDATE public.inventario i
        SET stock_actual=i.stock_actual-v_detalle.cantidad,
            stock_reservado=i.stock_reservado-v_consumo,
            fecha_actualizacion=clock_timestamp()
        WHERE i.id=v_detalle.inventario_id AND i.empresa_id=p_empresa_id
          AND i.stock_actual>=v_detalle.cantidad
          AND i.stock_reservado>=v_consumo;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Stock cambiado concurrentemente'
                USING ERRCODE='23514';
        END IF;

        INSERT INTO public.movimiento_inventario (
            empresa_id,inventario_id,usuario_id,tipo,cantidad,fecha_hora,
            observaciones,referencia_tipo,referencia_id
        ) VALUES (
            p_empresa_id,v_detalle.inventario_id,p_usuario_id,
            'SALIDA_VENTA',v_detalle.cantidad,clock_timestamp(),
            'Salida de inventario por venta','VENTA',p_venta_id
        );
    END LOOP;

    IF v_venta.reserva_id IS NOT NULL THEN
        FOR v_detalle IN
            SELECT dr.inventario_id,dr.cantidad
            FROM public.detalle_reserva dr
            WHERE dr.empresa_id=p_empresa_id
              AND dr.reserva_id=v_venta.reserva_id
            ORDER BY dr.inventario_id
        LOOP
            SELECT COALESCE(SUM(dv.cantidad),0) INTO v_vendido
            FROM public.detalle_venta dv
            WHERE dv.empresa_id=p_empresa_id AND dv.venta_id=p_venta_id
              AND dv.inventario_id=v_detalle.inventario_id;
            v_consumo:=LEAST(v_detalle.cantidad,v_vendido);
            v_liberar:=v_detalle.cantidad-v_consumo;

            IF v_liberar>0 THEN
                SELECT i.stock_reservado INTO v_inv
                FROM public.inventario i
                WHERE i.id=v_detalle.inventario_id
                  AND i.empresa_id=p_empresa_id
                FOR UPDATE OF i;
                IF NOT FOUND OR v_inv.stock_reservado<v_liberar THEN
                    RAISE EXCEPTION 'Inconsistencia al liberar reservado'
                        USING ERRCODE='23514';
                END IF;

                UPDATE public.inventario i
                SET stock_reservado=i.stock_reservado-v_liberar,
                    fecha_actualizacion=clock_timestamp()
                WHERE i.id=v_detalle.inventario_id
                  AND i.empresa_id=p_empresa_id
                  AND i.stock_reservado>=v_liberar;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'Stock cambiado concurrentemente'
                        USING ERRCODE='23514';
                END IF;

                INSERT INTO public.movimiento_inventario (
                    empresa_id,inventario_id,usuario_id,tipo,cantidad,
                    fecha_hora,observaciones,referencia_tipo,referencia_id
                ) VALUES (
                    p_empresa_id,v_detalle.inventario_id,p_usuario_id,
                    'LIBERACION_RESERVA',v_liberar,clock_timestamp(),
                    'Prenda reservada no adquirida durante la venta',
                    'RESERVA',v_venta.reserva_id
                );
            END IF;
        END LOOP;

        UPDATE public.reserva re
        SET estado='ATENDIDA'
        WHERE re.id=v_venta.reserva_id AND re.empresa_id=p_empresa_id
          AND re.estado IN ('PENDIENTE','CONFIRMADA');
        IF NOT FOUND THEN
            RAISE EXCEPTION 'No se pudo atender la reserva'
                USING ERRCODE='23514';
        END IF;
    END IF;

    SELECT COALESCE(SUM(dv.cantidad*dv.precio_unitario),0)
    INTO v_total
    FROM public.detalle_venta dv
    WHERE dv.empresa_id=p_empresa_id AND dv.venta_id=p_venta_id;

    UPDATE public.venta ve
    SET total=v_total,estado='COMPLETADA'
    WHERE ve.id=p_venta_id AND ve.empresa_id=p_empresa_id
      AND ve.estado='PENDIENTE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No se pudo completar la venta' USING ERRCODE='23514';
    END IF;
END;
$sp_pago$;

-- Lecturas de personal: mismo tenant y, cuando la tabla tiene sucursal, sólo
-- la sucursal efectiva del empleado (ADMIN conserva alcance global).
-- El personal de sucursal necesita nombre/telefono del cliente de la reserva.
DROP POLICY IF EXISTS p_api_select_staff_cliente ON public.cliente;
CREATE POLICY p_api_select_staff_cliente ON public.cliente
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
 );

DROP POLICY IF EXISTS p_api_select_staff_reserva ON public.reserva;
CREATE POLICY p_api_select_staff_reserva ON public.reserva
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
  AND ((SELECT fashionstore_private.actor_es_admin())
       OR sucursal_id=(SELECT fashionstore_private.actor_sucursal_id()))
 );

DROP POLICY IF EXISTS p_api_select_staff_detalle_reserva ON public.detalle_reserva;
CREATE POLICY p_api_select_staff_detalle_reserva ON public.detalle_reserva
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
  AND EXISTS (
   SELECT 1 FROM public.reserva re
   WHERE re.id=detalle_reserva.reserva_id
     AND re.empresa_id=(SELECT fashionstore_private.actor_empresa_id())
     AND ((SELECT fashionstore_private.actor_es_admin())
          OR re.sucursal_id=(SELECT fashionstore_private.actor_sucursal_id()))
  )
 );

DROP POLICY IF EXISTS p_api_select_staff_venta ON public.venta;
CREATE POLICY p_api_select_staff_venta ON public.venta
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
  AND ((SELECT fashionstore_private.actor_es_admin())
       OR sucursal_id=(SELECT fashionstore_private.actor_sucursal_id()))
 );

DROP POLICY IF EXISTS p_api_select_staff_detalle_venta ON public.detalle_venta;
CREATE POLICY p_api_select_staff_detalle_venta ON public.detalle_venta
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
  AND EXISTS (
   SELECT 1 FROM public.venta ve
   WHERE ve.id=detalle_venta.venta_id
     AND ve.empresa_id=(SELECT fashionstore_private.actor_empresa_id())
     AND ((SELECT fashionstore_private.actor_es_admin())
          OR ve.sucursal_id=(SELECT fashionstore_private.actor_sucursal_id()))
  )
 );

DROP POLICY IF EXISTS p_api_select_staff_pago ON public.pago;
CREATE POLICY p_api_select_staff_pago ON public.pago
 FOR SELECT TO fashionstore_api USING (
  empresa_id=(SELECT fashionstore_private.actor_empresa_id())
  AND (SELECT fashionstore_private.actor_es_personal())
  AND EXISTS (
   SELECT 1 FROM public.venta ve
   WHERE ve.id=pago.venta_id
     AND ve.empresa_id=(SELECT fashionstore_private.actor_empresa_id())
     AND ((SELECT fashionstore_private.actor_es_admin())
          OR ve.sucursal_id=(SELECT fashionstore_private.actor_sucursal_id()))
  )
 );

-- Policies del owner NOLOGIN para las tablas nuevas del flujo de venta.
DROP POLICY IF EXISTS p_sp_tenant_detalle_venta ON public.detalle_venta;
CREATE POLICY p_sp_tenant_detalle_venta ON public.detalle_venta
 FOR ALL TO fashionstore_sp_owner
 USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))
 WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()));

DROP POLICY IF EXISTS p_sp_tenant_pago ON public.pago;
CREATE POLICY p_sp_tenant_pago ON public.pago
 FOR ALL TO fashionstore_sp_owner
 USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))
 WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()));

-- ACL minima del owner NOLOGIN. NO se concede DML a fashionstore_api.
GRANT SELECT,INSERT ON public.detalle_venta TO fashionstore_sp_owner;
GRANT SELECT,INSERT ON public.pago TO fashionstore_sp_owner;
GRANT INSERT,UPDATE ON public.venta TO fashionstore_sp_owner;
GRANT USAGE,SELECT ON SEQUENCE
 public.venta_id_seq,public.detalle_venta_id_seq,public.pago_id_seq
 TO fashionstore_sp_owner;

-- Marcador verificable (mismo patron que 080). Se escribe ANTES de transferir
-- ownership: después el instalador ya no es dueño de los objetos.
DO $marcar$
DECLARE r record; marca text:='fashionstore.cu12_cu13.090.v2';
BEGIN
 FOR r IN SELECT p.oid,p.prokind,p.oid::regprocedure firma,md5(p.prosrc) hash
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE (n.nspname='public' AND p.proname IN (
    'sp_confirmar_reserva_tenant','sp_crear_venta_presencial_tenant',
    'sp_registrar_pago_presencial_tenant'))
     OR (n.nspname='fashionstore_private'
         AND p.proname='actor_sucursal_id')
 LOOP
  EXECUTE format('COMMENT ON %s %s IS %L',
   CASE WHEN r.prokind='p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
   r.firma, marca||'|body_md5='||r.hash);
 END LOOP;
END $marcar$;

-- Ownership: solo se conceden temporalmente CREATE y SET, lo mínimo para
-- transferir. Las membresías preexistentes de postgres (ADMIN OPTION vía
-- supabase_admin, SET FALSE) se fotografían y se verifican sin modificarlas.
CREATE TEMP TABLE cu12_cu13_membresias_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid IN ('fashionstore_context_owner'::regrole,
                 'fashionstore_sp_owner'::regrole);

-- SET temporal seguro: PostgreSQL 17 permite multiples otorgantes de un rol.
-- Si ya existe una fila de CURRENT_USER, GRANT la actualiza en vez de crear
-- otra; por eso se fotografia su estado y se restaura con GRANT, nunca con
-- REVOKE. La concesion de supabase_admin no se toca jamás.
CREATE TEMP TABLE cu12_cu13_set_temporal (
    rol name PRIMARY KEY,
    existente boolean NOT NULL,
    admin_previo boolean,
    inherit_previo boolean,
    set_previo boolean
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu12_cu13_preparar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v_admin boolean;
    v_inherit boolean;
    v_set boolean;
BEGIN
    SELECT m.admin_option,m.inherit_option,m.set_option
    INTO v_admin,v_inherit,v_set
    FROM pg_auth_members m
    WHERE m.member=CURRENT_USER::regrole
      AND m.roleid=p_rol::regrole
      AND m.grantor=CURRENT_USER::regrole;

    IF FOUND THEN
        IF v_set IS NOT TRUE THEN
            EXECUTE format(
              'GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE',
              p_rol,
              CASE WHEN v_admin THEN 'ADMIN OPTION,' ELSE '' END,
              CASE WHEN v_inherit THEN 'TRUE' ELSE 'FALSE' END
            );
        END IF;
        INSERT INTO pg_temp.cu12_cu13_set_temporal(
            rol,existente,admin_previo,inherit_previo,set_previo
        ) VALUES (p_rol,true,v_admin,v_inherit,v_set)
        ON CONFLICT (rol) DO UPDATE SET
            existente=EXCLUDED.existente,
            admin_previo=EXCLUDED.admin_previo,
            inherit_previo=EXCLUDED.inherit_previo,
            set_previo=EXCLUDED.set_previo;
    ELSE
        EXECUTE format(
          'GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE', p_rol
        );
        INSERT INTO pg_temp.cu12_cu13_set_temporal(
            rol,existente,admin_previo,inherit_previo,set_previo
        ) VALUES (p_rol,false,NULL,NULL,NULL)
        ON CONFLICT (rol) DO UPDATE SET
            existente=EXCLUDED.existente,
            admin_previo=EXCLUDED.admin_previo,
            inherit_previo=EXCLUDED.inherit_previo,
            set_previo=EXCLUDED.set_previo;
    END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu12_cu13_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    v record;
BEGIN
    SELECT * INTO v FROM pg_temp.cu12_cu13_set_temporal WHERE rol=p_rol;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF v.existente THEN
        EXECUTE format(
          'GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s',
          p_rol,
          CASE WHEN v.admin_previo THEN 'ADMIN OPTION,' ELSE '' END,
          CASE WHEN v.inherit_previo THEN 'TRUE' ELSE 'FALSE' END,
          CASE WHEN v.set_previo THEN 'TRUE' ELSE 'FALSE' END
        );
    ELSE
        EXECUTE format(
          'REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER', p_rol
        );
    END IF;
END $fn$;

CREATE TEMP TABLE cu12_cu13_creates_temporales (
    rol name,
    esquema name,
    PRIMARY KEY (rol,esquema)
) ON COMMIT DROP;

DO $grant_create_context_owner$
BEGIN
 IF NOT has_schema_privilege(
      'fashionstore_context_owner','fashionstore_private','CREATE') THEN
  GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_context_owner;
  INSERT INTO cu12_cu13_creates_temporales(rol,esquema)
  VALUES ('fashionstore_context_owner','fashionstore_private');
 END IF;
END $grant_create_context_owner$;

DO $temp_set_context_owner$
BEGIN
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_context_owner','SET') THEN
  PERFORM pg_temp.cu12_cu13_preparar_set('fashionstore_context_owner');
 END IF;
END $temp_set_context_owner$;

ALTER FUNCTION fashionstore_private.actor_sucursal_id()
 OWNER TO fashionstore_context_owner;

DO $restore_set_context_owner$
BEGIN
 PERFORM pg_temp.cu12_cu13_restaurar_set('fashionstore_context_owner');
END $restore_set_context_owner$;

DO $revoke_create_context_owner$
BEGIN
 IF EXISTS (SELECT 1 FROM cu12_cu13_creates_temporales
            WHERE rol='fashionstore_context_owner'
              AND esquema='fashionstore_private') THEN
  REVOKE CREATE ON SCHEMA fashionstore_private
   FROM fashionstore_context_owner;
 END IF;
END $revoke_create_context_owner$;

DO $grant_create_sp_owner$
BEGIN
 IF NOT has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
  GRANT CREATE ON SCHEMA public TO fashionstore_sp_owner;
  INSERT INTO cu12_cu13_creates_temporales(rol,esquema)
  VALUES ('fashionstore_sp_owner','public');
 END IF;
END $grant_create_sp_owner$;

DO $temp_set_sp_owner$
BEGIN
 IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
  PERFORM pg_temp.cu12_cu13_preparar_set('fashionstore_sp_owner');
 END IF;
END $temp_set_sp_owner$;

REVOKE ALL ON PROCEDURE public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)
 FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)
 TO fashionstore_api;
ALTER PROCEDURE public.sp_confirmar_reserva_tenant(bigint,bigint,bigint)
 OWNER TO fashionstore_sp_owner;

REVOKE ALL ON PROCEDURE public.sp_crear_venta_presencial_tenant(
 bigint,bigint,bigint,bigint,jsonb,bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_crear_venta_presencial_tenant(
 bigint,bigint,bigint,bigint,jsonb,bigint) TO fashionstore_api;
ALTER PROCEDURE public.sp_crear_venta_presencial_tenant(
 bigint,bigint,bigint,bigint,jsonb,bigint) OWNER TO fashionstore_sp_owner;

REVOKE ALL ON PROCEDURE public.sp_registrar_pago_presencial_tenant(
 bigint,bigint,bigint,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.sp_registrar_pago_presencial_tenant(
 bigint,bigint,bigint,text,bigint) TO fashionstore_api;
ALTER PROCEDURE public.sp_registrar_pago_presencial_tenant(
 bigint,bigint,bigint,text,bigint) OWNER TO fashionstore_sp_owner;

DO $restore_set_sp_owner$
BEGIN
 PERFORM pg_temp.cu12_cu13_restaurar_set('fashionstore_sp_owner');
END $restore_set_sp_owner$;

DO $revoke_create_sp_owner$
BEGIN
 IF EXISTS (SELECT 1 FROM cu12_cu13_creates_temporales
            WHERE rol='fashionstore_sp_owner'
              AND esquema='public') THEN
  REVOKE CREATE ON SCHEMA public FROM fashionstore_sp_owner;
 END IF;
END $revoke_create_sp_owner$;

DO $verify_ownership_helpers$
BEGIN
 IF EXISTS (
   SELECT 1 FROM cu12_cu13_membresias_previas p
   WHERE NOT EXISTS (
     SELECT 1 FROM pg_auth_members m
     WHERE m.roleid=p.roleid AND m.member=p.member
       AND m.grantor=p.grantor
       AND m.admin_option=p.admin_option
       AND m.inherit_option=p.inherit_option
       AND m.set_option=p.set_option
   )
 ) THEN
  RAISE EXCEPTION 'Membresia preexistente alterada';
 END IF;

 IF EXISTS (
   SELECT 1 FROM pg_auth_members m
   WHERE m.member=CURRENT_USER::regrole
     AND m.roleid IN ('fashionstore_context_owner'::regrole,
                      'fashionstore_sp_owner'::regrole)
     AND NOT EXISTS (
       SELECT 1 FROM cu12_cu13_membresias_previas p
       WHERE p.roleid=m.roleid AND p.member=m.member
         AND p.grantor=m.grantor
     )
 ) THEN
  RAISE EXCEPTION 'Membresia temporal no revocada';
 END IF;

 IF has_schema_privilege(
      'fashionstore_context_owner','fashionstore_private','CREATE') THEN
  RAISE EXCEPTION 'CREATE residual en fashionstore_private';
 END IF;
 IF has_schema_privilege('fashionstore_sp_owner','public','CREATE') THEN
  RAISE EXCEPTION 'CREATE residual en public';
 END IF;
END $verify_ownership_helpers$;

COMMIT;
