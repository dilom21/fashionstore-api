"""Consultas agregadas de pagos para CU28 (solo lectura).

Una venta puede tener VARIOS intentos de pago: aqui se cuentan TRANSACCIONES
de pago, nunca ventas. No se expone ``referencia_transaccion`` ni ningun dato
de tarjeta/secretos de pasarela.

El alcance por sucursal se aplica via ``Venta.sucursal_id`` (el pago cuelga de
la venta).
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.pagos.models.models import Pago
from app.modules.ventas.models.models import Venta

ESTADOS_PAGO = ("PENDIENTE", "APROBADO", "RECHAZADO", "ANULADO", "REEMBOLSADO")
METODOS_PAGO = ("EFECTIVO", "TARJETA", "TRANSFERENCIA", "QR", "OTRO")
ESTADO_APROBADO = "APROBADO"
SIN_PASARELA = "SIN_PASARELA"

NOTA_PAGOS = (
    "Los pagos son transacciones de pago (1 venta puede tener varios "
    "intentos); no equivalen a cantidad de ventas."
)


class PagosReporteRepository:
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
            condiciones.append(Pago.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Pago.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def resumen(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        condiciones = PagosReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        total = db.execute(
            select(
                func.count(Pago.id),
                func.coalesce(func.sum(Pago.monto), 0),
            )
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(*condiciones)
        ).one()
        aprobado = db.scalar(
            select(func.coalesce(func.sum(Pago.monto), 0))
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(Pago.estado == ESTADO_APROBADO, *condiciones)
        )
        return {
            "cantidad_pagos": int(total[0] or 0),
            "monto_total_procesado": total[1] or 0,
            "monto_aprobado": aprobado or 0,
            "nota": NOTA_PAGOS,
        }

    @staticmethod
    def por_estado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = PagosReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Pago.estado,
                func.count(Pago.id),
                func.coalesce(func.sum(Pago.monto), 0),
            )
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(*condiciones)
            .group_by(Pago.estado)
            .order_by(func.count(Pago.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_metodo(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = PagosReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Pago.metodo,
                func.count(Pago.id),
                func.coalesce(func.sum(Pago.monto), 0),
            )
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(*condiciones)
            .group_by(Pago.metodo)
            .order_by(func.count(Pago.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_pasarela(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = PagosReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        etiqueta = func.coalesce(Pago.pasarela, SIN_PASARELA)
        statement = (
            select(
                etiqueta,
                func.count(Pago.id),
                func.coalesce(func.sum(Pago.monto), 0),
            )
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(*condiciones)
            .group_by(etiqueta)
            .order_by(func.count(Pago.id).desc())
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
        condiciones = PagosReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        periodo = func.date_trunc(unidad, Pago.fecha_hora).label("periodo")
        statement = (
            select(
                periodo,
                func.count(Pago.id),
                func.coalesce(func.sum(Pago.monto), 0),
            )
            .select_from(Pago)
            .join(Venta, Venta.id == Pago.venta_id)
            .where(*condiciones)
            .group_by(periodo)
            .order_by(periodo)
        )
        return list(db.execute(statement).all())
