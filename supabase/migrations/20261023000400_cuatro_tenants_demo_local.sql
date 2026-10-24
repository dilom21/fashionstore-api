-- FashionStore / Segundo parcial / Bloque 2 / Paso 6
-- CARGA DE DEMOSTRACION, EXCLUSIVAMENTE EN LA COPIA LOCAL.
-- NO EJECUTAR CON SUPABASE CLI, DB PUSH, NI CONTRA PRODUCCION.
-- Base fija: fashionstore_restore_20261023_193554_594.
-- Fuente: examen, apartados 9.1-9.6. Las empresas y datos son ficticios.
--
-- Preserva los datos de Vanter Men. Las cuentas admin demo quedan DESACTIVADAS,
-- con hashes bcrypt de secretos aleatorios no conservados, sin acceso.
-- Se activaran y provisionaran contrasenas mediante proceso seguro posterior.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
SET LOCAL TIME ZONE 'America/La_Paz';

DO $preflight_demo$
DECLARE
  v_n BIGINT;
BEGIN
  IF current_database() <> 'fashionstore_restore_20261023_193554_594' THEN
    RAISE EXCEPTION 'SOLO base local de restauracion, recibida: %', current_database();
  END IF;
  SELECT count(*) INTO v_n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r';
  IF v_n <> 46 THEN RAISE EXCEPTION 'Se esperaban 46 tablas, existen %', v_n; END IF;
  IF (SELECT count(*) FROM public.empresa) <> 1
    OR (SELECT count(*) FROM public.empresa WHERE slug='vanter-men') <> 1
    OR (SELECT count(*) FROM public.plan_saas WHERE codigo IN ('BASICO','PROFESIONAL')) <> 2
    OR (SELECT count(*) FROM public.usuario_empresa) <> 9
    OR (SELECT count(*) FROM public.bitacora) <> 806
    OR (SELECT count(*) FROM public.usuario) <> 9
    OR (SELECT count(*) FROM public.sucursal) <> 4
    OR (SELECT count(*) FROM public.cliente) <> 5
    OR (SELECT count(*) FROM public.empleado) <> 4
    OR (SELECT count(*) FROM public.producto) <> 14
    OR (SELECT count(*) FROM public.variante_producto) <> 120
    OR (SELECT count(*) FROM public.inventario) <> 265
    OR (SELECT count(*) FROM public.venta) <> 40
    OR (SELECT count(*) FROM public.pago) <> 30 THEN
    RAISE EXCEPTION 'Los datos de Vanter Men ya no coinciden con Paso 5: no aplicar';
  END IF;
  IF (SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk_tenant_%') <> 48
    OR (SELECT count(*) FROM pg_constraint WHERE conname LIKE 'uq_tenant_%') <> 28 THEN
    RAISE EXCEPTION 'No se detecta la integridad estructural completa del Paso 5';
  END IF;
  IF (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND NOT t.tgisinternal) <> 27 THEN
    RAISE EXCEPTION 'Los triggers existentes no coinciden con el Paso 5';
  END IF;
  IF EXISTS (SELECT 1 FROM public.empresa WHERE slug IN
        ('urbanwear','elegancia-sur','denim-house','sportline'))
     OR EXISTS (SELECT 1 FROM public.usuario WHERE correo LIKE 'admin.%@demo.invalid') THEN
    RAISE EXCEPTION 'Los tenants o admins demo ya existen; no duplicar registros';
  END IF;
  IF (SELECT count(*) FROM public.rol WHERE nombre='ADMINISTRADOR' AND estado) <> 1
    OR NOT EXISTS (SELECT 1 FROM public.ciudad WHERE nombre='Santa Cruz de la Sierra')
    OR NOT EXISTS (SELECT 1 FROM public.talla)
    OR NOT EXISTS (SELECT 1 FROM public.color) THEN
    RAISE EXCEPTION 'Faltan catalogos globales de roles/ciudad/talla/color';
  END IF;
END;
$preflight_demo$;

-- Mejora de auditoria: conservar empresa_id del registro afectado.
-- Los eventos de usuario global (sin empresa_id) seguiran siendo de plataforma.
-- El mismo trigger existente se mantiene: no se desactiva ninguno de los 27.
CREATE OR REPLACE FUNCTION public.fn_registrar_bitacora()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
AS $fn_auditoria_empresa$
DECLARE
 v_usuario_text TEXT;
 v_usuario_id BIGINT;
 v_empresa_text TEXT;
 v_empresa_id BIGINT;
 v_registro_id TEXT;
 v_accion TEXT;
 v_fila JSONB;
BEGIN
 v_usuario_text := current_setting('app.usuario_id',TRUE);
 IF v_usuario_text IS NOT NULL AND v_usuario_text ~ '^[0-9]+$' THEN
   v_usuario_id := v_usuario_text::BIGINT;
 ELSE
   v_usuario_id := NULL;
 END IF;
 IF TG_OP='DELETE' THEN v_fila:=to_jsonb(OLD);
 ELSE v_fila:=to_jsonb(NEW); END IF;
 v_empresa_text:=v_fila->>'empresa_id';
 IF v_empresa_text IS NOT NULL AND v_empresa_text ~ '^[0-9]+$' THEN
   v_empresa_id:=v_empresa_text::BIGINT;
 ELSE
   v_empresa_id:=NULL; -- Operaciones sobre usuario/rol global.
 END IF;
 CASE TG_OP
   WHEN 'INSERT' THEN v_accion:='CREAR'; v_registro_id:=NEW.id::TEXT;
   WHEN 'UPDATE' THEN v_accion:='MODIFICAR'; v_registro_id:=NEW.id::TEXT;
   WHEN 'DELETE' THEN v_accion:='ELIMINAR'; v_registro_id:=OLD.id::TEXT;
   ELSE v_accion:=TG_OP;
 END CASE;
 INSERT INTO public.bitacora
   (usuario_id,empresa_id,fecha_hora,accion,entidad_afectada,descripcion)
 VALUES
   (v_usuario_id,v_empresa_id,CURRENT_TIMESTAMP,v_accion,TG_TABLE_NAME,
    format('Operación %s sobre registro ID %s',v_accion,v_registro_id));
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$fn_auditoria_empresa$;

DO $seed_demo$
DECLARE
 r RECORD;
 v_empresa BIGINT;
 v_plan BIGINT;
 v_rol_admin BIGINT;
 v_ciudad BIGINT;
 v_talla BIGINT;
 v_color BIGINT;
 v_usuario BIGINT;
 v_sucursal BIGINT;
 v_categoria BIGINT;
 v_temporada BIGINT;
 v_producto BIGINT;
 v_variante BIGINT;
 v_indice INTEGER;
 v_creadas INTEGER := 0;
BEGIN
 SELECT id INTO STRICT v_rol_admin FROM public.rol WHERE nombre='ADMINISTRADOR' AND estado;
 SELECT id INTO STRICT v_ciudad FROM public.ciudad WHERE nombre='Santa Cruz de la Sierra';
 SELECT id INTO v_talla FROM public.talla WHERE estado=TRUE ORDER BY id LIMIT 1;
 SELECT id INTO v_color FROM public.color WHERE estado=TRUE ORDER BY id LIMIT 1;
 IF v_talla IS NULL OR v_color IS NULL THEN
   RAISE EXCEPTION 'No hay talla o color activo para las variantes demo';
 END IF;

 -- Nombres, emails y productos: datos ficticios sin informacion real.
 -- 1,3,2,3 sucursales respectivamente: todos dentro de su plan.
 -- Contrasenas BCrypt NO conocidas; las identidades permanecen inactivas.
 FOR r IN SELECT * FROM (VALUES
    ('urbanwear','UrbanWear','BASICO',1,'admin.urbanwear@demo.invalid',
     '$2y$12$2XOTg4c3SlrA8QaUSM.NpO3hXkXG.YpBxFWaHdExzwN77/GI4Mh/e',
     'Poleras Urbanas',120.00,'URBAN-DEMO-001','#2563EB'),
    ('elegancia-sur','Elegancia Sur','PROFESIONAL',3,'admin.elegancia-sur@demo.invalid',
     '$2y$12$VTs3EP3pw9o3p1SL3sS8zuESyKJZVHlrshDByq1BlI8fVZC6QwrqC',
     'Camisa Elegante',245.00,'ELEGANCIA-DEMO-001','#7C3AED'),
    ('denim-house','Denim House','BASICO',2,'admin.denim-house@demo.invalid',
     '$2y$12$uvLPXl3f/q6kuv0iurSMWeeuYw.ZyInunVY3fb0GYpGqxMmg8su3y',
     'Jeans Clásico',199.00,'DENIM-DEMO-001','#0F766E'),
    ('sportline','SportLine','PROFESIONAL',3,'admin.sportline@demo.invalid',
     '$2y$12$FM81SlM6aH3mZFHdg2GQr.d.JyqD7a6JwU3aLu.UNgubovjdSnUPu',
     'Camiseta Deportiva',135.00,'SPORT-DEMO-001','#EA580C')
 ) AS d(slug,nombre,plan_codigo,num_sucursales,email_admin,hash_admin,
         producto_nombre,precio,sku,color_hex)
 LOOP
   SELECT id INTO STRICT v_plan FROM public.plan_saas
     WHERE codigo=r.plan_codigo AND estado;
   IF r.num_sucursales >
     (SELECT max_sucursales_activas FROM public.plan_saas WHERE id=v_plan) THEN
     RAISE EXCEPTION 'Plan % insuficiente para % sucursales',r.plan_codigo,r.num_sucursales;
   END IF;
   INSERT INTO public.empresa(razon_social,nombre_comercial,slug,estado)
   VALUES(r.nombre,r.nombre,r.slug,TRUE) RETURNING id INTO v_empresa;
   INSERT INTO public.empresa_configuracion(empresa_id,color_primario)
   VALUES(v_empresa,r.color_hex);
   INSERT INTO public.suscripcion
     (empresa_id,plan_saas_id,fecha_inicio,fecha_vencimiento,estado,observacion)
   VALUES(v_empresa,v_plan,CURRENT_DATE,CURRENT_DATE+365,'ACTIVA',
     'Suscripcion ficticia de demostracion; sin facturacion real');

   INSERT INTO public.usuario(rol_id,correo,password_hash,estado)
   VALUES(v_rol_admin,r.email_admin,r.hash_admin,FALSE)
   RETURNING id INTO v_usuario;
   INSERT INTO public.usuario_empresa(usuario_id,empresa_id,rol_id,estado)
   VALUES(v_usuario,v_empresa,v_rol_admin,FALSE);

   -- Una categoria/temporada/producto/variante distintos por tenant.
   -- Reutilizar nombre de categoria/temporada entre empresas prueba el UNIQUE scoped.
   INSERT INTO public.categoria(empresa_id,nombre,descripcion,estado)
   VALUES(v_empresa,'Moda Demo','Catalogo ficticio para pruebas multitenant',TRUE)
   RETURNING id INTO v_categoria;
   INSERT INTO public.temporada
     (empresa_id,nombre,fecha_inicio,fecha_fin,estado)
   VALUES(v_empresa,'Temporada Demo',CURRENT_DATE,CURRENT_DATE+365,TRUE)
   RETURNING id INTO v_temporada;
   INSERT INTO public.producto(empresa_id,categoria_id,nombre,descripcion,precio,estado)
   VALUES(v_empresa,v_categoria,r.producto_nombre,
     'Producto ficticio exclusivo de '||r.nombre,r.precio,TRUE)
   RETURNING id INTO v_producto;
   INSERT INTO public.variante_producto
     (empresa_id,producto_id,talla_id,color_id,sku,estado)
   VALUES(v_empresa,v_producto,v_talla,v_color,r.sku,TRUE)
   RETURNING id INTO v_variante;

   INSERT INTO public.cliente(empresa_id,nombre,apellido,estado)
   VALUES(v_empresa,'Cliente','Demo '||r.nombre,TRUE);

   FOR v_indice IN 1..r.num_sucursales LOOP
     INSERT INTO public.sucursal(empresa_id,ciudad_id,nombre,direccion,estado)
     VALUES(v_empresa,v_ciudad,r.nombre||' - Sucursal '||v_indice,
       'Direccion de demostracion (no corresponde a local real)',TRUE)
     RETURNING id INTO v_sucursal;
     INSERT INTO public.inventario
       (empresa_id,sucursal_id,variante_producto_id,temporada_id,
        stock_actual,stock_reservado)
     VALUES(v_empresa,v_sucursal,v_variante,v_temporada,10+v_indice,0);
     IF v_indice=1 THEN
       INSERT INTO public.empleado
         (empresa_id,usuario_id,sucursal_id,nombres,apellidos,ci,
          fecha_contratacion,estado)
       VALUES(v_empresa,v_usuario,v_sucursal,'Administrador',
         'Demo '||r.nombre,'DEMO-'||upper(replace(r.slug,'-',''))||'-ADM',
         CURRENT_DATE,FALSE);
     END IF;
   END LOOP;
   v_creadas:=v_creadas+1;
 END LOOP;
 IF v_creadas<>4 THEN RAISE EXCEPTION 'Se esperaban cuatro empresas demo'; END IF;

 -- Reglas operativas y aislamiento referencial (defensas del paso 5).
 IF (SELECT count(*) FROM public.empresa)<>5 OR
    (SELECT count(*) FROM public.usuario_empresa)<>13 OR
    (SELECT count(*) FROM public.sucursal)<>13 OR
    (SELECT count(*) FROM public.producto)<>18 OR
    (SELECT count(*) FROM public.variante_producto)<>124 OR
    (SELECT count(*) FROM public.inventario)<>274 OR
    (SELECT count(*) FROM public.cliente)<>9 OR
    (SELECT count(*) FROM public.empleado)<>8 OR
    (SELECT count(*) FROM public.usuario)<>13 OR
    (SELECT count(*) FROM public.venta)<>40 OR
    (SELECT count(*) FROM public.pago)<>30 THEN
   RAISE EXCEPTION 'Fallaron los conteos esperados de recursos por tenant';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.empresa e JOIN public.suscripcion s ON s.empresa_id=e.id
     JOIN public.plan_saas p ON p.id=s.plan_saas_id
     LEFT JOIN LATERAL (
       SELECT count(*) AS activas FROM public.sucursal su
       WHERE su.empresa_id=e.id AND su.estado=TRUE
     ) x ON TRUE
   WHERE s.estado='ACTIVA' AND x.activas > p.max_sucursales_activas
 ) THEN
   RAISE EXCEPTION 'Alguna empresa supera el limite de sucursales del plan';
 END IF;
 -- Auditoria: 38 eventos nuevos exactos:
 --   4 usuario (globales), 4 cliente, 4 producto, 4 variante_producto,
 --   4 empleado, 9 sucursal y 9 inventario (34 empresariales).
 -- El primer calculo omitio trg_bitacora_cliente; este conteo lo incluye.
 IF (SELECT count(*) FROM public.bitacora) <> 844 THEN
   RAISE EXCEPTION
     'Auditoria inesperada: total real=%, esperado=844 (806 historicos + 38 nuevos)',
     (SELECT count(*) FROM public.bitacora);
 END IF;
 IF (SELECT count(*) FROM public.bitacora WHERE empresa_id IS NULL) <> 4
    OR (SELECT count(*) FROM public.bitacora
         WHERE empresa_id IS NULL AND entidad_afectada='usuario'
           AND accion='CREAR') <> 4 THEN
   RAISE EXCEPTION 'Auditoria global incorrecta: deben existir 4 altas de usuario';
 END IF;
 IF (SELECT count(*) FROM public.bitacora b
     JOIN public.empresa e ON e.id=b.empresa_id
     WHERE e.slug IN ('urbanwear','elegancia-sur','denim-house','sportline')) <> 34 THEN
   RAISE EXCEPTION 'Auditoria tenant incorrecta: 34 eventos deben tener empresa demo';
 END IF;
 IF EXISTS (
   SELECT 1
   FROM (VALUES
     ('cliente',4),('producto',4),('variante_producto',4),('empleado',4),
     ('sucursal',9),('inventario',9)
   ) AS esperado(entidad,cantidad)
   LEFT JOIN LATERAL (
     SELECT count(*) AS eventos
     FROM public.bitacora b
     JOIN public.empresa e ON e.id=b.empresa_id
     WHERE e.slug IN ('urbanwear','elegancia-sur','denim-house','sportline')
       AND b.entidad_afectada=esperado.entidad
       AND b.accion='CREAR'
   ) AS real ON TRUE
   WHERE real.eventos<>esperado.cantidad
 ) THEN
   RAISE EXCEPTION 'Faltan o sobran eventos de auditoria por entidad demo';
 END IF;
 RAISE NOTICE 'Creadas cuatro empresas, nueve sucursales, cuatro admin inactivos y recursos separados.';
END;
$seed_demo$;
COMMIT;

-- Credenciales demo INACTIVAS por seguridad; no proporcionar claves ficticias
-- utilizables ni cambiar SAAS_AUTH_ENABLED. Probar autenticacion en Paso 7.
