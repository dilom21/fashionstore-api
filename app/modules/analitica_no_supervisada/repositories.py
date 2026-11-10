from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta


def obtener_productos_features(
    db: Session,
    *,
    empresa_id: int,
    fecha_desde: date,
    fecha_hasta: date,
    sucursal_id: int | None,
    limite: int,
) -> list:
    """Agrega una fila por producto, con todos los predicados tenant en SQL.

    La subconsulta de ventas se agrupa antes de unir inventario: esto evita que
    las variantes o existencias multipliquen unidades e ingresos.
    """
    ventas_condiciones = [
        Venta.empresa_id == empresa_id,
        DetalleVenta.empresa_id == empresa_id,
        Inventario.empresa_id == empresa_id,
        VarianteProducto.empresa_id == empresa_id,
        Venta.estado == "COMPLETADA",
        Venta.fecha_hora >= datetime.combine(fecha_desde, time.min),
        Venta.fecha_hora < datetime.combine(fecha_hasta + timedelta(days=1), time.min),
    ]
    if sucursal_id is not None:
        ventas_condiciones.append(Venta.sucursal_id == sucursal_id)
    ventas = (
        select(
            VarianteProducto.producto_id.label("producto_id"),
            func.coalesce(func.sum(DetalleVenta.cantidad), 0).label("unidades"),
            func.coalesce(
                func.sum(DetalleVenta.cantidad * DetalleVenta.precio_unitario), 0
            ).label("ingresos"),
            func.count(func.distinct(func.date(Venta.fecha_hora))).label("dias"),
        )
        .select_from(DetalleVenta)
        .join(Venta, Venta.id == DetalleVenta.venta_id)
        .join(Inventario, Inventario.id == DetalleVenta.inventario_id)
        .join(VarianteProducto, VarianteProducto.id == Inventario.variante_producto_id)
        .where(*ventas_condiciones)
        .group_by(VarianteProducto.producto_id)
        .subquery()
    )
    stock_condiciones = [
        Inventario.empresa_id == empresa_id,
        VarianteProducto.empresa_id == empresa_id,
    ]
    if sucursal_id is not None:
        stock_condiciones.append(Inventario.sucursal_id == sucursal_id)
    stock = (
        select(
            VarianteProducto.producto_id.label("producto_id"),
            func.coalesce(
                func.sum(Inventario.stock_actual - Inventario.stock_reservado), 0
            ).label("stock"),
        )
        .select_from(Inventario)
        .join(VarianteProducto, VarianteProducto.id == Inventario.variante_producto_id)
        .where(*stock_condiciones)
        .group_by(VarianteProducto.producto_id)
        .subquery()
    )
    statement = (
        select(
            Producto.id,
            Producto.nombre,
            func.coalesce(ventas.c.unidades, 0),
            func.coalesce(ventas.c.ingresos, 0),
            func.coalesce(ventas.c.dias, 0),
            func.coalesce(stock.c.stock, 0),
        )
        .select_from(Producto)
        .outerjoin(ventas, ventas.c.producto_id == Producto.id)
        .outerjoin(stock, stock.c.producto_id == Producto.id)
        .where(
            Producto.empresa_id == empresa_id,
            Producto.estado.is_(True),
            (func.coalesce(ventas.c.unidades, 0) > 0)
            | (func.coalesce(stock.c.stock, 0) > 0),
        )
        .order_by(func.coalesce(ventas.c.unidades, 0).desc(), Producto.id)
        .limit(limite)
    )
    return list(db.execute(statement).all())


def obtener_sucursal_autorizada(db: Session, *, empresa_id: int, sucursal_id: int):
    return db.scalar(
        select(Sucursal).where(
            Sucursal.id == sucursal_id,
            Sucursal.empresa_id == empresa_id,
            Sucursal.estado.is_(True),
        )
    )
