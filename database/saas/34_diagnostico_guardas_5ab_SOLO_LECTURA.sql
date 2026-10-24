-- FashionStore B3 / 5AB - DIAGNOSTICO READ ONLY tras primer bloqueo.
-- Solo metadatos booleanos; NO imprimir system_identifier ni data_directory.
-- Ejecutar exclusivamente -h 127.0.0.1 -p 55432 -d fashionstore_rls_lab_5u.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '3s';

SELECT
    current_database() = 'fashionstore_rls_lab_5u' AS base_correcta,
    inet_server_addr()::text IS NOT DISTINCT FROM '127.0.0.1' AS ip_loopback_correcta,
    inet_server_port() = 55432 AS puerto_correcto,
    session_user = 'postgres' AS session_postgres,
    current_user = 'postgres' AS current_postgres,
    current_setting('server_version_num')::int / 10000 = 18 AS version_pg18,
    lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/'))
      = lower('C:/SI2_Parcial1/fashionstore-rls-lab-5v-data') AS pgdata_correcto,
    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind='r') AS tablas_publicas,
    (SELECT count(*) FROM pg_constraint co
        JOIN pg_class c ON c.oid=co.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND co.contype='f'
       AND co.convalidated AND co.conname LIKE 'fk_tenant_%') AS fk_tenant_validas,
    (SELECT count(*) FROM pg_roles WHERE rolname IN
      ('fashionstore_5ab_a','fashionstore_5ab_b')) AS roles_5ab_creados,
    (SELECT count(*) FROM pg_policies WHERE schemaname='public') AS policies_public,
    (SELECT count(*) FROM public.empresa WHERE slug IN
      ('empresa-lab-a','empresa-lab-b')) AS empresas_ficticias_5ab,
    (SELECT count(DISTINCT c.oid)
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     JOIN pg_attribute a ON a.attrelid=c.oid
     WHERE n.nspname='public' AND c.relkind='r'
       AND a.attname='empresa_id' AND a.attnum>0 AND NOT a.attisdropped) AS tablas_con_empresa_id;

ROLLBACK;
-- Este diagnostico nunca autoriza repetir 33_aplicar_rls_principales_lab_5ab.ps1.
