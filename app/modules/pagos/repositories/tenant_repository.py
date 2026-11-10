"""Lecturas tenant de pagos CU13. Las escrituras las hace el SP tenant."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.pagos.models.models import Pago
from app.modules.reservas.models.models import Reserva


class PagoTenantRepository:
    @staticmethod
    def obtener_pago(
        db: Session, *, empresa_id: int, pago_id: int
    ) -> Pago | None:
        if isinstance(pago_id, bool) or not isinstance(pago_id, int) or pago_id <= 0:
            return None
        return db.scalars(
            select(Pago).where(
                Pago.id == pago_id, Pago.empresa_id == empresa_id
            )
        ).first()

    @staticmethod
    def obtener_pago_aprobado(
        db: Session, *, empresa_id: int, venta_id: int
    ) -> Pago | None:
        return db.scalars(
            select(Pago)
            .where(
                Pago.empresa_id == empresa_id,
                Pago.venta_id == venta_id,
                Pago.estado == "APROBADO",
            )
            .order_by(Pago.id.desc())
        ).first()

    @staticmethod
    def obtener_reserva(
        db: Session, *, empresa_id: int, reserva_id: int
    ) -> Reserva | None:
        return db.scalars(
            select(Reserva).where(
                Reserva.id == reserva_id, Reserva.empresa_id == empresa_id
            )
        ).first()
