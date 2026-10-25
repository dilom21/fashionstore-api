-- FashionStore B3/5AB - SOLO laboratorio ya creado en 127.0.0.1:55432.
-- Dos empresas FICTICIAS, dos roles LOGIN restringidos, RLS SELECT por
-- identidad SQL de SESSION_USER (NO depende de app.empresa_id).
-- No crea credenciales de usuario real ni importa informacion historica.
-- Las operaciones INSERT/UPDATE comerciales continuan DENEGADAS a roles A/B.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL password_encryption = 'scram-sha-256';

DO $preflight$
DECLARE v_tabla record; v_count bigint;
BEGIN
  IF current_database() <> 'fashionstore_rls_lab_5u'
     OR host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'
     OR inet_server_port() <> 55432
     OR session_user <> 'postgres'
     OR current_user <> 'postgres'
     OR current_setting('server_version_num')::int / 10000 <> 18
     OR lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/'))
       <> lower('C:/SI2_Parcial1/fashionstore-rls-lab-5v-data')
  THEN
    RAISE EXCEPTION '5AB: identificador de laboratorio o principal incorrectos';
  END IF;

  IF (SELECT count(*) FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r') <> 46
    OR (SELECT count(*) FROM pg_constraint co
      JOIN pg_class c ON c.oid=co.conrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND co.contype='f'
        AND co.convalidated AND co.conname LIKE 'fk_tenant_%') <> 48
  THEN
    RAISE EXCEPTION '5AB: estructura 46 tablas / 48 FK tenant inesperada';
  END IF;

  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN
       ('fashionstore_5ab_a','fashionstore_5ab_b'))
  THEN
    RAISE EXCEPTION '5AB: roles ya presentes. No repetir instalacion';
  END IF;

  IF EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public')
  THEN
    RAISE EXCEPTION '5AB: ya existen policies. Revision manual requerida';
  END IF;

  FOR v_tabla IN
      SELECT c.oid::regclass AS nombre
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r'
  LOOP
      EXECUTE format('SELECT count(*) FROM %s', v_tabla.nombre) INTO v_count;
      IF v_count <> 0 THEN
          RAISE EXCEPTION '5AB: tabla % contiene filas: abortar', v_tabla.nombre;
      END IF;
  END LOOP;

  IF (SELECT count(DISTINCT c.oid)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_attribute a ON a.attrelid=c.oid
      WHERE n.nspname='public' AND c.relkind='r'
        AND a.attname='empresa_id' AND a.attnum>0 AND NOT a.attisdropped) <> 34
  THEN
    RAISE EXCEPTION '5AB: se esperaban exactamente 34 tablas empresariales';
  END IF;
END
$preflight$;

-- No establecer PASSWORD en SQL versionado. Se asignara con psql password
-- interactivo DESPUES del COMMIT; hasta entonces login SQL imposible.
CREATE ROLE fashionstore_5ab_a WITH
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD NULL;
CREATE ROLE fashionstore_5ab_b WITH
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD NULL;

INSERT INTO public.empresa (razon_social, nombre_comercial, slug)
VALUES ('Empresa de Prueba A (ficticia)', 'Empresa Ficticia A', 'empresa-lab-a'),
       ('Empresa de Prueba B (ficticia)', 'Empresa Ficticia B', 'empresa-lab-b');

GRANT CONNECT ON DATABASE fashionstore_rls_lab_5u
    TO fashionstore_5ab_a, fashionstore_5ab_b;
GRANT USAGE ON SCHEMA public
    TO fashionstore_5ab_a, fashionstore_5ab_b;

-- Lectura minima para demostrar aislamiento. Usuario/password_hash NO se concede.
GRANT SELECT ON public.empresa, public.carrito, public.inventario,
    public.reserva, public.bitacora
    TO fashionstore_5ab_a, fashionstore_5ab_b;

-- Cada identidad SQL solo ve SU empresa.
ALTER TABLE public.empresa ENABLE ROW LEVEL SECURITY;
CREATE POLICY fs_lab_empresa_select ON public.empresa
  FOR SELECT TO fashionstore_5ab_a, fashionstore_5ab_b
  USING (
    (session_user='fashionstore_5ab_a' AND slug='empresa-lab-a')
    OR
    (session_user='fashionstore_5ab_b' AND slug='empresa-lab-b')
  );

-- Seleccion empresarial sin GUC editable; busqueda del ID en empresa
-- protegida por la policy anterior. No hay GRANT directo de INSERT/UPDATE/DELETE.
DO $policies$
DECLARE t record;
BEGIN
    FOR t IN
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='public' AND c.relkind='r'
          AND a.attname='empresa_id' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.relname);
        EXECUTE format($pol$
            CREATE POLICY fs_lab_tenant_select
              ON public.%I FOR SELECT TO fashionstore_5ab_a, fashionstore_5ab_b
              USING (
                  empresa_id = (
                    SELECT id FROM public.empresa
                    WHERE slug =
                    CASE session_user
                    WHEN 'fashionstore_5ab_a' THEN 'empresa-lab-a'
                    WHEN 'fashionstore_5ab_b' THEN 'empresa-lab-b'
                    ELSE NULL
                    END
                  )
              )
        $pol$,t.relname);
    END LOOP;
END
$policies$;

-- Garantias de instalacion sin efectuar acceso superusuario simulado.
DO $post$
BEGIN
 IF (SELECT count(*) FROM public.empresa) <> 2
    OR (SELECT count(*) FROM pg_policies WHERE schemaname='public') <> 35
    OR EXISTS (
       SELECT 1 FROM pg_roles
       WHERE rolname IN ('fashionstore_5ab_a','fashionstore_5ab_b')
         AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolinherit)
    )
 THEN
    RAISE EXCEPTION '5AB: fallo comprobacion de roles/policies';
 END IF;
END
$post$;
COMMIT;

SELECT '5AB SQL INSTALADO: 2 empresas ficticias, 2 roles SIN password, 35 policies SELECT.' AS resultado;
-- IMPORTANTE: asignar passwords con \password en otro paso interactivo.
