"""Consultas de SOLO LECTURA del catalogo en un tenant validado.

Proyecciones SQLAlchemy Core: no se reutilizan las rutas/ORM legacy
sin filtros por empresa y no hay lazy-load de recursos de otra empresa.
empresa_id es obligatorio y se recibe UNICAMENTE del TenantContext del
endpoint, no desde el cliente HTTP.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, VarianteProducto,
)


class TenantCatalogoRepository:
    @staticmethod
    def _empresa_valida(empresa_id: int) -> int:
        if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id <= 0:
            raise ValueError("El contexto empresarial validado es obligatorio")
        return empresa_id

    @classmethod
    def listar_categorias(cls, db: Session, *, empresa_id: int) -> list[dict]:
        tenant_id = cls._empresa_valida(empresa_id)
        c = Categoria.__table__
        statement = (
            select(c.c.id, c.c.nombre, c.c.descripcion)
            .where(c.c.empresa_id == tenant_id, c.c.estado.is_(True))
            .order_by(c.c.nombre, c.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_productos(
        cls,
        db: Session,
        *,
        empresa_id: int,
        categoria_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        statement = (
            select(
                p.c.id, p.c.nombre, p.c.descripcion,
                p.c.precio, p.c.categoria_id,
                c.c.nombre.label("categoria_nombre"),
            )
            .join(c, (c.c.id == p.c.categoria_id) & (c.c.empresa_id == tenant_id))
            .where(
                p.c.empresa_id == tenant_id,
                p.c.estado.is_(True),
                c.c.estado.is_(True),
            )
        )
        if categoria_id is not None:
            statement = statement.where(c.c.id == categoria_id)
        statement = statement.order_by(p.c.id).limit(limit).offset(offset)
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def obtener_producto(
        cls, db: Session, *, empresa_id: int, producto_id: int
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        statement = (
            select(
                p.c.id, p.c.nombre, p.c.descripcion,
                p.c.precio, p.c.categoria_id,
                c.c.nombre.label("categoria_nombre"),
            )
            .join(c, (c.c.id == p.c.categoria_id) & (c.c.empresa_id == tenant_id))
            .where(
                p.c.empresa_id == tenant_id,
                p.c.id == producto_id,
                p.c.estado.is_(True),
                c.c.estado.is_(True),
            )
        )
        resultado = db.execute(statement).mappings().one_or_none()
        return dict(resultado) if resultado is not None else None

    @classmethod
    def listar_variantes(
        cls, db: Session, *, empresa_id: int, producto_id: int
    ) -> list[dict]:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        v = VarianteProducto.__table__
        talla = Talla.__table__
        color = Color.__table__
        statement = (
            select(
                v.c.id, v.c.producto_id, v.c.sku,
                talla.c.nombre.label("talla"),
                color.c.nombre.label("color"),
            )
            .join(p, (p.c.id == v.c.producto_id) & (p.c.empresa_id == tenant_id))
            .join(c, (c.c.id == p.c.categoria_id) & (c.c.empresa_id == tenant_id))
            .join(talla, talla.c.id == v.c.talla_id)
            .join(color, color.c.id == v.c.color_id)
            .where(
                v.c.empresa_id == tenant_id,
                v.c.producto_id == producto_id,
                v.c.estado.is_(True),
                p.c.estado.is_(True),
                c.c.estado.is_(True),
                talla.c.estado.is_(True),
                color.c.estado.is_(True),
            )
            .order_by(v.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def obtener_variante(
        cls, db: Session, *, empresa_id: int, variante_id: int
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        v = VarianteProducto.__table__
        talla = Talla.__table__
        color = Color.__table__
        statement = (
            select(
                v.c.id, v.c.producto_id, v.c.sku,
                talla.c.nombre.label("talla"),
                color.c.nombre.label("color"),
            )
            .join(p, (p.c.id == v.c.producto_id) & (p.c.empresa_id == tenant_id))
            .join(c, (c.c.id == p.c.categoria_id) & (c.c.empresa_id == tenant_id))
            .join(talla, talla.c.id == v.c.talla_id)
            .join(color, color.c.id == v.c.color_id)
            .where(
                v.c.empresa_id == tenant_id,
                v.c.id == variante_id,
                v.c.estado.is_(True),
                p.c.estado.is_(True),
                c.c.estado.is_(True),
                talla.c.estado.is_(True),
                color.c.estado.is_(True),
            )
        )
        resultado = db.execute(statement).mappings().one_or_none()
        return dict(resultado) if resultado is not None else None
