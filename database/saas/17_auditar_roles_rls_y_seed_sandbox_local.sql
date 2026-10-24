-- B3 / P5J: auditoria de POLITICAS RLS, GRANTS y columnas para datos FICTICIOS.
-- EXCLUSIVAMENTE sandbox LOCAL. NO crea roles ni inserta datos.
-- Nota: CREATE ROLE en PostgreSQL tiene alcance de CLUSTER, no de DB.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '60s';

DO $guard$
DECLARE
    v_tablas integer;
    v_fks integer;
    v_sp integer;
    v_filas bigint;
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1' THEN
        RAISE EXCEPTION '5J requiere exclusivamente sandbox LOCAL: %',
          current_database();
    END IF;
    SELECT count(*) INTO v_tablas
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r';
    IF v_tablas <> 46 THEN
        RAISE EXCEPTION 'Sandbox incompatible, tablas % (esperadas 46)', v_tablas;
    END IF;
    SELECT count(*) INTO v_fks
      FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND co.contype='f'
        AND co.convalidated AND co.conname LIKE 'fk_tenant_%';
    IF v_fks <> 48 THEN
        RAISE EXCEPTION 'Sandbox sin 48 FK tenant (% encontradas)',v_fks;
    END IF;
    SELECT count(*) INTO v_sp
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.prokind='p'
        AND p.proname IN (
          'sp_crear_reserva_tenant','sp_cancelar_reserva_tenant',
          'sp_finalizar_reserva_sin_compra_tenant'
        );
    IF v_sp <> 0 THEN
        RAISE EXCEPTION 'SP tenant instalados antes de auditoria 5J (%)',v_sp;
    END IF;
    -- No seed antes de auditar: tablas de 2 tenants, identidad y operaciones vacias.
    SELECT
      (SELECT count(*) FROM public.empresa) +
      (SELECT count(*) FROM public.usuario) +
      (SELECT count(*) FROM public.usuario_empresa) +
      (SELECT count(*) FROM public.cliente) +
      (SELECT count(*) FROM public.carrito) +
      (SELECT count(*) FROM public.reserva) +
      (SELECT count(*) FROM public.inventario)
    INTO v_filas;
    IF v_filas <> 0 THEN
        RAISE EXCEPTION 'Sandbox ya contiene datos en tablas centrales (%)',v_filas;
    END IF;
    RAISE NOTICE '5J PREFLIGHT OK: 46 tablas, 48 FK tenant, cero SP SaaS, sin datos centrales.';
END
$guard$;

-- Observacion crucial: cuenta conectada puede tener BYPASSRLS.
SELECT '1_ROLE_CURRENT' AS seccion, current_database() AS base,
       session_user AS conexion, current_user AS rol_actual,
       r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
       has_database_privilege(current_user,current_database(),'CREATE') AS puede_crear_en_db
FROM pg_roles r WHERE r.rolname=current_user;

-- Roles de ensayo preexistentes: NO tomar un nombre global sin inspeccionarlo.
SELECT '2_ROLE_NAMES_RESERVED' AS seccion, rolname,rolcanlogin,rolsuper,
       rolbypassrls,rolinherit,rolcreaterole,rolcreatedb
FROM pg_roles WHERE rolname LIKE 'fashionstore_5j_%'
ORDER BY rolname;

-- Resumen de RLS y número de políticas por tabla empresarial.
SELECT '3_RLS_TABLES' AS seccion, c.relname AS tabla,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS force_rls,
       pg_get_userbyid(c.relowner) AS tabla_owner,
       count(pol.policyname) AS numero_politicas
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_policies pol ON pol.schemaname='public'
  AND pol.tablename=c.relname
WHERE n.nspname='public' AND c.relkind='r'
  AND c.relname IN (
    'empresa','plan_saas','suscripcion','usuario_empresa','rol',
    'usuario','empleado','cliente','sucursal','categoria','producto',
    'variante_producto','inventario','carrito','detalle_carrito',
    'reserva','detalle_reserva','movimiento_inventario','venta','pago'
  )
GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner
ORDER BY c.relname;

-- Texto original de las POLITICAS. Sin datos de negocio.
SELECT '4_POLICIES_DETAIL' AS seccion, schemaname,tablename,policyname,
       permissive,roles,cmd,qual AS using_sql,with_check AS with_check_sql
FROM pg_policies WHERE schemaname='public'
  AND tablename IN (
    'empresa','usuario_empresa','cliente','empleado','carrito',
    'detalle_carrito','reserva','detalle_reserva',
    'inventario','movimiento_inventario','venta','pago'
  ) ORDER BY tablename,policyname;

-- Privilegios efectivamente otorgados a PUBLIC sobre tablas comerciales.
SELECT '5_PUBLIC_GRANTS' AS seccion,t.table_name,
       coalesce(string_agg(t.privilege_type,',' ORDER BY t.privilege_type),
         '<NINGUNO>') AS privilegios_public
FROM (
  SELECT c.relname AS table_name,acl.privilege_type
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(c.relacl,acldefault('r',c.relowner))
  ) acl
  WHERE n.nspname='public' AND c.relkind IN ('r','p')
    AND c.relname IN ('usuario','empresa','usuario_empresa','cliente',
      'carrito','detalle_carrito','reserva','detalle_reserva','inventario',
      'movimiento_inventario')
    AND acl.grantee=0
) t
RIGHT JOIN (
 SELECT unnest(ARRAY[
 'usuario','empresa','usuario_empresa','cliente','carrito',
 'detalle_carrito','reserva','detalle_reserva','inventario',
 'movimiento_inventario']) AS table_name
) todas ON todas.table_name=t.table_name
GROUP BY t.table_name,todas.table_name
ORDER BY todas.table_name;

-- Columnas obligatorias/defaults para diseñar seed con FK correctas.
SELECT '6_SEED_COLUMNS' AS seccion,table_name,
       jsonb_agg(
         jsonb_build_object(
           'col',column_name,'type',data_type,'not_null',is_nullable='NO',
           'default',coalesce(column_default,''),
           'identity',is_identity
         ) ORDER BY ordinal_position
       ) AS columnas
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN (
    'empresa','plan_saas','suscripcion','rol','usuario',
    'usuario_empresa','cliente','empleado','ciudad','sucursal',
    'categoria','producto','talla','color','temporada',
    'variante_producto','inventario','carrito','detalle_carrito',
    'reserva','detalle_reserva','movimiento_inventario',
    'modulo','funcion','accion','rol_funcion'
  )
GROUP BY table_name ORDER BY table_name;

-- Restricciones que el seed ficticio y los SP deberán respetar.
SELECT '7_SEED_CONSTRAINTS' AS seccion,c.relname AS tabla,
       co.conname AS nombre,co.contype AS tipo,
       pg_get_constraintdef(co.oid) AS definicion
FROM pg_constraint co
JOIN pg_class c ON c.oid=co.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN (
    'empresa','plan_saas','suscripcion','rol','usuario',
    'usuario_empresa','cliente','empleado','ciudad','sucursal',
    'categoria','producto','talla','color','temporada',
    'variante_producto','inventario','carrito','detalle_carrito',
    'reserva','detalle_reserva','movimiento_inventario'
)
AND co.contype IN ('p','f','u','c')
ORDER BY c.relname,co.contype,co.conname;

-- Definiciones de triggers relacionadas con el seed (NO ejecutarlos).
SELECT '8_TRIGGERS' AS seccion,c.relname AS tabla,t.tgname AS nombre,
       t.tgenabled,pg_get_triggerdef(t.oid) AS definicion
FROM pg_trigger t
JOIN pg_class c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND NOT t.tgisinternal
  AND c.relname IN ('usuario','empresa','sucursal','cliente',
    'inventario','carrito','detalle_carrito','reserva','detalle_reserva',
    'movimiento_inventario')
ORDER BY c.relname,t.tgname;

ROLLBACK;
