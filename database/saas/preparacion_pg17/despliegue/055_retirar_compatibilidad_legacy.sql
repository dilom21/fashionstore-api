-- COPIA DE DESPLIEGUE generada por generar_despliegue.py — NO EDITAR A MANO.
-- Propuesta: 055
-- Original protegido: 055_retirar_compatibilidad_legacy_PROPUESTA_NO_EJECUTAR.sql
-- sha256 del original: a9043ced485702e13c687a8d709dfd4ee6d1d8f2f0f149918534e5c1f8ba653f
-- Cambio aplicado: retirada de la guarda 'BLOQUEADO: propuesta ... no aprobada'
--   (una sola sentencia DO del encabezado). El resto del archivo es idéntico
--   byte a byte; el sha256 de esta copia queda en MANIFIESTO.sha256.
-- Esta copia no modifica el original ni constituye por sí misma una
-- autorización: la ventana de despliegue se aprueba aparte.
-- Nota: los comentarios 'NO EJECUTAR' del cuerpo son texto del original y
--   se conservan sin cambios para que la copia sea auditable.

-- PROPUESTA LOCAL. NO EJECUTAR de forma aislada ni antes de 050a-050c.

-- Retira exclusivamente la compatibilidad monocompania instalada por 045.
-- Debe ejecutarse con la API detenida, inmediatamente despues de 050c.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';
SELECT pg_advisory_xact_lock(hashtextextended('fashionstore_saas_pg17',0));

DO $preflight$
DECLARE n integer;
BEGIN
 IF (SELECT count(*) FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
     WHERE ns.nspname='public' AND c.relkind='r')<>46 THEN
  RAISE EXCEPTION 'Se esperaban 46 tablas antes de retirar 045';
 END IF;
 IF (SELECT count(*) FROM public.empresa)<>1
    OR (SELECT count(*) FROM public.empresa WHERE slug='vanter-men' AND estado)<>1 THEN
  RAISE EXCEPTION '055 exige que Vanter Men siga siendo la unica empresa';
 END IF;
 SELECT count(*) INTO n FROM pg_roles
 WHERE rolname=ANY(ARRAY['fashionstore_api','fashionstore_bootstrap',
  'fashionstore_platform_admin','fashionstore_context_owner',
  'fashionstore_bootstrap_owner','fashionstore_sp_owner',
  'fashionstore_audit_owner']);
 IF n<>7 THEN RAISE EXCEPTION '050a no esta completo: roles=%',n; END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname LIKE 'fashionstore%'
           AND (rolsuper OR rolbypassrls)) THEN
  RAISE EXCEPTION 'Existe un rol FashionStore privilegiado';
 END IF;
 SELECT count(*) INTO n
 FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
 WHERE ns.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity;
 IF n<>46 THEN RAISE EXCEPTION '050b no esta completo: FORCE RLS=%/46',n; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public') THEN
  RAISE EXCEPTION '050b no instalo policies';
 END IF;
 IF has_table_privilege('fashionstore_api','public.producto','SELECT') IS NOT TRUE
    OR has_table_privilege('fashionstore_platform_admin','public.empresa','INSERT') IS NOT TRUE
    OR EXISTS(
      SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace
      WHERE ns.nspname='public' AND p.prokind='p'
        AND p.proname=ANY(ARRAY['sp_ajustar_inventario','sp_cancelar_reserva',
          'sp_confirmar_venta','sp_crear_reserva_desde_carrito','sp_crear_reserva',
          'sp_finalizar_reserva_sin_compra','sp_recalcular_total_venta',
          'sp_recibir_orden_compra','sp_registrar_devolucion'])
        AND (has_function_privilege('anon',p.oid,'EXECUTE')
             OR has_function_privilege('authenticated',p.oid,'EXECUTE'))
    ) THEN
  RAISE EXCEPTION '050c no dejo las ACL esperadas';
 END IF;
 SELECT count(*) INTO n FROM pg_trigger
 WHERE tgname LIKE 'trg_%_asignar_vanter_legacy' AND NOT tgisinternal;
 IF n<>31 THEN RAISE EXCEPTION '045 incompleto: triggers de asignacion=%/31',n; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger
   WHERE tgrelid='public.empresa'::regclass
     AND tgname='trg_empresa_guard_legacy_vanter_monotenant' AND NOT tgisinternal)
 OR NOT EXISTS(SELECT 1 FROM pg_trigger
   WHERE tgrelid='public.usuario'::regclass
     AND tgname='trg_usuario_empresa_vanter_legacy' AND NOT tgisinternal) THEN
  RAISE EXCEPTION '045 incompleto: faltan guardas temporales';
 END IF;
END $preflight$;

DO $drop_insert_triggers$
DECLARE t text;
 tables constant text[]:=ARRAY[
  'bitacora','carrito','categoria','cliente','coleccion',
  'configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
  'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
  'empleado','inventario','movimiento_inventario','orden_compra','pago',
  'producto','producto_coleccion','promocion','promocion_producto','proveedor',
  'proveedor_producto','prueba_vestidor_ar','recurso_producto','reserva',
  'sesion_vestidor_ar','sucursal','temporada','variante_producto','venta'];
BEGIN
 FOREACH t IN ARRAY tables LOOP
  EXECUTE format('DROP TRIGGER %I ON public.%I',
                 'trg_'||t||'_asignar_vanter_legacy',t);
 END LOOP;
END $drop_insert_triggers$;

DROP TRIGGER trg_usuario_empresa_vanter_legacy ON public.usuario;
DROP TRIGGER trg_empresa_guard_legacy_vanter_monotenant ON public.empresa;
DROP FUNCTION public.fn_sincronizar_usuario_empresa_vanter_legacy();
DROP FUNCTION public.fn_asignar_vanter_insert_legacy();
DROP FUNCTION public.fn_guard_legacy_vanter_monotenant();

DO $postcheck$
DECLARE n integer;
BEGIN
 SELECT count(*) INTO n FROM pg_trigger
 WHERE (tgname LIKE 'trg_%_asignar_vanter_legacy'
        OR tgname IN('trg_usuario_empresa_vanter_legacy',
                     'trg_empresa_guard_legacy_vanter_monotenant'))
   AND NOT tgisinternal;
 IF n<>0 THEN RAISE EXCEPTION 'Persisten % triggers temporales',n; END IF;
 IF to_regprocedure('public.fn_sincronizar_usuario_empresa_vanter_legacy()') IS NOT NULL
 OR to_regprocedure('public.fn_asignar_vanter_insert_legacy()') IS NOT NULL
 OR to_regprocedure('public.fn_guard_legacy_vanter_monotenant()') IS NOT NULL THEN
  RAISE EXCEPTION 'Persisten funciones temporales de 045';
 END IF;
 IF EXISTS(SELECT 1 FROM information_schema.columns
  WHERE table_schema='public' AND column_name='empresa_id'
    AND column_default IS NOT NULL) THEN
  RAISE EXCEPTION 'Existe un DEFAULT empresarial global';
 END IF;
 IF (SELECT count(*) FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
     WHERE ns.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity)<>46 THEN
  RAISE EXCEPTION 'FORCE RLS dejo de cubrir las 46 tablas';
 END IF;
END $postcheck$;
COMMIT;
