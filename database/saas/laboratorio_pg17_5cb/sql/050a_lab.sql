-- COPIA DE LABORATORIO 5CB de 050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql
-- Cambio 1: se retiró la guarda 'BLOQUEADO' de la primera línea
-- para poder ensayarla en un clúster desechable.
-- No se alteró nada más.
-- Las 1 corrección(es) 5CB de este archivo ya están
-- incorporadas en la propuesta original: no se duplican aquí.
-- sha256 del original: 5981810d14aede018342062f6e041ee31ed3e4490a98c9d59ed46305614679bc
-- PROPUESTA LOCAL. NO EJECUTAR hasta retirar deliberadamente esta guarda
-- en una copia de laboratorio y cargar el secreto en Vault fuera del SQL.
-- REVISIÓN 5CA (2026-11-06). Sigue sin ejecutarse: la guarda anterior es
-- deliberada y sólo se retira en una copia de laboratorio. Se incorporan aquí
-- los huecos que el laboratorio 5CA demostró bloqueantes, con el mismo código
-- que allí pasó 73/73 PASS. Originales intactos en
-- respaldo_050_originales_5ca/.
--   HUECO 1: actor_coincide() no existía y 050c lo exige como guardián de SP.
--   HUECO 4: context_owner sin USAGE en extensions => hmac() falla y muere
--            toda verificación JWT (en Supabase tampoco se concede a PUBLIC).
--   HUECO 5: audit_owner sin USAGE en fashionstore_private => el trigger de
--            bitácora rompe toda escritura de la API.
--   HUECO 7: el login por empresa_slug no puede ejecutarse con el pool api
--            restringido (toda policy exige actor_claims()). Se añade una
--            proyección estrecha de bootstrap, igual que bootstrap_credencial.

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';

DO $preflight$
BEGIN
 IF current_setting('server_version_num')::integer/10000<>17 THEN
  RAISE EXCEPTION 'Se requiere PostgreSQL 17';
 END IF;
 IF to_regclass('vault.decrypted_secrets') IS NULL THEN
  RAISE EXCEPTION 'Supabase Vault no disponible';
 END IF;
 IF to_regprocedure('extensions.hmac(bytea,bytea,text)') IS NULL THEN
  RAISE EXCEPTION 'pgcrypto.hmac no está disponible en schema extensions';
 END IF;
 IF (SELECT count(*) FROM vault.decrypted_secrets
     WHERE name IN('fashionstore_tenant_jwt_hs256_active','fashionstore_tenant_jwt_hs256_previous')) NOT BETWEEN 1 AND 2 THEN
  RAISE EXCEPTION 'Vault debe contener una clave activa y opcionalmente una anterior';
 END IF;
END $preflight$;

DO $roles$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_api') THEN
  CREATE ROLE fashionstore_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_bootstrap') THEN
  CREATE ROLE fashionstore_bootstrap NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_platform_admin') THEN
  CREATE ROLE fashionstore_platform_admin NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_context_owner') THEN
  CREATE ROLE fashionstore_context_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_bootstrap_owner') THEN
  CREATE ROLE fashionstore_bootstrap_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_sp_owner') THEN
  CREATE ROLE fashionstore_sp_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fashionstore_audit_owner') THEN
  CREATE ROLE fashionstore_audit_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
END $roles$;

CREATE SCHEMA IF NOT EXISTS fashionstore_private;
REVOKE ALL ON SCHEMA fashionstore_private FROM PUBLIC;
DO $optional_supabase_roles$
DECLARE r text;
BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA fashionstore_private FROM %I',r);
  END IF;
 END LOOP;
END $optional_supabase_roles$;
GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_api,
 fashionstore_bootstrap,fashionstore_platform_admin,fashionstore_context_owner,
 fashionstore_bootstrap_owner,fashionstore_sp_owner,fashionstore_audit_owner;
-- 5CA HUECO 5: fashionstore_audit_owner entra en la lista anterior porque
-- fn_registrar_bitacora() (050c) es SECURITY DEFINER suyo y llama a
-- fashionstore_private.actor_usuario_id(). Sin USAGE, toda escritura de la API
-- aborta con "permission denied for schema fashionstore_private".
-- 5CA HUECO 4: actor_claims() también es SECURITY DEFINER de context_owner y
-- llama a extensions.hmac(); sin USAGE del esquema y EXECUTE de la función,
-- el fallo es "permission denied for schema extensions" (idéntico en Supabase,
-- que no concede extensions a PUBLIC).
GRANT USAGE ON SCHEMA extensions TO fashionstore_context_owner;
GRANT EXECUTE ON FUNCTION extensions.hmac(bytea,bytea,text)
 TO fashionstore_context_owner;
-- PostgreSQL exige CREATE para transferir ownership. Es temporal y se revoca
-- dentro de esta misma transacción después de crear las funciones.
GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_context_owner,
 fashionstore_bootstrap_owner;

-- La vista no contiene secretos en el archivo. Vault se aprovisiona mediante
-- Dashboard/operación segura y no mediante una migración versionada.
CREATE OR REPLACE VIEW fashionstore_private.jwt_keys
WITH(security_barrier=true) AS
SELECT name,decrypted_secret
FROM vault.decrypted_secrets
WHERE name IN('fashionstore_tenant_jwt_hs256_active','fashionstore_tenant_jwt_hs256_previous');
REVOKE ALL ON fashionstore_private.jwt_keys FROM PUBLIC,fashionstore_api,fashionstore_bootstrap;
GRANT SELECT ON fashionstore_private.jwt_keys TO fashionstore_context_owner;

CREATE OR REPLACE FUNCTION fashionstore_private.base64url_decode(p text)
RETURNS bytea LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT pg_catalog.decode(
   pg_catalog.rpad(pg_catalog.translate(p,'-_','+/'),
     length(p)+((4-length(p)%4)%4),'='),'base64');
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.base64url_decode(text) FROM PUBLIC,fashionstore_api,fashionstore_bootstrap;
ALTER FUNCTION fashionstore_private.base64url_decode(text) OWNER TO fashionstore_context_owner;

CREATE OR REPLACE FUNCTION fashionstore_private.actor_claims()
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $fn$
DECLARE
 token text; parts text[]; header jsonb; claims jsonb;
 supplied bytea; signing_input bytea; secret text; valid_sig boolean:=false;
 now_epoch bigint:=extract(epoch from clock_timestamp())::bigint;
 uid bigint; eid bigint; ctx text;
BEGIN
 token:=current_setting('app.actor_token',true);
 IF token IS NULL OR length(token)>8192 THEN RAISE EXCEPTION 'Contexto no autenticado' USING ERRCODE='42501'; END IF;
 parts:=string_to_array(token,'.');
 IF cardinality(parts)<>3 THEN RAISE EXCEPTION 'Token inválido' USING ERRCODE='42501'; END IF;
 BEGIN
  header:=convert_from(fashionstore_private.base64url_decode(parts[1]),'UTF8')::jsonb;
  claims:=convert_from(fashionstore_private.base64url_decode(parts[2]),'UTF8')::jsonb;
  supplied:=fashionstore_private.base64url_decode(parts[3]);
 EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'Token inválido' USING ERRCODE='42501'; END;
 IF header->>'alg'<>'HS256' OR coalesce(header->>'typ','JWT')<>'JWT' THEN RAISE EXCEPTION 'Algoritmo JWT no permitido' USING ERRCODE='42501'; END IF;
 signing_input:=convert_to(parts[1]||'.'||parts[2],'UTF8');
 FOR secret IN SELECT k.decrypted_secret FROM fashionstore_private.jwt_keys k LOOP
  valid_sig:=valid_sig OR supplied=extensions.hmac(signing_input,convert_to(secret,'UTF8'),'sha256');
 END LOOP;
 IF NOT valid_sig THEN RAISE EXCEPTION 'Firma JWT inválida' USING ERRCODE='42501'; END IF;
 IF claims->>'scope'<>'tenant' OR claims->>'contexto' NOT IN('cliente','personal')
    OR coalesce(claims->>'jti','')='' OR coalesce(claims->>'sub','')!~'^[1-9][0-9]*$'
    OR coalesce(claims->>'empresa_id','')!~'^[1-9][0-9]*$'
    OR coalesce(claims->>'exp','')!~'^[0-9]+$' OR (claims->>'exp')::bigint<=now_epoch
    OR coalesce(claims->>'iat','')!~'^[0-9]+$' OR (claims->>'iat')::bigint>now_epoch+60 THEN
  RAISE EXCEPTION 'Claims JWT inválidos o expirados' USING ERRCODE='42501';
 END IF;
 uid:=(claims->>'sub')::bigint; eid:=(claims->>'empresa_id')::bigint; ctx:=claims->>'contexto';
 IF NOT EXISTS(
  SELECT 1 FROM public.usuario_empresa ue
  JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado
  JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado
  JOIN public.rol r ON r.id=ue.rol_id AND r.estado
  JOIN public.suscripcion s ON s.empresa_id=e.id AND s.estado='ACTIVA'
  JOIN public.plan_saas p ON p.id=s.plan_saas_id AND p.estado
  WHERE ue.usuario_id=uid AND ue.empresa_id=eid AND ue.estado
    AND (timezone('America/La_Paz',clock_timestamp()))::date BETWEEN s.fecha_inicio AND s.fecha_vencimiento
    AND ((ctx='cliente' AND EXISTS(SELECT 1 FROM public.cliente c WHERE c.empresa_id=eid AND c.usuario_id=uid AND c.estado))
      OR (ctx='personal' AND EXISTS(SELECT 1 FROM public.empleado em JOIN public.sucursal su ON (su.empresa_id,su.id)=(em.empresa_id,em.sucursal_id) WHERE em.empresa_id=eid AND em.usuario_id=uid AND em.estado)))
 ) THEN RAISE EXCEPTION 'Membresía tenant no vigente' USING ERRCODE='42501'; END IF;
 RETURN claims;
END $fn$;
REVOKE ALL ON FUNCTION fashionstore_private.actor_claims() FROM PUBLIC,fashionstore_api,fashionstore_bootstrap;
ALTER FUNCTION fashionstore_private.actor_claims() OWNER TO fashionstore_context_owner;

CREATE OR REPLACE FUNCTION fashionstore_private.actor_empresa_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
AS $fn$ SELECT (fashionstore_private.actor_claims()->>'empresa_id')::bigint $fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_usuario_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
AS $fn$ SELECT (fashionstore_private.actor_claims()->>'sub')::bigint $fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_contexto() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
AS $fn$ SELECT fashionstore_private.actor_claims()->>'contexto' $fn$;

CREATE OR REPLACE FUNCTION fashionstore_private.actor_tiene_rol(p_roles text[])
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT EXISTS(SELECT 1 FROM public.usuario_empresa ue JOIN public.rol r ON r.id=ue.rol_id
  WHERE ue.empresa_id=fashionstore_private.actor_empresa_id()
    AND ue.usuario_id=fashionstore_private.actor_usuario_id()
    AND ue.estado AND r.estado AND r.nombre=ANY(p_roles));
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_es_miembro() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT fashionstore_private.actor_claims() IS NOT NULL;
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_es_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT fashionstore_private.actor_contexto()='personal'
    AND fashionstore_private.actor_tiene_rol(ARRAY['ADMINISTRADOR']);
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_es_personal() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT fashionstore_private.actor_contexto()='personal';
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_posee_cliente(p_cliente bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT EXISTS(SELECT 1 FROM public.cliente c WHERE c.id=p_cliente
  AND c.empresa_id=fashionstore_private.actor_empresa_id()
  AND c.usuario_id=fashionstore_private.actor_usuario_id() AND c.estado);
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_posee_carrito(p_carrito bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT EXISTS(SELECT 1 FROM public.carrito ca WHERE ca.id=p_carrito
  AND ca.empresa_id=fashionstore_private.actor_empresa_id()
  AND fashionstore_private.actor_posee_cliente(ca.cliente_id));
$fn$;
CREATE OR REPLACE FUNCTION fashionstore_private.actor_posee_reserva(p_reserva bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT EXISTS(SELECT 1 FROM public.reserva re WHERE re.id=p_reserva
  AND re.empresa_id=fashionstore_private.actor_empresa_id()
  AND fashionstore_private.actor_posee_cliente(re.cliente_id));
$fn$;

-- 5CA HUECO 1: 050c documenta que cada SP tenant debe empezar con
--   IF NOT fashionstore_private.actor_coincide(p_empresa_id,p_usuario_id)
-- pero la función nunca se creaba, así que el guardián no existía. El bloque
-- $owner_and_acl$ siguiente la recoge automáticamente (LIKE 'actor_%').
CREATE OR REPLACE FUNCTION fashionstore_private.actor_coincide(
 p_empresa_id bigint,p_usuario_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT p_empresa_id IS NOT NULL AND p_usuario_id IS NOT NULL
    AND p_empresa_id=fashionstore_private.actor_empresa_id()
    AND p_usuario_id=fashionstore_private.actor_usuario_id();
$fn$;

DO $owner_and_acl$
DECLARE f record;
BEGIN
 FOR f IN SELECT p.oid::regprocedure sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='fashionstore_private' AND p.proname LIKE 'actor_%'
    AND p.proname<>'actor_claims'
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,fashionstore_bootstrap',f.sig);
  EXECUTE format('ALTER FUNCTION %s OWNER TO fashionstore_context_owner',f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO fashionstore_api,fashionstore_sp_owner',f.sig);
 END LOOP;
END $owner_and_acl$;
GRANT EXECUTE ON FUNCTION fashionstore_private.actor_usuario_id()
 TO fashionstore_audit_owner;

-- Bootstrap aislado: sólo una proyección id/hash y sin acceso directo a usuario.
CREATE OR REPLACE FUNCTION fashionstore_private.bootstrap_credencial(p_correo text)
RETURNS TABLE(usuario_id bigint,password_hash varchar) LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT u.id,u.password_hash FROM public.usuario u
 WHERE u.correo=lower(btrim(p_correo)) AND u.estado LIMIT 2;
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.bootstrap_credencial(text) FROM PUBLIC,fashionstore_api;
ALTER FUNCTION fashionstore_private.bootstrap_credencial(text) OWNER TO fashionstore_bootstrap_owner;
GRANT EXECUTE ON FUNCTION fashionstore_private.bootstrap_credencial(text) TO fashionstore_bootstrap;

-- 5CA HUECO 7 (nuevo, no estaba en la propuesta): el login del API recibe
-- empresa_slug, pero la consulta de membresía corre en el pool api y TODA
-- policy de 050b exige actor_claims(); sin token aborta con 42501. El token, a
-- su vez, necesita empresa_id. Se resuelve con una proyección estrecha de
-- bootstrap (slug -> id de empresa activa) y el resto de la validación sigue
-- siendo la consulta de membresía ya revisada, ejecutada con el token
-- publicado: un slug ajeno NO autoriza nada, sólo permite intentar el login.
CREATE OR REPLACE FUNCTION fashionstore_private.bootstrap_empresa_id(p_slug text)
RETURNS TABLE(empresa_id bigint) LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $fn$
 SELECT e.id FROM public.empresa e
 WHERE e.slug=lower(btrim(p_slug)) AND e.estado LIMIT 2;
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.bootstrap_empresa_id(text) FROM PUBLIC,fashionstore_api;
ALTER FUNCTION fashionstore_private.bootstrap_empresa_id(text) OWNER TO fashionstore_bootstrap_owner;
GRANT EXECUTE ON FUNCTION fashionstore_private.bootstrap_empresa_id(text) TO fashionstore_bootstrap;

-- Privilegios de lectura mínimos de los owners NOLOGIN. Las policies internas
-- para estos owners se crean en 050b antes de FORCE RLS.
GRANT SELECT ON public.usuario,public.usuario_empresa,public.empresa,public.rol,
 public.suscripcion,public.plan_saas,public.cliente,public.empleado,public.sucursal,
 public.carrito,public.reserva TO fashionstore_context_owner;
GRANT SELECT(id,correo,password_hash,estado) ON public.usuario TO fashionstore_bootstrap_owner;
-- 5CA HUECO 7: la proyección bootstrap_empresa_id() sólo proyecta id de
-- empresa activa; la policy p_bootstrap_owner_empresa de 050b limita las filas.
-- 5CB (2026-11-06, BLOQUEANTE): el predicado de esa proyección filtra por slug
-- y estado, así que el privilegio por columnas debe cubrirlos. Con sólo
-- SELECT(id) la función SECURITY DEFINER aborta con 42501 «permission denied
-- for table empresa» y el login por slug queda inutilizable (BST-01…06 del
-- laboratorio 5CB). Probado verde en BST-08 con esta misma línea.
GRANT SELECT(id,slug,estado) ON public.empresa TO fashionstore_bootstrap_owner;

REVOKE CREATE ON SCHEMA fashionstore_private FROM fashionstore_context_owner,
 fashionstore_bootstrap_owner;

COMMIT;

