"""Tenant-scoped persistence for seasons, collections and promotions.

The read methods deliberately keep the tenant predicate in every query.  This
is required even when PostgreSQL RLS is enabled: it documents the contract and
prevents a future non-RLS test/backend from turning an ID into a cross-tenant
lookup.
"""
from datetime import datetime, timezone
from decimal import Decimal

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
    def listar(
        db: Session,
        model,
        empresa_id: int,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
        tipo_descuento: str | None = None,
        vigente_ahora: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        statement = select(model).where(model.empresa_id == empresa_id)
        if buscar and hasattr(model, "nombre"):
            statement = statement.where(model.nombre.ilike(f"%{buscar.strip()}%"))
        if estado is not None and hasattr(model, "estado"):
            statement = statement.where(model.estado.is_(estado))
        if tipo_descuento is not None and hasattr(model, "tipo_descuento"):
            statement = statement.where(model.tipo_descuento == tipo_descuento)
        if vigente_ahora is not None and hasattr(model, "fecha_inicio"):
            statement = statement.where(
                model.estado.is_(True),
                model.fecha_inicio <= vigente_ahora,
                model.fecha_fin >= vigente_ahora,
            )
        return list(
            db.scalars(statement.order_by(model.id.desc()).limit(limit).offset(offset)).all()
        )

    @staticmethod
    def obtener(db: Session, model, empresa_id: int, item_id: int):
        return db.scalar(select(model).where(
            model.id == item_id, model.empresa_id == empresa_id,
        ))

    @staticmethod
    def colecciones(
        db: Session,
        empresa_id: int,
        *,
        temporada_id: int | None = None,
        buscar: str | None = None,
        estado: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        # La FK legacy de coleccion -> temporada es simple. Mantener ambos
        # predicados evita exponer una fila inconsistente si aún no están
        # instaladas las FKs compuestas tenant de la preparación SQL.
        statement = (
            select(Coleccion)
            .join(Temporada, Temporada.id == Coleccion.temporada_id)
            .where(
                Coleccion.empresa_id == empresa_id,
                Temporada.empresa_id == empresa_id,
            )
        )
        if temporada_id is not None:
            statement = statement.where(Coleccion.temporada_id == temporada_id)
        if buscar:
            statement = statement.where(Coleccion.nombre.ilike(f"%{buscar.strip()}%"))
        if estado is not None:
            statement = statement.where(Coleccion.estado.is_(estado))
        return list(db.scalars(
            statement.order_by(Coleccion.id.desc()).limit(limit).offset(offset)
        ).all())

    @staticmethod
    def obtener_coleccion(db: Session, empresa_id: int, item_id: int):
        return db.scalar(
            select(Coleccion)
            .join(Temporada, Temporada.id == Coleccion.temporada_id)
            .where(
                Coleccion.id == item_id,
                Coleccion.empresa_id == empresa_id,
                Temporada.empresa_id == empresa_id,
            )
        )

    @staticmethod
    def productos_de_coleccion(db: Session, empresa_id: int, coleccion_id: int):
        return list(db.scalars(
            select(Producto)
            .join(ProductoColeccion, ProductoColeccion.producto_id == Producto.id)
            .where(
                Producto.empresa_id == empresa_id,
                ProductoColeccion.empresa_id == empresa_id,
                ProductoColeccion.coleccion_id == coleccion_id,
            )
            .order_by(Producto.nombre, Producto.id)
        ).all())

    @staticmethod
    def productos_de_promocion(db: Session, empresa_id: int, promocion_id: int):
        return list(db.scalars(
            select(Producto)
            .join(PromocionProducto, PromocionProducto.producto_id == Producto.id)
            .where(
                Producto.empresa_id == empresa_id,
                PromocionProducto.empresa_id == empresa_id,
                PromocionProducto.promocion_id == promocion_id,
            )
            .order_by(Producto.nombre, Producto.id)
        ).all())

    @staticmethod
    def promociones_vigentes_para_producto(
        db: Session, empresa_id: int, producto_id: int, ahora: datetime | None = None
    ):
        ahora = ahora or datetime.now(timezone.utc)
        return list(db.scalars(
            select(Promocion)
            .join(PromocionProducto, PromocionProducto.promocion_id == Promocion.id)
            .where(
                Promocion.empresa_id == empresa_id,
                PromocionProducto.empresa_id == empresa_id,
                PromocionProducto.producto_id == producto_id,
                Promocion.estado.is_(True),
                Promocion.fecha_inicio <= ahora,
                Promocion.fecha_fin >= ahora,
            )
            .order_by(Promocion.fecha_inicio.desc(), Promocion.id.desc())
        ).all())

    @staticmethod
    def precio_promocional(base: Decimal, promocion) -> Decimal:
        if promocion.tipo_descuento == "PORCENTAJE":
            descuento = base * promocion.valor_descuento / Decimal("100")
        else:
            descuento = promocion.valor_descuento
        return max(Decimal("0"), base - descuento)

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
