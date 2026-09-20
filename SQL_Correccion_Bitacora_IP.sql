-- ============================================================================
-- Corrección Bitácora: completar IP del cliente en nuevos eventos
-- ============================================================================
-- Objetivo
--   Que TODA fila nueva de public.bitacora registre la IP visible para el
--   backend cuando el INSERT no la traiga.
--
--   La IP se propaga desde FastAPI con:
--       SELECT set_config('app.ip', :ip, true)
--   (ver app/core/database.py, listener after_begin). Este trigger cubre:
--     - auditoría por triggers fn_registrar_bitacora();
--     - inserts manuales con ORM Bitacora(...);
--     - cualquier escritura futura que use el mismo contexto.
--
-- Reglas
--   - Si NEW.ip ya tiene valor, se respeta.
--   - Si NEW.ip es NULL o vacío, se toma current_setting('app.ip', TRUE).
--   - Vacío -> NULL.
--
-- IMPORTANTE
--   - No se modifica el esquema.
--   - No se backfillean las 664 filas históricas: no hay información fiable
--     para reconstruir sus IP. Solo aplica a INSERT nuevos.
--   - Este DDL NO se ejecuta automáticamente al iniciar FastAPI.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_completar_ip_bitacora()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $$
DECLARE
    v_ip TEXT;
BEGIN
    IF NEW.ip IS NULL OR btrim(NEW.ip) = '' THEN
        v_ip := NULLIF(current_setting('app.ip', TRUE), '');
        IF v_ip IS NOT NULL THEN
            NEW.ip := v_ip;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bitacora_completar_ip ON public.bitacora;

CREATE TRIGGER trg_bitacora_completar_ip
BEFORE INSERT ON public.bitacora
FOR EACH ROW
EXECUTE FUNCTION public.fn_completar_ip_bitacora();
