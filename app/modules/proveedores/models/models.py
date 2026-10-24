from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.tenant_models import EmpresaScopedMixin
from app.modules.catalogo.models.models import Producto


class Proveedor(EmpresaScopedMixin, Base):
    """Proveedor administrable por CU11: NIT unico DENTRO de la empresa."""

    __tablename__ = "proveedor"
    __table_args__ = (
        UniqueConstraint("empresa_id", "nit", name="uq_proveedor_empresa_nit"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    razon_social: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str | None] = mapped_column(String(30))
    correo: Mapped[str | None] = mapped_column(String(150))
    telefono: Mapped[str | None] = mapped_column(String(30))
    direccion: Mapped[str | None] = mapped_column(String(255))
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    productos: Mapped[list["ProveedorProducto"]] = relationship(
        back_populates="proveedor"
    )


class ProveedorProducto(EmpresaScopedMixin, Base):
    """Relacion N:M entre proveedor y producto (PK compuesta)."""

    __tablename__ = "proveedor_producto"

    proveedor_id: Mapped[int] = mapped_column(
        ForeignKey("proveedor.id"), primary_key=True
    )
    producto_id: Mapped[int] = mapped_column(
        ForeignKey("producto.id"), primary_key=True
    )
    costo_referencia: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, default=Decimal("0")
    )
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    proveedor: Mapped["Proveedor"] = relationship(back_populates="productos")
    producto: Mapped["Producto"] = relationship()


class OrdenCompra(EmpresaScopedMixin, Base):
    """Vista minima de orden_compra para validar dependencias de CU11.

    CU12 es el dueno funcional de esta tabla; CU11 solo la consulta en modo
    lectura para impedir deshabilitar proveedores con ordenes no finalizadas.
    """

    __tablename__ = "orden_compra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sucursal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False)
