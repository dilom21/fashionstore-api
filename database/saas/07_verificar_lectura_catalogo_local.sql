-- BLOQUE 3 / PASO 3 - SOLO LECTURA en copia PostgreSQL local.
-- No usar Supabase, DB PUSH ni bases distintas de la restauracion.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

DO $catalogo_test$
DECLARE
    r RECORD;
    v_num BIGINT;
    v_ajenos BIGINT;
BEGIN
    IF current_database() <> 'fashionstore_restore_20261023_193554_594'
       OR host(inet_server_addr()) <> '127.0.0.1' THEN
        RAISE EXCEPTION 'Destino no autorizado: % / %',
          current_database(), host(inet_server_addr());
    END IF;

    IF (SELECT count(*) FROM public.empresa) <> 5 OR
       (SELECT count(*) FROM public.producto) <> 18 OR
       (SELECT count(*) FROM public.variante_producto) <> 124 OR
       (SELECT count(*) FROM public.venta) <> 40 OR
       (SELECT count(*) FROM public.pago) <> 30 OR
       (SELECT count(*) FROM public.bitacora) <> 844 THEN
        RAISE EXCEPTION 'Base local no coincide con los conteos del Bloque 2';
    END IF;

    FOR r IN SELECT * FROM (VALUES
         ('vanter-men',14,120),
         ('urbanwear',1,1),
         ('elegancia-sur',1,1),
         ('denim-house',1,1),
         ('sportline',1,1)
       ) AS esperado(slug, productos, minimo_variantes)
    LOOP
        SELECT count(*) INTO v_num
        FROM public.producto p
        JOIN public.empresa e ON e.id=p.empresa_id
        WHERE e.slug=r.slug;
        IF v_num<>r.productos THEN
            RAISE EXCEPTION 'Empresa %: productos esperados %, reales %',
                r.slug,r.productos,v_num;
        END IF;
        SELECT count(*) INTO v_num
        FROM public.variante_producto v
        JOIN public.empresa e ON e.id=v.empresa_id
        WHERE e.slug=r.slug;
        IF v_num<r.minimo_variantes THEN
            RAISE EXCEPTION 'Empresa %: menos variantes que las esperadas',r.slug;
        END IF;
        RAISE NOTICE 'LECTURA OK: % tiene % producto(s)',r.slug,r.productos;
    END LOOP;

    -- Intentar cruzar por ID no debe devolver producto si el tenant no coincide.
    SELECT count(*) INTO v_ajenos
    FROM public.producto p
    WHERE p.empresa_id = (SELECT id FROM public.empresa WHERE slug='urbanwear')
      AND p.id IN (SELECT id FROM public.producto
                   WHERE empresa_id=(SELECT id FROM public.empresa WHERE slug='vanter-men'));
    IF v_ajenos<>0 THEN
        RAISE EXCEPTION 'Filtro por empresa devuelve producto de otro tenant';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.producto p
        JOIN public.categoria c ON c.id=p.categoria_id
        WHERE p.empresa_id<>c.empresa_id
    ) OR EXISTS (
        SELECT 1 FROM public.variante_producto v
        JOIN public.producto p ON p.id=v.producto_id
        WHERE v.empresa_id<>p.empresa_id
    ) THEN
        RAISE EXCEPTION 'Se detecto relacion cruzada producto/categoria/variante';
    END IF;

    IF (SELECT count(*) FROM public.categoria c
        JOIN public.empresa e ON e.id=c.empresa_id
        WHERE c.nombre='Moda Demo'
          AND e.slug IN ('urbanwear','elegancia-sur','denim-house','sportline'))<>4 THEN
        RAISE EXCEPTION 'No hay cuatro categorias demo independientes';
    END IF;

    RAISE NOTICE 'LECTURA AISLADA OK: 5 tenants; sin modificaciones';
END;
$catalogo_test$;

SELECT e.slug,
    count(DISTINCT p.id) AS productos,
    count(DISTINCT v.id) AS variantes
FROM public.empresa e
LEFT JOIN public.producto p ON p.empresa_id=e.id
LEFT JOIN public.variante_producto v ON v.empresa_id=e.id
GROUP BY e.slug
ORDER BY e.slug;

ROLLBACK;
