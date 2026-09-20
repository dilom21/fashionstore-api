"""Mapping de la tabla real ``pago`` (CU21 - Registrar pago presencial).

Columnas y constraints verificadas contra la base real:

- ``ck_pago_metodo``  CHECK metodo IN (EFECTIVO, TARJETA, TRANSFERENCIA, QR, OTRO)
- ``ck_pago_estado``  CHECK estado IN (PENDIENTE, APROBADO, RECHAZADO, ANULADO,
  REEMBOLSADO)
- ``ck_pago_monto``   CHECK monto > 0
- ``uq_pago_referencia`` UNIQUE (referencia_transaccion) (los NULL no chocan)
- ``fk_pago_venta``   FK venta_id -> venta(id) ON DELETE CASCADE
- ``trg_bitacora_pago`` auditoria

No existe ``uq_pago_venta``: la relacion logica es 1 venta -> N pagos/intentos.
El id es BIGINT IDENTITY BY DEFAULT, por lo que no se envia desde Python.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Pago(Base):
    __tablename__ = "pago"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(
        ForeignKey("venta.id"), nullable=False
    )
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    metodo: Mapped[str] = mapped_column(String(30), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False)
    referencia_transaccion: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    pasarela: Mapped[str | None] = mapped_column(String(100), nullable=True)
