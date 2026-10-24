-- FashionStore / BLOQUE 2 / PASO 7 - PRUEBAS LOCALES NO PERSISTENTES
-- EXCLUSIVAMENTE: fashionstore_restore_20261023_193554_594
-- NO ejecutar con Supabase ni habilitar SAAS_AUTH_ENABLED.
-- Las inserciones negativas estan dentro de bloques EXCEPTION y de una
-- transaccion que termina EXPRESAMENTE en ROLLBACK (nunca COMMIT).
-- Esta suite comprueba FK y datos, NO sustituye pruebas de autorizacion API.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '90s';
SET LOCAL TIME ZONE 'America/La_Paz';

DO $prueba_saas$
DECLARE
    v_vanter BIGINT;
    v_urban BIGINT;
    v_denim BIGINT;
    v_categoria_urban BIGINT;
    v_variante_urban BIGINT;
    v_sucursal_vanter BIGINT;
    v_temporada_vanter BIGINT;
    v_usuario_urban BIGINT;
    v_contraint TEXT;
    v_err_state TEXT;
    v_bloqueada BOOLEAN;
    v_total BIGINT;
    v_tabla TEXT;
    v_col_empresa TEXT[] := ARRAY[
        'bitacora','carrito','categoria','cliente','coleccion',
        'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
        'detalle_orden_compra','detalle_reserva','detalle_venta',
        'devolucion','empleado','inventario','movimiento_inventario',
        'orden_compra','pago','producto','producto_coleccion','promocion',
        'promocion_producto','proveedor','proveedor_producto',
        'prueba_vestidor_ar','recurso_producto','reserva',
        'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'
    ];
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
       RAISE EXCEPTION 'Destino no autorizado para pruebas: % en %',
         current_database(), host(inet_server_addr());
    END IF;

    SELECT id INTO STRICT v_vanter FROM public.empresa WHERE slug='vanter-men';
    SELECT id INTO STRICT v_urban FROM public.empresa WHERE slug='urbanwear';
    SELECT id INTO STRICT v_denim FROM public.empresa WHERE slug='denim-house';

    IF v_vanter=v_urban OR v_vanter=v_denim OR v_urban=v_denim THEN
       RAISE EXCEPTION 'Los tenants de prueba no tienen IDs distintos';
    END IF;
    IF (SELECT count(*) FROM public.empresa)<>5
      OR (SELECT count(*) FROM public.plan_saas)<>2
      OR (SELECT count(*) FROM public.suscripcion WHERE estado='ACTIVA')<>5
      OR (SELECT count(*) FROM public.usuario_empresa)<>13
      OR (SELECT count(*) FROM public.usuario)<>13
      OR (SELECT count(*) FROM public.sucursal)<>13
      OR (SELECT count(*) FROM public.cliente)<>9
      OR (SELECT count(*) FROM public.empleado)<>8
      OR (SELECT count(*) FROM public.producto)<>18
      OR (SELECT count(*) FROM public.variante_producto)<>124
      OR (SELECT count(*) FROM public.inventario)<>274
      OR (SELECT count(*) FROM public.venta)<>40
      OR (SELECT count(*) FROM public.pago)<>30
      OR (SELECT count(*) FROM public.reserva)<>11
      OR (SELECT count(*) FROM public.bitacora)<>844 THEN
      RAISE EXCEPTION 'El estado de las cinco empresas no coincide con el Paso 6';
    END IF;

    IF (SELECT count(*) FROM pg_constraint
          WHERE conname LIKE 'fk_tenant_%' AND contype='f' AND convalidated)<>48
      OR (SELECT count(*) FROM pg_constraint
          WHERE conname LIKE 'uq_tenant_%' AND contype='u' AND convalidated)<>28 THEN
      RAISE EXCEPTION 'No estan validadas las 48 FK y 28 UNIQUE tenant';
    END IF;

    IF (SELECT count(*) FROM pg_trigger tg
        JOIN pg_class cl ON cl.oid=tg.tgrelid
        JOIN pg_namespace ns ON ns.oid=cl.relnamespace
        WHERE ns.nspname='public' AND NOT tg.tgisinternal)<>27 THEN
      RAISE EXCEPTION 'No se conservaron los 27 triggers';
    END IF;

    IF (SELECT count(*) FROM pg_class cl
        JOIN pg_namespace ns ON ns.oid=cl.relnamespace
        WHERE ns.nspname='public' AND cl.relname IN
           ('empresa','plan_saas','suscripcion','usuario_empresa','empresa_configuracion')
        AND cl.relrowsecurity) <> 5 THEN
      RAISE EXCEPTION 'Falta RLS en alguna tabla base SaaS';
    END IF;

    -- Todos los registros comerciales deben tener un propietario valido.
    IF cardinality(v_col_empresa)<>31 OR
       (SELECT count(DISTINCT nombre) FROM unnest(v_col_empresa) AS t(nombre))<>31 THEN
      RAISE EXCEPTION 'Lista incompleta de tablas propias de tenant';
    END IF;
    FOREACH v_tabla IN ARRAY v_col_empresa LOOP
      EXECUTE format(
        'SELECT count(*) FROM public.%I AS t LEFT JOIN public.empresa e ' ||
        'ON e.id=t.empresa_id WHERE t.empresa_id IS NOT NULL AND e.id IS NULL',
        v_tabla
      ) INTO v_total;
      IF v_total<>0 THEN
        RAISE EXCEPTION 'Empresa inexistente en tabla %',v_tabla;
      END IF;
      IF v_tabla<>'bitacora' THEN
        EXECUTE format('SELECT count(*) FROM public.%I WHERE empresa_id IS NULL',v_tabla)
          INTO v_total;
        IF v_total<>0 THEN
          RAISE EXCEPTION 'Filas comerciales sin empresa_id en %',v_tabla;
        END IF;
      END IF;
    END LOOP;
    RAISE NOTICE 'OK 1/6: cinco tenants, 31 tablas, conteos historicos y FK validadas';

    IF EXISTS (
      SELECT 1 FROM public.suscripcion s
      JOIN public.plan_saas p ON p.id=s.plan_saas_id
      LEFT JOIN LATERAL (
        SELECT count(*) AS n FROM public.sucursal su
        WHERE su.empresa_id=s.empresa_id AND su.estado=TRUE
      ) x ON TRUE
      WHERE s.estado='ACTIVA' AND x.n>p.max_sucursales_activas
    ) THEN
      RAISE EXCEPTION 'Alguna empresa supera las sucursales de su plan';
    END IF;
    IF (SELECT count(*) FROM public.plan_saas
        WHERE codigo='BASICO' AND max_sucursales_activas=2
          AND permite_reportes_ia=FALSE)<>1
       OR (SELECT count(*) FROM public.plan_saas
        WHERE codigo='PROFESIONAL' AND max_sucursales_activas=10
          AND permite_reportes_ia=TRUE)<>1 THEN
      RAISE EXCEPTION 'Capacidades de planes no corresponden al examen';
    END IF;
    IF (SELECT count(*) FROM public.usuario u
        WHERE u.correo LIKE 'admin.%@demo.invalid' AND u.estado=FALSE)<>4
       OR (SELECT count(*) FROM public.usuario_empresa ue
        JOIN public.usuario u ON u.id=ue.usuario_id
        WHERE u.correo LIKE 'admin.%@demo.invalid' AND ue.estado=FALSE)<>4 THEN
      RAISE EXCEPTION 'Los cuatro administradores de demostracion no estan inactivos';
    END IF;
    IF (SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL)<>4 THEN
      RAISE EXCEPTION 'Eventos globales de auditoria inesperados';
    END IF;
    RAISE NOTICE 'OK 2/6: planes y suscripciones consistentes, admins demo inactivos';

    -- No usar IDs fijos. Datos existentes, sin modificar filas de Vanter Men.
    SELECT id INTO STRICT v_categoria_urban FROM public.categoria
       WHERE empresa_id=v_urban AND nombre='Moda Demo';
    SELECT id INTO STRICT v_variante_urban FROM public.variante_producto
       WHERE empresa_id=v_urban AND sku='URBAN-DEMO-001';
    SELECT id INTO STRICT v_sucursal_vanter FROM public.sucursal
       WHERE empresa_id=v_vanter ORDER BY id LIMIT 1;
    SELECT id INTO STRICT v_temporada_vanter FROM public.temporada
       WHERE empresa_id=v_vanter ORDER BY id LIMIT 1;
    SELECT u.id INTO STRICT v_usuario_urban FROM public.usuario u
       WHERE u.correo='admin.urbanwear@demo.invalid';

    -- Prueba negativa A: producto Vanter NO puede usar categoria UrbanWear.
    v_bloqueada:=FALSE;
    BEGIN
      INSERT INTO public.producto
        (empresa_id,categoria_id,nombre,precio,estado)
      VALUES (v_vanter,v_categoria_urban,'SI2-PRUEBA-CRUZADA-NO-GUARDAR',0,TRUE);
    EXCEPTION WHEN foreign_key_violation THEN
      GET STACKED DIAGNOSTICS v_contraint=CONSTRAINT_NAME, v_err_state=RETURNED_SQLSTATE;
      IF v_contraint='fk_tenant_fk_producto_categoria' AND v_err_state='23503' THEN
        v_bloqueada:=TRUE;
      ELSE
        RAISE EXCEPTION 'Rechazo por FK equivocada: % (SQLSTATE %)',
          v_contraint,v_err_state;
      END IF;
    END;
    IF NOT v_bloqueada THEN
      RAISE EXCEPTION 'FALLO AISLAMIENTO: producto Vanter acepto categoria UrbanWear';
    END IF;
    RAISE NOTICE 'OK 3/6: FK cruzada producto-categoria RECHAZADA';

    -- Prueba negativa B: inventario Vanter NO puede usar variante UrbanWear.
    v_bloqueada:=FALSE;
    BEGIN
      INSERT INTO public.inventario
        (empresa_id,sucursal_id,variante_producto_id,temporada_id,
         stock_actual,stock_reservado)
      VALUES (v_vanter,v_sucursal_vanter,v_variante_urban,
              v_temporada_vanter,1,0);
    EXCEPTION WHEN foreign_key_violation THEN
      GET STACKED DIAGNOSTICS v_contraint=CONSTRAINT_NAME, v_err_state=RETURNED_SQLSTATE;
      IF v_contraint='fk_tenant_fk_inventario_variante' AND v_err_state='23503' THEN
        v_bloqueada:=TRUE;
      ELSE
        RAISE EXCEPTION 'Rechazo por FK equivocada: % (SQLSTATE %)',
          v_contraint,v_err_state;
      END IF;
    END;
    IF NOT v_bloqueada THEN
      RAISE EXCEPTION 'FALLO AISLAMIENTO: inventario Vanter acepto variante UrbanWear';
    END IF;
    RAISE NOTICE 'OK 4/6: FK cruzada inventario-variante RECHAZADA';

    -- Prueba negativa C: perfil Vanter NO puede vincular usuario sin membresia.
    v_bloqueada:=FALSE;
    BEGIN
      INSERT INTO public.cliente (empresa_id,usuario_id,nombre,apellido,estado)
      VALUES (v_vanter,v_usuario_urban,'SI2 TEST','NO GUARDAR',TRUE);
    EXCEPTION WHEN foreign_key_violation THEN
      GET STACKED DIAGNOSTICS v_contraint=CONSTRAINT_NAME, v_err_state=RETURNED_SQLSTATE;
      IF v_contraint='fk_tenant_cliente_usuario' AND v_err_state='23503' THEN
        v_bloqueada:=TRUE;
      ELSE
        RAISE EXCEPTION 'Rechazo por FK equivocada: % (SQLSTATE %)',
          v_contraint,v_err_state;
      END IF;
    END;
    IF NOT v_bloqueada THEN
      RAISE EXCEPTION 'FALLO AISLAMIENTO: cliente Vanter acepto usuario UrbanWear';
    END IF;
    RAISE NOTICE 'OK 5/6: FK perfil-membresia cruzada RECHAZADA';

    -- Verificacion de conservacion. Todas las transacciones negativas
    -- se han revertido en sus bloques EXCEPTION; se emite ROLLBACK externo.
    IF (SELECT count(*) FROM public.empresa)<>5
       OR (SELECT count(*) FROM public.producto)<>18
       OR (SELECT count(*) FROM public.inventario)<>274
       OR (SELECT count(*) FROM public.cliente)<>9
       OR (SELECT count(*) FROM public.bitacora)<>844
       OR (SELECT count(*) FROM public.venta)<>40
       OR (SELECT count(*) FROM public.pago)<>30 THEN
      RAISE EXCEPTION 'Las pruebas alteraron conteos historicos';
    END IF;
    RAISE NOTICE 'OK 6/6: conteos estables; falta comprobar rollback final';
    RAISE NOTICE 'PENDIENTE: limites/IA, RLS y acceso cruzado via FastAPI';
END;
$prueba_saas$;

SELECT 'SQL comprobado: 6 fases; 3 intentos cruzados rechazados; SIN COMMIT' AS resumen;
ROLLBACK;
-- Si aparece ROLLBACK, la prueba no ha persistido cambios comerciales.
