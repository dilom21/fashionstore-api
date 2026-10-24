-- B3/P5F: RECUPERAR DEFINICIONES COMPLETAS DE SP, EXCLUSIVAMENTE LECTURA.
-- Nunca ejecutar el SQL que devuelve pg_get_functiondef; es TEXTO.
-- Los cuerpos pueden contener logica sensible: revisar antes de compartirlos.
\pset pager off
\pset format unaligned
\pset tuples_only on

BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='30s';

DO $revision$
DECLARE v_procs BIGINT; v_fks BIGINT;
BEGIN
  IF current_database() <> 'fashionstore_restore_20261023_193554_594'
     OR host(inet_server_addr()) <> '127.0.0.1' THEN
     RAISE EXCEPTION 'Conexion no autorizada: % / %',
       current_database(), host(inet_server_addr());
  END IF;
  IF (SELECT count(*) FROM public.empresa) <> 5
     OR (SELECT count(*) FROM public.reserva) <> 11
     OR (SELECT count(*) FROM public.inventario) <> 274
     OR (SELECT count(*) FROM public.carrito) <> 48
     OR (SELECT count(*) FROM public.pago) <> 30 THEN
     RAISE EXCEPTION 'Los datos locales no coinciden con auditoria 5E';
  END IF;
  SELECT count(*) INTO v_fks FROM pg_constraint c
    JOIN pg_class t ON t.oid=c.conrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname='public' AND c.contype='f'
      AND c.convalidated AND c.conname LIKE 'fk_tenant_%';
  IF v_fks <> 48 THEN
     RAISE EXCEPTION 'Integridad tenant alterada: %/48', v_fks;
  END IF;
  SELECT count(*) INTO v_procs FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind='p'
      AND p.proname IN (
        'sp_crear_reserva_desde_carrito',
        'sp_cancelar_reserva',
        'sp_finalizar_reserva_sin_compra'
      );
  IF v_procs <> 3 THEN
     RAISE EXCEPTION 'Cantidad incorrecta de procedimientos: %', v_procs;
  END IF;
  RAISE NOTICE '5F: controles previos OK; 3 SP, 5 empresas, 48 FK. SOLO LECTURA.';
END
$revision$;

SELECT '=== METADATOS / PRIVILEGIOS ===';
SELECT format(
  'SP: %s | owner=%s | security_definer=%s | search_path=%s | public_execute=%s',
  p.oid::regprocedure::text,
  pg_get_userbyid(p.proowner),
  p.prosecdef::text,
  coalesce(array_to_string(p.proconfig, ','), '<predeterminado>'),
  EXISTS (
    SELECT 1 FROM aclexplode(
      coalesce(p.proacl, acldefault('f', p.proowner))
    ) priv
    WHERE priv.grantee=0 AND priv.privilege_type='EXECUTE'
  )::text
)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.prokind='p'
  AND p.proname IN (
    'sp_crear_reserva_desde_carrito',
    'sp_cancelar_reserva',
    'sp_finalizar_reserva_sin_compra'
  ) ORDER BY p.proname;

SELECT '=== DEFINICIONES COMPLETAS; SON TEXTO, NO SE EJECUTAN ===';
SELECT format(
  E'========= INICIO %s =========\n%s\n========= FIN %s =========',
  p.oid::regprocedure::text,
  pg_get_functiondef(p.oid),
  p.oid::regprocedure::text
)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.prokind='p'
  AND p.proname IN (
    'sp_crear_reserva_desde_carrito',
    'sp_cancelar_reserva',
    'sp_finalizar_reserva_sin_compra'
  ) ORDER BY p.proname;

ROLLBACK;
