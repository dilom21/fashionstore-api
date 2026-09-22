"""Consultas agregadas de devoluciones para CU28 (solo lectura).

- "Unidades devueltas" y "valor referencial" del RESUMEN cuentan solo
  devoluciones ``COMPLETADA`` (producto ya reincorporado al inventario).
- ``valor_referencial = SUM(DetalleDevolucion.cantidad *
  DetalleVenta.precio_unitario)``. NO es un reembolso financiero.
- El alcance por sucursal sale de ``Venta.sucursal_id``.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.devoluciones.models.models import (
    DetalleDevolucion,
    Devolucion,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADOS_DEVOLUCION = ("SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA")
ESTADO_COMPLETADA = "COMPLETADA"


class DevolucionesReporteRepository:
    @staticmethod
    def _condiciones(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Venta.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Devolucion.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Devolucion.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def _valor_referencial():
        return func.coalesce(
            func.sum(
                DetalleDevolucion.cantidad * DetalleVenta.precio_unitario
            ),
            0,
        )

    @staticmethod
    def resumen(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        total = int(
            db.scalar(
                select(func.count())
                .select_from(Devolucion)
                .join(Venta, Venta.id == Devolucion.venta_id)
                .where(*condiciones)
            )
            or 0
        )
        completadas = int(
            db.scalar(
                select(func.count())
                .select_from(Devolucion)
                .join(Venta, Venta.id == Devolucion.venta_id)
                .where(Devolucion.estado == ESTADO_COMPLETADA, *condiciones)
            )
            or 0
        )
        unidades = int(
            db.scalar(
                select(func.coalesce(func.sum(DetalleDevolucion.cantidad), 0))
                .select_from(DetalleDevolucion)
                .join(
                    Devolucion,
                    Devolucion.id == DetalleDevolucion.devolucion_id,
                )
                .join(Venta, Venta.id == Devolucion.venta_id)
                .where(Devolucion.estado == ESTADO_COMPLETADA, *condiciones)
            )
            or 0
        )
        valor = db.scalar(
            select(DevolucionesReporteRepository._valor_referencial())
            .select_from(DetalleDevolucion)
            .join(
                Devolucion, Devolucion.id == DetalleDevolucion.devolucion_id
            )
            .join(
                DetalleVenta,
                DetalleVenta.id == DetalleDevolucion.detalle_venta_id,
            )
            .join(Venta, Venta.id == Devolucion.venta_id)
            .where(Devolucion.estado == ESTADO_COMPLETADA, *condiciones)
        )
        return {
            "total_devoluciones": total,
            "completadas": completadas,
            "unidades_devueltas": unidades,
            "valor_referencial": valor or 0,
        }

    @staticmethod
    def por_estado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Devolucion.estado,
                func.count(func.distinct(Devolucion.id)),
                func.coalesce(func.sum(DetalleDevolucion.cantidad), 0),
                DevolucionesReporteRepository._valor_referencial(),
            )
            .select_from(Devolucion)
            .join(Venta, Venta.id == Devolucion.venta_id)
            .outerjoin(
                DetalleDevolucion,
                DetalleDevolucion.devolucion_id == Devolucion.id,
            )
            .outerjoin(
                DetalleVenta,
                DetalleVenta.id == DetalleDevolucion.detalle_venta_id,
            )
            .where(*condiciones)
            .group_by(Devolucion.estado)
            .order_by(func.count(func.distinct(Devolucion.id)).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_sucursal(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Sucursal.id,
                Sucursal.nombre,
                func.count(func.distinct(Devolucion.id)),
                func.coalesce(func.sum(DetalleDevolucion.cantidad), 0),
            )
            .select_from(Devolucion)
            .join(Venta, Venta.id == Devolucion.venta_id)
            .join(Sucursal, Sucursal.id == Venta.sucursal_id)
            .outerjoin(
                DetalleDevolucion,
                DetalleDevolucion.devolucion_id == Devolucion.id,
            )
            .where(Devolucion.estado == ESTADO_COMPLETADA, *condiciones)
            .group_by(Sucursal.id, Sucursal.nombre)
            .order_by(func.count(func.distinct(Devolucion.id)).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def evolucion(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        agrupacion: str = "DIA",
    ) -> list:
        unidad = "day" if agrupacion.upper() == "DIA" else "month"
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        periodo = func.date_trunc(unidad, Devolucion.fecha_hora).label(
            "periodo"
        )
        statement = (
            select(periodo, func.count(Devolucion.id))
            .join(Venta, Venta.id == Devolucion.venta_id)
            .where(*condiciones)
            .group_by(periodo)
            .order_by(periodo)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def productos_mas_devueltos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        """Solo devoluciones COMPLETADA (producto ya reincorporado)."""
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(DetalleDevolucion.cantidad), 0)
        valor = DevolucionesReporteRepository._valor_referencial()
        statement = (
            select(Producto.id, Producto.nombre, unidades, valor)
            .select_from(DetalleDevolucion)
            .join(
                Devolucion, Devolucion.id == DetalleDevolucion.devolucion_id
            )
            .join(Venta, Venta.id == Devolucion.venta_id)
            .join(
                DetalleVenta,
                DetalleVenta.id == DetalleDevolucion.detalle_venta_id,
            )
            .join(Inventario, Inventario.id == DetalleVenta.inventario_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .where(Devolucion.estado == ESTADO_COMPLETADA, *condiciones)
            .group_by(Producto.id, Producto.nombre)
            .order_by(unidades.desc(), Producto.id)
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def motivos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        """Agrupa el texto real de ``devolucion.motivo`` (sin modificarlo)."""
        condiciones = DevolucionesReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Devolucion.motivo,
                func.count(func.distinct(Devolucion.id)),
                func.coalesce(func.sum(DetalleDevolucion.cantidad), 0),
            )
            .select_from(Devolucion)
            .join(Venta, Venta.id == Devolucion.venta_id)
            .outerjoin(
                DetalleDevolucion,
                DetalleDevolucion.devolucion_id == Devolucion.id,
            )
            .where(*condiciones)
            .group_by(Devolucion.motivo)
            .order_by(func.count(func.distinct(Devolucion.id)).desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

