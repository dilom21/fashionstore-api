"""Published storefront queries resolved exclusively from an active tenant slug."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, String, and_, column, func, select, table
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, RecursoProducto, Talla, Temporada,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.promociones.models.models import Promocion, PromocionProducto
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.temporadas_colecciones.models.models import Coleccion, ProductoColeccion


empresa = table(
    "empresa",
    column("id", BigInteger), column("slug", String),
    column("nombre_comercial", String), column("estado", Boolean),
)


class TenantPublicRepository:
    @staticmethod
    def resolver_empresa(db: Session, slug: str) -> dict | None:
        fila = db.execute(
            select(empresa.c.id, empresa.c.slug, empresa.c.nombre_comercial)
            .where(empresa.c.slug == slug, empresa.c.estado.is_(True))
        ).mappings().one_or_none()
        return dict(fila) if fila is not None else None

    @staticmethod
    def listar_sucursales(db: Session, empresa_id: int) -> list[dict]:
        s, c = Sucursal.__table__, Ciudad.__table__
        consulta = (
            select(
                s.c.id, s.c.nombre, s.c.direccion, s.c.telefono,
                c.c.id.label("ciudad_id"), c.c.nombre.label("ciudad_nombre"),
            )
            .join(c, c.c.id == s.c.ciudad_id)
            .where(s.c.empresa_id == empresa_id, s.c.estado.is_(True), c.c.estado.is_(True))
            .order_by(c.c.nombre, s.c.nombre, s.c.id)
        )
        return [dict(fila) for fila in db.execute(consulta).mappings().all()]

    @staticmethod
    def listar_productos(
        db: Session, empresa_id: int, *, categoria_id: int | None,
        texto: str | None, limit: int, offset: int,
    ) -> list[dict]:
        p, c = Producto.__table__, Categoria.__table__
        r, v = RecursoProducto.__table__, VarianteProducto.__table__
        i, s = Inventario.__table__, Sucursal.__table__
        imagen = (
            select(r.c.url)
            .where(
                r.c.empresa_id == empresa_id,
                r.c.producto_id == p.c.id,
                r.c.estado.is_(True),
            )
            .order_by(r.c.es_principal.desc(), r.c.id)
            .limit(1)
            .scalar_subquery()
        )
        stock = (
            select(func.coalesce(func.sum(i.c.stock_actual - i.c.stock_reservado), 0))
            .select_from(v.join(i, i.c.variante_producto_id == v.c.id).join(
                s, s.c.id == i.c.sucursal_id
            ))
            .where(
                v.c.producto_id == p.c.id,
                v.c.empresa_id == empresa_id,
                v.c.estado.is_(True),
                i.c.empresa_id == empresa_id,
                s.c.empresa_id == empresa_id,
                s.c.estado.is_(True),
            )
            .correlate(p)
            .scalar_subquery()
        )
        consulta = (
            select(
                p.c.id, p.c.nombre, p.c.descripcion, p.c.precio,
                c.c.id.label("categoria_id"), c.c.nombre.label("categoria_nombre"),
                imagen.label("imagen_url"), stock.label("stock_disponible"),
            )
            .join(c, and_(c.c.id == p.c.categoria_id, c.c.empresa_id == empresa_id))
            .where(p.c.empresa_id == empresa_id, p.c.estado.is_(True), c.c.estado.is_(True))
        )
        if categoria_id is not None:
            consulta = consulta.where(c.c.id == categoria_id)
        if texto:
            limpio = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            patron = f"%{limpio}%"
            consulta = consulta.where(p.c.nombre.ilike(patron, escape="\\"))
        consulta = consulta.order_by(p.c.nombre, p.c.id).limit(limit).offset(offset)
        return [dict(fila) for fila in db.execute(consulta).mappings().all()]

    @staticmethod
    def detalle_producto(db: Session, empresa_id: int, producto_id: int) -> dict | None:
        p, c = Producto.__table__, Categoria.__table__
        base = db.execute(
            select(
                p.c.id, p.c.nombre, p.c.descripcion, p.c.precio,
                c.c.id.label("categoria_id"), c.c.nombre.label("categoria_nombre"),
            ).join(c, and_(c.c.id == p.c.categoria_id, c.c.empresa_id == empresa_id))
            .where(
                p.c.id == producto_id, p.c.empresa_id == empresa_id,
                p.c.estado.is_(True), c.c.estado.is_(True),
            )
        ).mappings().one_or_none()
        producto = dict(base) if base is not None else None
        if producto is None:
            return None
        # Reuse the list projection for derived image/stock without imposing a
        # global catalog pagination limit on detail lookup.
        derived = TenantPublicRepository.listar_productos(
            db, empresa_id, categoria_id=int(producto["categoria_id"]),
            texto=str(producto["nombre"]), limit=100, offset=0,
        )
        exact = next((item for item in derived if int(item["id"]) == producto_id), {})
        producto.update({
            "imagen_url": exact.get("imagen_url"),
            "stock_disponible": exact.get("stock_disponible", 0),
        })
        v, t, c = VarianteProducto.__table__, Talla.__table__, Color.__table__
        i, s, temp = Inventario.__table__, Sucursal.__table__, Temporada.__table__
        filas = db.execute(
            select(
                v.c.id, v.c.sku, t.c.nombre.label("talla"), c.c.nombre.label("color"),
                s.c.id.label("sucursal_id"), s.c.nombre.label("sucursal_nombre"),
                temp.c.nombre.label("temporada"),
                (i.c.stock_actual - i.c.stock_reservado).label("stock_disponible"),
            )
            .join(t, t.c.id == v.c.talla_id)
            .join(c, c.c.id == v.c.color_id)
            .join(i, and_(i.c.variante_producto_id == v.c.id, i.c.empresa_id == empresa_id))
            .join(s, and_(s.c.id == i.c.sucursal_id, s.c.empresa_id == empresa_id, s.c.estado.is_(True)))
            .join(temp, and_(temp.c.id == i.c.temporada_id, temp.c.empresa_id == empresa_id, temp.c.estado.is_(True)))
            .where(
                v.c.empresa_id == empresa_id, v.c.producto_id == producto_id,
                v.c.estado.is_(True), t.c.estado.is_(True), c.c.estado.is_(True),
                (i.c.stock_actual - i.c.stock_reservado) > 0,
            )
            .order_by(v.c.id, s.c.id)
        ).mappings().all()
        return {**producto, "variantes": [dict(fila) for fila in filas]}

    @staticmethod
    def listar_temporadas_colecciones(db: Session, empresa_id: int) -> list[dict]:
        t, col, pc, p = (
            Temporada.__table__, Coleccion.__table__,
            ProductoColeccion.__table__, Producto.__table__,
        )
        filas = db.execute(
            select(
                t.c.id.label("temporada_id"), t.c.nombre.label("temporada_nombre"),
                t.c.fecha_inicio, t.c.fecha_fin,
                col.c.id.label("coleccion_id"), col.c.nombre.label("coleccion_nombre"),
                col.c.descripcion,
                func.count(p.c.id).label("productos_activos"),
            )
            .join(col, and_(col.c.temporada_id == t.c.id, col.c.empresa_id == empresa_id, col.c.estado.is_(True)))
            .outerjoin(pc, and_(pc.c.coleccion_id == col.c.id, pc.c.empresa_id == empresa_id))
            .outerjoin(p, and_(p.c.id == pc.c.producto_id, p.c.empresa_id == empresa_id, p.c.estado.is_(True)))
            .where(t.c.empresa_id == empresa_id, t.c.estado.is_(True))
            .group_by(t.c.id, col.c.id)
            .order_by(t.c.fecha_inicio.desc(), col.c.nombre)
        ).mappings().all()
        return [dict(fila) for fila in filas]

    @staticmethod
    def listar_promociones(db: Session, empresa_id: int) -> list[dict]:
        ahora = datetime.now(timezone.utc)
        pr, pp, p = Promocion.__table__, PromocionProducto.__table__, Producto.__table__
        filas = db.execute(
            select(
                pr.c.id, pr.c.nombre, pr.c.descripcion, pr.c.tipo_descuento,
                pr.c.valor_descuento, pr.c.fecha_inicio, pr.c.fecha_fin,
                func.count(p.c.id).label("productos_activos"),
            )
            .outerjoin(pp, and_(pp.c.promocion_id == pr.c.id, pp.c.empresa_id == empresa_id))
            .outerjoin(p, and_(p.c.id == pp.c.producto_id, p.c.empresa_id == empresa_id, p.c.estado.is_(True)))
            .where(
                pr.c.empresa_id == empresa_id, pr.c.estado.is_(True),
                pr.c.fecha_inicio <= ahora, pr.c.fecha_fin >= ahora,
            )
            .group_by(pr.c.id)
            .order_by(pr.c.fecha_fin, pr.c.id)
        ).mappings().all()
        return [dict(fila) for fila in filas]
