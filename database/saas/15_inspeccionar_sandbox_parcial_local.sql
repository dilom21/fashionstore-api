-- B3/P5I: inspeccion de la base PARCIAL tras pg_restore (solo lectura).
-- NO instala funciones, NO crea tablas, NO elimina ni recupera bases.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $guard$
BEGIN
    IF current_database() <> 'fashionstore_saas_reservas_sandbox_5h'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'No es la base sandbox parcial LOCAL esperada';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_namespace WHERE nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'Sandbox sin schema public: abortar';
    END IF;
END
$guard$;

-- Objetos del esquema public: tras error en el primer CREATE SCHEMA,
-- normalmente no existe ningun objeto; NO asumirlo sin inspeccion.
WITH objetos AS (
    SELECT
        (SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public') AS relaciones,
        (SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public') AS rutinas,
        (SELECT count(*) FROM pg_type t
            JOIN pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname='public') AS tipos,
        (SELECT count(*) FROM pg_extension e
            JOIN pg_namespace n ON n.oid=e.extnamespace
            WHERE n.nspname='public') AS extensiones,
        (SELECT count(*) FROM pg_namespace WHERE nspname='public') AS esquema_public
)
SELECT
    relaciones, rutinas, tipos, extensiones, esquema_public,
    CASE
      WHEN relaciones=0 AND rutinas=0 AND tipos=0
           AND extensiones=0 AND esquema_public=1
      THEN 'SANDBOX_PARCIAL_VACIO_APTO_PARA_RECUPERACION_CONTROLADA'
      ELSE 'NO_RECUPERAR: EXISTEN OBJETOS O EXTENSIONES EN PUBLIC'
    END AS resultado
FROM objetos;

ROLLBACK;
