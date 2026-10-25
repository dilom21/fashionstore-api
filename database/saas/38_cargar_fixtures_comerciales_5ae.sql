-- FashionStore / B3-5AE: SEMILLA FICTICIA CON DATOS COMERCIALES REALES DE LAB.
-- UNICO DESTINO: 127.0.0.1:55432 / fashionstore_rls_lab_5u / PG18.
-- Sin credenciales ni informacion privada; fuente 5432 NO CONSULTADA.
-- Falla de cualquier FK, CHECK o trigger -> ROLLBACK COMPLETO.
BEGIN;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SELECT set_config('app.ip','127.0.0.1',true);
DO $seed$
DECLARE
 v RECORD;
 v_old record;
 v_count bigint;
 v_ciudad bigint; v_talla bigint; v_color bigint;
 v_cliente bigint; v_sucursal bigint; v_categoria bigint;
 v_producto bigint; v_temporada bigint; v_variante bigint;
 v_inventario bigint; v_carrito bigint; v_reserva bigint;
BEGIN
 IF current_database() <> 'fashionstore_rls_lab_5u'
    OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'
    OR inet_server_port() <> 55432
    OR session_user <> 'postgres'
    OR current_user <> 'postgres'
    OR current_setting('server_version_num')::int / 10000 <> 18
    OR lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/'))
      <> 'c:/si2_parcial1/fashionstore-rls-lab-5v-data'
 THEN
    RAISE EXCEPTION '5AE: entorno no corresponde al laboratorio';
 END IF;
 IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r') <> 46
   OR (SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND co.contype='f' AND co.convalidated
        AND co.conname LIKE 'fk_tenant_%') <> 48
   OR (SELECT count(*) FROM pg_policies WHERE schemaname='public') <> 35
   OR (SELECT count(*) FROM pg_roles WHERE rolname IN
        ('fashionstore_5ab_a','fashionstore_5ab_b')
        AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls) <> 2
 THEN
   RAISE EXCEPTION '5AE: baseline de laboratorio inconsistente';
 END IF;
 IF (SELECT count(*) FROM public.empresa) <> 2 OR
    (SELECT count(*) FROM public.empresa WHERE slug IN
        ('empresa-lab-a','empresa-lab-b')) <> 2 THEN
    RAISE EXCEPTION '5AE: no hay exactamente dos empresas ficticias';
 END IF;
 -- Todas las otras tablas deben estar VACIAS para evitar mezclar sesiones/datos.
 FOR v_old IN
   SELECT c.oid::regclass AS tabla,c.relname
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r' AND c.relname <> 'empresa'
 LOOP
   EXECUTE format('SELECT count(*) FROM %s',v_old.tabla) INTO v_count;
   IF v_count <> 0 THEN
     RAISE EXCEPTION '5AE: datos preexistentes en %. NO reintentar semilla',v_old.relname;
   END IF;
 END LOOP;

 INSERT INTO public.ciudad(nombre) VALUES('Ciudad-Ficticia-5AE') RETURNING id INTO v_ciudad;
 INSERT INTO public.talla(nombre) VALUES('Talla-Lab-5AE') RETURNING id INTO v_talla;
 INSERT INTO public.color(nombre) VALUES('Color-Lab-5AE') RETURNING id INTO v_color;

 FOR v IN SELECT id,slug FROM public.empresa
          WHERE slug IN ('empresa-lab-a','empresa-lab-b')
          ORDER BY slug
 LOOP
   INSERT INTO public.cliente(empresa_id,nombre,apellido)
      VALUES(v.id,'Cliente-Ficticio-'||v.slug,'Prueba')
      RETURNING id INTO v_cliente;
   INSERT INTO public.sucursal(empresa_id,ciudad_id,nombre,direccion)
      VALUES(v.id,v_ciudad,'Sucursal-Ficticia-'||v.slug,'Direccion academica ficticia')
      RETURNING id INTO v_sucursal;
   INSERT INTO public.categoria(empresa_id,nombre)
      VALUES(v.id,'Categoria-Ficticia-'||v.slug)
      RETURNING id INTO v_categoria;
   INSERT INTO public.producto(empresa_id,categoria_id,nombre,precio)
      VALUES(v.id,v_categoria,'Producto-Ficticio-'||v.slug,100.00)
      RETURNING id INTO v_producto;
   INSERT INTO public.temporada(empresa_id,nombre,fecha_inicio,fecha_fin)
      VALUES(v.id,'Temporada-Ficticia-'||v.slug,DATE '2026-01-01',DATE '2027-12-31')
      RETURNING id INTO v_temporada;
   INSERT INTO public.variante_producto(empresa_id,producto_id,talla_id,color_id,sku)
      VALUES(v.id,v_producto,v_talla,v_color,'SKU-5AE-'||v.slug)
      RETURNING id INTO v_variante;
   INSERT INTO public.inventario(
       empresa_id,sucursal_id,variante_producto_id,temporada_id,
       stock_actual,stock_reservado)
      VALUES(v.id,v_sucursal,v_variante,v_temporada,5,0)
      RETURNING id INTO v_inventario;
   INSERT INTO public.carrito(empresa_id,cliente_id,sucursal_id,estado)
      VALUES(v.id,v_cliente,v_sucursal,'ACTIVO')
      RETURNING id INTO v_carrito;
   INSERT INTO public.detalle_carrito(empresa_id,carrito_id,inventario_id,cantidad)
      VALUES(v.id,v_carrito,v_inventario,1);
   INSERT INTO public.reserva(
       empresa_id,cliente_id,sucursal_id,carrito_id,fecha_atencion,estado)
      VALUES(v.id,v_cliente,v_sucursal,v_carrito,
             now()+ interval '1 day','PENDIENTE')
      RETURNING id INTO v_reserva;
   INSERT INTO public.detalle_reserva(empresa_id,reserva_id,inventario_id,cantidad)
      VALUES(v.id,v_reserva,v_inventario,1);
 END LOOP;

 IF (SELECT count(*) FROM public.carrito) <> 2
    OR (SELECT count(*) FROM public.detalle_carrito) <> 2
    OR (SELECT count(*) FROM public.inventario) <> 2
    OR (SELECT count(*) FROM public.reserva) <> 2
    OR (SELECT count(*) FROM public.detalle_reserva) <> 2
    OR (SELECT count(*) FROM public.bitacora WHERE empresa_id IN
        (SELECT id FROM public.empresa)) < 2
 THEN
    RAISE EXCEPTION '5AE: fixtures/carritos/reservas/bitacora incompletos';
 END IF;
 IF EXISTS(SELECT 1 FROM public.inventario
        WHERE stock_actual<>5 OR stock_reservado<>0) THEN
    RAISE EXCEPTION '5AE: inventario ficticio inesperado';
 END IF;
 RAISE NOTICE '5AE FIXTURES PRE-COMMIT OK: 2 carritos, 2 inventarios, 2 reservas';
END
$seed$;
COMMIT;
SELECT '5AE OK: 2 EMPRESAS CON CARRITO/INVENTARIO/RESERVA FICTICIOS' AS resultado;
-- NO certifica automaticamente permisos de UPDATE/INSERT ni concurrencia.
