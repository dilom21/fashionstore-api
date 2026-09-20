from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


class Venta(Base):
    """Venta digital preparada por CU19 (aun no confirmada ni pagada).

    La tabla real ``venta`` posee:
    - CHECK canal IN (WEB, MOVIL, PRESENCIAL)
    - CHECK estado IN (PENDIENTE, PAGADA, COMPLETADA, CANCELADA, REEMBOLSADA)
    - CHECK total >= 0
    - UNIQUE (carrito_id): un carrito origina como maximo UNA venta
    - UNIQUE (reserva_id)
    - FK carrito_id -> carrito(id) ON DELETE SET NULL
    - trg_bitacora_venta (auditoria)
    - trg_proteger_eliminacion_venta

    CU19 solo crea ventas PENDIENTE con carrito_id. El pago (CU22) y la
    confirmacion definitiva de inventario (sp_confirmar_venta) son posteriores.
    """

    __tablename__ = "venta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(
        ForeignKey("cliente.id"), nullable=True
    )
    empleado_id: Mapped[int | None] = mapped_column(
        ForeignKey("empleado.id"), nullable=True
    )
    sucursal_id: Mapped[int] = mapped_column(
        ForeignKey("sucursal.id"), nullable=False
    )
    reserva_id: Mapped[int | None] = mapped_column(
        ForeignKey("reserva.id"), nullable=True
    )
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    canal: Mapped[str] = mapped_column(String(20), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    carrito_id: Mapped[int | None] = mapped_column(
        ForeignKey("carrito.id"), nullable=True
    )

    sucursal: Mapped["Sucursal"] = relationship()
    detalles: Mapped[list["DetalleVenta"]] = relationship(
        back_populates="venta",
        order_by="DetalleVenta.id",
    )


class DetalleVenta(Base):
    """Linea de venta: snapshot economico de una fila de inventario.

    La tabla real ``detalle_venta`` posee:
    - CHECK cantidad > 0
    - CHECK precio_unitario >= 0
    - UNIQUE (venta_id, inventario_id)
    - trg_proteger_detalle_venta (bloquea cambios si la venta esta cerrada)
    - trg_recalcular_total_venta: recalcula venta.total al insertar/editar
    - trg_validar_detalle_venta_sucursal: el inventario debe ser de la sucursal
      de la venta
    """

    __tablename__ = "detalle_venta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(
        ForeignKey("venta.id"), nullable=False
    )
    inventario_id: Mapped[int] = mapped_column(
        ForeignKey("inventario.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    venta: Mapped["Venta"] = relationship(back_populates="detalles")
    inventario: Mapped["Inventario"] = relationship()
