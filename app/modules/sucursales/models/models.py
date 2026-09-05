from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Ciudad(Base):
    __tablename__ = "ciudad"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    sucursales: Mapped[list["Sucursal"]] = relationship(back_populates="ciudad")


class Sucursal(Base):
    __tablename__ = "sucursal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ciudad_id: Mapped[int] = mapped_column(ForeignKey("ciudad.id"), nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    direccion: Mapped[str] = mapped_column(String(255), nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(30))
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    ciudad: Mapped["Ciudad"] = relationship(back_populates="sucursales")
    inventarios: Mapped[list["Inventario"]] = relationship(back_populates="sucursal")
