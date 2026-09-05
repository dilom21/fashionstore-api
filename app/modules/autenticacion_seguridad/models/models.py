from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Rol(Base):
    __tablename__ = "rol"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    descripcion: Mapped[str | None] = mapped_column(String(255))
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="rol")


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rol_id: Mapped[int] = mapped_column(
        ForeignKey("rol.id"),
        nullable=False,
    )
    correo: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    fecha_creacion: Mapped[datetime]
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    rol: Mapped["Rol"] = relationship(back_populates="usuarios")


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
