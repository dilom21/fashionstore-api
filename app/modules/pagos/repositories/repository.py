"""Acceso a datos de CU21 - Registrar pago presencial.

Solo persistencia e invocacion del procedimiento existente. Las reglas de
negocio (roles, alcance, estado, monto) viven en el service.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.pagos.models.models import Pago
from app.modules.reservas.models.models import Reserva
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_APROBADO = "APROBADO"

# Autoridad transaccional de PostgreSQL: valida pago APROBADO, descuenta
# stock_actual, consume/libera stock_reservado, registra SALIDA_VENTA y
# LIBERACION_RESERVA, marca la reserva ATENDIDA y la venta COMPLETADA.
SQL_CONFIRMAR_VENTA = text(
    "CALL public.sp_confirmar_venta(:venta_id, :usuario_id)"
)


class PagoRepository:
    @staticmethod
    def obtener_venta(db: Session, venta_id: int) -> Venta | None:
        statement = select(Venta).where(Venta.id == venta_id)
        return db.scalars(statement).first()

    @staticmethod
    def bloquear_venta(db: Session, venta_id: int) -> Venta | None:
        """SELECT ... FOR UPDATE sobre venta (serializa pagos concurrentes).

        Sin relaciones a proposito: PostgreSQL no permite FOR UPDATE sobre el
        lado opcional de un outer join y aqui solo se necesita la fila base.
        """
        statement = (
            select(Venta).where(Venta.id == venta_id).with_for_update()
        )
        return db.scalars(statement).first()

    @staticmethod
    def contar_detalles(db: Session, venta_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(DetalleVenta)
            .where(DetalleVenta.venta_id == venta_id)
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def obtener_pago_aprobado(db: Session, venta_id: int) -> Pago | None:
        statement = (
            select(Pago)
            .where(
                Pago.venta_id == venta_id,
                Pago.estado == ESTADO_APROBADO,
            )
            .order_by(Pago.id.desc())
        )
        return db.scalars(statement).first()

    @staticmethod
    def crear_pago(
        db: Session,
        *,
        venta_id: int,
        monto: Decimal,
        metodo: str,
        estado: str,
        fecha_hora: datetime,
        referencia_transaccion: str | None = None,
        pasarela: str | None = None,
    ) -> Pago:
        pago = Pago(
            venta_id=venta_id,
            monto=monto,
            metodo=metodo,
            estado=estado,
            fecha_hora=fecha_hora,
            referencia_transaccion=referencia_transaccion,
            pasarela=pasarela,
        )
        db.add(pago)
        db.flush()
        return pago

    @staticmethod
    def confirmar_venta(db: Session, venta_id: int, usuario_id: int) -> None:
        db.execute(
            SQL_CONFIRMAR_VENTA,
            {"venta_id": venta_id, "usuario_id": usuario_id},
        )

    @staticmethod
    def obtener_reserva(db: Session, reserva_id: int) -> Reserva | None:
        return db.get(Reserva, reserva_id)
