-- PRUEBA DE LABORATORIO: siempre revierte sus datos ficticios.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='30s';

DO $test$
DECLARE
  v_vanter bigint;
  v_otro bigint;
  v_categoria_vanter bigint;
  v_nombre varchar;
  v_id bigint;
  v_constraint text;
BEGIN
  SELECT id INTO STRICT v_vanter FROM public.empresa WHERE slug='vanter-men';
  SELECT id,nombre INTO STRICT v_categoria_vanter,v_nombre
    FROM public.categoria WHERE empresa_id=v_vanter ORDER BY id LIMIT 1;

  INSERT INTO public.empresa(razon_social,nombre_comercial,slug)
  VALUES('TENANT REVERSIBLE','TENANT REVERSIBLE','tenant-reversible')
  RETURNING id INTO v_otro;

  SELECT coalesce(max(id),0)+100000 INTO v_id FROM public.categoria;
  -- El mismo nombre es válido en otra empresa.
  INSERT INTO public.categoria(id,nombre,estado,empresa_id)
  VALUES(v_id,v_nombre,true,v_otro);

  BEGIN
    INSERT INTO public.categoria(id,nombre,estado,empresa_id)
    VALUES(v_id+1,v_nombre,true,v_otro);
    RAISE EXCEPTION 'No se rechazó UNIQUE dentro del mismo tenant';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME;
    IF v_constraint<>'uq_categoria_empresa_nombre' THEN
      RAISE EXCEPTION 'UNIQUE inesperado: %',v_constraint;
    END IF;
  END;

  -- El FK simple aceptaría categoria_id, pero el compuesto debe impedir el cruce.
  BEGIN
    INSERT INTO public.producto(id,categoria_id,nombre,precio,estado,empresa_id)
    VALUES((SELECT coalesce(max(id),0)+100000 FROM public.producto),
           v_categoria_vanter,'PRODUCTO REVERSIBLE',1,true,v_otro);
    RAISE EXCEPTION 'No se rechazó FK cruzada';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME;
    IF v_constraint<>'fk_tenant_fk_producto_categoria' THEN
      RAISE EXCEPTION 'FK inesperada: %',v_constraint;
    END IF;
  END;

  BEGIN
    UPDATE public.categoria SET empresa_id=v_otro WHERE id=v_categoria_vanter;
    RAISE EXCEPTION 'No se rechazó cambio de empresa_id';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;

  RAISE NOTICE 'INTEGRIDAD_OK: unique tenant, FK compuesta e inmutabilidad';
END $test$;

ROLLBACK;

SELECT count(*)=0 AS sin_tenant_ficticio
FROM public.empresa WHERE slug='tenant-reversible';
