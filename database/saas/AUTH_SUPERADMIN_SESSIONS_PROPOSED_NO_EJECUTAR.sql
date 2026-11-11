-- Propuesta para autenticación web SUPERADMIN SaaS.
-- NO EJECUTAR en Supabase, producción ni en una base comercial desde este trabajo.
-- Aplicar únicamente con revisión DBA, backup y prueba PostgreSQL aislada.
CREATE TABLE IF NOT EXISTS platform_admin_sessions (
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
  ON platform_admin_sessions (token_hash, expires_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_platform_admin_sessions_principal
  ON platform_admin_sessions (principal, created_at);

-- Rollback (manual, only after approval):
-- DROP TABLE IF EXISTS platform_admin_sessions;
