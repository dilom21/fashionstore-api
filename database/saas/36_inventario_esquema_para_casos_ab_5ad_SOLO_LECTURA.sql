-- FashionStore B3 / 5AD: catalogo estructural (NO datos) para pruebas A01-A14.
-- SOLO 127.0.0.1:55432 / fashionstore_rls_lab_5u, usuario postgres.
-- Todo SQL es READ ONLY, finaliza ROLLBACK, NO muestra password ni datos reales.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
  IF current_database() <> 'fashionstore_rls_lab_5u'
     OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'
     OR inet_server_port() <> 55432
     OR session_user <> 'postgres'
     OR current_user <> 'postgres'
     OR current_setting('server_version_num')::int / 10000 <> 18
     OR lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/'))
       <> 'c:/si2_parcial1/fashionstore-rls-lab-5v-data'
  THEN
    RAISE EXCEPTION '5AD: entorno distinto del laboratorio PostgreSQL 18';
  END IF;
  IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r') <> 46
     OR (SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
         JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public' AND co.contype='f' AND co.convalidated
           AND co.conname LIKE 'fk_tenant_%') <> 48
     OR (SELECT count(*) FROM pg_policies WHERE schemaname='public') <> 35
     OR (SELECT count(*) FROM public.empresa WHERE slug IN
       ('empresa-lab-a','empresa-lab-b')) <> 2
  THEN
    RAISE EXCEPTION '5AD: contrato estructural laboratorio cambió; detener';
  END IF;
END
$guard$;

SELECT '5AD_PREFLIGHT_OK_46_TABLAS_48_FK_35_POLICIES_2_EMPRESAS' AS estado;

-- Se omiten expresiones DEFAULT, datos de filas y definiciones de funciones.
-- Sirve para escribir INSERTs ficticios respetando NOT NULL/FK/CHECK/trigger.
SELECT '01_COLUMNAS' AS seccion,
       table_name AS tabla,
       string_agg(
           column_name || ':' || udt_name ||
           CASE WHEN is_nullable='NO' THEN ':NN' ELSE '' END ||
           CASE WHEN column_default IS NOT NULL THEN ':DEF' ELSE '' END ||
           CASE WHEN is_identity='YES' THEN ':IDENTITY' ELSE '' END,
           ', ' ORDER BY ordinal_position
       ) AS estructura
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN (
    'ciudad','rol','usuario','usuario_empresa','empresa','plan_saas',
    'suscripcion','cliente','sucursal','categoria','producto',
    'variante_producto','color','talla','temporada','inventario',
    'carrito','detalle_carrito','reserva','detalle_reserva',
    'bitacora','movimiento_inventario','coleccion','proveedor'
  )
GROUP BY table_name
ORDER BY table_name;

SELECT '02_RESTRICCIONES' AS seccion,
       c.conrelid::regclass::text AS tabla,
       c.conname AS nombre,
       c.contype AS tipo,
       pg_get_constraintdef(c.oid, true) AS definicion
FROM pg_constraint c
WHERE c.connamespace='public'::regnamespace
  AND c.conrelid::regclass::text IN (
    'ciudad','rol','usuario','usuario_empresa','empresa','plan_saas',
    'suscripcion','cliente','sucursal','categoria','producto',
    'variante_producto','color','talla','temporada','inventario',
    'carrito','detalle_carrito','reserva','detalle_reserva',
    'bitacora','movimiento_inventario','coleccion','proveedor'
  )
ORDER BY c.conrelid::regclass::text, c.contype, c.conname;

SELECT '03_TRIGGERS' AS seccion,
       t.tgrelid::regclass::text AS tabla,
       t.tgname AS trigger,
       t.tgenabled AS habilitado,
       t.tgfoid::regprocedure::text AS funcion
FROM pg_trigger t
WHERE NOT t.tgisinternal
  AND t.tgrelid::regclass::text IN (
    'ciudad','usuario','empresa','cliente','sucursal','producto',
    'inventario','carrito','detalle_carrito','reserva',
    'detalle_reserva','bitacora','movimiento_inventario'
  )
ORDER BY t.tgrelid::regclass::text,t.tgname;

-- Solo CONTEOS de filas, jamás contenido.
SELECT '04_FILAS_POR_TABLA' AS seccion,
       table_name AS tabla,
       (xpath('/r/n/text()',
          query_to_xml(format('SELECT count(*) AS n FROM public.%I',table_name),
                       false,true,'')))[1]::text AS total
FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE'
  AND table_name IN (
    'empresa','ciudad','rol','usuario','cliente','sucursal','categoria',
    'producto','variante_producto','color','talla','temporada',
    'inventario','carrito','detalle_carrito','reserva',
    'detalle_reserva','bitacora','movimiento_inventario'
  )
ORDER BY table_name;

ROLLBACK;
