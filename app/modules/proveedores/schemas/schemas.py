from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CU11 - Proveedores
# ---------------------------------------------------------------------------


class ProveedorCreate(BaseModel):
    razon_social: str = Field(min_length=1, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=255)


class ProveedorUpdate(BaseModel):
    razon_social: str | None = Field(default=None, min_length=1, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=255)


class ProveedorEstadoUpdate(BaseModel):
    estado: bool


class ProveedorResponse(BaseModel):
    id: int
    razon_social: str
    nit: str | None
    correo: str | None
    telefono: str | None
    direccion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ProveedorDetalleResponse(ProveedorResponse):
    total_productos: int


# ---------------------------------------------------------------------------
# CU11 - Productos asociados a un proveedor
# ---------------------------------------------------------------------------


class ProveedorProductoResponse(BaseModel):
    producto_id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    categoria_id: int
    producto_estado: bool
    costo_referencia: Decimal
    estado: bool


class ProveedorProductosResponse(BaseModel):
    proveedor_id: int
    total: int
    productos: list[ProveedorProductoResponse]


class ProveedorProductoInput(BaseModel):
    producto_id: int = Field(gt=0)
    costo_referencia: Decimal | None = None
    estado: bool = True


class ProveedorProductosUpdate(BaseModel):
    productos: list[ProveedorProductoInput] = Field(default_factory=list)
