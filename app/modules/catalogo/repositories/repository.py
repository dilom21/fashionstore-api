from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload, selectinload, with_loader_criteria

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    RecursoProducto,
    Talla,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.temporadas_colecciones.models.models import (
    Coleccion,
    ProductoColeccion,
)


def registrar_evento_bitacora(
    db: Session,
    *,
    usuario_id: int,
    accion: str,
    entidad_afectada: str,
    descripcion: str,
) -> None:
    """Escritura manual en bitacora para entidades sin trigger de auditoria.

    producto y variante_producto poseen trigger propio (fn_registrar_bitacora),
    por lo que NO deben auditarse manualmente para no duplicar eventos.
    categoria, talla, color y recurso_producto no tienen trigger.
    """
    db.add(
        Bitacora(
            usuario_id=usuario_id,
            fecha_hora=datetime.now(timezone.utc),
            accion=accion,
            entidad_afectada=entidad_afectada,
            descripcion=descripcion,
        )
    )
    db.flush()


class CategoriaRepository:
    """Metodos CU07 para categorias. Los metodos publicos de catalogo se conservan."""

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

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Categoria]:
        statement = select(Categoria)
        if buscar:
            statement = statement.where(
                Categoria.nombre.ilike(f"%{buscar.strip()}%")
            )
        if estado is not None:
            statement = statement.where(Categoria.estado.is_(estado))
        statement = statement.order_by(Categoria.nombre, Categoria.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, categoria_id: int) -> Categoria | None:
        return db.get(Categoria, categoria_id)

    @staticmethod
    def buscar_por_nombre_normalizado(
        db: Session, nombre: str
    ) -> Categoria | None:
        statement = select(Categoria).where(
            func.lower(Categoria.nombre) == nombre.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session, *, nombre: str, descripcion: str | None
    ) -> Categoria:
        categoria = Categoria(nombre=nombre, descripcion=descripcion, estado=True)
        db.add(categoria)
        db.flush()
        return categoria

    @staticmethod
    def contar_productos_activos(db: Session, categoria_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Producto)
            .where(
                Producto.categoria_id == categoria_id,
                Producto.estado.is_(True),
            )
        )
        return int(db.scalar(statement) or 0)


class ProductoRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        coleccion_id: int | None = None,
        sucursal_id: int | None = None,
        con_stock: bool | None = None,
    ) -> list[Producto]:
        """Listado publico CU09: productos activos de categoria activa.

        Los filtros de variante, inventario y coleccion se resuelven con
        EXISTS correlacionados para no duplicar filas de producto y evitar N+1.

        Semantica de temporada_id: producto con inventario en esa temporada
        O producto asociado a una coleccion activa de esa temporada.
        """
        statement = (
            select(Producto)
            .options(joinedload(Producto.categoria))
            .where(
                Producto.estado.is_(True),
                Producto.categoria.has(Categoria.estado.is_(True)),
            )
        )
        if buscar:
            patron = f"%{buscar.strip()}%"
            statement = statement.where(
                or_(
                    Producto.nombre.ilike(patron),
                    Producto.descripcion.ilike(patron),
                )
            )
        if categoria_id is not None:
            statement = statement.where(Producto.categoria_id == categoria_id)
        if talla_id is not None:
            statement = statement.where(
                select(VarianteProducto.id)
                .where(
                    VarianteProducto.producto_id == Producto.id,
                    VarianteProducto.estado.is_(True),
                    VarianteProducto.talla_id == talla_id,
                )
                .exists()
            )
        if color_id is not None:
            statement = statement.where(
                select(VarianteProducto.id)
                .where(
                    VarianteProducto.producto_id == Producto.id,
                    VarianteProducto.estado.is_(True),
                    VarianteProducto.color_id == color_id,
                )
                .exists()
            )
        if temporada_id is not None:
            inventario_temporada = (
                select(Inventario.id)
                .join(
                    VarianteProducto,
                    Inventario.variante_producto_id == VarianteProducto.id,
                )
                .where(
                    VarianteProducto.producto_id == Producto.id,
                    VarianteProducto.estado.is_(True),
                    Inventario.temporada_id == temporada_id,
                )
                .exists()
            )
            coleccion_temporada = (
                select(ProductoColeccion.producto_id)
                .join(
                    Coleccion,
                    ProductoColeccion.coleccion_id == Coleccion.id,
                )
                .where(
                    ProductoColeccion.producto_id == Producto.id,
                    Coleccion.temporada_id == temporada_id,
                    Coleccion.estado.is_(True),
                )
                .exists()
            )
            statement = statement.where(
                or_(inventario_temporada, coleccion_temporada)
            )
        if coleccion_id is not None:
            statement = statement.where(
                select(ProductoColeccion.producto_id)
                .join(
                    Coleccion,
                    ProductoColeccion.coleccion_id == Coleccion.id,
                )
                .where(
                    ProductoColeccion.producto_id == Producto.id,
                    ProductoColeccion.coleccion_id == coleccion_id,
                    Coleccion.estado.is_(True),
                )
                .exists()
            )
        if sucursal_id is not None:
            statement = statement.where(
                select(Inventario.id)
                .join(
                    VarianteProducto,
                    Inventario.variante_producto_id == VarianteProducto.id,
                )
                .join(Sucursal, Inventario.sucursal_id == Sucursal.id)
                .where(
                    VarianteProducto.producto_id == Producto.id,
                    VarianteProducto.estado.is_(True),
                    Inventario.sucursal_id == sucursal_id,
                    Sucursal.estado.is_(True),
                )
                .exists()
            )
        if con_stock:
            statement = statement.where(
                select(Inventario.id)
                .join(
                    VarianteProducto,
                    Inventario.variante_producto_id == VarianteProducto.id,
                )
                .join(Sucursal, Inventario.sucursal_id == Sucursal.id)
                .where(
                    VarianteProducto.producto_id == Producto.id,
                    VarianteProducto.estado.is_(True),
                    Sucursal.estado.is_(True),
                    Inventario.stock_actual - Inventario.stock_reservado > 0,
                )
                .exists()
            )
        statement = statement.order_by(Producto.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def listar_admin(
        db: Session,
        *,
        buscar: str | None = None,
        categoria_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Producto]:
        statement = select(Producto).options(joinedload(Producto.categoria))
        if buscar:
            patron = f"%{buscar.strip()}%"
            statement = statement.where(
                or_(
                    Producto.nombre.ilike(patron),
                    Producto.categoria.has(Categoria.nombre.ilike(patron)),
                )
            )
        if categoria_id is not None:
            statement = statement.where(Producto.categoria_id == categoria_id)
        if estado is not None:
            statement = statement.where(Producto.estado.is_(estado))
        statement = statement.order_by(Producto.id)
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

    @staticmethod
    def obtener_admin_por_id(db: Session, producto_id: int) -> Producto | None:
        """Detalle administrativo: sin filtrar por estado."""
        statement = (
            select(Producto)
            .options(joinedload(Producto.categoria))
            .where(Producto.id == producto_id)
        )
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        categoria_id: int,
        nombre: str,
        descripcion: str | None,
        precio: Decimal,
    ) -> Producto:
        producto = Producto(
            categoria_id=categoria_id,
            nombre=nombre,
            descripcion=descripcion,
            precio=precio,
            estado=True,
        )
        db.add(producto)
        db.flush()
        return producto


class TallaRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Talla]:
        statement = select(Talla)
        if buscar:
            statement = statement.where(Talla.nombre.ilike(f"%{buscar.strip()}%"))
        if estado is not None:
            statement = statement.where(Talla.estado.is_(estado))
        statement = statement.order_by(Talla.nombre, Talla.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, talla_id: int) -> Talla | None:
        return db.get(Talla, talla_id)

    @staticmethod
    def obtener_activa_por_id(db: Session, talla_id: int) -> Talla | None:
        statement = select(Talla).where(
            Talla.id == talla_id,
            Talla.estado.is_(True),
        )
        return db.scalar(statement)

    @staticmethod
    def buscar_por_nombre_normalizado(db: Session, nombre: str) -> Talla | None:
        statement = select(Talla).where(
            func.lower(Talla.nombre) == nombre.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def crear(db: Session, *, nombre: str) -> Talla:
        talla = Talla(nombre=nombre, estado=True)
        db.add(talla)
        db.flush()
        return talla

    @staticmethod
    def contar_variantes_activas(db: Session, talla_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(VarianteProducto)
            .where(
                VarianteProducto.talla_id == talla_id,
                VarianteProducto.estado.is_(True),
            )
        )
        return int(db.scalar(statement) or 0)


class ColorRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Color]:
        statement = select(Color)
        if buscar:
            statement = statement.where(Color.nombre.ilike(f"%{buscar.strip()}%"))
        if estado is not None:
            statement = statement.where(Color.estado.is_(estado))
        statement = statement.order_by(Color.nombre, Color.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, color_id: int) -> Color | None:
        return db.get(Color, color_id)

    @staticmethod
    def obtener_activa_por_id(db: Session, color_id: int) -> Color | None:
        statement = select(Color).where(
            Color.id == color_id,
            Color.estado.is_(True),
        )
        return db.scalar(statement)

    @staticmethod
    def buscar_por_nombre_normalizado(db: Session, nombre: str) -> Color | None:
        statement = select(Color).where(
            func.lower(Color.nombre) == nombre.strip().lower()
        )
        return db.scalar(statement)

    @staticmethod
    def crear(db: Session, *, nombre: str) -> Color:
        color = Color(nombre=nombre, estado=True)
        db.add(color)
        db.flush()
        return color

    @staticmethod
    def contar_variantes_activas(db: Session, color_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(VarianteProducto)
            .where(
                VarianteProducto.color_id == color_id,
                VarianteProducto.estado.is_(True),
            )
        )
        return int(db.scalar(statement) or 0)


class VarianteProductoRepository:
    @staticmethod
    def listar_por_producto(
        db: Session,
        producto_id: int,
        *,
        estado: bool | None = None,
    ) -> list[VarianteProducto]:
        statement = (
            select(VarianteProducto)
            .options(
                joinedload(VarianteProducto.talla),
                joinedload(VarianteProducto.color),
            )
            .where(VarianteProducto.producto_id == producto_id)
        )
        if estado is not None:
            statement = statement.where(VarianteProducto.estado.is_(estado))
        statement = statement.order_by(VarianteProducto.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(
        db: Session, variante_id: int
    ) -> VarianteProducto | None:
        statement = (
            select(VarianteProducto)
            .options(
                joinedload(VarianteProducto.talla),
                joinedload(VarianteProducto.color),
                joinedload(VarianteProducto.producto),
            )
            .where(VarianteProducto.id == variante_id)
        )
        return db.scalar(statement)

    @staticmethod
    def buscar_combinacion(
        db: Session,
        *,
        producto_id: int,
        talla_id: int,
        color_id: int,
        excluir_id: int | None = None,
    ) -> VarianteProducto | None:
        statement = select(VarianteProducto).where(
            VarianteProducto.producto_id == producto_id,
            VarianteProducto.talla_id == talla_id,
            VarianteProducto.color_id == color_id,
        )
        if excluir_id is not None:
            statement = statement.where(VarianteProducto.id != excluir_id)
        return db.scalar(statement)

    @staticmethod
    def buscar_por_sku_normalizado(
        db: Session, sku: str, excluir_id: int | None = None
    ) -> VarianteProducto | None:
        statement = select(VarianteProducto).where(
            func.upper(VarianteProducto.sku) == sku.strip().upper()
        )
        if excluir_id is not None:
            statement = statement.where(VarianteProducto.id != excluir_id)
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        producto_id: int,
        talla_id: int,
        color_id: int,
        sku: str,
    ) -> VarianteProducto:
        variante = VarianteProducto(
            producto_id=producto_id,
            talla_id=talla_id,
            color_id=color_id,
            sku=sku,
            estado=True,
        )
        db.add(variante)
        db.flush()
        return variante


class RecursoProductoRepository:
    @staticmethod
    def listar_por_producto(
        db: Session,
        producto_id: int,
        *,
        estado: bool | None = None,
    ) -> list[RecursoProducto]:
        statement = (
            select(RecursoProducto)
            .options(joinedload(RecursoProducto.color))
            .where(RecursoProducto.producto_id == producto_id)
        )
        if estado is not None:
            statement = statement.where(RecursoProducto.estado.is_(estado))
        statement = statement.order_by(
            RecursoProducto.es_principal.desc(), RecursoProducto.id
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(
        db: Session, recurso_id: int
    ) -> RecursoProducto | None:
        statement = (
            select(RecursoProducto)
            .options(joinedload(RecursoProducto.color))
            .where(RecursoProducto.id == recurso_id)
        )
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        producto_id: int,
        color_id: int | None,
        tipo: str,
        url: str,
        es_principal: bool,
    ) -> RecursoProducto:
        recurso = RecursoProducto(
            producto_id=producto_id,
            color_id=color_id,
            tipo=tipo,
            url=url,
            es_principal=es_principal,
            estado=True,
        )
        db.add(recurso)
        db.flush()
        return recurso

    @staticmethod
    def desmarcar_principales(
        db: Session,
        *,
        producto_id: int,
        color_id: int | None,
        excluir_id: int | None = None,
    ) -> None:
        """Quita es_principal a los recursos activos del mismo producto+color.

        Necesario para no violar el indice unico parcial
        uq_recurso_producto_principal.
        """
        condicion_color = (
            RecursoProducto.color_id.is_(None)
            if color_id is None
            else RecursoProducto.color_id == color_id
        )
        statement = (
            update(RecursoProducto)
            .where(
                RecursoProducto.producto_id == producto_id,
                condicion_color,
                RecursoProducto.es_principal.is_(True),
            )
            .values(es_principal=False)
        )
        if excluir_id is not None:
            statement = statement.where(RecursoProducto.id != excluir_id)
        db.execute(statement)
