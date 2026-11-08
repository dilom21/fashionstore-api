-- FashionStore SaaS - CU16 / reportes: solo lectura para ENCARGADO_SUCURSAL.
-- Migration destinada al proyecto Supabase ya configurado. NO reejecutar una vez aplicada.
-- Administradores conservan sus politicas SELECT preexistentes. No modifica datos.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE faltantes int;
BEGIN
  IF to_regrole('fashionstore_api_login') IS NULL THEN
    RAISE EXCEPTION 'Falta rol fashionstore_api_login';
  END IF;
  IF to_regprocedure('fashionstore_private.cu16_encargado_puede_ver_sucursal(bigint,bigint)') IS NOT NULL
     OR EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND policyname LIKE 'p_cu16_encargado_%') THEN
    RAISE EXCEPTION 'CU16 RLS ya existe; no reejecutar';
  END IF;
  SELECT count(*) INTO faltantes FROM (
    SELECT unnest(ARRAY['venta','detalle_venta','pago','reserva','detalle_reserva','devolucion','detalle_devolucion']) AS t
  ) required
  LEFT JOIN pg_class c ON c.relname=required.t
     AND c.relnamespace='public'::regnamespace AND c.relkind='r'
  WHERE c.oid IS NULL OR NOT c.relrowsecurity OR NOT c.relforcerowsecurity;
  IF faltantes <> 0 THEN RAISE EXCEPTION 'CU16 requiere tablas y FORCE RLS existentes (% invalidas)',faltantes; END IF;
  IF NOT has_schema_privilege('fashionstore_api_login','fashionstore_private','USAGE') THEN
    RAISE EXCEPTION 'API no tiene USAGE de fashionstore_private';
  END IF;
  IF NOT (
     has_function_privilege('fashionstore_api_login','fashionstore_private.actor_empresa_id()','EXECUTE')
     AND has_function_privilege('fashionstore_api_login','fashionstore_private.actor_usuario_id()','EXECUTE')
     AND has_function_privilege('fashionstore_api_login','fashionstore_private.actor_es_personal()','EXECUTE')
     AND has_function_privilege('fashionstore_api_login','fashionstore_private.actor_tiene_rol(text[])','EXECUTE')
  ) THEN RAISE EXCEPTION 'Funciones JWT no disponibles para API'; END IF;
END $preflight$;

-- Usamos el actor del JWT firmado/validado por la capa de seguridad PostgreSQL.
-- No admitimos IDs ni roles enviados desde parámetros de frontend.
CREATE FUNCTION fashionstore_private.cu16_encargado_puede_ver_sucursal(
    p_empresa_id bigint, p_sucursal_id bigint
) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $fn$
  SELECT COALESCE(
    p_empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND (SELECT fashionstore_private.actor_es_personal())
    AND (SELECT fashionstore_private.actor_tiene_rol(ARRAY['ENCARGADO_SUCURSAL']::text[]))
    AND EXISTS (
      SELECT 1
      FROM public.empleado e
      JOIN public.sucursal s ON s.id=e.sucursal_id AND s.empresa_id=e.empresa_id AND s.estado IS TRUE
      WHERE e.empresa_id=p_empresa_id
        AND e.usuario_id=(SELECT fashionstore_private.actor_usuario_id())
        AND e.sucursal_id=p_sucursal_id
        AND e.estado IS TRUE
    ), false
  );
$fn$;
REVOKE ALL ON FUNCTION fashionstore_private.cu16_encargado_puede_ver_sucursal(bigint,bigint) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION fashionstore_private.cu16_encargado_puede_ver_sucursal(bigint,bigint) TO fashionstore_api_login;

-- No se reemplazan las politicas admin; las nuevas solo autorizan SELECT para
-- la membresia autenticada como ENCARGADO_SUCURSAL y la sucursal asignada.
CREATE POLICY p_cu16_encargado_venta ON public.venta
  FOR SELECT TO fashionstore_api_login
  USING (fashionstore_private.cu16_encargado_puede_ver_sucursal(empresa_id,sucursal_id));

-- Detalles y pagos solo si su venta padre es VISIBLE bajo su propia RLS.
CREATE POLICY p_cu16_encargado_detalle_venta ON public.detalle_venta
  FOR SELECT TO fashionstore_api_login
  USING (empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND EXISTS (SELECT 1 FROM public.venta v
                WHERE v.id=detalle_venta.venta_id AND v.empresa_id=detalle_venta.empresa_id));
CREATE POLICY p_cu16_encargado_pago ON public.pago
  FOR SELECT TO fashionstore_api_login
  USING (empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND EXISTS (SELECT 1 FROM public.venta v
                WHERE v.id=pago.venta_id AND v.empresa_id=pago.empresa_id));

-- CU16 también incluye KPIs de reservas y devoluciones. Alcance sucursal.
CREATE POLICY p_cu16_encargado_reserva ON public.reserva
  FOR SELECT TO fashionstore_api_login
  USING (fashionstore_private.cu16_encargado_puede_ver_sucursal(empresa_id,sucursal_id));
CREATE POLICY p_cu16_encargado_detalle_reserva ON public.detalle_reserva
  FOR SELECT TO fashionstore_api_login
  USING (empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND EXISTS (SELECT 1 FROM public.reserva r
                WHERE r.id=detalle_reserva.reserva_id AND r.empresa_id=detalle_reserva.empresa_id));
CREATE POLICY p_cu16_encargado_devolucion ON public.devolucion
  FOR SELECT TO fashionstore_api_login
  USING (empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND EXISTS (SELECT 1 FROM public.venta v
                WHERE v.id=devolucion.venta_id AND v.empresa_id=devolucion.empresa_id));
CREATE POLICY p_cu16_encargado_detalle_devolucion ON public.detalle_devolucion
  FOR SELECT TO fashionstore_api_login
  USING (empresa_id = (SELECT fashionstore_private.actor_empresa_id())
    AND EXISTS (SELECT 1 FROM public.devolucion d
                WHERE d.id=detalle_devolucion.devolucion_id AND d.empresa_id=detalle_devolucion.empresa_id));

COMMIT;
