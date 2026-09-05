from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field


class CategoriaResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class CategoriaResumen(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class ProductoResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    estado: bool
    categoria: CategoriaResumen

    model_config = ConfigDict(from_attributes=True)


class TallaResumen(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class ColorResumen(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class SucursalResumen(BaseModel):
    id: int
    nombre: str
    direccion: str

    model_config = ConfigDict(from_attributes=True)


class TemporadaResumen(BaseModel):
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date

    model_config = ConfigDict(from_attributes=True)


class InventarioDetalleResponse(BaseModel):
    id: int
    stock_actual: int
    stock_reservado: int
    fecha_actualizacion: datetime
    sucursal: SucursalResumen
    temporada: TemporadaResumen

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def stock_disponible(self) -> int:
        return self.stock_actual - self.stock_reservado


class VarianteProductoDetalleResponse(BaseModel):
    id: int
    sku: str
    estado: bool
    talla: TallaResumen
    color: ColorResumen
    inventarios: list[InventarioDetalleResponse]

    model_config = ConfigDict(from_attributes=True)


class RecursoProductoResponse(BaseModel):
    id: int
    tipo: str
    url: str
    es_principal: bool
    color: ColorResumen | None

    model_config = ConfigDict(from_attributes=True)


class ProductoDetalleResponse(ProductoResponse):
    recursos: list[RecursoProductoResponse]
    variantes: list[VarianteProductoDetalleResponse]


class DisponibilidadVarianteResponse(BaseModel):
    variante_id: int
    sku: str
    talla: str
    color: str
    temporada: str
    stock_disponible: int


class DisponibilidadSucursalResponse(BaseModel):
    sucursal_id: int
    sucursal: str
    variantes: list[DisponibilidadVarianteResponse]


class DisponibilidadProductoResponse(BaseModel):
    producto_id: int
    producto: str
    sucursales: list[DisponibilidadSucursalResponse]
