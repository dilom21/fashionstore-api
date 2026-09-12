from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.catalogo.models.models import Producto


class Promocion(Base):
    """Promocion administrable por CU10.

    No se define una restriccion UNIQUE sobre ``nombre`` porque la tabla
    real ``promocion`` no la posee.
    """

    __tablename__ = "promocion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(255))
    tipo_descuento: Mapped[str] = mapped_column(String(20), nullable=False)
    valor_descuento: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fecha_inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fecha_fin: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    productos: Mapped[list["PromocionProducto"]] = relationship(
        back_populates="promocion"
    )


class PromocionProducto(Base):
    """Relacion N:M entre promocion y producto (PK compuesta)."""

    __tablename__ = "promocion_producto"

    promocion_id: Mapped[int] = mapped_column(
        ForeignKey("promocion.id"), primary_key=True
    )
    producto_id: Mapped[int] = mapped_column(
        ForeignKey("producto.id"), primary_key=True
    )

    promocion: Mapped["Promocion"] = relationship(back_populates="productos")
    producto: Mapped["Producto"] = relationship()
