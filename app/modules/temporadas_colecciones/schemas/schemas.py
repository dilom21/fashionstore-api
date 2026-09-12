from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# CU08 - Temporadas
# ---------------------------------------------------------------------------


class TemporadaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    fecha_inicio: date
    fecha_fin: date


class TemporadaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None


class TemporadaEstadoUpdate(BaseModel):
    estado: bool


class TemporadaResponse(BaseModel):
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date
    estado: bool

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# CU08 - Colecciones
# ---------------------------------------------------------------------------


class TemporadaResumenResponse(BaseModel):
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ColeccionCreate(BaseModel):
    temporada_id: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=255)


class ColeccionUpdate(BaseModel):
    temporada_id: int | None = Field(default=None, gt=0)
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=255)


class ColeccionEstadoUpdate(BaseModel):
    estado: bool


class ColeccionResponse(BaseModel):
    id: int
    temporada_id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ColeccionDetalleResponse(ColeccionResponse):
    temporada: TemporadaResumenResponse
    total_productos: int


# ---------------------------------------------------------------------------
# CU08 - Productos asignados a una coleccion
# ---------------------------------------------------------------------------


class ProductoResumenResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    estado: bool
    categoria_id: int

    model_config = ConfigDict(from_attributes=True)


class ColeccionProductosResponse(BaseModel):
    coleccion_id: int
    total: int
    productos: list[ProductoResumenResponse]


class ColeccionProductosUpdate(BaseModel):
    producto_ids: list[int] = Field(default_factory=list)

    @field_validator("producto_ids")
    @classmethod
    def _ids_positivos(cls, valor: list[int]) -> list[int]:
        for producto_id in valor:
            if producto_id <= 0:
                raise ValueError(
                    "Los identificadores de producto deben ser mayores a 0"
                )
        return valor
