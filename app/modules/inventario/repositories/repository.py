from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.models.models import (
    Color,
    Producto,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal

DISPONIBILIDAD_CON_STOCK = "CON_STOCK"
DISPONIBILIDAD_SIN_STOCK = "SIN_STOCK"
DISPONIBILIDAD_STOCK_BAJO = "STOCK_BAJO"

# Regla de negocio CU13: stock bajo cuando 0 < stock disponible <= umbral.
UMBRAL_STOCK_BAJO = 5


class InventarioRepository:
    @staticmethod
    def _consulta_base():
        return select(Inventario).options(
            joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.producto)
            .joinedload(Producto.categoria),
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
        db: Session,
        producto_id: int,
        *,
        sucursal_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> list[Inventario]:
        """Disponibilidad publica CU09.

        Devuelve unicamente stock disponible > 0 (stock_actual - stock_reservado)
        de producto, sucursal, variante, talla y color activos.
        """
        statement = (
            InventarioRepository._consulta_base()
            .join(Inventario.variante_producto)
            .join(VarianteProducto.talla)
            .join(VarianteProducto.color)
            .join(Inventario.sucursal)
            .where(
                VarianteProducto.producto_id == producto_id,
                VarianteProducto.estado.is_(True),
                Talla.estado.is_(True),
                Color.estado.is_(True),
                Sucursal.estado.is_(True),
                Inventario.stock_actual - Inventario.stock_reservado > 0,
            )
        )
        if sucursal_id is not None:
            statement = statement.where(Inventario.sucursal_id == sucursal_id)
        if talla_id is not None:
            statement = statement.where(VarianteProducto.talla_id == talla_id)
        if color_id is not None:
            statement = statement.where(VarianteProducto.color_id == color_id)
        if temporada_id is not None:
            statement = statement.where(Inventario.temporada_id == temporada_id)
        statement = statement.order_by(
            Inventario.sucursal_id, Inventario.variante_producto_id
        )
        return list(db.scalars(statement).all())

    # ------------------------------------------------------------------
    # CU13 - Consultar inventario por sucursal
    # ------------------------------------------------------------------

    @staticmethod
    def _condiciones_consulta(
        *,
        sucursal_id: int | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        disponibilidad: str | None = None,
    ) -> list:
        """Condiciones reutilizables por el listado y el conteo (evita drift)."""
        condiciones = []

        if sucursal_id is not None:
            condiciones.append(Inventario.sucursal_id == sucursal_id)
        if producto_id is not None:
            condiciones.append(Producto.id == producto_id)

        termino = (producto or "").strip()
        if termino:
            condiciones.append(Producto.nombre.ilike(f"%{termino}%"))

        if categoria_id is not None:
            condiciones.append(Producto.categoria_id == categoria_id)
        if talla_id is not None:
            condiciones.append(VarianteProducto.talla_id == talla_id)
        if color_id is not None:
            condiciones.append(VarianteProducto.color_id == color_id)
        if temporada_id is not None:
            condiciones.append(Inventario.temporada_id == temporada_id)

        disponible = Inventario.stock_actual - Inventario.stock_reservado
        if disponibilidad == DISPONIBILIDAD_CON_STOCK:
            condiciones.append(disponible > 0)
        elif disponibilidad == DISPONIBILIDAD_SIN_STOCK:
            condiciones.append(disponible <= 0)
        elif disponibilidad == DISPONIBILIDAD_STOCK_BAJO:
            condiciones.append(disponible > 0)
            condiciones.append(disponible <= UMBRAL_STOCK_BAJO)

        return condiciones

    @staticmethod
    def consultar(
        db: Session,
        *,
        sucursal_id: int | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        disponibilidad: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Inventario], int]:
        """Listado paginado de inventario con filtros y total de coincidencias."""
        condiciones = InventarioRepository._condiciones_consulta(
            sucursal_id=sucursal_id,
            producto=producto,
            producto_id=producto_id,
            categoria_id=categoria_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
            disponibilidad=disponibilidad,
        )

        conteo = (
            select(func.count())
            .select_from(Inventario)
            .join(Inventario.variante_producto)
            .join(VarianteProducto.producto)
            .where(*condiciones)
        )
        total = int(db.scalar(conteo) or 0)

        statement = (
            InventarioRepository._consulta_base()
            .join(Inventario.variante_producto)
            .join(VarianteProducto.producto)
            .where(*condiciones)
            .order_by(Inventario.sucursal_id, Inventario.id)
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(statement).all()), total

    @staticmethod
    def obtener_sucursal_activa(db: Session, sucursal_id: int) -> Sucursal | None:
        statement = select(Sucursal).where(
            Sucursal.id == sucursal_id,
            Sucursal.estado.is_(True),
        )
        return db.scalar(statement)

    # ------------------------------------------------------------------
    # CU14 - Consultar movimientos de inventario (Kardex, solo lectura)
    # ------------------------------------------------------------------

    @staticmethod
    def _condiciones_movimientos(
        *,
        sucursal_id: int | None = None,
        tipo: str | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        variante_producto_id: int | None = None,
        temporada_id: int | None = None,
        usuario_id: int | None = None,
        referencia_tipo: str | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
    ) -> list:
        """Condiciones compartidas por listado y conteo (evita drift)."""
        condiciones = []

        if sucursal_id is not None:
            condiciones.append(Inventario.sucursal_id == sucursal_id)
        if tipo is not None:
            condiciones.append(MovimientoInventario.tipo == tipo)

        termino = (producto or "").strip()
        if termino:
            condiciones.append(Producto.nombre.ilike(f"%{termino}%"))

        if producto_id is not None:
            condiciones.append(Producto.id == producto_id)
        if variante_producto_id is not None:
            condiciones.append(VarianteProducto.id == variante_producto_id)
        if temporada_id is not None:
            condiciones.append(Inventario.temporada_id == temporada_id)
        if usuario_id is not None:
            condiciones.append(MovimientoInventario.usuario_id == usuario_id)
        if referencia_tipo is not None:
            condiciones.append(
                MovimientoInventario.referencia_tipo == referencia_tipo
            )
        if fecha_desde is not None:
            condiciones.append(MovimientoInventario.fecha_hora >= fecha_desde)
        if fecha_hasta is not None:
            condiciones.append(MovimientoInventario.fecha_hora <= fecha_hasta)

        return condiciones

    @staticmethod
    def _base_movimientos():
        """Select de entidades relacionadas (un solo query, sin N+1)."""
        return (
            select(
                MovimientoInventario,
                Inventario,
                Sucursal,
                VarianteProducto,
                Producto,
                Talla,
                Color,
                Temporada,
                Usuario,
            )
            .join(Inventario, Inventario.id == MovimientoInventario.inventario_id)
            .join(Sucursal, Sucursal.id == Inventario.sucursal_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .join(Talla, Talla.id == VarianteProducto.talla_id)
            .join(Color, Color.id == VarianteProducto.color_id)
            .join(Temporada, Temporada.id == Inventario.temporada_id)
            .outerjoin(Usuario, Usuario.id == MovimientoInventario.usuario_id)
        )

    @staticmethod
    def consultar_movimientos(
        db: Session,
        *,
        sucursal_id: int | None = None,
        tipo: str | None = None,
        producto: str | None = None,
        producto_id: int | None = None,
        variante_producto_id: int | None = None,
        temporada_id: int | None = None,
        usuario_id: int | None = None,
        referencia_tipo: str | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
        limit: int = 10,
        offset: int = 0,
    ):
        """Listado paginado del Kardex + total con los mismos filtros."""
        condiciones = InventarioRepository._condiciones_movimientos(
            sucursal_id=sucursal_id,
            tipo=tipo,
            producto=producto,
            producto_id=producto_id,
            variante_producto_id=variante_producto_id,
            temporada_id=temporada_id,
            usuario_id=usuario_id,
            referencia_tipo=referencia_tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        conteo = select(func.count()).select_from(
            select(MovimientoInventario.id)
            .join(Inventario, Inventario.id == MovimientoInventario.inventario_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .outerjoin(Usuario, Usuario.id == MovimientoInventario.usuario_id)
            .where(*condiciones)
            .subquery()
        )
        total = int(db.scalar(conteo) or 0)

        statement = (
            InventarioRepository._base_movimientos()
            .where(*condiciones)
            .order_by(
                MovimientoInventario.fecha_hora.desc(),
                MovimientoInventario.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return db.execute(statement).all(), total


