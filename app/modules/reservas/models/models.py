from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


class Reserva(Base):
    """Reserva de prendas de un cliente en una sucursal (CU16).

    La tabla real ``reserva`` posee:
    - CHECK estado IN (PENDIENTE, CONFIRMADA, ATENDIDA, CANCELADA, VENCIDA)
    - CHECK fecha_atencion >= fecha_reserva
    - UNIQUE (carrito_id): un carrito origina como maximo UNA reserva
    - FK carrito_id -> carrito(id)

    Una reserva NO es una venta: el procedimiento
    sp_crear_reserva_desde_carrito incrementa inventario.stock_reservado y
    marca el carrito como CONVERTIDO; stock_actual nunca cambia.
    """

    __tablename__ = "reserva"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), nullable=False)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("sucursal.id"), nullable=False)
    carrito_id: Mapped[int | None] = mapped_column(
        ForeignKey("carrito.id"), nullable=True
    )
    fecha_reserva: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fecha_atencion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDIENTE"
    )
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    sucursal: Mapped["Sucursal"] = relationship()
    cliente: Mapped["Cliente"] = relationship()
    detalles: Mapped[list["DetalleReserva"]] = relationship(
        back_populates="reserva",
        order_by="DetalleReserva.id",
    )


class DetalleReserva(Base):
    """Linea de reserva: fila de inventario reservada y su cantidad.

    La tabla real ``detalle_reserva`` posee:
    - CHECK cantidad > 0
    - UNIQUE (reserva_id, inventario_id)
    - trigger fn_validar_detalle_reserva_sucursal (misma sucursal)
    """

    __tablename__ = "detalle_reserva"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reserva_id: Mapped[int] = mapped_column(ForeignKey("reserva.id"), nullable=False)
    inventario_id: Mapped[int] = mapped_column(
        ForeignKey("inventario.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)

    reserva: Mapped["Reserva"] = relationship(back_populates="detalles")
    inventario: Mapped["Inventario"] = relationship()
