"""Acceso a datos de CU22 - Pago electronico con Stripe.

Solo persistencia y bloqueos. Reutiliza ``PagoRepository`` para la insercion
generica de ``pago`` y anade las consultas propias de Stripe (por
``referencia_transaccion`` = PaymentIntent.id). Las reglas de negocio viven en
``services/electronico_service.py``.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.pagos.models.models import Pago
from app.modules.pagos.repositories.repository import PagoRepository

PASARELA_STRIPE = "STRIPE"
METODO_TARJETA = "TARJETA"

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_APROBADO = "APROBADO"
ESTADO_RECHAZADO = "RECHAZADO"
ESTADO_ANULADO = "ANULADO"
ESTADO_REEMBOLSADO = "REEMBOLSADO"


class PagoStripeRepository:
    @staticmethod
    def obtener_ultimo_pago_stripe(db: Session, venta_id: int) -> Pago | None:
        statement = (
            select(Pago)
            .where(
                Pago.venta_id == venta_id,
                Pago.pasarela == PASARELA_STRIPE,
            )
            .order_by(Pago.id.desc())
        )
        return db.scalars(statement).first()

    @staticmethod
    def bloquear_pago_por_referencia(
        db: Session, referencia: str
    ) -> Pago | None:
        """SELECT ... FOR UPDATE sobre el pago del PaymentIntent.

        Serializa webhooks duplicados/concurrentes del mismo PaymentIntent.
        """
        statement = (
            select(Pago)
            .where(Pago.referencia_transaccion == referencia)
            .with_for_update()
        )
        return db.scalars(statement).first()

    @staticmethod
    def contar_pagos_stripe(db: Session, venta_id: int) -> int:
        """Cantidad de intentos Stripe de una venta (para la idempotency key)."""
        statement = (
            select(func.count())
            .select_from(Pago)
            .where(
                Pago.venta_id == venta_id,
                Pago.pasarela == PASARELA_STRIPE,
            )
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def crear_pago_stripe(
        db: Session,
        *,
        venta_id: int,
        monto: Decimal,
        fecha_hora: datetime,
        referencia_transaccion: str,
    ) -> Pago:
        return PagoRepository.crear_pago(
            db,
            venta_id=venta_id,
            monto=monto,
            metodo=METODO_TARJETA,
            estado=ESTADO_PENDIENTE,
            fecha_hora=fecha_hora,
            referencia_transaccion=referencia_transaccion,
            pasarela=PASARELA_STRIPE,
        )
