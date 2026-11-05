-- FASHIONSTORE - SEGUNDO PARCIAL / SAAS / BLOQUE 2 / PASO 7
-- MIGRACION PORTABLE DE TENANCIA: agrega empresa_id a las tablas
-- empresariales heredadas y las vincula con public.empresa.
--
-- Reemplaza a 20261023000200_vanter_tenancy_historica.sql (laboratorio local)
-- conservando su estrategia probada para no reescribir datos ni disparar
-- triggers: ADD COLUMN ... DEFAULT constante aprovecha la optimizacion de
-- PostgreSQL 11+ (lectura del valor por tuplas antiguas, sin UPDATE masivo).
--
-- Diferencias deliberadas frente al script de laboratorio:
--   * no fija current_database(), ni puertos, ni conteos exactos de filas;
--   * es idempotente: si la columna ya existe solo verifica y completa
--     restricciones e indices faltantes;
--   * el tenant historico NO se inventa: se resuelve por slug mediante el
--     parametro de sesion `saas.bootstrap_empresa_slug`.
--
-- USO (misma sesion, antes de aplicar este archivo):
--   SET saas.bootstrap_empresa_slug = '<slug-de-la-empresa-historica>';
--   SET saas.bootstrap_crear_membresias = 'true';   -- opcional y explicito
--
-- `saas.bootstrap_crear_membresias=true` crea, de forma idempotente, una
-- membresia usuario_empresa para cada usuario historico que aun no tenga
-- ninguna en el tenant bootstrap, copiando su rol y estado globales. Sin ese
-- parametro NO se crean membresias: la migracion solo informa cuantas faltan.
--
-- El DEFAULT se retira inmediatamente: toda escritura nueva debe enviar
-- empresa_id explicito desde un contexto validado por FastAPI.
BEGIN;

SET LOCAL lock_timeout = '5s';

DO $tenancy_portable$
DECLARE
  v_tablas CONSTANT TEXT[] := ARRAY[
    'bitacora', 'sucursal', 'empleado', 'cliente', 'categoria', 'producto',
    'variante_producto', 'recurso_producto', 'temporada', 'coleccion',
    'producto_coleccion', 'promocion', 'promocion_producto', 'proveedor',
    'proveedor_producto', 'orden_compra', 'detalle_orden_compra',
    'inventario', 'movimiento_inventario', 'carrito', 'detalle_carrito',
    'reserva', 'detalle_reserva', 'venta', 'detalle_venta', 'pago',
    'devolucion', 'detalle_devolucion', 'configuracion_vestidor_ar',
    'sesion_vestidor_ar', 'prueba_vestidor_ar'
  ];
  v_slug TEXT := NULLIF(BTRIM(COALESCE(current_setting('saas.bootstrap_empresa_slug', TRUE), '')), '');
  v_crear_membresias BOOLEAN :=
    COALESCE(current_setting('saas.bootstrap_crear_membresias', TRUE), 'false') = 'true';
  v_empresa BIGINT;
  v_tabla TEXT;
  v_sin_columna BIGINT := 0;
  v_agregadas INTEGER := 0;
  v_fks INTEGER := 0;
  v_pendientes BIGINT;
  v_empresas BIGINT;
  v_membresias INTEGER := 0;
  v_usuarios_sin_membresia BIGINT := 0;
BEGIN
  IF to_regclass('public.empresa') IS NULL THEN
    RAISE EXCEPTION
      'Falta public.empresa: aplicar primero la migracion del nucleo SaaS';
  END IF;

  IF cardinality(v_tablas) <> 31
     OR (SELECT count(DISTINCT t) FROM unnest(v_tablas) u(t)) <> 31 THEN
    RAISE EXCEPTION 'La lista de 31 tablas empresariales esta incompleta o duplicada';
  END IF;

  FOREACH v_tabla IN ARRAY v_tablas LOOP
    IF to_regclass(format('public.%I', v_tabla)) IS NULL THEN
      RAISE EXCEPTION 'No existe la tabla empresarial requerida: %', v_tabla;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = v_tabla
        AND column_name = 'empresa_id'
    ) THEN
      v_sin_columna := v_sin_columna + 1;
    END IF;
  END LOOP;

  IF v_sin_columna > 0 THEN
    IF v_slug IS NULL THEN
      RAISE EXCEPTION
        'Hay % tablas sin empresa_id. Defina el tenant historico en la misma sesion con: SET saas.bootstrap_empresa_slug = ''<slug>'';',
        v_sin_columna;
    END IF;
    SELECT id INTO v_empresa FROM public.empresa WHERE slug = v_slug;
    IF v_empresa IS NULL THEN
      RAISE EXCEPTION
        'No existe empresa con slug=%: registre el tenant historico antes de migrar',
        v_slug;
    END IF;
  END IF;

  -- Resolucion del tenant para membresias/reportes en reejecuciones.
  IF v_empresa IS NULL AND v_slug IS NOT NULL THEN
    SELECT id INTO v_empresa FROM public.empresa WHERE slug = v_slug;
  END IF;
  IF v_empresa IS NULL THEN
    SELECT count(*), min(id) INTO v_empresas, v_empresa FROM public.empresa;
    IF v_empresas <> 1 THEN
      v_empresa := NULL;
    END IF;
  END IF;

  FOREACH v_tabla IN ARRAY v_tablas LOOP
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = v_tabla
        AND column_name = 'empresa_id'
    ) THEN
      -- ADD COLUMN con DEFAULT constante: no reescribe la tabla ni ejecuta
      -- UPDATE, por lo que los triggers historicos no se disparan.
      EXECUTE format(
        'ALTER TABLE public.%I ADD COLUMN empresa_id BIGINT %s DEFAULT %s',
        v_tabla,
        CASE WHEN v_tabla = 'bitacora' THEN '' ELSE 'NOT NULL' END,
        v_empresa
      );
      v_agregadas := v_agregadas + 1;
    END IF;

    -- Ninguna escritura nueva puede heredar el tenant por defecto.
    EXECUTE format(
      'ALTER TABLE public.%I ALTER COLUMN empresa_id DROP DEFAULT',
      v_tabla
    );

    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint co
      JOIN pg_catalog.pg_class cl ON cl.oid = co.conrelid
      JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
      WHERE ns.nspname = 'public' AND cl.relname = v_tabla
        AND co.conname = 'fk_empresa_' || v_tabla
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (empresa_id) REFERENCES public.empresa(id) ON UPDATE CASCADE ON DELETE RESTRICT',
        v_tabla, 'fk_empresa_' || v_tabla
      );
      v_fks := v_fks + 1;
    END IF;

    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS %I ON public.%I(empresa_id)',
      'idx_' || v_tabla || '_empresa', v_tabla
    );
  END LOOP;

  -- Membresias del tenant historico: solo si se autorizan explicitamente.
  IF v_empresa IS NOT NULL THEN
    SELECT count(*) INTO v_usuarios_sin_membresia
    FROM public.usuario u
    WHERE NOT EXISTS (
      SELECT 1 FROM public.usuario_empresa ue
      WHERE ue.usuario_id = u.id
    );

    IF v_crear_membresias THEN
      INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado)
      SELECT u.id, v_empresa, u.rol_id, u.estado
      FROM public.usuario u
      WHERE NOT EXISTS (
        SELECT 1 FROM public.usuario_empresa ue
        WHERE ue.usuario_id = u.id
      )
      ON CONFLICT (usuario_id, empresa_id) DO NOTHING;
      GET DIAGNOSTICS v_membresias = ROW_COUNT;
      RAISE NOTICE 'Membresias historicas creadas en empresa %: %',
        v_empresa, v_membresias;
    ELSIF v_usuarios_sin_membresia > 0 THEN
      RAISE NOTICE
        'Aviso: % usuarios historicos sin membresia. Use SET saas.bootstrap_crear_membresias = ''true'' para crearlas.',
        v_usuarios_sin_membresia;
    END IF;
  END IF;

  -- Endurecer NOT NULL solo cuando ya no queden filas sin tenant.
  FOREACH v_tabla IN ARRAY v_tablas LOOP
    IF v_tabla <> 'bitacora' THEN
      EXECUTE format(
        'SELECT count(*) FROM public.%I WHERE empresa_id IS NULL', v_tabla
      ) INTO v_pendientes;
      IF v_pendientes > 0 THEN
        RAISE EXCEPTION
          'La tabla % conserva % filas sin empresa_id: revise la asignacion antes de continuar',
          v_tabla, v_pendientes;
      END IF;
      EXECUTE format(
        'ALTER TABLE public.%I ALTER COLUMN empresa_id SET NOT NULL', v_tabla
      );
    END IF;
  END LOOP;

  RAISE NOTICE 'Tenancia: % columnas agregadas, % FK empresariales nuevas, tenant historico id=%',
    v_agregadas, v_fks, v_empresa;
END;
$tenancy_portable$;

COMMIT;
