-- PROPUESTA LOCAL. NO EJECUTAR. Depende de 050a revisado y ensayado.
DO $$ BEGIN RAISE EXCEPTION 'BLOQUEADO: propuesta 050b no aprobada'; END $$;
BEGIN;
SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='180s';

-- Owners NOLOGIN necesitan leer exclusivamente las tablas de verificación.
DO $owner_policies$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['usuario','usuario_empresa','empresa','rol','suscripcion','plan_saas','cliente','empleado','sucursal','carrito','reserva'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_context_owner USING(true)','p_ctx_owner_'||t,t);
 END LOOP;
 CREATE POLICY p_bootstrap_owner_usuario ON public.usuario FOR SELECT TO fashionstore_bootstrap_owner USING(estado IS TRUE);
END $owner_policies$;

CREATE POLICY p_audit_owner_insert ON public.bitacora FOR INSERT
 TO fashionstore_audit_owner WITH CHECK(true);

-- Administración global deliberada, sólo para el pool superadmin separado.
-- No se concede a fashionstore_api ni al bootstrap.
DO $platform_policies$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['empresa','empresa_configuracion','plan_saas','suscripcion','usuario','usuario_empresa','sucursal','empleado','ciudad','rol'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO fashionstore_platform_admin USING(true) WITH CHECK(true)','p_platform_'||t,t);
 END LOOP;
END $platform_policies$;

-- Catálogos globales sin empresa_id. Sólo lectura; la identidad continúa
-- siendo obligatoria porque cada policy evalúa actor_es_miembro().
DO $global_select$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['ciudad','talla','color','rol','modulo','funcion','accion','rol_funcion'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_api USING ((SELECT fashionstore_private.actor_es_miembro()))','p_api_select_global_'||t,t);
 END LOOP;
END $global_select$;
CREATE POLICY p_api_usuario_select ON public.usuario FOR SELECT TO fashionstore_api
 USING(id=(SELECT fashionstore_private.actor_usuario_id()) OR
  ((SELECT fashionstore_private.actor_es_admin()) AND EXISTS(
    SELECT 1 FROM public.usuario_empresa ue
    WHERE ue.usuario_id=usuario.id
      AND ue.empresa_id=(SELECT fashionstore_private.actor_empresa_id()))));

-- Catálogos empresariales visibles a cualquier miembro vigente.
DO $catalog_select$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['categoria','coleccion','configuracion_vestidor_ar','inventario','producto','producto_coleccion','promocion','promocion_producto','recurso_producto','sucursal','temporada','variante_producto'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_api USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))','p_api_select_tenant_'||t,t);
 END LOOP;
END $catalog_select$;

-- Datos operativos sensibles: sólo personal autenticado del tenant.
DO $staff_select$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['bitacora','empleado','movimiento_inventario','orden_compra','detalle_orden_compra','proveedor','proveedor_producto'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_api USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_personal()))','p_api_select_staff_'||t,t);
 END LOOP;
END $staff_select$;

CREATE POLICY p_api_cliente_select ON public.cliente FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  ((SELECT fashionstore_private.actor_es_admin()) OR usuario_id=(SELECT fashionstore_private.actor_usuario_id())));
CREATE POLICY p_api_carrito_select ON public.carrito FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  ((SELECT fashionstore_private.actor_es_admin()) OR (SELECT fashionstore_private.actor_posee_cliente(cliente_id))));
CREATE POLICY p_api_detalle_carrito_select ON public.detalle_carrito FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  ((SELECT fashionstore_private.actor_es_admin()) OR (SELECT fashionstore_private.actor_posee_carrito(carrito_id))));
CREATE POLICY p_api_reserva_select ON public.reserva FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  ((SELECT fashionstore_private.actor_es_admin()) OR (SELECT fashionstore_private.actor_posee_cliente(cliente_id))));
CREATE POLICY p_api_detalle_reserva_select ON public.detalle_reserva FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  ((SELECT fashionstore_private.actor_es_admin()) OR (SELECT fashionstore_private.actor_posee_reserva(reserva_id))));

-- Comercial, devoluciones y AR quedan sólo para administrador en esta etapa:
-- los routers SaaS actuales sólo exponen lectura comercial de administrador.
DO $admin_only$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['venta','detalle_venta','pago','devolucion','detalle_devolucion','sesion_vestidor_ar','prueba_vestidor_ar'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_api USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))','p_api_select_admin_'||t,t);
 END LOOP;
END $admin_only$;

-- Tablas SaaS.
CREATE POLICY p_api_empresa_select ON public.empresa FOR SELECT TO fashionstore_api
 USING(id=(SELECT fashionstore_private.actor_empresa_id()));
CREATE POLICY p_api_empresa_update ON public.empresa FOR UPDATE TO fashionstore_api
 USING(id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))
 WITH CHECK(id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()));
CREATE POLICY p_api_config_select ON public.empresa_configuracion FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()));
CREATE POLICY p_api_config_update ON public.empresa_configuracion FOR UPDATE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()));
CREATE POLICY p_api_suscripcion_select ON public.suscripcion FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()));
CREATE POLICY p_api_suscripcion_update ON public.suscripcion FOR UPDATE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()));
CREATE POLICY p_api_membresia_select ON public.usuario_empresa FOR SELECT TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
  (usuario_id=(SELECT fashionstore_private.actor_usuario_id()) OR (SELECT fashionstore_private.actor_es_admin())));
CREATE POLICY p_api_membresia_update ON public.usuario_empresa FOR UPDATE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()));
CREATE POLICY p_api_plan_select ON public.plan_saas FOR SELECT TO fashionstore_api USING(estado IS TRUE);

-- Escritura administrativa actualmente implementada.
DO $admin_write$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['categoria','producto','variante_producto','sucursal'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT TO fashionstore_api WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))','p_api_insert_admin_'||t,t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE TO fashionstore_api USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin())) WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND (SELECT fashionstore_private.actor_es_admin()))','p_api_update_admin_'||t,t);
 END LOOP;
END $admin_write$;

-- Carrito: sólo el cliente propietario; evita suplantación dentro del tenant.
CREATE POLICY p_api_carrito_insert ON public.carrito FOR INSERT TO fashionstore_api
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_cliente(cliente_id)));
CREATE POLICY p_api_carrito_update ON public.carrito FOR UPDATE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_cliente(cliente_id)))
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_cliente(cliente_id)));
CREATE POLICY p_api_detalle_carrito_insert ON public.detalle_carrito FOR INSERT TO fashionstore_api
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_carrito(carrito_id)));
CREATE POLICY p_api_detalle_carrito_update ON public.detalle_carrito FOR UPDATE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_carrito(carrito_id)))
 WITH CHECK(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_carrito(carrito_id)));
CREATE POLICY p_api_detalle_carrito_delete ON public.detalle_carrito FOR DELETE TO fashionstore_api
 USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()) AND
   (SELECT fashionstore_private.actor_posee_carrito(carrito_id)));

-- SP owner: sólo tablas CU16/17/18 y siempre limitado por el token verificado.
DO $sp_policies$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['carrito','detalle_carrito','inventario','reserva','detalle_reserva','movimiento_inventario','venta'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO fashionstore_sp_owner USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id())) WITH CHECK (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))','p_sp_tenant_'||t,t);
 END LOOP;
END $sp_policies$;

-- Lecturas adicionales usadas por los SP. El owner es NOLOGIN y el actor
-- firmado sigue limitando toda tabla empresarial.
DO $sp_read_tenant$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['cliente','empleado','sucursal','categoria','producto','variante_producto','temporada'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_sp_owner USING (empresa_id=(SELECT fashionstore_private.actor_empresa_id()))','p_sp_select_'||t,t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['usuario','rol','plan_saas','talla','color','funcion','accion','rol_funcion'] LOOP
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO fashionstore_sp_owner USING(true)','p_sp_select_global_'||t,t);
 END LOOP;
 CREATE POLICY p_sp_empresa_select ON public.empresa FOR SELECT TO fashionstore_sp_owner
  USING(id=(SELECT fashionstore_private.actor_empresa_id()));
 CREATE POLICY p_sp_membresia_select ON public.usuario_empresa FOR SELECT TO fashionstore_sp_owner
  USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()));
 CREATE POLICY p_sp_suscripcion_select ON public.suscripcion FOR SELECT TO fashionstore_sp_owner
  USING(empresa_id=(SELECT fashionstore_private.actor_empresa_id()));
END $sp_read_tenant$;

-- Deny-by-default para cualquier tabla sin policy aplicable y FORCE para que
-- ni owners accidentales eviten RLS. Superuser/service_role siguen siendo una
-- frontera administrativa que debe auditarse fuera del login de aplicación.
DO $force$
DECLARE t record;
BEGIN
 FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r'
 LOOP EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t.relname);
      EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t.relname);
 END LOOP;
END $force$;
COMMIT;

