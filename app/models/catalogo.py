from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Categoria(Base):
    __tablename__ = "categoria"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    descripcion: Mapped[str | None] = mapped_column(String)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    productos: Mapped[list["Producto"]] = relationship(back_populates="categoria")


class Producto(Base):
    __tablename__ = "producto"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categoria.id"), nullable=False)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    categoria: Mapped["Categoria"] = relationship(back_populates="productos")
    variantes: Mapped[list["VarianteProducto"]] = relationship(back_populates="producto")
    recursos: Mapped[list["RecursoProducto"]] = relationship(back_populates="producto")


class Talla(Base):
    __tablename__ = "talla"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    variantes: Mapped[list["VarianteProducto"]] = relationship(back_populates="talla")


class Color(Base):
    __tablename__ = "color"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    variantes: Mapped[list["VarianteProducto"]] = relationship(back_populates="color")
    recursos: Mapped[list["RecursoProducto"]] = relationship(back_populates="color")


class Temporada(Base):
    __tablename__ = "temporada"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    inventarios: Mapped[list["Inventario"]] = relationship(back_populates="temporada")


class VarianteProducto(Base):
    __tablename__ = "variante_producto"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("producto.id"), nullable=False)
    talla_id: Mapped[int] = mapped_column(ForeignKey("talla.id"), nullable=False)
    color_id: Mapped[int] = mapped_column(ForeignKey("color.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    producto: Mapped["Producto"] = relationship(back_populates="variantes")
    talla: Mapped["Talla"] = relationship(back_populates="variantes")
    color: Mapped["Color"] = relationship(back_populates="variantes")
    inventarios: Mapped[list["Inventario"]] = relationship(back_populates="variante_producto")


class RecursoProducto(Base):
    __tablename__ = "recurso_producto"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("producto.id"), nullable=False)
    color_id: Mapped[int | None] = mapped_column(ForeignKey("color.id"))
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    es_principal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    producto: Mapped["Producto"] = relationship(back_populates="recursos")
    color: Mapped["Color | None"] = relationship(back_populates="recursos")
