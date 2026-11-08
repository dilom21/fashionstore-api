/* FASHIONSTORE SAAS - PROPUESTA 080 V2
   NO EJECUTAR SIN APROBACION Y PRUEBA COMPLETA EN POSTGRESQL 17 AISLADO.

   Solo fashionstore_bootstrap obtiene EXECUTE. Las funciones pertenecen a
   fashionstore_bootstrap_owner (NOLOGIN/NOBYPASSRLS) y siguen sujetas a FORCE
   RLS. El instalador recibe SET al owner y este CREATE en el schema solo dentro
   de la transaccion. No se aceptan IDs, roles, estados ni claims del cliente. */
DO $no_ejecutar$ BEGIN
 RAISE EXCEPTION 'GUARDA: PROPUESTA 080 NO AUTORIZADA PARA EJECUCION - V2';
END $no_ejecutar$;

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_080_v2',0));

/* PRE-FLIGHT: falla antes de cambiar ACL, policies o funciones. */
DO $preflight$
DECLARE executor name:=current_user; owner_oid oid; item text;
BEGIN
 IF current_setting('server_version_num')::integer/10000<>17 THEN
  RAISE EXCEPTION '080 requiere PostgreSQL 17';
 END IF;
 SELECT oid INTO owner_oid FROM pg_roles
 WHERE rolname='fashionstore_bootstrap_owner'
   AND NOT rolcanlogin AND NOT rolbypassrls AND NOT rolsuper;
 IF owner_oid IS NULL THEN
  RAISE EXCEPTION 'bootstrap_owner debe ser NOLOGIN, NOBYPASSRLS y NOSUPERUSER';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_bootstrap'
   AND NOT rolcanlogin AND NOT rolbypassrls AND NOT rolsuper) THEN
  RAISE EXCEPTION 'fashionstore_bootstrap restringido no existe';
 END IF;
 IF NOT EXISTS(
  SELECT 1 FROM pg_auth_members m
  JOIN pg_roles g ON g.oid=m.roleid JOIN pg_roles u ON u.oid=m.member
  WHERE g.rolname='fashionstore_bootstrap_owner' AND u.rolname=executor
    AND m.admin_option) THEN
  RAISE EXCEPTION 'El instalador % no tiene ADMIN OPTION sobre bootstrap_owner',executor;
 END IF;
 IF NOT has_schema_privilege(executor,'fashionstore_private','USAGE')
    OR NOT has_schema_privilege(executor,'fashionstore_private','CREATE') THEN
  RAISE EXCEPTION 'El instalador necesita USAGE y CREATE en fashionstore_private';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_namespace n
   WHERE n.nspname='fashionstore_private' AND pg_get_userbyid(n.nspowner)=executor) THEN
  RAISE EXCEPTION 'El instalador debe ser owner de fashionstore_private para conceder CREATE temporal';
 END IF;
 FOREACH item IN ARRAY ARRAY['empresa','suscripcion','plan_saas','rol','usuario',
   'usuario_empresa','cliente'] LOOP
  IF to_regclass('public.'||item) IS NULL THEN RAISE EXCEPTION 'Falta public.%',item; END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=item
      AND c.relrowsecurity AND c.relforcerowsecurity) THEN
   RAISE EXCEPTION 'public.% debe conservar RLS y FORCE RLS',item;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname=item
      AND pg_get_userbyid(c.relowner)=executor) THEN
   RAISE EXCEPTION 'El instalador debe ser owner de public.% para ACL y policies atomicas',item;
  END IF;
 END LOOP;
 IF to_regprocedure('fashionstore_private.bootstrap_empresas_publicas()') IS NOT NULL
    OR to_regprocedure('fashionstore_private.bootstrap_registrar_cliente(text,text,text,text,text,text,text,text,date)') IS NOT NULL THEN
  RAISE EXCEPTION 'Las funciones 080 ya existen; no sobrescribir';
 END IF;
 FOREACH item IN ARRAY ARRAY['p_bootstrap_080_suscripcion_select',
  'p_bootstrap_080_plan_select','p_bootstrap_080_rol_select',
  'p_bootstrap_080_usuario_insert','p_bootstrap_080_membresia_insert',
  'p_bootstrap_080_cliente_insert','p_bootstrap_080_cliente_id_select'] LOOP
  IF EXISTS(SELECT 1 FROM pg_policies WHERE policyname=item) THEN
   RAISE EXCEPTION 'La policy % ya existe',item;
  END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.usuario'::regclass
   AND conname='uq_usuario_correo') OR to_regclass('public.uq_usuario_correo_lower') IS NULL THEN
  RAISE EXCEPTION 'Faltan protecciones globales de correo';
 END IF;
 IF (SELECT count(*) FROM pg_constraint WHERE conrelid='public.cliente'::regclass
   AND conname IN('uq_cliente_empresa_ci','uq_cliente_empresa_usuario'))<>2 THEN
  RAISE EXCEPTION 'Faltan restricciones tenant unicas de cliente';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.cliente'::regclass
   AND conname='fk_tenant_cliente_usuario' AND convalidated) THEN
  RAISE EXCEPTION 'FK tenant cliente -> usuario_empresa no esta validada';
 END IF;
 IF pg_get_serial_sequence('public.usuario','id') IS NULL
    OR pg_get_serial_sequence('public.usuario_empresa','id') IS NULL
    OR pg_get_serial_sequence('public.cliente','id') IS NULL THEN
  RAISE EXCEPTION 'No se resolvieron las secuencias Identity';
 END IF;
 IF NOT has_column_privilege('fashionstore_bootstrap_owner','public.usuario','id','SELECT')
    OR NOT has_column_privilege('fashionstore_bootstrap_owner','public.empresa','id','SELECT')
    OR NOT has_column_privilege('fashionstore_bootstrap_owner','public.empresa','slug','SELECT')
    OR NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public'
      AND tablename='usuario' AND policyname='p_bootstrap_owner_usuario')
    OR NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public'
      AND tablename='empresa' AND policyname='p_bootstrap_owner_empresa') THEN
  RAISE EXCEPTION 'Faltan ACL/policies bootstrap base instaladas por 050a/050b';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.usuario'::regclass
   AND tgname='trg_bitacora_usuario' AND NOT tgisinternal)
    OR NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.cliente'::regclass
   AND tgname='trg_bitacora_cliente' AND NOT tgisinternal) THEN
  RAISE EXCEPTION 'Faltan triggers de auditoria de usuario o cliente';
 END IF;
END $preflight$;

/* ACL minimas del owner. El login bootstrap nunca obtiene tablas. */
GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_bootstrap_owner;
GRANT SELECT(nombre_comercial) ON public.empresa TO fashionstore_bootstrap_owner;
GRANT SELECT(empresa_id,plan_saas_id,fecha_inicio,fecha_vencimiento,estado)
 ON public.suscripcion TO fashionstore_bootstrap_owner;
GRANT SELECT(id,estado) ON public.plan_saas TO fashionstore_bootstrap_owner;
GRANT SELECT(id,nombre,estado) ON public.rol TO fashionstore_bootstrap_owner;
GRANT INSERT(rol_id,correo,password_hash,fecha_creacion,estado)
 ON public.usuario TO fashionstore_bootstrap_owner;
GRANT INSERT(usuario_id,empresa_id,rol_id,estado)
 ON public.usuario_empresa TO fashionstore_bootstrap_owner;
GRANT INSERT(usuario_id,empresa_id,nombre,apellido,telefono,sexo,ci,fecha_nacimiento,estado)
 ON public.cliente TO fashionstore_bootstrap_owner;
-- INSERT ... RETURNING cliente.id requiere exactamente este SELECT de columna.
GRANT SELECT(id) ON public.cliente TO fashionstore_bootstrap_owner;

DO $identity_sequences$
DECLARE seq text;
BEGIN
 FOREACH seq IN ARRAY ARRAY[pg_get_serial_sequence('public.usuario','id'),
  pg_get_serial_sequence('public.usuario_empresa','id'),
  pg_get_serial_sequence('public.cliente','id')] LOOP
  EXECUTE format('GRANT USAGE ON SEQUENCE %s TO fashionstore_bootstrap_owner',seq);
 END LOOP;
END $identity_sequences$;

/* Policies estrechas; FORCE RLS sigue activo. */
CREATE POLICY p_bootstrap_080_suscripcion_select ON public.suscripcion
 FOR SELECT TO fashionstore_bootstrap_owner USING(estado='ACTIVA'
 AND (timezone('America/La_Paz',clock_timestamp()))::date BETWEEN fecha_inicio AND fecha_vencimiento);
CREATE POLICY p_bootstrap_080_plan_select ON public.plan_saas
 FOR SELECT TO fashionstore_bootstrap_owner USING(estado IS TRUE);
CREATE POLICY p_bootstrap_080_rol_select ON public.rol
 FOR SELECT TO fashionstore_bootstrap_owner USING(estado IS TRUE AND nombre='CLIENTE');
CREATE POLICY p_bootstrap_080_usuario_insert ON public.usuario
 FOR INSERT TO fashionstore_bootstrap_owner WITH CHECK(estado IS TRUE AND rol_id=(
  SELECT r.id FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE));
CREATE POLICY p_bootstrap_080_membresia_insert ON public.usuario_empresa
 FOR INSERT TO fashionstore_bootstrap_owner WITH CHECK(estado IS TRUE AND rol_id=(
  SELECT r.id FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE)
  AND EXISTS(SELECT 1 FROM public.empresa e WHERE e.id=empresa_id AND e.estado IS TRUE));
CREATE POLICY p_bootstrap_080_cliente_insert ON public.cliente
 FOR INSERT TO fashionstore_bootstrap_owner WITH CHECK(estado IS TRUE
  AND EXISTS(SELECT 1 FROM public.empresa e WHERE e.id=empresa_id AND e.estado IS TRUE));
-- Solo se concedio SELECT(id); CI y datos personales siguen inaccesibles.
CREATE POLICY p_bootstrap_080_cliente_id_select ON public.cliente
 FOR SELECT TO fashionstore_bootstrap_owner USING(estado IS TRUE);

/* Ownership autorizado PG17: SET y CREATE temporales, sin ALTER OWNER tardio. */
DO $temporary_set$
DECLARE executor name:=current_user;
BEGIN
 EXECUTE format('GRANT fashionstore_bootstrap_owner TO %I WITH SET TRUE, INHERIT FALSE',executor);
 IF NOT pg_has_role(executor,'fashionstore_bootstrap_owner','SET') THEN
  RAISE EXCEPTION 'No se pudo habilitar SET temporal al bootstrap_owner';
 END IF;
END $temporary_set$;
GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_bootstrap_owner;
SET LOCAL ROLE fashionstore_bootstrap_owner;

CREATE FUNCTION fashionstore_private.bootstrap_empresas_publicas()
RETURNS TABLE(slug text,nombre_comercial text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT e.slug::text,e.nombre_comercial::text FROM public.empresa e
 WHERE e.estado IS TRUE AND EXISTS(SELECT 1 FROM public.suscripcion s
  JOIN public.plan_saas p ON p.id=s.plan_saas_id
  WHERE s.empresa_id=e.id AND s.estado='ACTIVA' AND p.estado IS TRUE
    AND (timezone('America/La_Paz',clock_timestamp()))::date BETWEEN s.fecha_inicio AND s.fecha_vencimiento)
 ORDER BY lower(e.nombre_comercial),e.slug
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.bootstrap_empresas_publicas() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fashionstore_private.bootstrap_empresas_publicas() TO fashionstore_bootstrap;

CREATE FUNCTION fashionstore_private.bootstrap_registrar_cliente(
 p_empresa_slug text,p_correo text,p_password_hash text,p_nombre text,
 p_apellido text,p_ci text,p_telefono text,p_sexo text,p_fecha_nacimiento date)
RETURNS TABLE(usuario_id bigint,cliente_id bigint,empresa_slug text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
DECLARE
 v_empresa_id bigint; v_rol_id bigint; v_usuario_id bigint; v_cliente_id bigint;
 v_correo text:=lower(btrim(p_correo));
 v_slug text:=lower(btrim(p_empresa_slug));
 v_constraint text;
BEGIN
 IF v_slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$' OR v_correo IS NULL
    OR length(v_correo) NOT BETWEEN 5 AND 150 OR position('@' IN v_correo)<=1
    OR p_password_hash IS NULL OR p_password_hash !~ '^\$argon2(id|i|d)\$'
    OR length(btrim(p_nombre))<2 OR length(btrim(p_apellido))<2
    OR length(btrim(p_ci))<3 OR length(btrim(p_telefono))<1
    OR p_sexo NOT IN('MASCULINO','FEMENINO','OTRO','NO_ESPECIFICA')
    OR p_fecha_nacimiento IS NULL OR p_fecha_nacimiento>CURRENT_DATE THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='datos de registro invalidos';
 END IF;
 SELECT e.id INTO v_empresa_id FROM public.empresa e
 WHERE e.slug=v_slug AND e.estado IS TRUE
 AND EXISTS(SELECT 1 FROM public.suscripcion s JOIN public.plan_saas p ON p.id=s.plan_saas_id
  WHERE s.empresa_id=e.id AND s.estado='ACTIVA' AND p.estado IS TRUE
   AND (timezone('America/La_Paz',clock_timestamp()))::date BETWEEN s.fecha_inicio AND s.fecha_vencimiento);
 IF v_empresa_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P2001',MESSAGE='empresa no disponible'; END IF;
 SELECT r.id INTO v_rol_id FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE;
 IF v_rol_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P2004',MESSAGE='rol cliente no disponible'; END IF;

 /* No hay preconsulta: los indices globales detectan tambien usuarios inactivos
    y serializan carreras. Nunca se vincula automaticamente una identidad. */
 BEGIN
  INSERT INTO public.usuario(rol_id,correo,password_hash,fecha_creacion,estado)
  VALUES(v_rol_id,v_correo,p_password_hash,clock_timestamp(),true)
  RETURNING id INTO v_usuario_id;
 EXCEPTION WHEN unique_violation THEN
  GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME;
  IF v_constraint IN('uq_usuario_correo','uq_usuario_correo_lower') THEN
   RAISE EXCEPTION USING ERRCODE='P2002',MESSAGE='identidad ya existente';
  END IF;
  RAISE;
 END;
 INSERT INTO public.usuario_empresa(usuario_id,empresa_id,rol_id,estado)
 VALUES(v_usuario_id,v_empresa_id,v_rol_id,true);
 BEGIN
  INSERT INTO public.cliente(usuario_id,empresa_id,nombre,apellido,telefono,sexo,ci,fecha_nacimiento,estado)
  VALUES(v_usuario_id,v_empresa_id,btrim(p_nombre),btrim(p_apellido),
   btrim(p_telefono),p_sexo,btrim(p_ci),p_fecha_nacimiento,true)
  RETURNING id INTO v_cliente_id;
 EXCEPTION WHEN unique_violation THEN
  GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME;
  IF v_constraint IN('uq_cliente_empresa_ci','uq_cliente_empresa_usuario') THEN
   RAISE EXCEPTION USING ERRCODE='P2003',MESSAGE='cliente ya existente';
  END IF;
  RAISE;
 END;
 RETURN QUERY SELECT v_usuario_id,v_cliente_id,v_slug;
END $fn$;
REVOKE ALL ON FUNCTION fashionstore_private.bootstrap_registrar_cliente(
 text,text,text,text,text,text,text,text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fashionstore_private.bootstrap_registrar_cliente(
 text,text,text,text,text,text,text,text,date) TO fashionstore_bootstrap;

RESET ROLE;
REVOKE CREATE ON SCHEMA fashionstore_private FROM fashionstore_bootstrap_owner;
DO $revoke_temporary_set$
DECLARE executor name:=current_user;
BEGIN
 EXECUTE format('REVOKE fashionstore_bootstrap_owner FROM %I GRANTED BY %I',executor,executor);
 IF pg_has_role(executor,'fashionstore_bootstrap_owner','SET') THEN
  RAISE EXCEPTION 'Persistio SET temporal al bootstrap_owner';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid
  JOIN pg_roles u ON u.oid=m.member JOIN pg_roles gr ON gr.oid=m.grantor
  WHERE g.rolname='fashionstore_bootstrap_owner' AND u.rolname=executor AND gr.rolname=executor) THEN
  RAISE EXCEPTION 'Persistio membresia temporal concedida por el instalador';
 END IF;
END $revoke_temporary_set$;

/* POST-FLIGHT: cualquier desviacion revierte toda la instalacion. */
DO $postflight$
DECLARE owner_oid oid; seq text;
BEGIN
 SELECT oid INTO STRICT owner_oid FROM pg_roles WHERE rolname='fashionstore_bootstrap_owner'
  AND NOT rolcanlogin AND NOT rolbypassrls AND NOT rolsuper;
 IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='fashionstore_private' AND p.proname IN('bootstrap_empresas_publicas','bootstrap_registrar_cliente')
   AND p.proowner=owner_oid AND p.prosecdef)<>2 THEN
  RAISE EXCEPTION 'Owner o SECURITY DEFINER final inesperado';
 END IF;
 IF has_schema_privilege('fashionstore_bootstrap_owner','fashionstore_private','CREATE') THEN
  RAISE EXCEPTION 'bootstrap_owner conserva CREATE temporal';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
  WHERE n.nspname='fashionstore_private'
   AND p.proname IN('bootstrap_empresas_publicas','bootstrap_registrar_cliente')
   AND a.grantee=0 AND a.privilege_type='EXECUTE') THEN
  RAISE EXCEPTION 'PUBLIC conserva EXECUTE';
 END IF;
 IF NOT has_function_privilege('fashionstore_bootstrap','fashionstore_private.bootstrap_empresas_publicas()','EXECUTE')
 OR NOT has_function_privilege('fashionstore_bootstrap','fashionstore_private.bootstrap_registrar_cliente(text,text,text,text,text,text,text,text,date)','EXECUTE') THEN
  RAISE EXCEPTION 'bootstrap no puede ejecutar ambas funciones';
 END IF;
 IF has_table_privilege('fashionstore_bootstrap','public.usuario','INSERT')
 OR has_table_privilege('fashionstore_bootstrap','public.cliente','SELECT')
 OR has_table_privilege('fashionstore_bootstrap','public.cliente','INSERT') THEN
  RAISE EXCEPTION 'bootstrap recibio privilegios directos sobre tablas';
 END IF;
 IF NOT has_column_privilege('fashionstore_bootstrap_owner','public.cliente','id','SELECT')
 OR has_column_privilege('fashionstore_bootstrap_owner','public.cliente','ci','SELECT') THEN
  RAISE EXCEPTION 'SELECT de cliente no es minimo';
 END IF;
 IF (SELECT count(*) FROM pg_policies WHERE policyname LIKE 'p_bootstrap_080_%')<>7 THEN
  RAISE EXCEPTION 'Cantidad inesperada de policies 080';
 END IF;
 FOREACH seq IN ARRAY ARRAY[pg_get_serial_sequence('public.usuario','id'),
  pg_get_serial_sequence('public.usuario_empresa','id'),pg_get_serial_sequence('public.cliente','id')] LOOP
  IF NOT has_sequence_privilege('fashionstore_bootstrap_owner',seq,'USAGE') THEN
   RAISE EXCEPTION 'Falta USAGE sobre Identity %',seq;
  END IF;
 END LOOP;
END $postflight$;
COMMIT;

/* PLAN DE REVERSION CONTROLADA (documental; nunca automatico).
   Mantener FastAPI detenida. Si 080 ya hizo COMMIT y se autoriza revertir:
   - BEGIN;
   - DROP de las dos funciones 080;
   - DROP de las siete policies p_bootstrap_080_*;
   - REVOKE SELECT(id) e INSERT de cliente, usuario_empresa y usuario al owner;
   - REVOKE SELECT de rol, plan_saas y suscripcion, y nombre_comercial de empresa;
   - REVOKE USAGE de las tres secuencias resueltas con pg_get_serial_sequence;
   - COMMIT despues de verificar que bootstrap no tiene EXECUTE ni tablas.

   No eliminar usuarios/clientes creados correctamente. Si falla antes del
   COMMIT, PostgreSQL revierte atomicamente ACL, policies, funciones, CREATE y
   membresia temporal; en ese caso no ejecutar el plan de reversion.

   MATRIZ DE PRUEBA AISLADA OBLIGATORIA (no ejecutar en Supabase compartido):
   1. Instalar una copia sin la guarda como el ejecutor CREATEROLE equivalente;
      exigir owner efectivo bootstrap_owner, NOBYPASSRLS, 7 policies, sin
      CREATE residual, sin SET residual y sin ACL de tablas para bootstrap.
   2. Como login bootstrap, listar: solo empresas activas con plan y
      suscripcion ACTIVA/vigente; una SUSPENDIDA no aparece ni admite registro.
   3. Registrar dos correos distintos en dos empresas y comprobar empresa_id,
      rol CLIENTE, FK compuesta, triggers de auditoria y ausencia de secretos.
   4. Mismo CI en empresas distintas: permitido. Mismo CI en la misma empresa:
      P2003 y cero filas parciales.
   5. Correo activo e inactivo ya existente: ambos P2002, sin vinculacion.
   6. Dos sesiones concurrentes con el mismo correo: exactamente una confirma;
      la otra obtiene P2002 por uq_usuario_correo/uq_usuario_correo_lower.
   7. Forzar fallo despues de crear usuario (CI duplicado): verificar rollback
      de usuario, usuario_empresa y cliente de esa llamada.
   8. Intentar invocar con rol_id, empresa_id o estado: la firma SQL no acepta
      esos argumentos; el contrato HTTP Pydantic rechaza campos extra.
   9. Ejecutar todas las altas de prueba dentro de transacciones reversibles y
      ROLLBACK; comparar conteos iniciales/finales. */
