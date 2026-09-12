from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)


def _validar_url_razonable(url: str) -> str:
    limpio = url.strip()
    if not limpio:
        raise ValueError("La URL no puede estar vacia")
    if not (limpio.startswith("http://") or limpio.startswith("https://")):
        raise ValueError("La URL debe iniciar con http:// o https://")
    if len(limpio) < 8:
        raise ValueError("La URL no es valida")
    return limpio


# ---------------------------------------------------------------------------
# Contratos publicos existentes (NO romper)
# ---------------------------------------------------------------------------


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
    categoria_id: int
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


# ---------------------------------------------------------------------------
# CU09 - Catalogo publico: filtros
# ---------------------------------------------------------------------------


class FiltroOpcionResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class ColeccionFiltroResponse(BaseModel):
    id: int
    nombre: str
    temporada_id: int

    model_config = ConfigDict(from_attributes=True)


class SucursalFiltroResponse(BaseModel):
    id: int
    nombre: str
    ciudad: str | None


class CatalogoFiltrosResponse(BaseModel):
    categorias: list[FiltroOpcionResponse]
    tallas: list[FiltroOpcionResponse]
    colores: list[FiltroOpcionResponse]
    temporadas: list[FiltroOpcionResponse]
    colecciones: list[ColeccionFiltroResponse]
    sucursales: list[SucursalFiltroResponse]


# ---------------------------------------------------------------------------
# CU07 - Categorias (administracion)
# ---------------------------------------------------------------------------


class CategoriaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)


class CategoriaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)


class CategoriaEstadoUpdate(BaseModel):
    estado: bool


# ---------------------------------------------------------------------------
# CU07 - Productos (administracion)
# ---------------------------------------------------------------------------


class ProductoCreate(BaseModel):
    categoria_id: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: str | None = None
    precio: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class ProductoUpdate(BaseModel):
    categoria_id: int | None = Field(default=None, gt=0)
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    descripcion: str | None = None
    precio: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )


class ProductoEstadoUpdate(BaseModel):
    estado: bool


# ---------------------------------------------------------------------------
# CU07 - Tallas
# ---------------------------------------------------------------------------


class TallaResponse(BaseModel):
    id: int
    nombre: str
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class TallaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=30)


class TallaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=30)


class TallaEstadoUpdate(BaseModel):
    estado: bool


# ---------------------------------------------------------------------------
# CU07 - Colores
# ---------------------------------------------------------------------------


class ColorResponse(BaseModel):
    id: int
    nombre: str
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ColorCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=60)


class ColorUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=60)


class ColorEstadoUpdate(BaseModel):
    estado: bool


# ---------------------------------------------------------------------------
# CU07 - Variantes de producto
# ---------------------------------------------------------------------------


class VarianteAdminResponse(BaseModel):
    id: int
    producto_id: int
    sku: str
    estado: bool
    talla: TallaResumen
    color: ColorResumen

    model_config = ConfigDict(from_attributes=True)


class VarianteCreate(BaseModel):
    talla_id: int = Field(gt=0)
    color_id: int = Field(gt=0)
    sku: str = Field(min_length=1, max_length=80)


class VarianteUpdate(BaseModel):
    talla_id: int | None = Field(default=None, gt=0)
    color_id: int | None = Field(default=None, gt=0)
    sku: str | None = Field(default=None, min_length=1, max_length=80)


class VarianteEstadoUpdate(BaseModel):
    estado: bool


# ---------------------------------------------------------------------------
# CU07 - Recursos de producto (imagenes por URL)
# ---------------------------------------------------------------------------


class RecursoProductoAdminResponse(BaseModel):
    id: int
    producto_id: int
    color_id: int | None
    tipo: str
    url: str
    es_principal: bool
    estado: bool
    color: ColorResumen | None

    model_config = ConfigDict(from_attributes=True)


class RecursoProductoCreate(BaseModel):
    tipo: str = Field(min_length=1, max_length=50)
    url: str = Field(min_length=8, max_length=2048)
    color_id: int | None = Field(default=None, gt=0)
    es_principal: bool = False

    @field_validator("url")
    @classmethod
    def _url_valida(cls, valor: str) -> str:
        return _validar_url_razonable(valor)


class RecursoProductoUpdate(BaseModel):
    tipo: str | None = Field(default=None, min_length=1, max_length=50)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    color_id: int | None = Field(default=None, gt=0)
    es_principal: bool | None = None

    @field_validator("url")
    @classmethod
    def _url_valida(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return _validar_url_razonable(valor)


class RecursoProductoEstadoUpdate(BaseModel):
    estado: bool
