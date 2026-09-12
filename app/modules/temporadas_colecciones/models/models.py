from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.catalogo.models.models import Producto, Temporada


class Coleccion(Base):
    """Coleccion de productos asociada a una temporada.

    No define ``back_populates`` hacia ``Temporada`` para no modificar el
    modelo de catalogo (CU07); la relacion se resuelve en un solo sentido.
    """

    __tablename__ = "coleccion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    temporada_id: Mapped[int] = mapped_column(
        ForeignKey("temporada.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    temporada: Mapped["Temporada"] = relationship()
    productos: Mapped[list["ProductoColeccion"]] = relationship(
        back_populates="coleccion"
    )


class ProductoColeccion(Base):
    """Relacion N:M entre producto y coleccion (PK compuesta)."""

    __tablename__ = "producto_coleccion"

    producto_id: Mapped[int] = mapped_column(
        ForeignKey("producto.id"), primary_key=True
    )
    coleccion_id: Mapped[int] = mapped_column(
        ForeignKey("coleccion.id"), primary_key=True
    )

    producto: Mapped["Producto"] = relationship()
    coleccion: Mapped["Coleccion"] = relationship(back_populates="productos")
