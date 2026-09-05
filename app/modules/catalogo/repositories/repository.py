from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload, with_loader_criteria

from app.modules.catalogo.models.models import (
    Categoria,
    Producto,
    RecursoProducto,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario


class CategoriaRepository:
    @staticmethod
    def listar_activas(db: Session) -> list[Categoria]:
        statement = (
            select(Categoria)
            .where(Categoria.estado.is_(True))
            .order_by(Categoria.nombre)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_activa_por_id(db: Session, categoria_id: int) -> Categoria | None:
        statement = select(Categoria).where(
            Categoria.id == categoria_id,
            Categoria.estado.is_(True),
        )
        return db.scalar(statement)


class ProductoRepository:
    @staticmethod
    def listar(db: Session) -> list[Producto]:
        statement = (
            select(Producto)
            .options(joinedload(Producto.categoria))
            .where(Producto.estado.is_(True))
            .order_by(Producto.id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, producto_id: int) -> Producto | None:
        statement = (
            select(Producto)
            .options(
                joinedload(Producto.categoria),
                selectinload(Producto.recursos).joinedload(RecursoProducto.color),
                selectinload(Producto.variantes).joinedload(VarianteProducto.talla),
                selectinload(Producto.variantes).joinedload(VarianteProducto.color),
                selectinload(Producto.variantes)
                .selectinload(VarianteProducto.inventarios)
                .joinedload(Inventario.sucursal),
                selectinload(Producto.variantes)
                .selectinload(VarianteProducto.inventarios)
                .joinedload(Inventario.temporada),
                with_loader_criteria(
                    RecursoProducto,
                    RecursoProducto.estado.is_(True),
                    include_aliases=True,
                ),
                with_loader_criteria(
                    VarianteProducto,
                    VarianteProducto.estado.is_(True),
                    include_aliases=True,
                ),
            )
            .where(Producto.id == producto_id, Producto.estado.is_(True))
        )
        return db.scalar(statement)
