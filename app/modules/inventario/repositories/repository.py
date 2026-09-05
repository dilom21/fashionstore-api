from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


class InventarioRepository:
    @staticmethod
    def _consulta_base():
        return select(Inventario).options(
            joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.producto),
            joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.talla),
            joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.color),
            joinedload(Inventario.sucursal).joinedload(Sucursal.ciudad),
            joinedload(Inventario.temporada),
        )

    @staticmethod
    def listar(
        db: Session,
        sucursal_id: int | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> list[Inventario]:
        statement = InventarioRepository._consulta_base().join(
            Inventario.variante_producto
        ).join(VarianteProducto.producto)

        if sucursal_id is not None:
            statement = statement.where(Inventario.sucursal_id == sucursal_id)
        if producto_id is not None:
            statement = statement.where(Producto.id == producto_id)
        if categoria_id is not None:
            statement = statement.where(Producto.categoria_id == categoria_id)
        if talla_id is not None:
            statement = statement.where(VarianteProducto.talla_id == talla_id)
        if color_id is not None:
            statement = statement.where(VarianteProducto.color_id == color_id)
        if temporada_id is not None:
            statement = statement.where(Inventario.temporada_id == temporada_id)

        return list(db.scalars(statement.order_by(Inventario.id)).all())

    @staticmethod
    def obtener_por_id(db: Session, inventario_id: int) -> Inventario | None:
        statement = InventarioRepository._consulta_base().where(
            Inventario.id == inventario_id
        )
        return db.scalar(statement)

    @staticmethod
    def listar_disponibles_por_producto(
        db: Session, producto_id: int
    ) -> list[Inventario]:
        statement = (
            InventarioRepository._consulta_base()
            .join(Inventario.variante_producto)
            .where(
                VarianteProducto.producto_id == producto_id,
                Inventario.stock_actual - Inventario.stock_reservado > 0,
            )
            .order_by(Inventario.sucursal_id, Inventario.variante_producto_id)
        )
        return list(db.scalars(statement).all())
