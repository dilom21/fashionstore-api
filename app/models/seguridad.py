from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
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