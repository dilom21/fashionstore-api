-- Bloque 3 / Paso 5S. FOTO DEL AISLAMIENTO RLS DEL USUARIO ACTUAL.
-- SOLO LECTURA: no demuestra aislamiento con cuentas SQL restringidas.
-- PostgreSQL LOCAL 127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h
-- Sin SET ROLE, CREATE ROLE, DDL, CALL, seed ni datos de usuarios.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SET LOCAL row_security = on;

DO $preflight$
DECLARE
    v_tablas integer;
    v_fks integer;
    v_policies integer;
    v_sp integer;
    v_rel record;
    v_hay_filas boolean;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1' THEN
        RAISE EXCEPTION '5S: solo sandbox LOCAL autorizado';
    END IF;
    -- Se busca caracterizar el contexto privilegiado EXISTENTE,
    -- NO crear ni cambiar a un rol de ensayo.
    IF session_user <> 'postgres' OR current_user <> 'postgres' THEN
        RAISE EXCEPTION '5S: usuario inesperado; NO usar SET ROLE';
    END IF;
    IF to_regclass('public.empresa') IS NULL
       OR to_regclass('public.usuario_empresa') IS NULL
       OR to_regclass('public.bitacora') IS NULL THEN
        RAISE EXCEPTION '5S: estructura de seguridad incompleta';
    END IF;

    SELECT count(*) INTO v_tablas
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r';
    IF v_tablas <> 46 THEN
        RAISE EXCEPTION '5S: 46 tablas esperadas, hay %',v_tablas;
    END IF;

    SELECT count(*) INTO v_fks
    FROM pg_constraint co
    JOIN pg_class c ON c.oid=co.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND co.contype='f'
      AND co.convalidated AND co.conname LIKE 'fk_tenant_%';
    IF v_fks <> 48 THEN
        RAISE EXCEPTION '5S: 48 FK tenant esperadas, hay %',v_fks;
    END IF;

    SELECT count(*) INTO v_policies
    FROM pg_policies
    WHERE schemaname='public'
      AND tablename IN (
        'usuario','rol','usuario_empresa','empresa','suscripcion',
        'plan_saas','cliente','empleado','sucursal','carrito',
        'detalle_carrito','inventario','reserva','detalle_reserva',
        'movimiento_inventario','bitacora'
      );
    IF v_policies <> 0 THEN
        RAISE EXCEPTION '5S: hay % policies criticas inesperadas',v_policies;
    END IF;

    SELECT count(*) INTO v_sp
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind='p'
      AND p.proname IN (
        'sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
        'sp_finalizar_reserva_sin_compra_tenant'
      );
    IF v_sp <> 0 THEN
        RAISE EXCEPTION '5S: SP tenant instalado inesperadamente';
    END IF;

    -- Ni datos reales, ni seed, ni conteo de filas personales.
    FOR v_rel IN
        SELECT n.nspname,c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
        ORDER BY c.relname
    LOOP
        EXECUTE format(
            'SELECT EXISTS(SELECT 1 FROM %I.%I LIMIT 1)',
            v_rel.nspname,v_rel.relname
        ) INTO v_hay_filas;
        IF v_hay_filas THEN
            RAISE EXCEPTION '5S: %.% no vacia; abortado',
                v_rel.nspname,v_rel.relname;
        END IF;
    END LOOP;
    RAISE NOTICE
        '5S PREFLIGHT OK: 46 tablas vacias, 48 FK tenant, 0 policies criticas, 0 SP tenant';
END $preflight$;

-- Prueba ACTUAL de si RLS se aplica al principal SQL de la conexion.
SELECT '1_SQL_PRINCIPAL_ACTUAL' AS seccion,
       current_database() AS base,session_user AS sesion,
       current_user AS principal_sql,
       current_setting('row_security') AS configuracion_row_security,
       r.rolsuper AS superusuario,
       r.rolbypassrls AS bypass_rls,
       r.rolinherit AS hereda,
       r.rolcanlogin AS login
FROM pg_roles r
WHERE r.rolname=current_user;

-- row_security_active() consulta a PostgreSQL: NO equivale a flag
-- relrowsecurity ni es una propiedad permanente de la tabla.
SELECT '2_RLS_EFECTIVO_PRINCIPAL_ACTUAL' AS seccion,
       c.relname AS tabla,
       c.relrowsecurity AS rls_habilitado,
       c.relforcerowsecurity AS forzar_owner,
       row_security_active(c.oid) AS rls_aplica_a_este_principal,
       (SELECT count(*) FROM pg_policies pol
        WHERE pol.schemaname='public' AND pol.tablename=c.relname)
          AS numero_policies,
       pg_get_userbyid(c.relowner) AS propietario
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN (
    'usuario','rol','usuario_empresa','empresa','suscripcion',
    'plan_saas','cliente','empleado','sucursal','carrito',
    'detalle_carrito','inventario','reserva','detalle_reserva',
    'movimiento_inventario','bitacora'
  )
ORDER BY c.relname;

-- Requisitos de un futuro principal limitado (NO creado en 5S).
-- Se muestra un estado INCOMPLETO por diseño, no aprobar RLS con postgres.
SELECT '3_DICTAMEN_ALCANCE' AS seccion,
       CASE
         WHEN (SELECT r.rolsuper OR r.rolbypassrls
               FROM pg_roles r WHERE r.rolname=current_user)
         THEN 'NO_CERTIFICADO: cuenta actual con SUPERUSER/BYPASSRLS'
         WHEN row_security_active('public.usuario'::regclass)
         THEN 'RLS_APLICA_AL_PRINCIPAL: falta probar A/B y permisos'
         ELSE 'NO_CERTIFICADO: RLS inactivo para principal actual'
       END AS dictamen,
       'Sin rol restringido, sin seed, sin policies nuevas y sin CALL' AS limites;

ROLLBACK;
