from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Inventario(Base):
    __tablename__ = "inventario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("sucursal.id"), nullable=False)
    variante_producto_id: Mapped[int] = mapped_column(
        ForeignKey("variante_producto.id"), nullable=False
    )
    temporada_id: Mapped[int] = mapped_column(ForeignKey("temporada.id"), nullable=False)
    stock_actual: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_reservado: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    sucursal: Mapped["Sucursal"] = relationship(back_populates="inventarios")
    variante_producto: Mapped["VarianteProducto"] = relationship(back_populates="inventarios")
    temporada: Mapped["Temporada"] = relationship(back_populates="inventarios")
