-- ============================================================================
-- 5BQ / Etapa H - Catalogo JSON de SOLO LECTURA del esquema real (cierre B1)
--
-- QUE ES
--   Puente que faltaba entre la extraccion autorizada (80/81 de 5BO) y el
--   comparador determinista (87 de 5BP). 81 produce texto de psql para humanos;
--   87 exige un JSON con un contrato exacto. Este archivo emite ese JSON con
--   las MISMAS expresiones de catalogo que usa 87, de modo que la comparacion
--   sea por objeto y no por cantidad.
--
-- COMO SE USA
--   A) psql (preferido, escribe el archivo directamente):
--        psql ... --no-psqlrc --tuples-only --no-align ^
--             --file database/saas/92_catalogo_json_solo_lectura_5bq.sql ^
--             | Out-File -Encoding utf8 <ruta fuera de Git>\catalogo_origen.json
--   B) Supabase SQL Editor: pegar TODO el archivo, ejecutar, copiar el unico
--      valor de la columna "catalogo" a un archivo .json fuera de Git.
--
-- SEGURIDAD
--   * Solo SELECT sobre catalogos del sistema (pg_class, pg_proc, pg_trigger,
--     pg_policies, pg_roles, pg_extension, information_schema.columns).
--   * No lee filas de negocio, no usa reltuples, no ejecuta DDL ni DML.
--   * No imprime cuerpos de funciones: emite md5(prosrc), no el texto.
--   * No requiere extensiones (nada de pgcrypto): usa md5() y json_build_*.
--   * No contiene secretos ni cadenas de conexion.
--
-- SALIDA
--   Una sola fila y una sola columna (`catalogo`) con este contrato, identico
--   al que produce 87 --exportar-dsn:
--     meta{version_servidor,base,usuario,capturado_utc,procedimiento}
--     objetos{tablas,vistas,columnas_empresa_id,rutinas,triggers,politicas,
--             propietarios,roles,extensiones,privilegios_rutinas}
--     conteos{tablas_public,triggers_public,rutinas_public,vistas_public}
--     extra{...}   -> auditoria adicional; 87 lo ignora a proposito
--   La clave `_firma` la calcula 94_validar_insumo_esquema_5bq.py, no SQL
--   (sha256 en Python, mismo algoritmo que 87).
--
-- LIMITE DECLARADO
--   Un catalogo NO equivale a un dump: permite comparar objetos y atributos,
--   pero no reconstruir DDL, cuerpos ni ACL completas. Para restaurar el
--   esquema en un laboratorio PG17 hace falta ademas el pg_dump --schema-only.
-- ============================================================================

SELECT json_build_object(

  'meta', json_build_object(
      'version_servidor', split_part(version(), ' on ', 1),
      'base', current_database(),
      'usuario', current_user,
      'capturado_utc',
        to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
      'procedimiento',
        '92_catalogo_json_solo_lectura_5bq.sql (SELECT de catalogo)'
  ),

  'objetos', json_build_object(

      -- tablas y tablas particionadas (misma consulta que 87)
      'tablas', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity)
                 ORDER BY n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p')
             AND n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      -- vistas y su security_invoker
      'vistas', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname,
                     COALESCE((SELECT option_value
                                 FROM pg_options_to_table(c.reloptions)
                                WHERE option_name = 'security_invoker'),
                              '<no definido>'))
                 ORDER BY n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'v'
             AND n.nspname = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      -- columnas de aislamiento logico
      'columnas_empresa_id', COALESCE((
          SELECT json_agg(json_build_array(table_schema, table_name)
                 ORDER BY table_schema, table_name)
            FROM information_schema.columns
           WHERE column_name = 'empresa_id'
             AND table_schema = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      -- rutinas: firma completa con argumentos de identidad
      'rutinas', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, p.proname,
                     CASE p.prokind
                       WHEN 'f' THEN 'funcion'
                       WHEN 'p' THEN 'procedimiento'
                       ELSE p.prokind::text END,
                     p.prosecdef,
                     pg_get_userbyid(p.proowner),
                     COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>'),
                     pg_get_function_identity_arguments(p.oid))
                 ORDER BY n.nspname, p.proname,
                          pg_get_function_identity_arguments(p.oid))
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      -- triggers de usuario (los internos de FK se excluyen)
      'triggers', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, t.tgname, p.proname,
                     p.prosecdef, t.tgenabled)
                 ORDER BY n.nspname, c.relname, t.tgname)
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE NOT t.tgisinternal
             AND n.nspname = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      -- politicas RLS
      'politicas', COALESCE((
          SELECT json_agg(json_build_array(
                     schemaname, tablename, policyname, cmd,
                     array_to_string(roles, ','),
                     COALESCE(qual, '<ninguno>'),
                     COALESCE(with_check, '<ninguno>'))
                 ORDER BY schemaname, tablename, policyname)
            FROM pg_policies
           WHERE schemaname = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      -- propietarios de tablas, vistas y materializadas
      'propietarios', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, pg_get_userbyid(c.relowner))
                 ORDER BY n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p', 'v', 'm')
             AND n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      -- roles del cluster (incluye los gestionados por Supabase: se clasifican
      -- despues como plataforma, no como diferencia de aplicacion)
      'roles', COALESCE((
          SELECT json_agg(json_build_array(
                     rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit)
                 ORDER BY rolname)
            FROM pg_roles
           WHERE rolname !~ '^pg_'
      ), '[]'::json),

      -- extensiones instaladas
      'extensiones', COALESCE((
          SELECT json_agg(json_build_array(e.extname, e.extversion, n.nspname)
                 ORDER BY e.extname)
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
      ), '[]'::json),

      -- privilegios EXECUTE de los principales del SaaS sobre rutinas saas.*
      'privilegios_rutinas', COALESCE((
          SELECT json_agg(json_build_array(
                     r.rolname, n.nspname || '.' || p.proname,
                     has_function_privilege(r.oid, p.oid, 'EXECUTE'))
                 ORDER BY r.rolname, n.nspname, p.proname)
            FROM pg_roles r
            CROSS JOIN pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname IN ('saas', 'saas_admin')
             AND r.rolname = ANY (ARRAY[
                   'fs_saas_runtime','saas_admin_runtime','saas_admin_owner',
                   'saas_personal_owner','saas_rls_owner','saas_actor_sql_owner'
                 ]::text[])
      ), '[]'::json)
  ),

  -- conteos de control externo (41/27/24/1 en el origen verificado)
  'conteos', json_build_object(
      'tablas_public', (SELECT count(*)
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE c.relkind = 'r' AND n.nspname = 'public'),
      'triggers_public', (SELECT count(*)
                            FROM pg_trigger t
                            JOIN pg_class c ON c.oid = t.tgrelid
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                           WHERE NOT t.tgisinternal AND n.nspname = 'public'),
      'rutinas_public', (SELECT count(*)
                           FROM pg_proc p
                           JOIN pg_namespace n ON n.oid = p.pronamespace
                          WHERE n.nspname = 'public'),
      'vistas_public', (SELECT count(*)
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE c.relkind = 'v' AND n.nspname = 'public')
  ),

  -- auditoria adicional: 87 la ignora; sirve para planificar el DDL y para
  -- detectar diferencias que 87 no compara (columnas, restricciones, indices)
  'extra', json_build_object(

      'columnas', COALESCE((
          SELECT json_agg(json_build_array(
                     table_schema, table_name, column_name, data_type,
                     is_nullable, column_default, ordinal_position)
                 ORDER BY table_schema, table_name, ordinal_position)
            FROM information_schema.columns
           WHERE table_schema = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      'restricciones', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, con.conname, con.contype::text,
                     pg_get_constraintdef(con.oid))
                 ORDER BY n.nspname, c.relname, con.conname)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      'indices', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, ic.relname,
                     pg_get_indexdef(i.indexrelid))
                 ORDER BY n.nspname, c.relname, ic.relname)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      -- huella del cuerpo: md5, nunca el texto
      'rutinas_hash', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, p.proname,
                     pg_get_function_identity_arguments(p.oid),
                     md5(COALESCE(p.prosrc, '')),
                     l.lanname, p.prosecdef,
                     COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>'))
                 ORDER BY n.nspname, p.proname,
                          pg_get_function_identity_arguments(p.oid))
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            LEFT JOIN pg_language l ON l.oid = p.prolang
           WHERE n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      'vistas_hash', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, md5(pg_get_viewdef(c.oid, true)))
                 ORDER BY n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'v'
             AND n.nspname = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      'triggers_definicion', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname, t.tgname,
                     pg_get_triggerdef(t.oid, true))
                 ORDER BY n.nspname, c.relname, t.tgname)
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE NOT t.tgisinternal
             AND n.nspname = ANY (ARRAY['public','saas']::text[])
      ), '[]'::json),

      -- ACL explicitas: '<sin ACL explicita>' significa "sin entrada propia",
      -- no "sin privilegios" (el propietario conserva los suyos)
      'acl_tablas', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, c.relname,
                     COALESCE(array_to_string(c.relacl, ','), '<sin ACL explicita>'))
                 ORDER BY n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p', 'v', 'm')
             AND n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json),

      'acl_rutinas', COALESCE((
          SELECT json_agg(json_build_array(
                     n.nspname, p.proname,
                     pg_get_function_identity_arguments(p.oid),
                     COALESCE(array_to_string(p.proacl, ','), '<sin ACL explicita>'))
                 ORDER BY n.nspname, p.proname,
                          pg_get_function_identity_arguments(p.oid))
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = ANY (ARRAY['public','saas','saas_admin']::text[])
      ), '[]'::json)
  )
)::text AS catalogo;
