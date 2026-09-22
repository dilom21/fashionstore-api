-- ============================================================================
-- CU26 - Vestidor Virtual AR: seed de assets y configuraciones (PLANTILLA)
-- ============================================================================
--
--  *** NO EJECUTAR SIN REVISION ***
--
-- Este script NO crea tablas (no hay DDL). Las tablas
-- configuracion_vestidor_ar, sesion_vestidor_ar y prueba_vestidor_ar YA EXISTEN
-- en Supabase y actualmente estan vacias.
--
-- OBJETIVO
--   Registrar 2-3 assets PNG transparentes de prendas superiores y su
--   configuracion AR (zona_cuerpo = TORSO, tipo_asset = PNG_2D), que es lo que
--   consume el motor AR de Harold a traves del backend CU26.
--
-- ANTES DE EJECUTAR
--   1) Subir cada PNG transparente (fondo alfa) a Supabase Storage y copiar su
--      URL publica.
--   2) Reemplazar los placeholders 'PENDIENTE_URL_*' por esas URLs reales.
--      No inventar URLs: si el PNG no existe, dejar el placeholder y no correr.
--   3) Verificar que los nombres de producto/color del bloque "objetivo"
--      coincidan EXACTAMENTE con el catalogo (el match es case-insensitive).
--   4) Ajustar factores/offsets segun el resultado visual en el telefono.
--
-- POR QUE NO CONTAMINA EL CATALOGO
--   - El recurso AR se registra con tipo = 'AR_PNG' y es_principal = FALSE.
--   - El listado publico (CU09) solo toma recursos con es_principal = TRUE y
--     color_id NULL como imagen principal.
--   - Flutter solo muestra recursos cuyo tipo contenga
--     'imag'/'image'/'foto'/'photo'/'galer' (o este vacio). 'AR_PNG' no
--     coincide, por lo que nunca aparece como foto del catalogo.
--
-- IDEMPOTENTE
--   Usa WHERE NOT EXISTS: puede ejecutarse dos veces sin duplicar filas
--   (respeta UNIQUE(recurso_producto_id) de configuracion_vestidor_ar).
--
-- SEGURIDAD
--   El script termina en ROLLBACK. Tras revisar el resultado, reemplazar
--   ROLLBACK por COMMIT para confirmar.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1) Recursos AR (PNG transparente) por producto/color
-- ----------------------------------------------------------------------------
WITH objetivo(nombre_producto, nombre_color, url_asset) AS (
    VALUES
        ('Polo Premium Pique',       'Negro', 'PENDIENTE_URL_POLO_NEGRO_TRYON'),
        ('Camiseta Essential Cotton','Negro', 'PENDIENTE_URL_CAMISETA_NEGRA_TRYON'),
        ('Camisa Oxford Classic',    NULL,    'PENDIENTE_URL_CAMISA_OXFORD_TRYON')
)
INSERT INTO recurso_producto (
    producto_id, color_id, tipo, url, es_principal, estado
)
SELECT
    p.id,
    c.id,
    'AR_PNG',
    o.url_asset,
    FALSE,
    TRUE
FROM objetivo o
JOIN producto p
    ON upper(p.nombre) = upper(o.nombre_producto)
LEFT JOIN color c
    ON upper(c.nombre) = upper(o.nombre_color)
WHERE NOT EXISTS (
    SELECT 1
    FROM recurso_producto r
    WHERE r.producto_id = p.id
      AND r.tipo = 'AR_PNG'
      AND r.color_id IS NOT DISTINCT FROM c.id
);

-- ----------------------------------------------------------------------------
-- 2) Configuracion AR (TORSO / PNG_2D) para cada recurso AR recien creado
--    Ajustar factor_ancho/factor_alto/offset/orden segun la prenda.
-- ----------------------------------------------------------------------------
INSERT INTO configuracion_vestidor_ar (
    recurso_producto_id,
    zona_cuerpo,
    tipo_asset,
    factor_ancho,
    factor_alto,
    offset_x,
    offset_y,
    rotacion_offset,
    orden_capa,
    opacidad,
    estado
)
SELECT
    r.id,
    'TORSO',
    'PNG_2D',
    CASE
        WHEN upper(p.nombre) = upper('Camiseta Essential Cotton') THEN 1.40
        WHEN upper(p.nombre) = upper('Camisa Oxford Classic')     THEN 1.50
        ELSE 1.45
    END,
    CASE
        WHEN upper(p.nombre) = upper('Camiseta Essential Cotton') THEN 1.55
        WHEN upper(p.nombre) = upper('Camisa Oxford Classic')     THEN 1.65
        ELSE 1.60
    END,
    0.00,
    0.05,
    0.00,
    1,
    1.00,
    TRUE
FROM recurso_producto r
JOIN producto p
    ON p.id = r.producto_id
WHERE r.tipo = 'AR_PNG'
  AND upper(p.nombre) IN (
      upper('Polo Premium Pique'),
      upper('Camiseta Essential Cotton'),
      upper('Camisa Oxford Classic')
  )
  AND NOT EXISTS (
      SELECT 1
      FROM configuracion_vestidor_ar cv
      WHERE cv.recurso_producto_id = r.id
  );

-- ----------------------------------------------------------------------------
-- 3) Verificacion (revisar antes de confirmar)
-- ----------------------------------------------------------------------------
SELECT
    cv.id AS configuracion_id,
    p.nombre AS producto,
    co.nombre AS color,
    cv.zona_cuerpo,
    cv.tipo_asset,
    cv.factor_ancho,
    cv.factor_alto,
    r.url AS asset_url
FROM configuracion_vestidor_ar cv
JOIN recurso_producto r ON r.id = cv.recurso_producto_id
JOIN producto p ON p.id = r.producto_id
LEFT JOIN color co ON co.id = r.color_id
ORDER BY p.nombre;

-- Tras revisar el SELECT anterior:
--   * Si todo es correcto -> reemplazar ROLLBACK por COMMIT.
--   * Si algo no cuadra   -> dejar ROLLBACK (no se persiste nada).
ROLLBACK;
