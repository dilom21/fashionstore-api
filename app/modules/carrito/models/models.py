from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Carrito(Base):
    """Carrito de compras de un cliente para una sucursal (CU15).

    La tabla real ``carrito`` posee:
    - CHECK estado IN (ACTIVO, EXPIRADO, ELIMINADO, CONVERTIDO)
    - indice unico parcial uq_carrito_activo_cliente_sucursal
      (cliente_id, sucursal_id) WHERE estado = 'ACTIVO'
    """

    __tablename__ = "carrito"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False
    )
    sucursal_id: Mapped[int] = mapped_column(
        ForeignKey("sucursal.id"), nullable=False
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVO")

    sucursal: Mapped["Sucursal"] = relationship()
    detalles: Mapped[list["DetalleCarrito"]] = relationship(
        back_populates="carrito",
        order_by="DetalleCarrito.id",
    )


class DetalleCarrito(Base):
    """Linea de carrito: una fila de inventario y su cantidad.

    La tabla real ``detalle_carrito`` posee:
    - CHECK cantidad > 0
    - UNIQUE (carrito_id, inventario_id)
    - trigger trg_actualizar_actividad_carrito (refresca fecha_actualizacion del carrito)
    - trigger trg_validar_sucursal_detalle_carrito (misma sucursal del carrito)
    """

    __tablename__ = "detalle_carrito"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    carrito_id: Mapped[int] = mapped_column(
        ForeignKey("carrito.id"), nullable=False
    )
    inventario_id: Mapped[int] = mapped_column(
        ForeignKey("inventario.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)

    carrito: Mapped["Carrito"] = relationship(back_populates="detalles")
    inventario: Mapped["Inventario"] = relationship()
