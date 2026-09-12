from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.catalogo.models.models import Temporada, VarianteProducto
from app.modules.proveedores.models.models import Proveedor
from app.modules.sucursales.models.models import Sucursal


class OrdenCompra(Base):
    """Orden de compra a proveedor (CU12).

    La tabla real posee:
    - CHECK estado IN (BORRADOR, ENVIADA, PARCIAL, RECIBIDA, CANCELADA)
    - CHECK fecha_estimada IS NULL OR fecha_estimada >= fecha_orden
    - trigger de bitacora trg_bitacora_orden_compra
    """

    __tablename__ = "orden_compra"
    # CU11 ya declara una vista minima de esta tabla; se reutiliza la Table
    # existente y se completan las columnas reales que CU12 necesita.
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(
        ForeignKey("proveedor.id"), nullable=False
    )
    sucursal_id: Mapped[int] = mapped_column(
        ForeignKey("sucursal.id"), nullable=False
    )
    empleado_id: Mapped[int | None] = mapped_column(
        ForeignKey("empleado.id"), nullable=True
    )
    fecha_orden: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fecha_estimada: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fecha_recepcion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estado: Mapped[str] = mapped_column(
        String(30), nullable=False, default="BORRADOR"
    )
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    proveedor: Mapped["Proveedor"] = relationship()
    sucursal: Mapped["Sucursal"] = relationship()
    detalles: Mapped[list["DetalleOrdenCompra"]] = relationship(
        back_populates="orden",
        cascade="all, delete-orphan",
        order_by="DetalleOrdenCompra.id",
    )


class DetalleOrdenCompra(Base):
    """Linea de una orden de compra.

    La tabla real posee:
    - UNIQUE (orden_compra_id, variante_producto_id, temporada_id)
    - CHECK cantidad > 0
    - CHECK costo_unitario >= 0
    - trg_proteger_detalle_orden_compra (bloquea RECIBIDA/CANCELADA)
    - trg_validar_producto_proveedor_orden (producto asociado al proveedor)
    Sin trigger de bitacora: CU12 registra el evento manual.
    """

    __tablename__ = "detalle_orden_compra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    orden_compra_id: Mapped[int] = mapped_column(
        ForeignKey("orden_compra.id"), nullable=False
    )
    variante_producto_id: Mapped[int] = mapped_column(
        ForeignKey("variante_producto.id"), nullable=False
    )
    temporada_id: Mapped[int] = mapped_column(
        ForeignKey("temporada.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric, nullable=False)

    orden: Mapped[OrdenCompra] = relationship(back_populates="detalles")
    variante: Mapped["VarianteProducto"] = relationship()
    temporada: Mapped["Temporada"] = relationship()


class MovimientoInventario(Base):
    """Movimiento de inventario (solo lectura en CU12).

    La recepcion de una orden escribe en esta tabla a traves del
    procedimiento sp_recibir_orden_compra. CU12 no inserta movimientos de
    forma manual; el modelo existe para verificar el efecto del SP.
    """

    __tablename__ = "movimiento_inventario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inventario_id: Mapped[int] = mapped_column(
        ForeignKey("inventario.id"), nullable=False
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    referencia_tipo: Mapped[str | None] = mapped_column(String, nullable=True)
    referencia_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
