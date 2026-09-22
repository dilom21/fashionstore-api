"""Modelos SQLAlchemy de CU26 - Vestidor Virtual (AR).

Mapean tablas YA EXISTENTES en Supabase. No generan DDL ni migraciones:

- ``configuracion_vestidor_ar``: configuracion de try-on de un recurso de
  producto (asset PNG 2D, factores y offsets de anclaje al torso).
- ``sesion_vestidor_ar``: sesion de vestidor de un CLIENTE.
- ``prueba_vestidor_ar``: prueba de una prenda dentro de una sesion.

Los ``id`` son IDENTITY BY DEFAULT en PostgreSQL: se omiten al insertar y la
base de datos los asigna.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Se importan para que SQLAlchemy registre las entidades referenciadas por las
# ForeignKey y las relaciones (el modulo no debe asumir que otro import ya las
# cargo).
from app.modules.catalogo.models.models import (  # noqa: F401
    Producto,
    RecursoProducto,
    VarianteProducto,
)


class ConfiguracionVestidorAR(Base):
    """Configuracion AR de un ``recurso_producto`` (CU26).

    Restricciones reales de la tabla:
    - ``recurso_producto_id`` UNIQUE (un recurso -> una configuracion).
    - ``zona_cuerpo`` IN (TORSO, PIERNAS, CUERPO_COMPLETO).
    - ``tipo_asset`` IN (PNG_2D, GLB_3D).
    - ``factor_ancho`` > 0 y ``factor_alto`` > 0.
    - 0 <= ``opacidad`` <= 1.
    """

    __tablename__ = "configuracion_vestidor_ar"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recurso_producto_id: Mapped[int] = mapped_column(
        ForeignKey("recurso_producto.id"), nullable=False, unique=True
    )
    zona_cuerpo: Mapped[str] = mapped_column(String, nullable=False)
    tipo_asset: Mapped[str] = mapped_column(
        String, nullable=False, default="PNG_2D"
    )
    factor_ancho: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    factor_alto: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    offset_x: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    offset_y: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    rotacion_offset: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    orden_capa: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    opacidad: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    recurso: Mapped["RecursoProducto"] = relationship()


class SesionVestidorAR(Base):
    """Sesion de vestidor virtual de un CLIENTE (CU26).

    Restricciones reales de la tabla:
    - ``estado`` IN (ACTIVA, FINALIZADA, CANCELADA).
    - ``fecha_fin`` NULL o ``fecha_fin >= fecha_inicio``.
    - indice NO unico ``idx_sesion_vestidor_ar_activa`` sobre cliente_id.
    """

    __tablename__ = "sesion_vestidor_ar"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False
    )
    fecha_inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(
        String, nullable=False, default="ACTIVA"
    )

    pruebas: Mapped[list["PruebaVestidorAR"]] = relationship(
        back_populates="sesion",
        order_by="PruebaVestidorAR.id",
    )


class PruebaVestidorAR(Base):
    """Prueba de una prenda dentro de una sesion de vestidor (CU26).

    Restricciones reales de la tabla:
    - ``estado`` IN (INICIADA, COMPLETADA, CANCELADA, ERROR).
    - ``fecha_fin`` NULL o ``fecha_fin >= fecha_inicio``.
    - ``variante_producto_id`` opcional (ON DELETE SET NULL).
    """

    __tablename__ = "prueba_vestidor_ar"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sesion_vestidor_ar_id: Mapped[int] = mapped_column(
        ForeignKey("sesion_vestidor_ar.id"), nullable=False
    )
    configuracion_vestidor_ar_id: Mapped[int] = mapped_column(
        ForeignKey("configuracion_vestidor_ar.id"), nullable=False
    )
    variante_producto_id: Mapped[int | None] = mapped_column(
        ForeignKey("variante_producto.id")
    )
    fecha_inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(
        String, nullable=False, default="INICIADA"
    )

    sesion: Mapped["SesionVestidorAR"] = relationship(back_populates="pruebas")
    configuracion: Mapped["ConfiguracionVestidorAR"] = relationship()
    variante: Mapped["VarianteProducto | None"] = relationship()
