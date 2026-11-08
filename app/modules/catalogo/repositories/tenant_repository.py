"""Consultas de SOLO LECTURA del catalogo en un tenant validado.

Proyecciones SQLAlchemy Core: no se reutilizan las rutas/ORM legacy
sin filtros por empresa y no hay lazy-load de recursos de otra empresa.
empresa_id es obligatorio y se recibe UNICAMENTE del TenantContext del
endpoint, no desde el cliente HTTP.

Excepcion deliberada: `talla`, `color` y `ciudad` son diccionarios GLOBALES
compartidos por todas las empresas y no tienen columna `empresa_id`; sus
listados no reciben tenant, pero solo se exponen a personal con el permiso
nominal correspondiente.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, RecursoProducto, Talla, Temporada, VarianteProducto,
)
from app.modules.proveedores.models.models import Proveedor
from app.modules.sucursales.models.models import Ciudad


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
            select(c.c.id, c.c.nombre, c.c.descripcion, c.c.estado)
            .where(c.c.empresa_id == tenant_id, c.c.estado.is_(True))
            .order_by(c.c.nombre, c.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def obtener_categoria(
        cls, db: Session, *, empresa_id: int, categoria_id: int,
        incluir_inactivo: bool = False,
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        c = Categoria.__table__
        statement = (
            select(c.c.id, c.c.nombre, c.c.descripcion, c.c.estado)
            .where(
                c.c.empresa_id == tenant_id,
                c.c.id == categoria_id,
            )
        )
        if not incluir_inactivo:
            statement = statement.where(c.c.estado.is_(True))
        row = db.execute(statement).mappings().one_or_none()
        return dict(row) if row is not None else None

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
                p.c.id, p.c.nombre, p.c.descripcion, p.c.estado,
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
        cls, db: Session, *, empresa_id: int, producto_id: int,
        incluir_inactivo: bool = False,
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        statement = (
            select(
                p.c.id, p.c.nombre, p.c.descripcion, p.c.estado,
                p.c.precio, p.c.categoria_id,
                c.c.nombre.label("categoria_nombre"),
            )
            .join(c, (c.c.id == p.c.categoria_id) & (c.c.empresa_id == tenant_id))
            .where(
                p.c.empresa_id == tenant_id,
                p.c.id == producto_id,
                c.c.estado.is_(True),
            )
        )
        if not incluir_inactivo:
            statement = statement.where(p.c.estado.is_(True))
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
                v.c.id, v.c.producto_id, v.c.sku, v.c.estado,
                v.c.talla_id, v.c.color_id,
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
        cls, db: Session, *, empresa_id: int, variante_id: int,
        incluir_inactivo: bool = False,
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        c = Categoria.__table__
        v = VarianteProducto.__table__
        talla = Talla.__table__
        color = Color.__table__
        statement = (
            select(
                v.c.id, v.c.producto_id, v.c.sku, v.c.estado,
                v.c.talla_id, v.c.color_id,
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
                p.c.estado.is_(True),
                c.c.estado.is_(True),
                talla.c.estado.is_(True),
                color.c.estado.is_(True),
            )
        )
        if not incluir_inactivo:
            statement = statement.where(v.c.estado.is_(True))
        resultado = db.execute(statement).mappings().one_or_none()
        return dict(resultado) if resultado is not None else None

    # ------------------------------------------------------------------
    # 5BZ: contratos de LECTURA que la UI resolvia con IDs escritos a mano.
    # `talla`, `color` y `ciudad` son diccionarios GLOBALES (sin empresa_id):
    # se devuelven completos solo a personal autorizado. `proveedor`,
    # `temporada` y `variante_producto` SI son empresariales: el filtro por
    # empresa_id va explicito en cada consulta (defensa en profundidad junto a
    # la politica RLS).
    # ------------------------------------------------------------------
    @staticmethod
    def _patron_texto(texto: str) -> str:
        """Patron ILIKE seguro: escapa comodines para no ampliar la busqueda."""
        limpio = (
            texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return f"%{limpio}%"

    @classmethod
    def listar_tallas(cls, db: Session) -> list[dict]:
        """Diccionario GLOBAL de tallas ACTIVAS, sin filtro de empresa."""
        t = Talla.__table__
        statement = (
            select(t.c.id, t.c.nombre)
            .where(t.c.estado.is_(True))
            .order_by(t.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_colores(cls, db: Session) -> list[dict]:
        """Diccionario GLOBAL de colores ACTIVOS, sin filtro de empresa."""
        c = Color.__table__
        statement = (
            select(c.c.id, c.c.nombre)
            .where(c.c.estado.is_(True))
            .order_by(c.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_ciudades(cls, db: Session) -> list[dict]:
        """Diccionario GLOBAL de ciudades, ordenado por nombre.

        El contrato no expone `estado` ni pide filtrar actividad: devolver el
        diccionario completo evita que la UI dependa de IDs fijos.
        """
        c = Ciudad.__table__
        statement = select(c.c.id, c.c.nombre).order_by(c.c.nombre, c.c.id)
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_proveedores(cls, db: Session, *, empresa_id: int) -> list[dict]:
        """Proveedores de la empresa; incluye inactivos con su `estado`."""
        tenant_id = cls._empresa_valida(empresa_id)
        p = Proveedor.__table__
        statement = (
            select(p.c.id, p.c.razon_social, p.c.estado)
            .where(p.c.empresa_id == tenant_id)
            .order_by(p.c.razon_social, p.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_temporadas(cls, db: Session, *, empresa_id: int) -> list[dict]:
        """Temporadas de la empresa; el contrato expone `estado` y fechas."""
        tenant_id = cls._empresa_valida(empresa_id)
        t = Temporada.__table__
        statement = (
            select(
                t.c.id, t.c.nombre, t.c.fecha_inicio, t.c.fecha_fin, t.c.estado,
            )
            .where(t.c.empresa_id == tenant_id)
            .order_by(t.c.id)
        )
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_variantes_empresa(
        cls,
        db: Session,
        *,
        empresa_id: int,
        producto_id: int | None = None,
        texto: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Variantes de toda la empresa, sin exigir un producto concreto.

        `texto` filtra por SKU o por nombre de producto. El JOIN con producto
        lleva empresa_id explicito para que una variante jamas resuelva un
        producto de otra empresa.
        """
        tenant_id = cls._empresa_valida(empresa_id)
        p = Producto.__table__
        v = VarianteProducto.__table__
        talla = Talla.__table__
        color = Color.__table__
        statement = (
            select(
                v.c.id,
                v.c.producto_id,
                p.c.nombre.label("producto_nombre"),
                v.c.sku,
                v.c.talla_id,
                talla.c.nombre.label("talla"),
                v.c.color_id,
                color.c.nombre.label("color"),
            )
            .join(p, (p.c.id == v.c.producto_id) & (p.c.empresa_id == tenant_id))
            .join(talla, talla.c.id == v.c.talla_id)
            .join(color, color.c.id == v.c.color_id)
            .where(
                v.c.empresa_id == tenant_id,
                v.c.estado.is_(True),
                p.c.estado.is_(True),
                talla.c.estado.is_(True),
                color.c.estado.is_(True),
            )
        )
        if producto_id is not None:
            statement = statement.where(v.c.producto_id == producto_id)
        if texto:
            patron = cls._patron_texto(texto)
            statement = statement.where(
                v.c.sku.ilike(patron, escape="\\")
                | p.c.nombre.ilike(patron, escape="\\")
            )
        statement = statement.order_by(v.c.id).limit(limit).offset(offset)
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def listar_recursos(
        cls, db: Session, *, empresa_id: int, producto_id: int,
        estado: bool | None = None,
    ) -> list[dict]:
        tenant_id = cls._empresa_valida(empresa_id)
        r = RecursoProducto.__table__
        p = Producto.__table__
        c = Color.__table__
        statement = (
            select(
                r.c.id, r.c.producto_id, r.c.color_id, r.c.tipo, r.c.url,
                r.c.es_principal, r.c.estado, c.c.nombre.label("color"),
            )
            .join(p, (p.c.id == r.c.producto_id) & (p.c.empresa_id == tenant_id))
            .outerjoin(c, c.c.id == r.c.color_id)
            .where(r.c.empresa_id == tenant_id, r.c.producto_id == producto_id)
            .order_by(r.c.id)
        )
        if estado is not None:
            statement = statement.where(r.c.estado.is_(estado))
        return [dict(row) for row in db.execute(statement).mappings().all()]

    @classmethod
    def obtener_recurso(
        cls, db: Session, *, empresa_id: int, recurso_id: int,
    ) -> dict | None:
        tenant_id = cls._empresa_valida(empresa_id)
        r = RecursoProducto.__table__
        p = Producto.__table__
        c = Color.__table__
        statement = (
            select(
                r.c.id, r.c.producto_id, r.c.color_id, r.c.tipo, r.c.url,
                r.c.es_principal, r.c.estado, c.c.nombre.label("color"),
            )
            .join(p, (p.c.id == r.c.producto_id) & (p.c.empresa_id == tenant_id))
            .outerjoin(c, c.c.id == r.c.color_id)
            .where(r.c.empresa_id == tenant_id, r.c.id == recurso_id)
        )
        row = db.execute(statement).mappings().one_or_none()
        return dict(row) if row is not None else None
