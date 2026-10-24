-- B3 / P5K: AUDITORIA de funciones de triggers, permisos y RLS.
-- SOLO la base NUEVA de ensayo, nunca la restaurada. CERO DDL/DML.
-- La salida contiene codigo SQL original: examinarla antes de compartirla.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
    v_tablas integer;
    v_fks integer;
    v_policies integer;
    v_procedimientos integer;
    v_filas bigint;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1' THEN
        RAISE EXCEPTION '5K: prohibido acceder fuera del sandbox 127.0.0.1';
    END IF;
    SELECT count(*) INTO v_tablas
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r';
    IF v_tablas<>46 THEN
        RAISE EXCEPTION '5K: 46 tablas esperadas; se observaron %',v_tablas;
    END IF;
    SELECT count(*) INTO v_fks
      FROM pg_constraint co
      JOIN pg_class c ON c.oid=co.conrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND co.contype='f'
        AND co.convalidated AND co.conname LIKE 'fk_tenant_%';
    IF v_fks<>48 THEN
        RAISE EXCEPTION '5K: 48 FK tenant esperadas; se observaron %',v_fks;
    END IF;
    SELECT count(*) INTO v_procedimientos
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.prokind='p'
        AND p.proname IN (
          'sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
          'sp_finalizar_reserva_sin_compra_tenant'
        );
    IF v_procedimientos<>0 THEN
        RAISE EXCEPTION '5K: SP tenant ya instalados; auditoria abortada';
    END IF;
    SELECT count(*) INTO v_policies
      FROM pg_policies pol WHERE pol.schemaname='public'
      AND pol.tablename IN (
        'empresa','plan_saas','suscripcion','usuario_empresa','rol',
        'usuario','empleado','cliente','sucursal','categoria','producto',
        'variante_producto','inventario','carrito','detalle_carrito',
        'reserva','detalle_reserva','movimiento_inventario','venta','pago'
      );
    IF v_policies<>0 THEN
        RAISE EXCEPTION '5K: RLS se modifico desde 5J: hay % policies',v_policies;
    END IF;
    SELECT
       (SELECT count(*) FROM public.empresa) +
       (SELECT count(*) FROM public.usuario) +
       (SELECT count(*) FROM public.usuario_empresa) +
       (SELECT count(*) FROM public.cliente) +
       (SELECT count(*) FROM public.carrito) +
       (SELECT count(*) FROM public.reserva) +
       (SELECT count(*) FROM public.inventario)
    INTO v_filas;
    IF v_filas<>0 THEN
        RAISE EXCEPTION '5K: seed detectado en tablas principales: %',v_filas;
    END IF;
    RAISE NOTICE '5K PREFLIGHT OK: sandbox, 46 tablas, 48 FK, 0 policies, 0 SP tenant, sin seed principal';
END $preflight$;

-- Cada trigger habilitado de tablas CU15-CU18: seguridad de la funcion,
-- owner, firma e inventario. La funcion puede reutilizarse en mas tablas.
SELECT '1_TRIGGER_FUNCTIONS' AS seccion,c.relname AS tabla,
       t.tgname AS trigger_nombre,
       t.tgenabled, p.oid::regprocedure::text AS funcion,
       pg_get_userbyid(p.proowner) AS propietario_funcion,
       p.prosecdef AS security_definer,
       coalesce(array_to_string(p.proconfig,';'),'<SIN_CONFIG>') AS set_config,
       pg_get_triggerdef(t.oid) AS definicion_trigger
FROM pg_trigger t
JOIN pg_class c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_proc p ON p.oid=t.tgfoid
WHERE n.nspname='public' AND NOT t.tgisinternal
  AND c.relname IN (
    'usuario','sucursal','cliente','inventario','carrito','detalle_carrito',
    'reserva','detalle_reserva','movimiento_inventario'
  )
ORDER BY c.relname,t.tgname;

-- Cuerpos ORIGINALES, sin ejecutar ninguna funcion.
-- Incluye todas las funciones referenciadas por los triggers anteriores.
SELECT '2_FUNCTION_BODIES' AS seccion,
       p.oid::regprocedure::text AS funcion,
       pg_get_userbyid(p.proowner) AS propietario,
       p.prosecdef AS security_definer,
       coalesce(array_to_string(p.proconfig,';'),'<SIN_CONFIG>') AS set_config,
       pg_get_functiondef(p.oid) AS sql_original
FROM pg_proc p JOIN pg_namespace fn ON fn.oid=p.pronamespace
WHERE fn.nspname='public'
  AND p.oid IN (
    SELECT DISTINCT t.tgfoid
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND NOT t.tgisinternal
      AND c.relname IN (
        'usuario','sucursal','cliente','inventario','carrito','detalle_carrito',
        'reserva','detalle_reserva','movimiento_inventario'
      )
  )
ORDER BY p.proname,p.oid;

-- EXECUTE a PUBLIC se inspecciona sobre rutinas relevantes; permiso para
-- 'PUBLIC' por defecto no certifica acceso a tablas y RLS.
SELECT '3_ROUTINE_EXECUTE_PUBLIC' AS seccion,
       p.oid::regprocedure::text AS funcion, p.prokind,
       pg_get_userbyid(p.proowner) AS propietario, p.prosecdef,
       bool_or(acl.grantee=0 AND acl.privilege_type='EXECUTE')
           AS execute_public
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
LEFT JOIN LATERAL aclexplode(coalesce(p.proacl,
    acldefault('f',p.proowner))) acl ON TRUE
WHERE n.nspname='public'
  AND (
    p.proname LIKE 'sp_%reserva%'
    OR p.oid IN (
       SELECT DISTINCT t.tgfoid FROM pg_trigger t
       JOIN pg_class c ON c.oid=t.tgrelid
       JOIN pg_namespace cn ON cn.oid=c.relnamespace
       WHERE cn.nspname='public' AND NOT t.tgisinternal
         AND c.relname IN ('usuario','sucursal','cliente','inventario',
           'carrito','detalle_carrito','reserva','detalle_reserva')
    )
  )
GROUP BY p.oid,p.prokind,p.proowner,p.prosecdef
ORDER BY p.oid::regprocedure::text;

-- ACL efectiva por clase de objeto (incluye concesiones DIRECTAS e
-- heredadas para roles que ya existen, sin simular identidades nuevas).
SELECT '4_ROLES_WITH_ACCESS' AS seccion,
       r.rolname,r.rolcanlogin,r.rolsuper,r.rolbypassrls,
       has_database_privilege(r.oid,current_database(),'CONNECT') AS puede_conectar,
       has_schema_privilege(r.oid,'public','USAGE') AS puede_usar_public,
       has_table_privilege(r.oid,'public.carrito','SELECT') AS lee_carrito,
       has_table_privilege(r.oid,'public.inventario','SELECT') AS lee_inventario,
       has_table_privilege(r.oid,'public.reserva','INSERT') AS inserta_reserva,
       has_table_privilege(r.oid,'public.movimiento_inventario','INSERT') AS inserta_kardex
FROM pg_roles r
WHERE r.rolname IN ('postgres','anon','authenticated','service_role')
   OR r.rolname LIKE 'fashionstore_5j_%'
   OR r.rolname LIKE 'fashionstore_5k_%'
ORDER BY r.rolname;

-- ACL reales por secuencia y tipo de objeto: un INSERT con IDENTITY
-- o SERIAL no es equivalente a tener SELECT/UPDATE de cualquier secuencia.
SELECT '5_ID_SEQUENCES' AS seccion,
       c.oid::regclass::text AS secuencia,
       pg_get_userbyid(c.relowner) AS propietario,
       coalesce(c.relacl::text,'<DEFAULT>') AS acl
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='S'
  AND c.relname ~ '(empresa|usuario|cliente|reserva|carrito|inventario|producto|suscripcion)'
ORDER BY c.relname;

-- Revisar privilegios/funciones de auditoria sin asumir que el trigger
-- use el mismo contexto de empresa que el nuevo SP.
SELECT '6_SECURITY_FUNCTIONS' AS seccion,
       p.oid::regprocedure::text AS funcion,
       p.prosecdef AS security_definer,
       pg_get_userbyid(p.proowner) AS propietario,
       coalesce(array_to_string(p.proconfig,';'),'<SIN_CONFIG>') AS set_config
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public'
  AND (p.proname LIKE '%tenant%' OR p.proname LIKE '%bitacora%'
       OR p.proname LIKE '%inventario%')
ORDER BY p.proname,p.oid;

ROLLBACK;
