-- Propuesta versionada para reproducir la migracion SaaS SUPERADMIN endurecida.
-- Aplicacion real registrada en Supabase fashionstore:
-- 20261111210804_fashionstore_superadmin_sessions_restricted_20261111
-- NO REEJECUTAR en Supabase que ya la contiene. Revisar historico antes de cualquier despliegue.
-- Solo el principal fashionstore_platform_admin accede por conexion PostgreSQL.
-- Las credenciales de la API/usuarios no son parte de este archivo.
BEGIN;

DO $guard$
BEGIN
  IF current_setting('server_version_num')::integer / 10000 <> 17
  THEN RAISE EXCEPTION 'Requiere PostgreSQL 17'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='fashionstore_platform_admin'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
  ) THEN RAISE EXCEPTION 'Principal platform_admin restringido ausente'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='fashionstore_platform_login'
      AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
  ) THEN RAISE EXCEPTION 'Login platform restringido ausente'; END IF;
END $guard$;

CREATE TABLE IF NOT EXISTS public.platform_admin_sessions (
  id BIGSERIAL PRIMARY KEY,
  token_hash CHAR(64) NOT NULL UNIQUE,
  principal VARCHAR(150) NOT NULL,
  csrf_token VARCHAR(128) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS ix_platform_admin_sessions_valid
 ON public.platform_admin_sessions(token_hash,expires_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_platform_admin_sessions_principal
 ON public.platform_admin_sessions(principal,created_at);

REVOKE ALL ON TABLE public.platform_admin_sessions
 FROM PUBLIC,anon,authenticated,service_role,
      fashionstore_api,fashionstore_bootstrap,fashionstore_platform_login;
REVOKE ALL ON SEQUENCE public.platform_admin_sessions_id_seq
 FROM PUBLIC,anon,authenticated,service_role,
      fashionstore_api,fashionstore_bootstrap,fashionstore_platform_login;

ALTER TABLE public.platform_admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_admin_sessions FORCE ROW LEVEL SECURITY;
DO $policy$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM pg_policies
   WHERE schemaname='public' AND tablename='platform_admin_sessions'
     AND policyname='p_platform_sessions_admin'
 ) THEN
  CREATE POLICY p_platform_sessions_admin ON public.platform_admin_sessions
    AS PERMISSIVE FOR ALL TO fashionstore_platform_admin
    USING (true) WITH CHECK (true);
 END IF;
END $policy$;

GRANT SELECT,INSERT,UPDATE ON public.platform_admin_sessions
 TO fashionstore_platform_admin;
GRANT USAGE ON SEQUENCE public.platform_admin_sessions_id_seq
 TO fashionstore_platform_admin;
COMMENT ON TABLE public.platform_admin_sessions IS
 'Sesiones opacas del superadministrador FashionStore: solo principal platform_admin';

DO $verify$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM pg_class WHERE oid='public.platform_admin_sessions'::regclass
     AND relrowsecurity AND relforcerowsecurity
 ) THEN RAISE EXCEPTION 'RLS/FORCE RLS ausentes'; END IF;
 IF has_table_privilege('anon','public.platform_admin_sessions','SELECT')
 OR has_table_privilege('authenticated','public.platform_admin_sessions','SELECT')
 OR has_table_privilege('fashionstore_api','public.platform_admin_sessions','SELECT')
 OR NOT has_table_privilege('fashionstore_platform_admin','public.platform_admin_sessions','SELECT')
 OR NOT has_table_privilege('fashionstore_platform_admin','public.platform_admin_sessions','INSERT')
 OR NOT has_table_privilege('fashionstore_platform_admin','public.platform_admin_sessions','UPDATE')
 THEN RAISE EXCEPTION 'ACL sesiones no segura'; END IF;
END $verify$;
COMMIT;

-- El rollback debe ser manual y autorizado; eliminar la tabla revocaria sesiones activas.
