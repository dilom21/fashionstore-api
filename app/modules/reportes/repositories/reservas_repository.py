"""Consultas agregadas de reservas para CU28 (solo lectura).

Conversion Reserva -> Venta usando la relacion REAL ``Venta.reserva_id``:

- ``reservas_atendidas``: reservas en estado ATENDIDA del periodo.
- ``reservas_convertidas_en_venta``: reservas ATENDIDA del periodo que tienen
  una Venta COMPLETADA asociada por ``Venta.reserva_id``.
- ``tasa_conversion = convertidas / atendidas * 100`` (0 si atendidas = 0).

El periodo se toma por ``reserva.fecha_reserva``.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import Venta

ESTADOS_RESERVA = (
    "PENDIENTE",
    "CONFIRMADA",
    "ATENDIDA",
    "CANCELADA",
    "VENCIDA",
)
ESTADO_ATENDIDA = "ATENDIDA"
ESTADO_VENTA_COMPLETADA = "COMPLETADA"


class ReservasReporteRepository:
    @staticmethod
    def _condiciones(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Reserva.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Reserva.fecha_reserva >= desde)
        if hasta is not None:
            condiciones.append(Reserva.fecha_reserva < hasta)
        return condiciones

    @staticmethod
    def kpis(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        condiciones = ReservasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        total = int(
            db.scalar(
                select(func.count()).select_from(Reserva).where(*condiciones)
            )
            or 0
        )
        unidades = int(
            db.scalar(
                select(func.coalesce(func.sum(DetalleReserva.cantidad), 0))
                .select_from(DetalleReserva)
                .join(Reserva, Reserva.id == DetalleReserva.reserva_id)
                .where(*condiciones)
            )
            or 0
        )
        atendidas = int(
            db.scalar(
                select(func.count())
                .select_from(Reserva)
                .where(Reserva.estado == ESTADO_ATENDIDA, *condiciones)
            )
            or 0
        )
        convertidas = int(
            db.scalar(
                select(func.count(func.distinct(Reserva.id)))
                .select_from(Reserva)
                .join(Venta, Venta.reserva_id == Reserva.id)
                .where(
                    Reserva.estado == ESTADO_ATENDIDA,
                    Venta.estado == ESTADO_VENTA_COMPLETADA,
                    *condiciones,
                )
            )
            or 0
        )
        return {
            "total_reservas": total,
            "cantidad_total_unidades": unidades,
            "reservas_atendidas": atendidas,
            "reservas_convertidas_en_venta": convertidas,
        }

    @staticmethod
    def por_estado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = ReservasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(
            func.sum(
                select(func.coalesce(func.sum(DetalleReserva.cantidad), 0))
                .where(DetalleReserva.reserva_id == Reserva.id)
                .scalar_subquery()
            ),
            0,
        )
        statement = (
            select(Reserva.estado, func.count(Reserva.id), unidades)
            .where(*condiciones)
            .group_by(Reserva.estado)
            .order_by(func.count(Reserva.id).desc())
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
        condiciones = ReservasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(DetalleReserva.cantidad), 0)
        statement = (
            select(
                Sucursal.id,
                Sucursal.nombre,
                func.count(func.distinct(Reserva.id)),
                unidades,
            )
            .select_from(Reserva)
            .join(Sucursal, Sucursal.id == Reserva.sucursal_id)
            .outerjoin(
                DetalleReserva, DetalleReserva.reserva_id == Reserva.id
            )
            .where(*condiciones)
            .group_by(Sucursal.id, Sucursal.nombre)
            .order_by(func.count(func.distinct(Reserva.id)).desc())
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
        condiciones = ReservasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        periodo = func.date_trunc(unidad, Reserva.fecha_reserva).label(
            "periodo"
        )
        statement = (
            select(periodo, func.count(Reserva.id))
            .where(*condiciones)
            .group_by(periodo)
            .order_by(periodo)
        )
        return list(db.execute(statement).all())
