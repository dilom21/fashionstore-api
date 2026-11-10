-- CU19/CU20: ledger offline e idempotencia real por UUID.
-- PROPUESTA LOCAL: NO EJECUTAR EN SUPABASE SIN AUTORIZACION.
-- Requiere CU11, CU12/CU13 y los roles SaaS ya instalados.
-- No crea ni modifica procedimientos comerciales existentes.
\echo 'NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA'
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';

DO $pre$
BEGIN
  IF current_setting('server_version_num')::int < 170000 THEN
    RAISE EXCEPTION 'Requiere PostgreSQL 17';
  END IF;
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
     AND NOT EXISTS (
       SELECT 1 FROM pg_auth_members
       WHERE member=current_user::regrole
         AND roleid='fashionstore_sp_owner'::regrole
         AND admin_option) THEN
    RAISE EXCEPTION 'Instalador sin ADMIN OPTION sobre fashionstore_sp_owner';
  END IF;
  IF NOT has_schema_privilege(current_user,'fashionstore_private','CREATE')
     OR NOT has_schema_privilege(current_user,'fashionstore_private','USAGE') THEN
    RAISE EXCEPTION 'Instalador sin CREATE/USAGE en fashionstore_private';
  END IF;
  IF to_regclass('fashionstore_private.offline_operacion') IS NOT NULL THEN
    RAISE EXCEPTION 'offline_operacion ya existe; detener y auditar';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='carrito' AND column_name='origen'
  ) THEN
    RAISE EXCEPTION 'carrito.origen ya existe; detener y auditar';
  END IF;
  IF to_regprocedure(
       'public.sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)'
     ) IS NULL
     OR to_regprocedure(
       'public.sp_crear_venta_presencial_tenant(bigint,bigint,bigint,bigint,jsonb,bigint)'
     ) IS NULL
     OR to_regprocedure(
       'public.sp_registrar_pago_presencial_tenant(bigint,bigint,bigint,text,bigint)'
     ) IS NULL THEN
    RAISE EXCEPTION 'Faltan procedimientos CU11/CU13';
  END IF;
  IF to_regprocedure('fashionstore_private.actor_empresa_id()') IS NULL
     OR to_regprocedure('fashionstore_private.actor_usuario_id()') IS NULL THEN
    RAISE EXCEPTION 'Faltan funciones de actor SaaS';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname='public' AND indexname='uq_carrito_activo_cliente_sucursal'
  ) THEN
    RAISE EXCEPTION 'Falta el indice unico del carrito CU15';
  END IF;
  IF NOT has_schema_privilege('fashionstore_api','fashionstore_private','USAGE') THEN
    RAISE EXCEPTION 'fashionstore_api sin USAGE en fashionstore_private';
  END IF;
END $pre$;

-- Staging offline separado del carrito ONLINE. El default preserva CU15.
ALTER TABLE public.carrito
  ADD COLUMN origen text NOT NULL DEFAULT 'ONLINE'
  CHECK (origen IN ('ONLINE','OFFLINE'));

DROP INDEX public.uq_carrito_activo_cliente_sucursal;
CREATE UNIQUE INDEX uq_carrito_activo_cliente_sucursal
  ON public.carrito (empresa_id, cliente_id, sucursal_id, origen)
  WHERE estado='ACTIVO';

CREATE TABLE fashionstore_private.offline_operacion (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_id bigint NOT NULL,
  usuario_id bigint NOT NULL,
  operacion_uuid uuid NOT NULL,
  tipo text NOT NULL CHECK (tipo IN ('RESERVA','VENTA_CONTADO')),
  payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
  estado text NOT NULL CHECK (estado IN (
    'PENDIENTE','ENVIANDO','ACEPTADA','CONFLICTO','RECHAZADA',
    'ERROR_REINTENTABLE')),
  sucursal_id bigint NOT NULL CHECK (sucursal_id > 0),
  id_definitivo bigint,
  resultado jsonb,
  error_codigo text,
  error_mensaje text,
  efectivo_recibido numeric(12,2) CHECK (
    efectivo_recibido IS NULL OR efectivo_recibido >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (empresa_id, operacion_uuid)
);

ALTER TABLE fashionstore_private.offline_operacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE fashionstore_private.offline_operacion FORCE ROW LEVEL SECURITY;
CREATE POLICY p_offline_operacion_owner
  ON fashionstore_private.offline_operacion
  FOR ALL TO fashionstore_sp_owner USING (true) WITH CHECK (true);
REVOKE ALL ON TABLE fashionstore_private.offline_operacion
  FROM PUBLIC, fashionstore_api;

CREATE OR REPLACE FUNCTION fashionstore_private.fn_offline_begin(
  p_empresa_id bigint,
  p_usuario_id bigint,
  p_operacion_uuid uuid,
  p_tipo text,
  p_payload_hash text,
  p_sucursal_id bigint,
  p_efectivo_recibido numeric
) RETURNS TABLE (
  procesar boolean,
  estado text,
  id_definitivo bigint,
  resultado jsonb,
  error_codigo text,
  error_mensaje text,
  payload_hash text
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $fn$
DECLARE v record; v_new_id bigint;
BEGIN
  IF p_empresa_id IS NULL OR p_empresa_id<=0
     OR p_usuario_id IS NULL OR p_usuario_id<=0
     OR p_operacion_uuid IS NULL
     OR p_tipo NOT IN ('RESERVA','VENTA_CONTADO')
     OR p_payload_hash IS NULL OR p_payload_hash !~ '^[0-9a-f]{64}$'
     OR p_sucursal_id IS NULL OR p_sucursal_id<=0
     OR (p_efectivo_recibido IS NOT NULL AND p_efectivo_recibido<0) THEN
    RAISE EXCEPTION 'Operacion offline invalida' USING ERRCODE='22023';
  END IF;
  IF p_empresa_id<>fashionstore_private.actor_empresa_id()
     OR p_usuario_id<>fashionstore_private.actor_usuario_id() THEN
    RAISE EXCEPTION 'Actor no coincide con la operacion' USING ERRCODE='42501';
  END IF;

  INSERT INTO fashionstore_private.offline_operacion(
    empresa_id,usuario_id,operacion_uuid,tipo,payload_hash,estado,
    sucursal_id,efectivo_recibido)
  VALUES(
    p_empresa_id,p_usuario_id,p_operacion_uuid,p_tipo,p_payload_hash,
    'ENVIANDO',p_sucursal_id,p_efectivo_recibido)
  ON CONFLICT (empresa_id,operacion_uuid) DO NOTHING
  RETURNING id INTO v_new_id;

  SELECT * INTO v FROM fashionstore_private.offline_operacion
  WHERE empresa_id=p_empresa_id AND operacion_uuid=p_operacion_uuid
  FOR UPDATE;

  IF v.payload_hash<>p_payload_hash OR v.tipo<>p_tipo
     OR v.sucursal_id<>p_sucursal_id THEN
    RETURN QUERY SELECT false,'CONFLICTO',NULL::bigint,NULL::jsonb,
      'UUID_PAYLOAD_MISMATCH',
      'La operacion ya existe con otro contenido',v.payload_hash;
    RETURN;
  END IF;
  IF v_new_id IS NOT NULL THEN
    RETURN QUERY SELECT true,'ENVIANDO',NULL::bigint,NULL::jsonb,
      NULL::text,NULL::text,v.payload_hash;
    RETURN;
  END IF;
  IF v.estado='ACEPTADA' THEN
    RETURN QUERY SELECT false,v.estado,v.id_definitivo,v.resultado,
      v.error_codigo,v.error_mensaje,v.payload_hash;
    RETURN;
  END IF;
  IF v.estado IN ('CONFLICTO','RECHAZADA') THEN
    RETURN QUERY SELECT false,v.estado,v.id_definitivo,v.resultado,
      v.error_codigo,v.error_mensaje,v.payload_hash;
    RETURN;
  END IF;
  IF v.estado='ERROR_REINTENTABLE' THEN
    UPDATE fashionstore_private.offline_operacion
    SET estado='ENVIANDO', id_definitivo=NULL, resultado=NULL,
        error_codigo=NULL, error_mensaje=NULL,
        updated_at=clock_timestamp()
    WHERE id=v.id;
    RETURN QUERY SELECT true,'ENVIANDO',NULL::bigint,NULL::jsonb,
      NULL::text,NULL::text,v.payload_hash;
    RETURN;
  END IF;
  RETURN QUERY SELECT false,v.estado,v.id_definitivo,v.resultado,
    v.error_codigo,v.error_mensaje,v.payload_hash;
END
$fn$;

CREATE OR REPLACE FUNCTION fashionstore_private.fn_offline_finish(
  p_empresa_id bigint,
  p_usuario_id bigint,
  p_operacion_uuid uuid,
  p_estado text,
  p_id_definitivo bigint,
  p_resultado jsonb,
  p_error_codigo text,
  p_error_mensaje text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $fn$
BEGIN
  IF p_estado NOT IN ('ACEPTADA','CONFLICTO','RECHAZADA','ERROR_REINTENTABLE') THEN
    RAISE EXCEPTION 'Estado offline invalido' USING ERRCODE='22023';
  END IF;
  IF p_estado='ACEPTADA' AND (p_id_definitivo IS NULL OR p_id_definitivo<=0) THEN
    RAISE EXCEPTION 'Operacion aceptada sin ID definitivo' USING ERRCODE='22023';
  END IF;
  IF p_empresa_id<>fashionstore_private.actor_empresa_id()
     OR p_usuario_id<>fashionstore_private.actor_usuario_id() THEN
    RAISE EXCEPTION 'Actor no coincide con la operacion' USING ERRCODE='42501';
  END IF;
  UPDATE fashionstore_private.offline_operacion
  SET estado=p_estado, id_definitivo=p_id_definitivo, resultado=p_resultado,
      error_codigo=p_error_codigo, error_mensaje=p_error_mensaje,
      updated_at=clock_timestamp()
  WHERE empresa_id=p_empresa_id AND usuario_id=p_usuario_id
    AND operacion_uuid=p_operacion_uuid AND estado='ENVIANDO';
  IF FOUND THEN RETURN; END IF;
  IF EXISTS(
    SELECT 1 FROM fashionstore_private.offline_operacion
    WHERE empresa_id=p_empresa_id AND usuario_id=p_usuario_id
      AND operacion_uuid=p_operacion_uuid AND estado='ACEPTADA'
  ) THEN RETURN; END IF;
  RAISE EXCEPTION 'Operacion offline no procesable' USING ERRCODE='P0002';
END
$fn$;

CREATE OR REPLACE FUNCTION fashionstore_private.fn_offline_rechazar(
  p_empresa_id bigint,
  p_usuario_id bigint,
  p_operacion_uuid uuid,
  p_tipo text,
  p_payload_hash text,
  p_sucursal_id bigint,
  p_efectivo_recibido numeric,
  p_estado text,
  p_error_codigo text,
  p_error_mensaje text
) RETURNS TABLE (
  estado text,
  id_definitivo bigint,
  resultado jsonb,
  error_codigo text,
  error_mensaje text,
  payload_hash text
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $fn$
DECLARE v record;
BEGIN
  IF p_estado NOT IN ('CONFLICTO','RECHAZADA','ERROR_REINTENTABLE') THEN
    RAISE EXCEPTION 'Estado de rechazo invalido' USING ERRCODE='22023';
  END IF;
  IF p_empresa_id<>fashionstore_private.actor_empresa_id()
     OR p_usuario_id<>fashionstore_private.actor_usuario_id() THEN
    RAISE EXCEPTION 'Actor no coincide con la operacion' USING ERRCODE='42501';
  END IF;

  INSERT INTO fashionstore_private.offline_operacion(
    empresa_id,usuario_id,operacion_uuid,tipo,payload_hash,estado,
    sucursal_id,efectivo_recibido,error_codigo,error_mensaje)
  VALUES(
    p_empresa_id,p_usuario_id,p_operacion_uuid,p_tipo,p_payload_hash,
    p_estado,p_sucursal_id,p_efectivo_recibido,p_error_codigo,p_error_mensaje)
  ON CONFLICT (empresa_id,operacion_uuid) DO NOTHING;

  SELECT * INTO v FROM fashionstore_private.offline_operacion
  WHERE empresa_id=p_empresa_id AND operacion_uuid=p_operacion_uuid
  FOR UPDATE;

  IF v.payload_hash<>p_payload_hash OR v.tipo<>p_tipo
     OR v.sucursal_id<>p_sucursal_id THEN
    RETURN QUERY SELECT 'CONFLICTO',NULL::bigint,NULL::jsonb,
      'UUID_PAYLOAD_MISMATCH',
      'La operacion ya existe con otro contenido',v.payload_hash;
    RETURN;
  END IF;
  IF v.estado='ACEPTADA' THEN
    RETURN QUERY SELECT v.estado,v.id_definitivo,v.resultado,
      v.error_codigo,v.error_mensaje,v.payload_hash;
    RETURN;
  END IF;
  UPDATE fashionstore_private.offline_operacion
  SET estado=p_estado,
      efectivo_recibido=COALESCE(v.efectivo_recibido,p_efectivo_recibido),
      error_codigo=p_error_codigo, error_mensaje=p_error_mensaje,
      updated_at=clock_timestamp()
  WHERE id=v.id;
  RETURN QUERY SELECT p_estado,v.id_definitivo,v.resultado,
    p_error_codigo,p_error_mensaje,v.payload_hash;
END
$fn$;

CREATE OR REPLACE FUNCTION fashionstore_private.fn_offline_get(
  p_empresa_id bigint,
  p_usuario_id bigint,
  p_operacion_uuid uuid
) RETURNS TABLE (
  tipo text,
  estado text,
  id_definitivo bigint,
  resultado jsonb,
  error_codigo text,
  error_mensaje text,
  efectivo_recibido numeric,
  sucursal_id bigint,
  payload_hash text
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $fn$
BEGIN
  IF p_empresa_id<>fashionstore_private.actor_empresa_id()
     OR p_usuario_id<>fashionstore_private.actor_usuario_id() THEN
    RAISE EXCEPTION 'Actor no coincide con la operacion' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT o.tipo,o.estado,o.id_definitivo,o.resultado,o.error_codigo,
         o.error_mensaje,o.efectivo_recibido,o.sucursal_id,o.payload_hash
  FROM fashionstore_private.offline_operacion o
  WHERE o.empresa_id=p_empresa_id AND o.usuario_id=p_usuario_id
    AND o.operacion_uuid=p_operacion_uuid;
END
$fn$;

-- ACL antes de transferir ownership: el creador puede revocar/conceder.
REVOKE ALL ON FUNCTION fashionstore_private.fn_offline_begin(
  bigint,bigint,uuid,text,text,bigint,numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION fashionstore_private.fn_offline_finish(
  bigint,bigint,uuid,text,bigint,jsonb,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fashionstore_private.fn_offline_rechazar(
  bigint,bigint,uuid,text,text,bigint,numeric,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fashionstore_private.fn_offline_get(
  bigint,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fashionstore_private.fn_offline_begin(
  bigint,bigint,uuid,text,text,bigint,numeric) TO fashionstore_api;
GRANT EXECUTE ON FUNCTION fashionstore_private.fn_offline_finish(
  bigint,bigint,uuid,text,bigint,jsonb,text,text) TO fashionstore_api;
GRANT EXECUTE ON FUNCTION fashionstore_private.fn_offline_rechazar(
  bigint,bigint,uuid,text,text,bigint,numeric,text,text,text) TO fashionstore_api;
GRANT EXECUTE ON FUNCTION fashionstore_private.fn_offline_get(
  bigint,bigint,uuid) TO fashionstore_api;

-- PostgreSQL exige poder SET ROLE al nuevo owner. Se conserva exactamente
-- cualquier membresia preexistente (incluida la de supabase_admin) y se
-- restaura tras las transferencias. No se revoca la membresia preexistente.
CREATE TEMP TABLE cu19_membresias_previas ON COMMIT DROP AS
SELECT roleid,member,grantor,admin_option,inherit_option,set_option
FROM pg_auth_members
WHERE member=CURRENT_USER::regrole
  AND roleid='fashionstore_sp_owner'::regrole;
CREATE TEMP TABLE cu19_set_temporal (
  rol name PRIMARY KEY, existente boolean NOT NULL,
  admin_previo boolean, inherit_previo boolean, set_previo boolean
) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp.cu19_preparar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v_admin boolean; v_inherit boolean; v_set boolean;
BEGIN
  SELECT m.admin_option,m.inherit_option,m.set_option
    INTO v_admin,v_inherit,v_set
  FROM pg_auth_members m
  WHERE m.member=CURRENT_USER::regrole
    AND m.roleid=p_rol::regrole
    AND m.grantor=CURRENT_USER::regrole;
  IF FOUND THEN
    IF v_set IS NOT TRUE THEN
      EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE',
        p_rol,
        CASE WHEN v_admin THEN 'ADMIN OPTION,' ELSE '' END,
        CASE WHEN v_inherit THEN 'TRUE' ELSE 'FALSE' END);
    END IF;
    INSERT INTO cu19_set_temporal VALUES(
      p_rol,true,v_admin,v_inherit,v_set);
  ELSE
    EXECUTE format('GRANT %I TO CURRENT_USER WITH INHERIT FALSE, SET TRUE',
      p_rol);
    INSERT INTO cu19_set_temporal VALUES(p_rol,false,NULL,NULL,NULL);
  END IF;
END $fn$;

CREATE OR REPLACE FUNCTION pg_temp.cu19_restaurar_set(p_rol name)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v record;
BEGIN
  SELECT * INTO v FROM cu19_set_temporal WHERE rol=p_rol;
  IF NOT FOUND THEN RETURN; END IF;
  IF v.existente THEN
    EXECUTE format('GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s',
      p_rol,
      CASE WHEN v.admin_previo THEN 'ADMIN OPTION,' ELSE '' END,
      CASE WHEN v.inherit_previo THEN 'TRUE' ELSE 'FALSE' END,
      CASE WHEN v.set_previo THEN 'TRUE' ELSE 'FALSE' END);
  ELSE
    EXECUTE format('REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER', p_rol);
  END IF;
END $fn$;

DO $prep$
BEGIN
  IF NOT pg_has_role(CURRENT_USER,'fashionstore_sp_owner','SET') THEN
    PERFORM pg_temp.cu19_preparar_set('fashionstore_sp_owner');
  END IF;
END $prep$;

CREATE TEMP TABLE cu19_creates_temporales (
  rol name, esquema name, PRIMARY KEY (rol,esquema)
) ON COMMIT DROP;
DO $grant_create$
BEGIN
  IF NOT has_schema_privilege('fashionstore_sp_owner',
                              'fashionstore_private','CREATE') THEN
    GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_sp_owner;
    INSERT INTO cu19_creates_temporales
      VALUES('fashionstore_sp_owner','fashionstore_private');
  END IF;
END $grant_create$;
GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_sp_owner;

ALTER TABLE fashionstore_private.offline_operacion
  OWNER TO fashionstore_sp_owner;
ALTER FUNCTION fashionstore_private.fn_offline_begin(
  bigint,bigint,uuid,text,text,bigint,numeric
) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION fashionstore_private.fn_offline_finish(
  bigint,bigint,uuid,text,bigint,jsonb,text,text
) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION fashionstore_private.fn_offline_rechazar(
  bigint,bigint,uuid,text,text,bigint,numeric,text,text,text
) OWNER TO fashionstore_sp_owner;
ALTER FUNCTION fashionstore_private.fn_offline_get(
  bigint,bigint,uuid
) OWNER TO fashionstore_sp_owner;

DO $revoke_create$
BEGIN
  IF EXISTS (
    SELECT 1 FROM cu19_creates_temporales
    WHERE rol='fashionstore_sp_owner' AND esquema='fashionstore_private'
  ) THEN
    REVOKE CREATE ON SCHEMA fashionstore_private FROM fashionstore_sp_owner;
  END IF;
END $revoke_create$;

DO $restore$
BEGIN
  PERFORM pg_temp.cu19_restaurar_set('fashionstore_sp_owner');
END $restore$;

DO $verify$
BEGIN
  IF has_schema_privilege('fashionstore_sp_owner',
                          'fashionstore_private','CREATE') THEN
    RAISE EXCEPTION 'CREATE residual en fashionstore_sp_owner';
  END IF;
  IF EXISTS (
    SELECT 1 FROM cu19_membresias_previas p
    WHERE NOT EXISTS (
      SELECT 1 FROM pg_auth_members m
      WHERE m.roleid=p.roleid AND m.member=p.member AND m.grantor=p.grantor
        AND m.admin_option=p.admin_option
        AND m.inherit_option=p.inherit_option
        AND m.set_option=p.set_option)) THEN
    RAISE EXCEPTION 'Membresia preexistente alterada';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_auth_members m
    WHERE m.member=CURRENT_USER::regrole
      AND m.roleid='fashionstore_sp_owner'::regrole
      AND NOT EXISTS (
        SELECT 1 FROM cu19_membresias_previas p
        WHERE p.roleid=m.roleid AND p.member=m.member
          AND p.grantor=m.grantor)) THEN
    RAISE EXCEPTION 'Membresia temporal no revocada';
  END IF;
END $verify$;

COMMIT;

SELECT 'CU19_CU20_OFFLINE_INSTALL=PASS NO_EJECUTADO_EN_SUPABASE' AS resultado;
