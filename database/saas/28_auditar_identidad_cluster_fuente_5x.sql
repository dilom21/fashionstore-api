-- B3 / P5X - Identidad del cluster PostgreSQL LOCAL, solo lectura.
-- SOLO: 127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h.
-- NO imprime ruta real PGDATA, NO consulta filas de negocio.
-- NO SET ROLE, CREATE ROLE, DDL, CALL, seed ni cambios a tablas.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SET LOCAL row_security = on;

DO $preflight$
DECLARE
    v_id bigint;
    v_pgdata text;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'
       OR inet_server_port() <> 5432 THEN
        RAISE EXCEPTION '5X: destino no es el sandbox local autorizado';
    END IF;
    IF session_user <> 'postgres' OR current_user <> 'postgres' THEN
        RAISE EXCEPTION '5X: usuario inesperado, NO usar SET ROLE';
    END IF;
    IF current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION '5X: se requiere PostgreSQL 18 local';
    END IF;

    SELECT system_identifier INTO v_id
    FROM pg_control_system();
    IF v_id IS NULL OR v_id <= 0 THEN
        RAISE EXCEPTION '5X: system_identifier de origen no verificable';
    END IF;

    v_pgdata := current_setting('data_directory', true);
    IF v_pgdata IS NULL OR btrim(v_pgdata) = '' THEN
        RAISE EXCEPTION '5X: PGDATA del cluster de origen no verificable';
    END IF;

    IF (SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r') <> 46 THEN
        RAISE EXCEPTION '5X: numero de tablas publicas diferente a 46';
    END IF;
    IF (SELECT count(*) FROM pg_constraint co
        JOIN pg_class c ON c.oid=co.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND co.contype='f'
          AND co.convalidated AND co.conname LIKE 'fk_tenant_%') <> 48 THEN
        RAISE EXCEPTION '5X: numero de FK tenant validas distinto a 48';
    END IF;
    IF (SELECT count(*) FROM pg_policies
        WHERE schemaname='public' AND tablename IN (
          'usuario','rol','usuario_empresa','empresa','suscripcion',
          'plan_saas','cliente','empleado','sucursal','carrito',
          'detalle_carrito','inventario','reserva','detalle_reserva',
          'movimiento_inventario','bitacora'
        )) <> 0 THEN
        RAISE EXCEPTION '5X: policies criticas instaladas inesperadamente';
    END IF;

    RAISE NOTICE '5X PREFLIGHT OK: sandbox localhost, postgres18, 46 tablas, 48 FK, 0 policies criticas';
END $preflight$;

-- El ID original sirve para compararlo DESPUES con otra instalacion
-- expresamente autorizada. No identifica empresas ni certifica RLS.
-- NO emitir el valor literal de data_directory ni PATH de servicios Windows.
WITH paths AS (
    SELECT lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/'))
             AS fuente_pgdata,
           lower(rtrim('C:/SI2_Parcial1/fashionstore-rls-lab-5v-data', '/'))
             AS ruta_propuesta
)
SELECT '1_IDENTIDAD_CLUSTER_FUENTE' AS seccion,
       current_database() AS base,
       session_user AS login_sql,
       current_user AS principal_sql,
       inet_server_port() AS puerto,
       current_setting('server_version_num')::int / 10000 AS version_mayor,
       sys.system_identifier::text AS system_identifier_fuente,
       CASE
         WHEN paths.fuente_pgdata = paths.ruta_propuesta
           OR left(paths.fuente_pgdata, length(paths.ruta_propuesta) + 1)
              = paths.ruta_propuesta || '/'
           OR left(paths.ruta_propuesta, length(paths.fuente_pgdata) + 1)
              = paths.fuente_pgdata || '/'
         THEN 'SOLAPAMIENTO_TEXTUAL_BLOQUEAR'
         ELSE 'SIN_SOLAPAMIENTO_TEXTUAL_NO_CERTIFICA'
       END AS pgdata_vs_ruta_propuesta,
       'RUTA_PGDATA_ORIGEN_NO_PUBLICADA' AS privacidad
FROM pg_control_system() AS sys CROSS JOIN paths;

SELECT '2_DICTAMEN_ALCANCE' AS seccion,
       'ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE' AS resultado,
       'SIN_CLUSTER_NUEVO_SIN_ROLES_SIN_RLS_A_B' AS limite;
ROLLBACK;
