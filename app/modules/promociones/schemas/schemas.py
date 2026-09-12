from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TIPOS_DESCUENTO = ("PORCENTAJE", "MONTO")


def _asegurar_timezone(valor: datetime) -> datetime:
    """Normaliza fechas naive a UTC; la BD almacena TIMESTAMPTZ."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor


# ---------------------------------------------------------------------------
# CU10 - Promociones
# ---------------------------------------------------------------------------


class PromocionCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=255)
    tipo_descuento: str = Field(min_length=1, max_length=20)
    valor_descuento: Decimal
    fecha_inicio: datetime
    fecha_fin: datetime

    @field_validator("fecha_inicio", "fecha_fin")
    @classmethod
    def _normalizar_fechas(cls, valor: datetime) -> datetime:
        return _asegurar_timezone(valor)


class PromocionUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=255)
    tipo_descuento: str | None = Field(default=None, min_length=1, max_length=20)
    valor_descuento: Decimal | None = None
    fecha_inicio: datetime | None = None
    fecha_fin: datetime | None = None

    @field_validator("fecha_inicio", "fecha_fin")
    @classmethod
    def _normalizar_fechas(cls, valor: datetime | None) -> datetime | None:
        if valor is None:
            return None
        return _asegurar_timezone(valor)


class PromocionEstadoUpdate(BaseModel):
    estado: bool


class PromocionResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    tipo_descuento: str
    valor_descuento: Decimal
    fecha_inicio: datetime
    fecha_fin: datetime
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class PromocionDetalleResponse(PromocionResponse):
    total_productos: int


# ---------------------------------------------------------------------------
# CU10 - Productos asociados a una promocion
# ---------------------------------------------------------------------------


class PromocionProductoResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    estado: bool
    categoria_id: int

    model_config = ConfigDict(from_attributes=True)


class PromocionProductosResponse(BaseModel):
    promocion_id: int
    total: int
    productos: list[PromocionProductoResponse]


class PromocionProductosUpdate(BaseModel):
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
