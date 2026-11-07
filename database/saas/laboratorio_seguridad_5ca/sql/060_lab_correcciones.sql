-- LABORATORIO 5CA — correcciones y huecos detectados en la propuesta 050a-c.
-- Este archivo NO existe en la propuesta original: documenta y tapa dos huecos
-- encontrados durante la validación. No se promociona a Supabase sin revisión.
BEGIN;

-- HUECO 1: 050c exige que cada SP empiece con
--   IF NOT fashionstore_private.actor_coincide(p_empresa_id,p_usuario_id)
-- pero 050a nunca crea esa función. Sin ella el guardián de los SP no existe.
CREATE OR REPLACE FUNCTION fashionstore_private.actor_coincide(
    p_empresa_id bigint, p_usuario_id bigint)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT p_empresa_id IS NOT NULL AND p_usuario_id IS NOT NULL
     AND p_empresa_id = fashionstore_private.actor_empresa_id()
     AND p_usuario_id = fashionstore_private.actor_usuario_id();
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.actor_coincide(bigint,bigint) FROM PUBLIC;
ALTER FUNCTION fashionstore_private.actor_coincide(bigint,bigint)
  OWNER TO fashionstore_context_owner;
GRANT EXECUTE ON FUNCTION fashionstore_private.actor_coincide(bigint,bigint)
  TO fashionstore_api, fashionstore_sp_owner;

-- HUECO 2: 050c no concede al owner de los SP el UPDATE sobre carrito que el
-- contrato de reserva necesita para marcarlo CONVERTIDO, ni el SELECT sobre
-- movimiento_inventario que usan los reportes del mismo SP.
GRANT UPDATE ON public.carrito TO fashionstore_sp_owner;
GRANT SELECT ON public.movimiento_inventario, public.detalle_carrito
  TO fashionstore_sp_owner;
GRANT USAGE, SELECT ON SEQUENCE public.movimiento_inventario_id_seq
  TO fashionstore_sp_owner;

-- HUECO 3 (bloqueante): 050b crea la política p_api_usuario_select sobre
-- public.usuario, pero 050c NUNCA concede SELECT sobre esa tabla a
-- fashionstore_api. Sin el privilegio la política es inaplicable y toda ruta
-- que une public.usuario (por ejemplo TenantAuthRepository.obtener_membresia_
-- vigente y el login) falla con "permission denied for table usuario".
-- Se concede sólo SELECT: la política limita las filas, los triggers y la ACL
-- siguen impidiendo escrituras.
GRANT SELECT ON public.usuario TO fashionstore_api;
-- El owner de los SP también necesita leer usuario para el JOIN de membresía
-- si la política p_sp_select_global_usuario debe aplicarse (050c sí lo concede,
-- esta línea documenta que ya estaba cubierto).

-- HUECO 4 (bloqueante): actor_claims() es SECURITY DEFINER y su owner es
-- fashionstore_context_owner. Ese rol no tiene USAGE sobre el esquema extensions,
-- por lo que la llamada a extensions.hmac(...) falla con
-- "permission denied for schema extensions" y TODA la verificación JWT se cae.
-- En Supabase ocurre lo mismo: el esquema extensions no se concede a PUBLIC.
-- Se concede únicamente USAGE del esquema y EXECUTE de la función hmac.
GRANT USAGE ON SCHEMA extensions TO fashionstore_context_owner;
GRANT EXECUTE ON FUNCTION extensions.hmac(bytea,bytea,text)
  TO fashionstore_context_owner;

-- HUECO 5 (bloqueante): 050c reemplaza fn_registrar_bitacora() por una versión
-- SECURITY DEFINER con owner fashionstore_audit_owner. Ese owner NO recibe USAGE
-- sobre fashionstore_private (050a sólo se lo concede a api, bootstrap,
-- platform_admin, context_owner, bootstrap_owner y sp_owner), así que TODA
-- escritura de la API falla con "permission denied for schema
-- fashionstore_private" al ejecutarse el trigger de bitácora.
GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_audit_owner;
-- HUECO 6 (menor): el mismo owner necesita poder insertar en bitácora cuando el
-- trigger se dispara desde tablas globales (usuario/rol) sin empresa_id.
GRANT INSERT ON public.bitacora TO fashionstore_audit_owner;
GRANT USAGE, SELECT ON SEQUENCE public.bitacora_id_seq TO fashionstore_audit_owner;
COMMIT;
