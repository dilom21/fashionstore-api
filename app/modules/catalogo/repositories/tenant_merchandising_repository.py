"""Tenant-scoped persistence for seasons, collections and promotions."""
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Producto, Temporada
from app.modules.promociones.models.models import Promocion, PromocionProducto
from app.modules.temporadas_colecciones.models.models import Coleccion, ProductoColeccion


class TenantMerchandisingRepository:
    @staticmethod
    def _next_sqlite_id(db: Session, model) -> int | None:
        if db.get_bind().dialect.name != "sqlite":
            return None
        return int(db.scalar(select(func.coalesce(func.max(model.id), 0) + 1)) or 1)

    @staticmethod
    def listar(db: Session, model, empresa_id: int):
        return list(db.scalars(select(model).where(
            model.empresa_id == empresa_id,
        ).order_by(model.id.desc())).all())

    @staticmethod
    def obtener(db: Session, model, empresa_id: int, item_id: int):
        return db.scalar(select(model).where(
            model.id == item_id, model.empresa_id == empresa_id,
        ))

    @classmethod
    def crear_temporada(cls, db: Session, empresa_id: int, datos) -> Temporada:
        item = Temporada(
            id=cls._next_sqlite_id(db, Temporada), empresa_id=empresa_id,
            nombre=datos.nombre.strip(), fecha_inicio=datos.fecha_inicio,
            fecha_fin=datos.fecha_fin, estado=True,
        )
        db.add(item)
        db.flush()
        return item

    @classmethod
    def crear_coleccion(cls, db: Session, empresa_id: int, datos) -> Coleccion:
        item = Coleccion(
            id=cls._next_sqlite_id(db, Coleccion), empresa_id=empresa_id,
            temporada_id=datos.temporada_id, nombre=datos.nombre.strip(),
            descripcion=datos.descripcion, estado=True,
        )
        db.add(item)
        db.flush()
        return item

    @classmethod
    def crear_promocion(cls, db: Session, empresa_id: int, datos) -> Promocion:
        item = Promocion(
            id=cls._next_sqlite_id(db, Promocion), empresa_id=empresa_id,
            nombre=datos.nombre.strip(), descripcion=datos.descripcion,
            tipo_descuento=datos.tipo_descuento.upper(),
            valor_descuento=datos.valor_descuento,
            fecha_inicio=datos.fecha_inicio, fecha_fin=datos.fecha_fin, estado=True,
        )
        db.add(item)
        db.flush()
        return item

    @staticmethod
    def productos(db: Session, empresa_id: int, relation, parent_column, parent_id: int):
        return list(db.scalars(
            select(Producto).join(relation, relation.producto_id == Producto.id)
            .where(
                parent_column == parent_id,
                relation.empresa_id == empresa_id,
                Producto.empresa_id == empresa_id,
            ).order_by(Producto.nombre, Producto.id)
        ).all())

    @staticmethod
    def reemplazar_productos(
        db: Session, *, empresa_id: int, relation, parent_column,
        parent_id: int, producto_ids: list[int], parent_attribute: str,
    ) -> list[Producto]:
        ids = list(dict.fromkeys(producto_ids))
        productos = list(db.scalars(select(Producto).where(
            Producto.empresa_id == empresa_id,
            Producto.id.in_(ids) if ids else False,
            Producto.estado.is_(True),
        )).all()) if ids else []
        if len(productos) != len(ids):
            raise ValueError("Uno o más productos no existen o están inactivos")
        db.execute(delete(relation).where(
            parent_column == parent_id, relation.empresa_id == empresa_id,
        ))
        for producto_id in ids:
            db.add(relation(
                empresa_id=empresa_id, producto_id=producto_id,
                **{parent_attribute: parent_id},
            ))
        db.flush()
        return sorted(productos, key=lambda item: (item.nombre, item.id))
