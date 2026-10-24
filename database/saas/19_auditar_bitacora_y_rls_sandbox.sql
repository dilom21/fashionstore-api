-- BLOQUE 3 / PASO 5L: BITACORA, TRIGGER DE IP, PRIVILEGIOS Y RLS.
-- Solo auditoria sobre la base aislada de ensayo PostgreSQL LOCAL.
-- NO crea roles, politicas ni seed. No invoca funciones ni SP.
-- Este SQL extrae CUERPOS de funciones: revisar literales antes de compartir.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
    v_tablas integer;
    v_fks integer;
    v_sp integer;
    v_policies integer;
    v_filas bigint;
    v_rel record;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1' THEN
        RAISE EXCEPTION '5L: destino NO es el sandbox local autorizado';
    END IF;

    IF to_regclass('public.bitacora') IS NULL
       OR to_regclass('public.usuario') IS NULL
       OR to_regclass('public.empresa') IS NULL THEN
        RAISE EXCEPTION '5L: faltan tablas de auditoria/identidad';
    END IF;

    SELECT count(*) INTO v_tablas
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r';
    IF v_tablas <> 46 THEN
        RAISE EXCEPTION '5L: esperaba 46 tablas public, encontro %',v_tablas;
    END IF;

    SELECT count(*) INTO v_fks
    FROM pg_constraint co
    JOIN pg_class c ON c.oid=co.conrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND co.contype='f'
      AND co.convalidated AND co.conname LIKE 'fk_tenant_%';
    IF v_fks <> 48 THEN
        RAISE EXCEPTION '5L: esperaba 48 FK tenant, encontro %',v_fks;
    END IF;

    SELECT count(*) INTO v_sp
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind='p'
      AND p.proname IN (
        'sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
        'sp_finalizar_reserva_sin_compra_tenant'
      );
    IF v_sp <> 0 THEN
        RAISE EXCEPTION '5L: SP tenant instalados sin autorizar (%)',v_sp;
    END IF;

    -- Verificar que la fotografia de 5J/5K aun corresponde a un RLS
    -- sin politicas en las tablas comerciales examinadas; dejar BITACORA
    -- fuera de esta guarda para consultar su estado independientemente.
    SELECT count(*) INTO v_policies
    FROM pg_policies WHERE schemaname='public' AND tablename IN (
      'empresa','plan_saas','suscripcion','usuario_empresa','rol',
      'usuario','empleado','cliente','sucursal','categoria','producto',
      'variante_producto','inventario','carrito','detalle_carrito',
      'reserva','detalle_reserva','movimiento_inventario','venta','pago'
    );
    IF v_policies <> 0 THEN
        RAISE EXCEPTION '5L: cambiaron las policies desde 5K: %',v_policies;
    END IF;

    -- 5I certifico la copia sin datos: revisar TODAS las tablas reales
    -- de public, incluida BITACORA, antes de interpretar ACL/triggers.
    FOR v_rel IN
      SELECT n.nspname,c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r'
      ORDER BY c.relname
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I',
          v_rel.nspname,v_rel.relname) INTO v_filas;
        IF v_filas <> 0 THEN
            RAISE EXCEPTION '5L: tabla %.% contiene % filas: abortar',
              v_rel.nspname,v_rel.relname,v_filas;
        END IF;
    END LOOP;
    RAISE NOTICE '5L PREFLIGHT OK: sandbox local, 46 tablas vacias (incl. bitacora), 48 FK, 0 policies comerciales, 0 SP tenant.';
END $preflight$;

-- Esquema, not null, tipo, identidad, DEFAULT, ningun valor de usuarios.
SELECT '1_BITACORA_COLUMNS' AS seccion,table_name,column_name,
       data_type,is_nullable,column_default,is_identity,
       character_maximum_length
FROM information_schema.columns
WHERE table_schema='public' AND table_name IN ('bitacora')
ORDER BY ordinal_position;

-- Checks, FK, indices, relaciones tenant e identidad.
SELECT '2_BITACORA_CONSTRAINTS' AS seccion,con.conname,
       con.contype,con.convalidated,pg_get_constraintdef(con.oid) AS definicion
FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname='bitacora'
ORDER BY con.contype,con.conname;

SELECT '3_BITACORA_INDEXES' AS seccion,
       i.relname AS indice,pg_get_indexdef(i.oid) AS definicion
FROM pg_index ix JOIN pg_class c ON c.oid=ix.indrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_class i ON i.oid=ix.indexrelid
WHERE n.nspname='public' AND c.relname='bitacora'
ORDER BY i.relname;

-- Estado RLS de bitacora, usuario y movimientos; numero de policies
-- incluye bitacora (no auditada individualmente en 5J).
SELECT '4_AUDIT_RLS_STATUS' AS seccion,c.relname AS tabla,
       c.relrowsecurity AS rls_habilitado,
       c.relforcerowsecurity AS forzar_rls,
       pg_get_userbyid(c.relowner) AS propietario,
       count(pol.policyname) AS policies
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_policies pol ON pol.schemaname='public' AND pol.tablename=c.relname
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN ('bitacora','usuario','empresa','cliente',
                   'carrito','inventario','reserva','movimiento_inventario')
GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner
ORDER BY c.relname;

SELECT '5_AUDIT_POLICIES' AS seccion,tablename,policyname,permissive,
       roles,cmd,qual AS using_sql,with_check AS with_check_sql
FROM pg_policies
WHERE schemaname='public'
  AND tablename IN ('bitacora','usuario','empresa',
                    'carrito','inventario','reserva')
ORDER BY tablename,policyname;

-- ACL directas incluyen PUBLIC. bool_or no inventa grants cuando
-- relacl sea NULL (acldefault usa ACL de PostgreSQL).
SELECT '6_BITACORA_ACL' AS seccion,
       c.relname AS tabla,
       CASE WHEN a.grantee=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(a.grantee) END AS beneficiario,
       a.privilege_type,a.is_grantable,
       pg_get_userbyid(a.grantor) AS concedente
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN LATERAL aclexplode(
  coalesce(c.relacl,acldefault('r',c.relowner))
) a
WHERE n.nspname='public' AND c.relname='bitacora' AND c.relkind='r'
ORDER BY beneficiario,a.privilege_type;

-- El trigger de IP, si existe, es de BITACORA; revisar definicion
-- y permiso de ejecucion de su funcion. Ninguna funcion se invoca.
SELECT '7_BITACORA_TRIGGERS' AS seccion,t.tgname,
       t.tgenabled,t.tgisinternal,
       p.oid::regprocedure::text AS funcion,pg_get_triggerdef(t.oid) AS ddl_trigger,
       p.prosecdef AS security_definer,
       coalesce(array_to_string(p.proconfig,';'),'<SIN_CONFIG>') AS set_config
FROM pg_trigger t
JOIN pg_class c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_proc p ON p.oid=t.tgfoid
WHERE n.nspname='public' AND c.relname='bitacora' AND NOT t.tgisinternal
ORDER BY t.tgname;

-- Registrar Bitacora y completar IP: cuerpos originales, nunca CALL/SELECT
-- de esas funciones. Dos nombres conocidos de auditoria 5K.
SELECT '8_AUDIT_FUNCTION_BODIES' AS seccion,
       p.oid::regprocedure::text AS funcion,
       pg_get_userbyid(p.proowner) AS propietario,p.prosecdef AS security_definer,
       coalesce(array_to_string(p.proconfig,';'),'<SIN_CONFIG>') AS set_config,
       pg_get_functiondef(p.oid) AS sql_original
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.proname IN (
    'fn_registrar_bitacora','fn_completar_ip_bitacora'
)
ORDER BY p.proname,p.oid;

SELECT '9_AUDIT_FUNCTION_EXECUTE_PUBLIC' AS seccion,
       p.oid::regprocedure::text AS funcion,
       pg_get_userbyid(p.proowner) AS propietario,
       p.prosecdef AS security_definer,
       coalesce(bool_or(a.grantee=0 AND a.privilege_type='EXECUTE'),false)
           AS execute_public
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
LEFT JOIN LATERAL aclexplode(
    coalesce(p.proacl,acldefault('f',p.proowner))
) a ON TRUE
WHERE n.nspname='public' AND p.proname IN (
    'fn_registrar_bitacora','fn_completar_ip_bitacora'
)
GROUP BY p.oid,p.proowner,p.prosecdef
ORDER BY p.oid::regprocedure::text;

-- Roles EXISTENTES, no se crean otros. Los permisos de postgres NO
-- certifican el comportamiento de un rol limitado.
SELECT '10_ROLE_EFFECTIVE_PRIVILEGES' AS seccion,
       r.rolname,r.rolcanlogin,r.rolsuper,r.rolbypassrls,
       has_database_privilege(r.oid,current_database(),'CONNECT') AS conectar,
       has_schema_privilege(r.oid,'public','USAGE') AS schema_usage,
       has_table_privilege(r.oid,'public.bitacora','INSERT') AS insertar_bitacora,
       has_table_privilege(r.oid,'public.bitacora','SELECT') AS leer_bitacora,
       has_table_privilege(r.oid,'public.carrito','UPDATE') AS actualizar_carrito,
       has_table_privilege(r.oid,'public.inventario','UPDATE') AS actualizar_inventario
FROM pg_roles r
WHERE r.rolname IN ('postgres','anon','authenticated','service_role')
   OR r.rolname LIKE 'fashionstore_5j_%'
   OR r.rolname LIKE 'fashionstore_5k_%'
   OR r.rolname LIKE 'fashionstore_5l_%'
ORDER BY r.rolname;

-- PK identity e indice en secuencias bitacora: solo metadata.
SELECT '11_BITACORA_SEQUENCES' AS seccion,
       c.oid::regclass::text AS secuencia,
       pg_get_userbyid(c.relowner) AS propietario,
       coalesce(c.relacl::text,'<DEFAULT>') AS acl
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='S'
  AND c.relname LIKE 'bitacora%'
ORDER BY c.relname;

ROLLBACK;
