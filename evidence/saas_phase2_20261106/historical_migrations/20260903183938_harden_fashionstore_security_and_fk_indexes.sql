-- Recovered read-only from supabase_migrations.schema_migrations
-- Project ref: zyitwlxfkphthlvlmuej
-- Version: 20260903183938; name: harden_fashionstore_security_and_fk_indexes; stored statements: 1
-- Evidence only: DO NOT EXECUTE

-- ============================================================
-- FASHIONSTORE - HARDENING POST-VERIFICACION
-- ============================================================

-- 1) La vista debe respetar los permisos/RLS del usuario invocador.
ALTER VIEW public.vw_inventario_stock SET (security_invoker = true);

-- 2) Fijar search_path en procedimientos para evitar resolución mutable.
ALTER PROCEDURE public.sp_ajustar_inventario(bigint, character varying, integer, bigint, text)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_cancelar_reserva(bigint, bigint, text)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_confirmar_venta(bigint, bigint)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_crear_reserva(bigint, bigint, timestamp with time zone, text, jsonb, bigint, bigint)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_recalcular_total_venta(bigint)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_recibir_orden_compra(bigint, bigint)
  SET search_path = public, pg_temp;
ALTER PROCEDURE public.sp_registrar_devolucion(bigint, bigint)
  SET search_path = public, pg_temp;

-- 3) Fijar search_path en funciones usadas por triggers.
ALTER FUNCTION public.fn_normalizar_correo_usuario()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_normalizar_sku()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_controlar_actualizacion_inventario()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_validar_detalle_reserva_sucursal()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_validar_detalle_venta_sucursal()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_proteger_detalle_venta()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_recalcular_total_venta_trigger()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_proteger_eliminacion_venta()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_proteger_detalle_orden_compra()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_validar_producto_proveedor_orden()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_validar_detalle_devolucion()
  SET search_path = public, pg_temp;
ALTER FUNCTION public.fn_registrar_bitacora()
  SET search_path = public, pg_temp;

-- 4) Índices de FK que faltaban según el asesor de rendimiento.
CREATE INDEX IF NOT EXISTS idx_carrito_sucursal
  ON public.carrito (sucursal_id);
CREATE INDEX IF NOT EXISTS idx_detalle_orden_compra_temporada
  ON public.detalle_orden_compra (temporada_id);
CREATE INDEX IF NOT EXISTS idx_orden_compra_empleado
  ON public.orden_compra (empleado_id);
CREATE INDEX IF NOT EXISTS idx_recurso_producto_color
  ON public.recurso_producto (color_id);
CREATE INDEX IF NOT EXISTS idx_variante_producto_color
  ON public.variante_producto (color_id);
CREATE INDEX IF NOT EXISTS idx_variante_producto_talla
  ON public.variante_producto (talla_id);

-- 5) Habilitar RLS en todas las tablas expuestas en public.
-- Sin políticas, anon/authenticated de PostgREST quedan bloqueados.
-- El backend FastAPI se conectará directamente a PostgreSQL.
ALTER TABLE public.rol ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.modulo ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.funcion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.accion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rol_funcion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usuario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.token_recuperacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bitacora ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ciudad ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sucursal ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.empleado ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cliente ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categoria ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.talla ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.color ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.variante_producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recurso_producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.temporada ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.coleccion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.producto_coleccion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.promocion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.promocion_producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.proveedor ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.proveedor_producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.movimiento_inventario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orden_compra ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_orden_compra ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.carrito ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_carrito ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reserva ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_reserva ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.venta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_venta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pago ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.devolucion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_devolucion ENABLE ROW LEVEL SECURITY;

