-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / ETAPA I (5BR)
-- COMPATIBILIDAD CON EL ESQUEMA HISTORICO **REAL** (41 tablas / 27 triggers / 24 rutinas)
--
-- Por que existe esta migracion
-- -----------------------------
-- Las etapas B..G se ensayaron sobre la reconstruccion 5BL (40/14/12). Al
-- restaurar el `pg_dump --schema-only` REAL del esquema `public` aparecen dos
-- incompatibilidades que la reconstruccion no podia revelar:
--
--   1. El origen habilita `ROW LEVEL SECURITY` en 38 de sus 41 tablas, pero con
--      CERO politicas (estado tipico de Supabase: la RLS se activa y se deja
--      deny-by-default). Nueve de esas tablas son diccionarios GLOBALES sin
--      `empresa_id`: rol, rol_funcion, modulo, accion, funcion, ciudad, color,
--      talla. Sin politica, `saas_rls_owner` (propietario NOLOGIN de
--      `saas.tenant_actual()` / `saas.login_membresia()`) y `fs_saas_runtime`
--      leen CERO filas y TODO el contrato de login devuelve NULL. Se corrige con
--      una politica de solo lectura explicita, NO desactivando la RLS.
--   2. El dump trae los `GRANT ALL ... TO anon/authenticated/service_role` que
--      aprovisiona la plataforma. Esos roles no forman parte del runtime SaaS y
--      conservaban privilegios plenos sobre las 41 tablas. Se revocan aqui.
--
-- Ademas, esta migracion cierra el contrato B1 con verificacion fail-closed:
-- exige la presencia nominal de las 41 tablas, los 27 triggers y las 24 rutinas
-- originales, y rechaza cualquier `SECURITY DEFINER` o `search_path` ausente en
-- las funciones de trigger heredadas.
--
-- Requisitos: 20261030000100..00500 aplicadas sobre un esquema historico REAL ya
-- restaurado. Idempotente. No debilita ninguna RLS: solo agrega lectura sobre
-- catalogos globales sin datos de negocio y revoca privilegios de plataforma.
BEGIN;

SET LOCAL lock_timeout = '5s';

-- ===========================================================================
-- 0. Preflight: el destino debe ser el esquema ORIGINAL, no una reconstruccion
-- ===========================================================================
DO $preflight_5br$
DECLARE
  v_faltan TEXT;
  v_inseguras TEXT;
  v_tablas CONSTANT TEXT[] := ARRAY[
    'accion','bitacora','carrito','categoria','ciudad','cliente','coleccion',
    'color','configuracion_vestidor_ar','detalle_carrito','detalle_devolucion',
    'detalle_orden_compra','detalle_reserva','detalle_venta','devolucion',
    'empleado','funcion','inventario','modulo','movimiento_inventario',
    'orden_compra','pago','producto','producto_coleccion','promocion',
    'promocion_producto','proveedor','proveedor_producto','prueba_vestidor_ar',
    'recurso_producto','reserva','rol','rol_funcion','sesion_vestidor_ar',
    'sucursal','talla','temporada','token_recuperacion','usuario',
    'variante_producto','venta'
  ];
  v_triggers CONSTANT TEXT[] := ARRAY[
    'trg_bitacora_completar_ip','trg_bitacora_cliente',
    'trg_actualizar_actividad_carrito','trg_validar_sucursal_detalle_carrito',
    'trg_validar_detalle_devolucion','trg_proteger_detalle_orden_compra',
    'trg_validar_producto_proveedor_orden','trg_validar_detalle_reserva_sucursal',
    'trg_proteger_detalle_venta','trg_recalcular_total_venta',
    'trg_validar_detalle_venta_sucursal','trg_bitacora_devolucion',
    'trg_bitacora_empleado','trg_bitacora_inventario',
    'trg_controlar_actualizacion_inventario','trg_bitacora_orden_compra',
    'trg_bitacora_pago','trg_bitacora_producto','trg_bitacora_reserva',
    'trg_bitacora_rol','trg_bitacora_sucursal','trg_bitacora_usuario',
    'trg_normalizar_correo_usuario','trg_bitacora_variante_producto',
    'trg_normalizar_sku','trg_bitacora_venta','trg_proteger_eliminacion_venta'
  ];
  v_rutinas CONSTANT TEXT[] := ARRAY[
    'fn_actualizar_actividad_carrito','fn_completar_ip_bitacora',
    'fn_controlar_actualizacion_inventario','fn_normalizar_correo_usuario',
    'fn_normalizar_sku','fn_proteger_detalle_orden_compra',
    'fn_proteger_detalle_venta','fn_proteger_eliminacion_venta',
    'fn_recalcular_total_venta_trigger','fn_registrar_bitacora',
    'fn_validar_detalle_devolucion','fn_validar_detalle_reserva_sucursal',
    'fn_validar_detalle_venta_sucursal','fn_validar_producto_proveedor_orden',
    'fn_validar_sucursal_detalle_carrito','sp_ajustar_inventario',
    'sp_cancelar_reserva','sp_confirmar_venta','sp_crear_reserva',
    'sp_crear_reserva_desde_carrito','sp_finalizar_reserva_sin_compra',
    'sp_recalcular_total_venta','sp_recibir_orden_compra','sp_registrar_devolucion'
  ];
BEGIN
  IF cardinality(v_tablas) <> 41 OR cardinality(v_triggers) <> 27
     OR cardinality(v_rutinas) <> 24 THEN
    RAISE EXCEPTION '5BR: las listas de objetos originales fueron alteradas';
  END IF;

  IF to_regclass('public.token_recuperacion') IS NULL THEN
    RAISE EXCEPTION
      '5BR: falta public.token_recuperacion. El destino NO es el esquema original restaurado (probablemente es la reconstruccion 5BL).';
  END IF;

  SELECT string_agg(t, ', ') INTO v_faltan
    FROM unnest(v_tablas) t
   WHERE to_regclass(format('public.%I', t)) IS NULL;
  IF v_faltan IS NOT NULL THEN
    RAISE EXCEPTION '5BR: faltan tablas originales: %', v_faltan;
  END IF;

  SELECT string_agg(t, ', ') INTO v_faltan
    FROM unnest(v_triggers) t
   WHERE NOT EXISTS (SELECT 1 FROM pg_trigger tg
                      WHERE tg.tgname = t AND NOT tg.tgisinternal);
  IF v_faltan IS NOT NULL THEN
    RAISE EXCEPTION '5BR: faltan triggers originales: %', v_faltan;
  END IF;

  SELECT string_agg(t, ', ') INTO v_faltan
    FROM unnest(v_rutinas) t
   WHERE NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n
                       ON n.oid = p.pronamespace
                      WHERE n.nspname = 'public' AND p.proname = t);
  IF v_faltan IS NOT NULL THEN
    RAISE EXCEPTION '5BR: faltan rutinas originales: %', v_faltan;
  END IF;

  -- Ninguna funcion de trigger original puede ser SECURITY DEFINER ni quedarse
  -- sin search_path fijo: eso convertiria el esquema heredado en superficie de
  -- escalamiento para el principal restringido.
  SELECT string_agg(p.proname, ', ') INTO v_inseguras
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    JOIN (SELECT DISTINCT tgfoid FROM pg_trigger WHERE NOT tgisinternal) t
      ON t.tgfoid = p.oid
   WHERE n.nspname = 'public'
     AND (p.prosecdef
          OR NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) c
                          WHERE c LIKE 'search_path=%'));
  IF v_inseguras IS NOT NULL THEN
    RAISE EXCEPTION
      '5BR: funciones de trigger sin search_path o SECURITY DEFINER: %', v_inseguras;
  END IF;
END;
$preflight_5br$;

-- ===========================================================================
-- 1. Privilegios heredados de la plataforma Supabase
-- ===========================================================================
-- El dump concede ALL a anon/authenticated/service_role. Esos roles NO son el
-- runtime SaaS: se revocan por completo (tablas, secuencias y rutinas) sin tocar
-- postgres ni los propietarios NOLOGIN. NO se desactiva ninguna RLS.
DO $acl_plataforma$
DECLARE
  v_rol  TEXT;
  v_item RECORD;
BEGIN
  FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);

    FOR v_item IN
      SELECT c.oid::regclass AS objeto,
             CASE c.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END AS tipo
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
       WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
         AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = v_rol)
    LOOP
      IF v_item.tipo = 'SEQUENCE' THEN
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', v_item.objeto, v_rol);
      ELSE
        EXECUTE format('REVOKE ALL ON TABLE %s FROM %I', v_item.objeto, v_rol);
      END IF;
    END LOOP;

    FOR v_item IN
      SELECT p.oid::regprocedure AS firma
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'public'
         AND has_function_privilege(v_rol, p.oid, 'EXECUTE')
    LOOP
      EXECUTE format('REVOKE ALL ON ROUTINE %s FROM %I', v_item.firma, v_rol);
    END LOOP;

    EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', v_rol);
  END LOOP;
END;
$acl_plataforma$;

-- ===========================================================================
-- 2. Diccionarios globales: lectura explicita bajo RLS (NO se desactiva)
-- ===========================================================================
-- Estas ocho tablas no contienen datos de negocio ni empresa_id: son catalogos
-- compartidos. El origen las dejo con RLS habilitada y CERO politicas, lo que
-- las hacia invisibles para cualquier rol que no fuese el propietario. La
-- politica siguiente concede SOLO SELECT, y solo a los principales del SaaS.
DO $catalogos$
DECLARE
  v_roles TEXT[] := ARRAY[
    COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
             'fs_saas_runtime'),
    COALESCE(NULLIF(current_setting('saas.rls_owner_role', TRUE), ''),
             'saas_rls_owner'),
    'saas_admin_owner', 'saas_actor_sql_owner', 'saas_personal_owner'
  ];
  v_tabla TEXT;
  v_rol   TEXT;
  v_destinos TEXT;
  v_tablas CONSTANT TEXT[] := ARRAY[
    'rol', 'rol_funcion', 'modulo', 'accion', 'funcion', 'ciudad', 'color', 'talla'
  ];
BEGIN
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', v_tabla);
    EXECUTE format('DROP POLICY IF EXISTS catalogo_lectura_saas ON public.%I', v_tabla);

    v_destinos := '';
    FOREACH v_rol IN ARRAY v_roles LOOP
      CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_rol);
      v_destinos := v_destinos
        || CASE WHEN v_destinos = '' THEN '' ELSE ', ' END
        || quote_ident(v_rol);
    END LOOP;

    IF v_destinos <> '' THEN
      EXECUTE format(
        'CREATE POLICY catalogo_lectura_saas ON public.%I AS PERMISSIVE '
        'FOR SELECT TO %s USING (true)', v_tabla, v_destinos);
    END IF;
  END LOOP;
END;
$catalogos$;

-- ===========================================================================
-- 3. token_recuperacion: objeto original conservado, FUERA del contrato tenant
-- ===========================================================================
-- La tabla existe en el origen y se conserva (no se borra funcionalidad), pero
-- no participa del aislamiento por empresa: guarda tokens de recuperacion por
-- usuario global. Se fuerza RLS sin politicas (deny-all salvo propietario) y se
-- garantiza que el runtime no tenga privilegios.
DO $token$
DECLARE
  v_runtime TEXT := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
BEGIN
  EXECUTE 'ALTER TABLE public.token_recuperacion ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE public.token_recuperacion FORCE ROW LEVEL SECURITY';
  EXECUTE format('REVOKE ALL ON TABLE public.token_recuperacion FROM %I', v_runtime);
  EXECUTE 'REVOKE ALL ON TABLE public.token_recuperacion FROM PUBLIC';
  EXECUTE 'COMMENT ON TABLE public.token_recuperacion IS '
    '''Esquema historico ORIGINAL: tokens de recuperacion por usuario global. '
    'Conservada sin cambios funcionales; FORCE RLS sin politicas (deny-all) y sin '
    'privilegios para el principal tenant. Clasificacion 5BR: CONSERVAR.''';
END;
$token$;

-- ===========================================================================
-- 4. Inventario de compatibilidad (evidencia consultable)
-- ===========================================================================
CREATE OR REPLACE VIEW saas.compatibilidad_esquema_real
WITH (security_invoker = true)
AS
WITH originales(clase, nombre) AS (
  VALUES
    ('tabla', 'accion'), ('tabla', 'bitacora'), ('tabla', 'carrito'),
    ('tabla', 'categoria'), ('tabla', 'ciudad'), ('tabla', 'cliente'),
    ('tabla', 'coleccion'), ('tabla', 'color'),
    ('tabla', 'configuracion_vestidor_ar'), ('tabla', 'detalle_carrito'),
    ('tabla', 'detalle_devolucion'), ('tabla', 'detalle_orden_compra'),
    ('tabla', 'detalle_reserva'), ('tabla', 'detalle_venta'),
    ('tabla', 'devolucion'), ('tabla', 'empleado'), ('tabla', 'funcion'),
    ('tabla', 'inventario'), ('tabla', 'modulo'),
    ('tabla', 'movimiento_inventario'), ('tabla', 'orden_compra'),
    ('tabla', 'pago'), ('tabla', 'producto'), ('tabla', 'producto_coleccion'),
    ('tabla', 'promocion'), ('tabla', 'promocion_producto'),
    ('tabla', 'proveedor'), ('tabla', 'proveedor_producto'),
    ('tabla', 'prueba_vestidor_ar'), ('tabla', 'recurso_producto'),
    ('tabla', 'reserva'), ('tabla', 'rol'), ('tabla', 'rol_funcion'),
    ('tabla', 'sesion_vestidor_ar'), ('tabla', 'sucursal'), ('tabla', 'talla'),
    ('tabla', 'temporada'), ('tabla', 'token_recuperacion'), ('tabla', 'usuario'),
    ('tabla', 'variante_producto'), ('tabla', 'venta'),
    ('trigger', 'trg_bitacora_completar_ip'), ('trigger', 'trg_bitacora_cliente'),
    ('trigger', 'trg_actualizar_actividad_carrito'),
    ('trigger', 'trg_validar_sucursal_detalle_carrito'),
    ('trigger', 'trg_validar_detalle_devolucion'),
    ('trigger', 'trg_proteger_detalle_orden_compra'),
    ('trigger', 'trg_validar_producto_proveedor_orden'),
    ('trigger', 'trg_validar_detalle_reserva_sucursal'),
    ('trigger', 'trg_proteger_detalle_venta'),
    ('trigger', 'trg_recalcular_total_venta'),
    ('trigger', 'trg_validar_detalle_venta_sucursal'),
    ('trigger', 'trg_bitacora_devolucion'), ('trigger', 'trg_bitacora_empleado'),
    ('trigger', 'trg_bitacora_inventario'),
    ('trigger', 'trg_controlar_actualizacion_inventario'),
    ('trigger', 'trg_bitacora_orden_compra'), ('trigger', 'trg_bitacora_pago'),
    ('trigger', 'trg_bitacora_producto'), ('trigger', 'trg_bitacora_reserva'),
    ('trigger', 'trg_bitacora_rol'), ('trigger', 'trg_bitacora_sucursal'),
    ('trigger', 'trg_bitacora_usuario'),
    ('trigger', 'trg_normalizar_correo_usuario'),
    ('trigger', 'trg_bitacora_variante_producto'),
    ('trigger', 'trg_normalizar_sku'), ('trigger', 'trg_bitacora_venta'),
    ('trigger', 'trg_proteger_eliminacion_venta'),
    ('rutina', 'fn_actualizar_actividad_carrito'),
    ('rutina', 'fn_completar_ip_bitacora'),
    ('rutina', 'fn_controlar_actualizacion_inventario'),
    ('rutina', 'fn_normalizar_correo_usuario'), ('rutina', 'fn_normalizar_sku'),
    ('rutina', 'fn_proteger_detalle_orden_compra'),
    ('rutina', 'fn_proteger_detalle_venta'),
    ('rutina', 'fn_proteger_eliminacion_venta'),
    ('rutina', 'fn_recalcular_total_venta_trigger'),
    ('rutina', 'fn_registrar_bitacora'),
    ('rutina', 'fn_validar_detalle_devolucion'),
    ('rutina', 'fn_validar_detalle_reserva_sucursal'),
    ('rutina', 'fn_validar_detalle_venta_sucursal'),
    ('rutina', 'fn_validar_producto_proveedor_orden'),
    ('rutina', 'fn_validar_sucursal_detalle_carrito'),
    ('rutina', 'sp_ajustar_inventario'), ('rutina', 'sp_cancelar_reserva'),
    ('rutina', 'sp_confirmar_venta'), ('rutina', 'sp_crear_reserva'),
    ('rutina', 'sp_crear_reserva_desde_carrito'),
    ('rutina', 'sp_finalizar_reserva_sin_compra'),
    ('rutina', 'sp_recalcular_total_venta'),
    ('rutina', 'sp_recibir_orden_compra'), ('rutina', 'sp_registrar_devolucion')
)
SELECT o.clase,
       o.nombre,
       CASE o.clase
         WHEN 'tabla' THEN to_regclass(format('public.%I', o.nombre)) IS NOT NULL
         WHEN 'trigger' THEN EXISTS (SELECT 1 FROM pg_trigger tg
                                      WHERE tg.tgname = o.nombre
                                        AND NOT tg.tgisinternal)
         ELSE EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n
                        ON n.oid = p.pronamespace
                       WHERE n.nspname = 'public' AND p.proname = o.nombre)
       END AS presente,
       CASE o.clase
         WHEN 'rutina' THEN (
           SELECT p.prosecdef FROM pg_proc p JOIN pg_namespace n
             ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = o.nombre LIMIT 1)
         ELSE NULL
       END AS security_definer
  FROM originales o;

COMMENT ON VIEW saas.compatibilidad_esquema_real IS
  '5BR: inventario nominal de las 41 tablas, 27 triggers y 24 rutinas del esquema historico REAL con su presencia tras migrar. Sin DDL ni datos.';

-- ===========================================================================
-- 5. Fail-closed: ninguna regresion de seguridad ni objeto perdido
-- ===========================================================================
CREATE OR REPLACE FUNCTION saas.validar_compatibilidad_esquema_real()
RETURNS TABLE(violacion TEXT)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public, saas
AS $validar$
  SELECT format('objeto original ausente: %s %s', clase, nombre)
    FROM saas.compatibilidad_esquema_real
   WHERE NOT presente
  UNION ALL
  SELECT format('rutina original SECURITY DEFINER: %s', nombre)
    FROM saas.compatibilidad_esquema_real
   WHERE clase = 'rutina' AND security_definer IS TRUE
  UNION ALL
  SELECT format('diccionario global sin politica de lectura: %s', c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relname = ANY (ARRAY['rol','rol_funcion','modulo','accion','funcion',
                                'ciudad','color','talla'])
     AND c.relrowsecurity
     AND NOT EXISTS (SELECT 1 FROM pg_policies p
                      WHERE p.schemaname = 'public' AND p.tablename = c.relname)
  UNION ALL
  SELECT format('rol de plataforma con privilegios: %s sobre %s', r.rolname,
                c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN (VALUES ('anon'), ('authenticated'), ('service_role')) v(r)
    JOIN pg_roles r ON r.rolname = v.r
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S') AND a.grantee = r.oid
  UNION ALL
  SELECT format('token_recuperacion con privilegios para el runtime')
   WHERE has_table_privilege(
           COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                    'fs_saas_runtime'),
           'public.token_recuperacion', 'SELECT');
$validar$;

DO $fail_closed_5br$
DECLARE
  v TEXT;
BEGIN
  SELECT string_agg(violacion, ' | ') INTO v
    FROM saas.validar_compatibilidad_esquema_real();
  IF v IS NOT NULL THEN
    RAISE EXCEPTION '5BR BLOQUEADO: %', v;
  END IF;
END;
$fail_closed_5br$;

DO $acl_final_5br$
DECLARE
  v_runtime TEXT := COALESCE(NULLIF(current_setting('saas.runtime_role', TRUE), ''),
                             'fs_saas_runtime');
  v_owner   TEXT := COALESCE(NULLIF(current_setting('saas.rls_owner_role', TRUE), ''),
                             'saas_rls_owner');
BEGIN
  EXECUTE 'REVOKE ALL ON saas.compatibilidad_esquema_real FROM PUBLIC';
  EXECUTE format('GRANT SELECT ON saas.compatibilidad_esquema_real TO %I', v_runtime);
  EXECUTE format('GRANT SELECT ON saas.compatibilidad_esquema_real TO %I', v_owner);
  EXECUTE 'REVOKE ALL ON FUNCTION saas.validar_compatibilidad_esquema_real() '
          'FROM PUBLIC';
  EXECUTE format('REVOKE ALL ON FUNCTION saas.validar_compatibilidad_esquema_real() '
                 'FROM %I', v_runtime);
END;
$acl_final_5br$;

COMMIT;
