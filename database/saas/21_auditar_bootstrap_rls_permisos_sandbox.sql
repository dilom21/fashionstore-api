-- B3/P5P: auditoria READ ONLY del bootstrap de autenticacion SaaS.
-- SOLELY: 127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h
-- Inspecciona metadatos, NUNCA filas de usuario ni hashes de password.
-- NO crear roles, policies, GRANT, REVOKE, seed, funciones ni SP.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';

DO $preflight$
DECLARE
  v_tablas integer;
  v_fks integer;
  v_sp integer;
  v_policies integer;
  v_hay_filas boolean;
  v_rel record;
BEGIN
  IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
     OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1' THEN
    RAISE EXCEPTION '5P: solo sandbox local autorizado; abortado';
  END IF;
  IF to_regclass('public.usuario') IS NULL
     OR to_regclass('public.usuario_empresa') IS NULL
     OR to_regclass('public.suscripcion') IS NULL THEN
    RAISE EXCEPTION '5P: falta esquema de bootstrap';
  END IF;

  SELECT count(*) INTO v_tablas
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r';
  IF v_tablas<>46 THEN
    RAISE EXCEPTION '5P: 46 tablas esperadas, hay %',v_tablas;
  END IF;

  SELECT count(*) INTO v_fks
  FROM pg_constraint co
  JOIN pg_class c ON c.oid=co.conrelid
  JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND co.contype='f'
    AND co.convalidated AND co.conname LIKE 'fk_tenant_%';
  IF v_fks<>48 THEN
    RAISE EXCEPTION '5P: 48 FK tenant esperadas, hay %',v_fks;
  END IF;

  SELECT count(*) INTO v_sp
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.prokind='p'
    AND p.proname IN (
      'sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
      'sp_finalizar_reserva_sin_compra_tenant'
    );
  IF v_sp<>0 THEN
    RAISE EXCEPTION '5P: SP tenant instalado inesperadamente: %',v_sp;
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
  IF v_policies<>0 THEN
    RAISE EXCEPTION '5P: aparecieron policies en tablas criticas: %',v_policies;
  END IF;

  -- Verifica 46 tablas SIN SEED, sin mostrar ninguna fila de clientes.
  FOR v_rel IN
    SELECT n.nspname,c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname
  LOOP
    EXECUTE format(
      'SELECT EXISTS(SELECT 1 FROM %I.%I LIMIT 1)',
      v_rel.nspname,v_rel.relname
    ) INTO v_hay_filas;
    IF v_hay_filas THEN
      RAISE EXCEPTION '5P: %.% NO esta vacia. Abortado',
        v_rel.nspname,v_rel.relname;
    END IF;
  END LOOP;
  RAISE NOTICE '5P PREFLIGHT OK: sandbox, 46 tablas vacias, 48 FK tenant, 0 policies criticas, 0 SP tenant';
END $preflight$;

-- Roles visibles: flags e inicios de sesion, no contrasenas ni hashes.
SELECT '1_SQL_ROLE_FLAGS' AS seccion,r.rolname,r.rolcanlogin,
       r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
       r.rolinherit
FROM pg_roles r
WHERE r.rolname IN ('postgres','anon','authenticated','service_role')
   OR r.rolname LIKE 'fashionstore_5j_%'
   OR r.rolname LIKE 'fashionstore_5k_%'
   OR r.rolname LIKE 'fashionstore_5l_%'
   OR r.rolname LIKE 'fashionstore_5p_%'
ORDER BY r.rolname;

-- RLS por objeto del login, previa a conocer app.empresa_id, y operaciones.
SELECT '2_BOOTSTRAP_RLS' AS seccion,c.relname AS tabla,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS force_rls,
       pg_get_userbyid(c.relowner) AS owner,
       count(pol.policyname) AS numero_politicas
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_policies pol
  ON pol.schemaname='public' AND pol.tablename=c.relname
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN (
    'usuario','rol','usuario_empresa','empresa','suscripcion',
    'plan_saas','cliente','empleado','sucursal',
    'carrito','detalle_carrito','inventario','reserva',
    'detalle_reserva','movimiento_inventario','bitacora'
  )
GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner
ORDER BY c.relname;

-- ACL de TABLAS realmente almacenadas o PostgreSQL default.
-- Mostrar solo propietario/PUBLIC y perfiles SaaS conocidos, no roles ajenos.
SELECT '3_AUTH_TABLE_GRANTS' AS seccion,c.relname AS tabla,
       CASE WHEN acl.grantee=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(acl.grantee) END AS beneficiario,
       acl.privilege_type,acl.is_grantable
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN LATERAL aclexplode(
  coalesce(c.relacl,acldefault('r',c.relowner))
) acl
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN (
    'usuario','rol','usuario_empresa','empresa','suscripcion','plan_saas',
    'cliente','empleado','sucursal','bitacora','carrito','inventario','reserva'
  )
  AND (acl.grantee=0 OR
       pg_get_userbyid(acl.grantee) IN
          ('postgres','anon','authenticated','service_role')
       OR pg_get_userbyid(acl.grantee) LIKE 'fashionstore_5p_%')
ORDER BY c.relname,beneficiario,acl.privilege_type;

-- Permisos por columna, incluyendo hash. Solo BOOLEANOS de permiso:
-- NO muestra ni obtiene ningun valor de hash/correo/usuario.
SELECT '4_BOOTSTRAP_COLUMN_RIGHTS' AS seccion,r.rolname,c.relname AS tabla,
       a.attname AS columna,
       has_column_privilege(r.oid,c.oid,a.attname,'SELECT') AS puede_leer_columna
FROM pg_roles r
JOIN pg_class c ON c.relname IN ('usuario','usuario_empresa','rol')
JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public'
JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
WHERE (
    (c.relname='usuario' AND a.attname IN ('id','correo','password_hash','estado','rol_id'))
    OR (c.relname='usuario_empresa' AND a.attname IN
         ('id','usuario_id','empresa_id','rol_id','estado'))
    OR (c.relname='rol' AND a.attname IN ('id','nombre','estado'))
  )
  AND (r.rolname IN ('postgres','anon','authenticated','service_role')
       OR r.rolname LIKE 'fashionstore_5p_%')
ORDER BY r.rolname,c.relname,a.attname;

-- ACL explícita por COLUMNA (NULL = no hay grants columna-specific).
SELECT '5_EXPLICIT_COLUMN_ACL' AS seccion,c.relname AS tabla,
       a.attname AS columna,a.attacl::text AS acl_explicita
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
WHERE n.nspname='public' AND c.relkind='r'
  AND (
    (c.relname='usuario' AND a.attname IN ('id','correo','password_hash','estado','rol_id'))
    OR (c.relname='usuario_empresa' AND a.attname IN
         ('usuario_id','empresa_id','rol_id','estado'))
  )
ORDER BY c.relname,a.attname;

-- CONNECT a la base, USAGE al schema; no tocar ACL.
SELECT '6_ROLE_CONNECT_SCHEMA' AS seccion,r.rolname,
       has_database_privilege(r.oid,current_database(),'CONNECT') AS db_connect,
       has_schema_privilege(r.oid,'public','USAGE') AS schema_usage,
       has_table_privilege(r.oid,'public.usuario','SELECT') AS select_usuario,
       has_table_privilege(r.oid,'public.usuario_empresa','SELECT') AS select_membresia,
       has_table_privilege(r.oid,'public.bitacora','INSERT') AS insert_bitacora
FROM pg_roles r
WHERE r.rolname IN ('postgres','anon','authenticated','service_role')
   OR r.rolname LIKE 'fashionstore_5p_%'
ORDER BY r.rolname;

-- Rutinas heredadas, acceso PUBLIC y SECURITY DEFINER. Solo firmas/metadatos.
SELECT '7_LEGACY_RESERVA_EXECUTE' AS seccion,
       p.oid::regprocedure::text AS rutina,
       p.prosecdef AS security_definer,
       pg_get_userbyid(p.proowner) AS owner,
       coalesce(bool_or(acl.grantee=0 AND acl.privilege_type='EXECUTE'),false)
           AS execute_public
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
LEFT JOIN LATERAL aclexplode(
   coalesce(p.proacl,acldefault('f',p.proowner))
) acl ON TRUE
WHERE n.nspname='public'
  AND p.proname IN (
    'sp_crear_reserva','sp_crear_reserva_desde_carrito',
    'sp_cancelar_reserva','sp_finalizar_reserva_sin_compra'
  )
GROUP BY p.oid,p.prosecdef,p.proowner
ORDER BY p.oid::regprocedure::text;

-- Metadatos de roles heredados: solo miembros SaaS si existieran.
-- NO crea usuarios; un rol PostgreSQL es GLOBAL al cluster.
SELECT '8_PG_ROLE_MEMBERSHIP' AS seccion,rol.rolname AS rol_otorgado,
       miembro.rolname AS miembro
FROM pg_auth_members m JOIN pg_roles rol ON rol.oid=m.roleid
JOIN pg_roles miembro ON miembro.oid=m.member
WHERE rol.rolname LIKE 'fashionstore_5p_%'
   OR miembro.rolname LIKE 'fashionstore_5p_%'
ORDER BY rol.rolname,miembro.rolname;

ROLLBACK;
