-- PLANTILLA BLOQUEADA: NO PROMOVER NI EJECUTAR.
-- Sustituir sólo después de provisionar conexiones PostgreSQL por usuario y
-- completar el mapa privado session_user -> (empresa_id,usuario_id).
DO $$ BEGIN
 RAISE EXCEPTION 'BLOQUEADO: faltan LOGIN individuales, mapa de actores, owners NOLOGIN y nombres aprobados';
END $$;

-- Contrato que debe materializar una revisión posterior:
-- 1. Login actor: LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT.
-- 2. Owner verificador y owner de SP: roles distintos, NOLOGIN, NOINHERIT,
--    NOSUPERUSER, NOBYPASSRLS, sin CREATE en base/public.
-- 3. fs_verificar_actor_reserva(bigint,bigint): SECURITY DEFINER, search_path
--    pg_catalog,pg_temp, PUBLIC revocado, compara session_user con mapa privado.
-- 4. Políticas de las 31 tablas: empresa_id igual a la empresa derivada de
--    session_user; USING y WITH CHECK; ENABLE + FORCE RLS.
-- 5. empresa/suscripcion/usuario_empresa/empresa_configuracion con el mismo
--    contexto; plan_saas sólo SELECT. Tablas globales con ACL mínimo explícito.
-- 6. Los tres SP tenant se originan en database/saas/borradores/12..14,
--    incorporan el verificador como primera instrucción y usan owner SP NOLOGIN.
-- 7. PUBLIC/anon/authenticated sin DML directo; service_role auditado aparte.
-- 8. FastAPI debe usar la credencial individual real. SET LOCAL app.* sigue
--    siendo contexto/auditoría, nunca identidad de seguridad.

