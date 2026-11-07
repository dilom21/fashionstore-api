-- SOLO LECTURA. Instantánea previa a la ventana de despliegue.
--
-- Para qué sirve: 000_preflight.sql aborta si las cantidades históricas no son
-- exactamente las del respaldo. Esta instantánea se ejecuta ANTES y muestra el
-- estado real (versión, objetos, columnas, policies, roles y conteos) para que
-- un fallo de 000 se diagnostique como deriva de datos y no como error de la
-- migración. También deja registrado qué roles pueden hoy ejecutar los 9
-- procedimientos legacy que 040 cierra.
--
-- No modifica nada: transacción READ ONLY y ROLLBACK al final.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='60s';

SELECT current_setting('server_version')                        AS version_pg,
       current_user                                              AS rol_conexion,
       (SELECT rolsuper      FROM pg_roles WHERE rolname=current_user) AS es_superuser,
       (SELECT rolbypassrls  FROM pg_roles WHERE rolname=current_user) AS bypass_rls,
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public' AND c.relkind='r')             AS tablas_public,
       (SELECT count(*) FROM information_schema.columns
         WHERE table_schema='public' AND column_name='empresa_id') AS columnas_empresa_id,
       (SELECT count(*) FROM pg_policies WHERE schemaname='public') AS policies_public,
       (SELECT count(*) FROM pg_roles WHERE rolname LIKE 'fashionstore%') AS roles_fashionstore,
       (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname='public' AND p.prokind='f')             AS funciones_public,
       (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname='public' AND p.prokind='p')             AS procedimientos_public,
       (SELECT count(*) FROM pg_trigger tg JOIN pg_class c ON c.oid=tg.tgrelid
         JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public' AND NOT tg.tgisinternal)       AS triggers_public;

-- Vault se consulta aparte: si el proyecto no lo tiene, la instantánea no aborta
-- (050a sí abortaría, y ese es justamente el dato que interesa registrar).
DO $vault$
DECLARE n integer;
BEGIN
 IF to_regclass('vault.decrypted_secrets') IS NULL THEN
  RAISE NOTICE 'vault.decrypted_secrets: NO DISPONIBLE (050a abortara)';
 ELSE
  EXECUTE $q$SELECT count(*) FROM vault.decrypted_secrets
              WHERE name IN('fashionstore_tenant_jwt_hs256_active',
                            'fashionstore_tenant_jwt_hs256_previous')$q$ INTO n;
  RAISE NOTICE 'vault.decrypted_secrets: % secreto(s) FashionStore', n;
 END IF;
END $vault$;

-- Cantidades que 000 (antes) y 060 (después) exigen idénticas.
SELECT 'usuario' AS tabla, count(*) AS filas FROM public.usuario
UNION ALL SELECT 'sucursal',          count(*) FROM public.sucursal
UNION ALL SELECT 'cliente',           count(*) FROM public.cliente
UNION ALL SELECT 'empleado',          count(*) FROM public.empleado
UNION ALL SELECT 'producto',          count(*) FROM public.producto
UNION ALL SELECT 'variante_producto', count(*) FROM public.variante_producto
UNION ALL SELECT 'inventario',        count(*) FROM public.inventario
UNION ALL SELECT 'reserva',           count(*) FROM public.reserva
UNION ALL SELECT 'venta',             count(*) FROM public.venta
UNION ALL SELECT 'pago',              count(*) FROM public.pago
UNION ALL SELECT 'bitacora',          count(*) FROM public.bitacora
ORDER BY 1;

-- Quién puede ejecutar hoy los procedimientos que 040 cierra.
SELECT p.proname AS procedimiento,
       coalesce(string_agg(DISTINCT coalesce(g.rolname,'PUBLIC'), ', '
                           ORDER BY coalesce(g.rolname,'PUBLIC')),
                '(sin ACL explícita: sólo owner)') AS puede_ejecutar
FROM pg_proc p
JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
LEFT JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a ON true
LEFT JOIN pg_roles g ON g.oid=a.grantee
WHERE p.prokind='p'
  AND p.proname IN('sp_ajustar_inventario','sp_cancelar_reserva','sp_confirmar_venta',
                   'sp_crear_reserva_desde_carrito','sp_crear_reserva',
                   'sp_finalizar_reserva_sin_compra','sp_recalcular_total_venta',
                   'sp_recibir_orden_compra','sp_registrar_devolucion')
GROUP BY p.proname
ORDER BY 1;

ROLLBACK;
