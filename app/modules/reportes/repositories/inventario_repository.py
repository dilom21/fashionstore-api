"""Consultas agregadas de inventario y movimientos para CU28 (solo lectura).

``stock_disponible = stock_actual - stock_reservado`` (nunca negativo, tanto
por fila como en el total: se usa GREATEST). No existe ``stock_minimo``: el
"stock bajo" es solo un criterio de reporte con umbral parametrizable.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Color,
    Producto,
    Talla,
    VarianteProducto,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.temporadas_colecciones.models.models import Temporada

TIPOS_MOVIMIENTO = (
    "ENTRADA_COMPRA",
    "SALIDA_VENTA",
    "RESERVA",
    "LIBERACION_RESERVA",
    "DEVOLUCION",
    "AJUSTE_ENTRADA",
    "AJUSTE_SALIDA",
    "TRANSFERENCIA_ENTRADA",
    "TRANSFERENCIA_SALIDA",
)


class InventarioReporteRepository:
    @staticmethod
    def _condiciones_sucursal(sucursal_id: int | None) -> list:
        if sucursal_id is None:
            return []
        return [Inventario.sucursal_id == sucursal_id]

    @staticmethod
    def resumen(
        db: Session, *, sucursal_id: int | None, umbral_stock_bajo: int
    ) -> dict:
        """Sumas y conteos en una sola consulta agregada."""
        disponible = Inventario.stock_actual - Inventario.stock_reservado
        fila = db.execute(
            select(
                func.coalesce(func.sum(Inventario.stock_actual), 0),
                func.coalesce(func.sum(Inventario.stock_reservado), 0),
                func.coalesce(func.sum(func.greatest(disponible, 0)), 0),
                func.count().filter(disponible <= 0),
                func.count().filter(
                    (disponible > 0) & (disponible <= umbral_stock_bajo)
                ),
            )
            .select_from(Inventario)
            .where(*InventarioReporteRepository._condiciones_sucursal(sucursal_id))
        ).one()
        return {
            "stock_actual_total": int(fila[0] or 0),
            "stock_reservado_total": int(fila[1] or 0),
            "stock_disponible_total": int(fila[2] or 0),
            "variantes_agotadas": int(fila[3] or 0),
            "variantes_stock_bajo": int(fila[4] or 0),
            "umbral_stock_bajo": int(umbral_stock_bajo),
        }

    @staticmethod
    def movimientos_por_tipo(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        """COUNT + SUM por tipo real de ``movimiento_inventario``."""
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Inventario.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(MovimientoInventario.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(MovimientoInventario.fecha_hora < hasta)
        statement = (
            select(
                MovimientoInventario.tipo,
                func.count(MovimientoInventario.id),
                func.coalesce(func.sum(MovimientoInventario.cantidad), 0),
            )
            .join(
                Inventario,
                Inventario.id == MovimientoInventario.inventario_id,
            )
            .where(*condiciones)
            .group_by(MovimientoInventario.tipo)
            .order_by(MovimientoInventario.tipo)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def listar(
        db: Session,
        *,
        sucursal_id: int | None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Tabla de existencias paginada en SQL, con catalogo real."""
        disponible = (
            Inventario.stock_actual - Inventario.stock_reservado
        ).label("stock_disponible")
        statement = (
            select(
                Inventario.id,
                Sucursal.nombre,
                Producto.nombre,
                VarianteProducto.sku,
                Talla.nombre,
                Color.nombre,
                Temporada.nombre,
                Inventario.stock_actual,
                Inventario.stock_reservado,
                disponible,
                Inventario.fecha_actualizacion,
            )
            .join(Sucursal, Sucursal.id == Inventario.sucursal_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .join(Talla, Talla.id == VarianteProducto.talla_id)
            .join(Color, Color.id == VarianteProducto.color_id)
            .join(Temporada, Temporada.id == Inventario.temporada_id)
            .where(*InventarioReporteRepository._condiciones_sucursal(sucursal_id))
            .order_by(Sucursal.nombre, Inventario.id)
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def contar(db: Session, *, sucursal_id: int | None) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(Inventario)
                .where(
                    *InventarioReporteRepository._condiciones_sucursal(
                        sucursal_id
                    )
                )
            )
            or 0
        )

