from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta


def obtener_historico(db: Session, *, empresa_id: int, producto_id: int,
                      sucursal_id: int | None, hasta: date, dias: int = 90):
    desde = hasta - timedelta(days=dias - 1)
    condiciones = [Producto.id == producto_id, Producto.empresa_id == empresa_id,
                   VarianteProducto.empresa_id == empresa_id,
                   Inventario.empresa_id == empresa_id, Venta.empresa_id == empresa_id,
                   DetalleVenta.empresa_id == empresa_id, Venta.estado == "COMPLETADA",
                   Venta.fecha_hora >= datetime.combine(desde, time.min),
                   Venta.fecha_hora < datetime.combine(hasta + timedelta(days=1), time.min)]
    if sucursal_id is not None:
        condiciones.extend([Sucursal.id == sucursal_id, Sucursal.empresa_id == empresa_id,
                             Venta.sucursal_id == sucursal_id])
    statement = (select(func.date(Venta.fecha_hora), func.sum(DetalleVenta.cantidad))
                 .select_from(DetalleVenta).join(Venta, Venta.id == DetalleVenta.venta_id)
                 .join(Inventario, Inventario.id == DetalleVenta.inventario_id)
                 .join(VarianteProducto, VarianteProducto.id == Inventario.variante_producto_id)
                 .join(Producto, Producto.id == VarianteProducto.producto_id)
                 .join(Sucursal, Sucursal.id == Venta.sucursal_id)
                 .where(*condiciones).group_by(func.date(Venta.fecha_hora)))
    return list(db.execute(statement).all())


def obtener_producto(db: Session, *, empresa_id: int, producto_id: int):
    return db.scalar(select(Producto).where(Producto.id == producto_id,
                                             Producto.empresa_id == empresa_id,
                                             Producto.estado.is_(True)))


def obtener_sucursal(db: Session, *, empresa_id: int, sucursal_id: int):
    return db.scalar(select(Sucursal).where(Sucursal.id == sucursal_id,
                                             Sucursal.empresa_id == empresa_id,
                                             Sucursal.estado.is_(True)))
