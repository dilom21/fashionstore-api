from datetime import date, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field


class ProductoInventarioResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class TallaInventarioResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class ColorInventarioResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class VarianteInventarioResponse(BaseModel):
    id: int
    sku: str
    producto: ProductoInventarioResponse
    talla: TallaInventarioResponse
    color: ColorInventarioResponse

    model_config = ConfigDict(from_attributes=True)


class CiudadInventarioResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class SucursalInventarioResponse(BaseModel):
    id: int
    nombre: str
    ciudad: CiudadInventarioResponse

    model_config = ConfigDict(from_attributes=True)


class TemporadaInventarioResponse(BaseModel):
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date

    model_config = ConfigDict(from_attributes=True)


class InventarioResponse(BaseModel):
    inventario_id: int = Field(validation_alias=AliasChoices("inventario_id", "id"))
    variante: VarianteInventarioResponse = Field(validation_alias="variante_producto")
    sucursal: SucursalInventarioResponse
    temporada: TemporadaInventarioResponse
    stock_actual: int
    stock_reservado: int
    fecha_actualizacion: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def stock_disponible(self) -> int:
        return self.stock_actual - self.stock_reservado
