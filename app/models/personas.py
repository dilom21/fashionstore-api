from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, String
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


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True, unique=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    apellido: Mapped[str] = mapped_column(String, nullable=False)
    telefono: Mapped[str | None] = mapped_column(String)
    sexo: Mapped[str | None] = mapped_column(String)
    ci: Mapped[str | None] = mapped_column(String, unique=True)
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Empleado(Base):
    __tablename__ = "empleado"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id"), nullable=False, unique=True
    )
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("sucursal.id"), nullable=False)
    nombres: Mapped[str] = mapped_column(String, nullable=False)
    apellidos: Mapped[str] = mapped_column(String, nullable=False)
    ci: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    telefono: Mapped[str | None] = mapped_column(String)
    fecha_contratacion: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)
