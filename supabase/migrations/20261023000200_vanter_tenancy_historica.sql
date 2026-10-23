-- FASHIONSTORE / BLOQUE 2 / PASO 4 - ASIGNACION HISTORICA A VANTER MEN.
-- SOLO BASE LOCAL RESTAURADA: fashionstore_restore_20261023_193554_594.
-- NO APLICAR A SUPABASE. No habilitar SAAS_AUTH_ENABLED.
-- Enunciado PDF, Desafio VI, apartados 9.1-9.6 y RNF02.
--
-- Estrategia especial: agregar empresa_id con DEFAULT constante del nuevo tenant
-- para que PostgreSQL 18 materialice el valor de las filas existentes SIN
-- ejecutar UPDATE sobre datos comerciales ni disparar los 27 triggers.
-- Se retira inmediatamente el DEFAULT para nuevas inserciones; sera FastAPI
-- quien deba proporcionar empresa_id valido en todos los recursos de empresa.
-- La validacion de FK cruzadas y RLS de tablas antiguas es PASO 5.
--
-- Esta migracion fue disenada para ejecutarse UNA VEZ en la copia local
-- creada por el usuario; tiene preflight y rollback transaccional completo.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL TIME ZONE 'America/La_Paz';

CREATE TEMP TABLE saas_conteos_antes (
  tabla TEXT PRIMARY KEY,
  filas BIGINT NOT NULL
) ON COMMIT DROP;

DO $vanter_migracion$
DECLARE
  v_tablas CONSTANT TEXT[] := ARRAY[
    'bitacora',
    'sucursal',
    'empleado',
    'cliente',
    'categoria',
    'producto',
    'variante_producto',
    'recurso_producto',
    'temporada',
    'coleccion',
    'producto_coleccion',
    'promocion',
    'promocion_producto',
    'proveedor',
    'proveedor_producto',
    'orden_compra',
    'detalle_orden_compra',
    'inventario',
    'movimiento_inventario',
    'carrito',
    'detalle_carrito',
    'reserva',
    'detalle_reserva',
    'venta',
    'detalle_venta',
    'pago',
    'devolucion',
    'detalle_devolucion',
    'configuracion_vestidor_ar',
    'sesion_vestidor_ar',
    'prueba_vestidor_ar'
  ];
  v_tabla TEXT;
  v_vanter BIGINT;
  v_profesional BIGINT;
  v_actual BIGINT;
  v_antes BIGINT;
  v_user_count BIGINT;
  v_trigger_count BIGINT;
  v_tablas_public BIGINT;
BEGIN
  -- Defensa en profundidad: este SQL no debe operar sobre la BD productiva.
  -- current_database() es una comprobacion mas; el script PowerShell
  -- del Paso 4 fija adicionalmente host/puerto y nombre de base.
  IF current_database() <> 'fashionstore_restore_20261023_193554_594' THEN
    RAISE EXCEPTION 'Base incorrecta: %; solo se permite la copia local', current_database();
  END IF;

  SELECT COUNT(*) INTO v_tablas_public
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r';
  IF v_tablas_public <> 46 THEN
    RAISE EXCEPTION 'Se esperaban 46 tablas public; encontradas %', v_tablas_public;
  END IF;

  IF (SELECT COUNT(*) FROM public.usuario) <> 9
     OR (SELECT COUNT(*) FROM public.sucursal) <> 4
     OR (SELECT COUNT(*) FROM public.producto) <> 14
     OR (SELECT COUNT(*) FROM public.variante_producto) <> 120
     OR (SELECT COUNT(*) FROM public.inventario) <> 265
     OR (SELECT COUNT(*) FROM public.reserva) <> 11
     OR (SELECT COUNT(*) FROM public.venta) <> 40
     OR (SELECT COUNT(*) FROM public.pago) <> 30 THEN
    RAISE EXCEPTION 'Conteos historicos diferentes a los de la restauracion validada; detener';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.empresa
    UNION ALL SELECT 1 FROM public.plan_saas
    UNION ALL SELECT 1 FROM public.suscripcion
    UNION ALL SELECT 1 FROM public.usuario_empresa
    UNION ALL SELECT 1 FROM public.empresa_configuracion
  ) THEN
    RAISE EXCEPTION 'Las cinco tablas SaaS deben estar vacias antes de asignar Vanter Men';
  END IF;

  SELECT COUNT(*) INTO v_trigger_count
  FROM pg_catalog.pg_trigger tg
  JOIN pg_catalog.pg_class cl ON cl.oid=tg.tgrelid
  JOIN pg_catalog.pg_namespace ns ON ns.oid=cl.relnamespace
  WHERE ns.nspname='public' AND NOT tg.tgisinternal;
  IF v_trigger_count <> 27 THEN
    RAISE EXCEPTION 'Se esperaban 27 triggers previos, encontrados %', v_trigger_count;
  END IF;

  IF cardinality(v_tablas) <> 31 OR (
    SELECT COUNT(DISTINCT x) FROM unnest(v_tablas) AS x
  ) <> 31 THEN
    RAISE EXCEPTION 'La lista de 31 tablas empresariales esta incompleta o duplicada';
  END IF;

  -- Capturar numeros historicos para todas las tablas afectadas.
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    IF to_regclass(format('public.%I', v_tabla)) IS NULL THEN
      RAISE EXCEPTION 'No existe tabla empresarial: %', v_tabla;
    END IF;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name=v_tabla
        AND column_name='empresa_id'
    ) THEN
      RAISE EXCEPTION 'Tabla % ya dispone de empresa_id; no repetir', v_tabla;
    END IF;
    EXECUTE format(
      'INSERT INTO pg_temp.saas_conteos_antes (tabla, filas) SELECT %L, COUNT(*) FROM public.%I',
      v_tabla, v_tabla
    );
  END LOOP;

  -- Planes del prototipo academico: configurables mediante tabla plan_saas.
  INSERT INTO public.plan_saas
    (codigo, nombre, descripcion, max_sucursales_activas,
     permite_productos, permite_ventas, permite_reservas, permite_offline,
     permite_reportes_convencionales, permite_reportes_ia)
  VALUES
    ('BASICO', 'Basico', 'Hasta 2 sucursales activas, sin IA generativa',
     2, TRUE, TRUE, TRUE, TRUE, TRUE, FALSE),
    ('PROFESIONAL', 'Profesional', 'Hasta 10 sucursales e IA generativa',
     10, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE);

  SELECT id INTO STRICT v_profesional FROM public.plan_saas
  WHERE codigo='PROFESIONAL';

  -- Datos academicos de la empresa original; razon_social pendiente
  -- de confirmacion si existe una razon social legal diferente.
  INSERT INTO public.empresa (razon_social, nombre_comercial, slug, estado)
  VALUES ('Vanter Men', 'Vanter Men', 'vanter-men', TRUE)
  RETURNING id INTO v_vanter;

  INSERT INTO public.empresa_configuracion (empresa_id)
  VALUES (v_vanter);

  -- Alta administrativa de demo: 365 dias desde el dia local de aplicacion.
  -- Fecha y vigencia revisables antes del despliegue; no es una factura real.
  INSERT INTO public.suscripcion
    (empresa_id, plan_saas_id, fecha_inicio, fecha_vencimiento, estado, observacion)
  VALUES
    (v_vanter, v_profesional, CURRENT_DATE, CURRENT_DATE + 365,
     'ACTIVA', 'Migracion local academica de la tienda original');

  -- Rol empresarial igual al antiguo POR AHORA, sin alterar usuario.rol_id.
  -- Se conserva el estado individual del usuario, sin inventar un superadmin.
  INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado)
  SELECT u.id, v_vanter, u.rol_id, u.estado
  FROM public.usuario AS u;

  GET DIAGNOSTICS v_user_count = ROW_COUNT;
  IF v_user_count <> 9 THEN
    RAISE EXCEPTION 'Se esperaban nueve membresias de Vanter Men; hubo %', v_user_count;
  END IF;

  -- Asignacion de la propiedad sin UPDATE de registros existentes.
  -- ADD COLUMN DEFAULT entero constante aprovecha la optimizacion PG 11+.
  -- NOT NULL excepto bitacora: eventos futuros globales podrian llevar NULL,
  -- sujeto a control estricto en el backend (paso siguiente).
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    EXECUTE format(
      'ALTER TABLE public.%I ADD COLUMN empresa_id BIGINT %s DEFAULT %s ' ||
      'REFERENCES public.empresa(id) ON UPDATE CASCADE ON DELETE RESTRICT',
      v_tabla,
      CASE WHEN v_tabla='bitacora' THEN '' ELSE 'NOT NULL' END,
      v_vanter
    );
    -- Las nuevas operaciones deben establecer tenant explicitamente.
    EXECUTE format(
      'ALTER TABLE public.%I ALTER COLUMN empresa_id DROP DEFAULT',
      v_tabla
    );
  END LOOP;

  -- Verificar pertenencia y TODOS los conteos tras el cambio.
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    SELECT filas INTO STRICT v_antes FROM pg_temp.saas_conteos_antes
    WHERE tabla=v_tabla;
    EXECUTE format('SELECT COUNT(*) FROM public.%I', v_tabla) INTO v_actual;
    IF v_actual <> v_antes THEN
      RAISE EXCEPTION 'Conteo alterado en %: % antes, % despues',
        v_tabla, v_antes, v_actual;
    END IF;
    EXECUTE format(
      'SELECT COUNT(*) FROM public.%I WHERE empresa_id IS DISTINCT FROM %s',
      v_tabla, v_vanter
    ) INTO v_actual;
    IF v_actual <> 0 THEN
      RAISE EXCEPTION 'Asignacion historica incompleta o cruzada en %', v_tabla;
    END IF;
  END LOOP;

  SELECT COUNT(*) INTO v_actual FROM pg_catalog.pg_trigger tg
  JOIN pg_catalog.pg_class cl ON cl.oid=tg.tgrelid
  JOIN pg_catalog.pg_namespace ns ON ns.oid=cl.relnamespace
  WHERE ns.nspname='public' AND NOT tg.tgisinternal;
  IF v_actual <> v_trigger_count THEN
    RAISE EXCEPTION 'Los triggers cambiaron inesperadamente: % frente a %',
      v_actual, v_trigger_count;
  END IF;

  IF (SELECT COUNT(*) FROM public.usuario_empresa WHERE empresa_id=v_vanter) <> 9
     OR (SELECT COUNT(*) FROM public.sucursal WHERE empresa_id=v_vanter) <> 4
     OR (SELECT COUNT(*) FROM public.venta WHERE empresa_id=v_vanter) <> 40
     OR (SELECT COUNT(*) FROM public.pago WHERE empresa_id=v_vanter) <> 30 THEN
    RAISE EXCEPTION 'Conteos esenciales de Vanter Men inconsistentes';
  END IF;

  -- Las 5 tablas SaaS ya existen desde Paso 3; el total sigue en 46.
  RAISE NOTICE 'Vanter Men id=%, 9 membresias, 31 tablas historicas asignadas', v_vanter;
END;
$vanter_migracion$;
COMMIT;

-- NO crea otras empresas ni cambia FK comerciales originales.
-- No ejecutar contra Supabase; tampoco activa de manera automatica FastAPI SaaS.
