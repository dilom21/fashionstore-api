"""5BZ: inventario tenant por sucursal (SOLO LECTURA).

Proyeccion SQLAlchemy Core sobre las tablas BASE. No se usa la vista
`public.vw_inventario_stock`: cada JOIN repite `empresa_id` explicito para que
una fila jamas resuelva sucursal, variante, producto o temporada de otra
empresa aunque la vista o una politica RLS cambiaran de criterio.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


class TenantInventarioRepository:
    @staticmethod
    def _empresa_valida(empresa_id: int) -> int:
        if (
            isinstance(empresa_id, bool)
            or not isinstance(empresa_id, int)
            or empresa_id <= 0
        ):
            raise ValueError("El contexto empresarial validado es obligatorio")
        return empresa_id

    @staticmethod
    def _patron_texto(texto: str) -> str:
        """Patron ILIKE seguro: escapa comodines para no ampliar la busqueda."""
        limpio = (
            texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return f"%{limpio}%"

    @classmethod
    def listar(
        cls,
        db: Session,
        *,
        empresa_id: int,
        sucursal_id: int | None = None,
        texto: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Filas de inventario de la empresa, con nombres para la UI.

        `stock_disponible` se calcula en SQL (no en el cliente) para que el
        valor mostrado sea siempre el mismo que evalúa el servidor.
        """
        tenant_id = cls._empresa_valida(empresa_id)
        i = Inventario.__table__
        s = Sucursal.__table__
        vp = VarianteProducto.__table__
        p = Producto.__table__
        t = Temporada.__table__
        talla = Talla.__table__
        color = Color.__table__
        statement = (
            select(
                i.c.id,
                i.c.sucursal_id,
                s.c.nombre.label("sucursal_nombre"),
                i.c.variante_producto_id,
                vp.c.producto_id,
                p.c.nombre.label("producto_nombre"),
                vp.c.sku,
                vp.c.talla_id,
                talla.c.nombre.label("talla"),
                vp.c.color_id,
                color.c.nombre.label("color"),
                i.c.temporada_id,
                t.c.nombre.label("temporada_nombre"),
                i.c.stock_actual,
                i.c.stock_reservado,
                (i.c.stock_actual - i.c.stock_reservado).label(
                    "stock_disponible"
                ),
            )
            .join(s, (s.c.id == i.c.sucursal_id) & (s.c.empresa_id == tenant_id))
            .join(
                vp,
                (vp.c.id == i.c.variante_producto_id)
                & (vp.c.empresa_id == tenant_id),
            )
            .join(
                p,
                (p.c.id == vp.c.producto_id) & (p.c.empresa_id == tenant_id),
            )
            .join(
                t,
                (t.c.id == i.c.temporada_id) & (t.c.empresa_id == tenant_id),
            )
            .join(talla, talla.c.id == vp.c.talla_id)
            .join(color, color.c.id == vp.c.color_id)
            .where(i.c.empresa_id == tenant_id)
        )
        if sucursal_id is not None:
            statement = statement.where(i.c.sucursal_id == sucursal_id)
        if texto:
            patron = cls._patron_texto(texto)
            statement = statement.where(
                vp.c.sku.ilike(patron, escape="\\")
                | p.c.nombre.ilike(patron, escape="\\")
            )
        statement = statement.order_by(i.c.id).limit(limit).offset(offset)
        return [dict(row) for row in db.execute(statement).mappings().all()]
