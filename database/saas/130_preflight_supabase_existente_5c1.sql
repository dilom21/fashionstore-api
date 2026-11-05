-- ============================================================================
-- 130 - PREFLIGHT DE SOLO LECTURA para migrar el Supabase EXISTENTE a SaaS
-- Etapa 5C1. NO modifica datos, NO crea objetos, NO requiere extensiones.
--
-- Ejecutar como propietario de las tablas (o superusuario del proyecto) en el
-- SQL Editor de Supabase, con la ventana de mantenimiento aun NO iniciada.
-- Guardar la salida completa como evidencia del redeploy.
--
-- Cada bloque devuelve una etiqueta y un valor. Criterio: si algun "estado"
-- aparece como BLOQUEA, la migracion se detiene y se corrige antes de seguir.
-- ============================================================================

-- 1) Version del motor. El pipeline exige PostgreSQL >= 17.
SELECT
  'version_motor' AS chequeo,
  current_setting('server_version_num')::int AS valor,
  CASE
    WHEN current_setting('server_version_num')::int >= 170000 THEN 'OK'
    ELSE 'BLOQUEA'
  END AS estado;

-- 2) Esquema original esperado (41 tablas + 1 vista). Si el conteo baja, la
--    base no es la que se respaldo y hay que detenerse.
SELECT
  'tablas_public' AS chequeo,
  count(*) AS valor,
  CASE WHEN count(*) >= 41 THEN 'OK' ELSE 'BLOQUEA' END AS estado
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';

-- 3) Estado del pipeline SaaS: 0 = base intacta, 19 = migracion completa.
SELECT
  'esquema_saas_existe' AS chequeo,
  (to_regnamespace('saas') IS NOT NULL)::text AS valor,
  CASE WHEN to_regnamespace('saas') IS NULL THEN 'OK (base limpia)' ELSE 'REVISAR' END AS estado;

SELECT
  'empresas_registradas' AS chequeo,
  COALESCE((SELECT count(*) FROM public.empresa), 0) AS valor,
  CASE
    WHEN to_regclass('public.empresa') IS NULL THEN 'OK (aun no migrado)'
    WHEN (SELECT count(*) FROM public.empresa) = 1 THEN 'OK'
    ELSE 'REVISAR (se espera 1 empresa historica antes de migrar)'
  END AS estado;

-- 4) Columnas empresa_id ya presentes (esperado 0 antes de 00200, 31 despues).
SELECT
  'tablas_con_empresa_id' AS chequeo,
  count(DISTINCT table_name) AS valor,
  CASE
    WHEN count(DISTINCT table_name) = 0 THEN 'OK (pendiente 00200)'
    WHEN count(DISTINCT table_name) >= 31 THEN 'OK (00200 aplicada)'
    ELSE 'BLOQUEA (migracion parcial: no reaplicar a ciegas)'
  END AS estado
FROM information_schema.columns
WHERE table_schema = 'public' AND column_name = 'empresa_id';

-- 5) RLS forzada (esperado 0 antes de 00400, 49 despues).
SELECT
  'tablas_force_rls' AS chequeo,
  count(*) AS valor,
  CASE
    WHEN count(*) = 0 THEN 'OK (pendiente 00400)'
    WHEN count(*) >= 49 THEN 'OK (00400 aplicada)'
    ELSE 'BLOQUEA (migracion parcial)'
  END AS estado
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity;

-- 6) Roles obligatorios. 00400 aborta si no existen o si el runtime es
--    SUPERUSER/BYPASSRLS o el owner puede iniciar sesion.
SELECT
  'roles_plataforma' AS chequeo,
  string_agg(rolname || '=' || CASE WHEN rolcanlogin THEN 'LOGIN' ELSE 'NOLOGIN' END, ', ' ORDER BY rolname) AS valor,
  CASE
    WHEN count(*) FILTER (WHERE rolname = 'fs_saas_runtime') = 0
      OR count(*) FILTER (WHERE rolname = 'saas_rls_owner') = 0
      THEN 'BLOQUEA (crear roles antes de 00400)'
    WHEN count(*) FILTER (
           WHERE rolname = 'fs_saas_runtime'
             AND (rolsuper OR rolbypassrls)
         ) > 0
      THEN 'BLOQUEA (fs_saas_runtime no puede ser SUPERUSER ni BYPASSRLS)'
    WHEN count(*) FILTER (
           WHERE rolname = 'saas_rls_owner' AND rolcanlogin
         ) > 0
      THEN 'BLOQUEA (saas_rls_owner debe ser NOLOGIN)'
    ELSE 'OK'
  END AS estado
FROM pg_roles
WHERE rolname IN ('fs_saas_runtime', 'saas_rls_owner', 'saas_admin_runtime');

-- 7) Bloqueo medido en laboratorio: 00300 exige que todo cliente tenga
--    membresia en su empresa. Sin esto la migracion aborta.
SELECT
  'clientes_sin_membresia' AS chequeo,
  count(*) AS valor,
  CASE
    WHEN to_regclass('public.usuario_empresa') IS NULL THEN 'OK (aun no migrado)'
    WHEN count(*) = 0 THEN 'OK'
    ELSE 'BLOQUEA (crear usuario_empresa antes de 00300)'
  END AS estado
FROM public.cliente c
WHERE to_regclass('public.usuario_empresa') IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM public.usuario_empresa ue
     WHERE ue.usuario_id = c.usuario_id
  );

-- 8) Duplicados que impedirian el cambio a unicidad POR EMPRESA (00300).
--    Antes de migrar, un NIT/SKU/nombre repetido en la empresa historica es
--    un dato a corregir por el propietario, no por la migracion.
SELECT 'duplicados_categoria_nombre' AS chequeo, count(*) AS valor,
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'BLOQUEA' END AS estado
FROM (
  SELECT lower(btrim(nombre)) FROM public.categoria GROUP BY 1 HAVING count(*) > 1
) d
UNION ALL
SELECT 'duplicados_variante_sku', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'BLOQUEA' END
FROM (
  SELECT lower(btrim(sku)) FROM public.variante_producto WHERE sku IS NOT NULL GROUP BY 1 HAVING count(*) > 1
) d
UNION ALL
SELECT 'duplicados_cliente_ci', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'BLOQUEA' END
FROM (
  SELECT lower(btrim(ci)) FROM public.cliente WHERE ci IS NOT NULL GROUP BY 1 HAVING count(*) > 1
) d;

-- 9) Volumen por tabla: linea base para comparar despues de migrar.
--    Solo lectura; sirve para demostrar que no se perdieron filas.
SELECT relname AS tabla, n_live_tup AS filas_estimadas
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY n_live_tup DESC, relname;

-- 10) Espacio y backups: confirmar que el plan de Supabase tiene PITR o que se
--     tomo un pg_dump verificado ANTES de aplicar cualquier DDL.
SELECT
  'tamano_base' AS chequeo,
  pg_size_pretty(pg_database_size(current_database())) AS valor,
  'INFORMATIVO' AS estado;
