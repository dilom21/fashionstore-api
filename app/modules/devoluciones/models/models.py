"""Mapping de las tablas reales ``devolucion`` y ``detalle_devolucion`` (CU25).

Columnas y constraints verificadas contra la base real:

- ``ck_devolucion_estado`` CHECK estado IN (SOLICITADA, APROBADA, RECHAZADA,
  COMPLETADA); default 'SOLICITADA'.
- ``ck_detalle_devolucion_cantidad`` CHECK cantidad > 0.
- ``uq_detalle_devolucion`` UNIQUE (devolucion_id, detalle_venta_id).
- ``fk_devolucion_venta`` FK venta_id -> venta(id).
- ``fk_detalle_devolucion_detalle_venta`` FK detalle_venta_id -> detalle_venta(id).
- ``trg_bitacora_devolucion`` auditoria sobre ``devolucion``.
- ``trg_validar_detalle_devolucion`` valida en la base que el detalle pertenece
  a la venta de la devolucion y que no se devuelve mas de lo vendido.

CU25 NO crea tablas ni migraciones: solo mapea lo existente. La autoridad
transaccional del procesamiento es ``sp_registrar_devolucion``.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.ventas.models.models import DetalleVenta, Venta


class Devolucion(Base):
    """Devolucion fisica de productos de una venta (sin reembolso financiero)."""

    __tablename__ = "devolucion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(
        ForeignKey("venta.id"), nullable=False
    )
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    motivo: Mapped[str] = mapped_column(String, nullable=False)
    estado: Mapped[str] = mapped_column(String, nullable=False)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    venta: Mapped["Venta"] = relationship()
    detalles: Mapped[list["DetalleDevolucion"]] = relationship(
        back_populates="devolucion",
        order_by="DetalleDevolucion.id",
    )


class DetalleDevolucion(Base):
    """Linea de la devolucion: cantidad a devolver de un ``detalle_venta``."""

    __tablename__ = "detalle_devolucion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    devolucion_id: Mapped[int] = mapped_column(
        ForeignKey("devolucion.id"), nullable=False
    )
    detalle_venta_id: Mapped[int] = mapped_column(
        ForeignKey("detalle_venta.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    motivo: Mapped[str | None] = mapped_column(String, nullable=True)

    devolucion: Mapped["Devolucion"] = relationship(
        back_populates="detalles"
    )
    detalle_venta: Mapped["DetalleVenta"] = relationship()
