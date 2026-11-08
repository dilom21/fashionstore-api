from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class PublicCategoria(BaseModel):
    id: int
    nombre: str


class PublicPromocion(BaseModel):
    id: int
    nombre: str
    tipo_descuento: str
    valor_descuento: Decimal


class PublicOpcion(BaseModel):
    id: int
    nombre: str


class PublicVariante(BaseModel):
    id: int
    sku: str
    estado: bool
    talla: PublicOpcion
    color: PublicOpcion


class PublicRecurso(BaseModel):
    id: int
    tipo: str
    url: str
    es_principal: bool


class PublicDisponibilidad(BaseModel):
    variante_id: int
    sucursal_id: int
    sucursal: str
    sku: str
    talla: str
    color: str
    temporada: str
    stock_disponible: int


class PublicProductoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    categoria: PublicCategoria
    imagen_principal_url: str | None = None
    promociones_vigentes: list[PublicPromocion] = []
    precio_definitivo: Decimal | None = None
    precio_calculo_pendiente: bool = False


class PublicProductoDetalleResponse(PublicProductoResponse):
    variantes: list[PublicVariante] = []
    disponibilidad: list[PublicDisponibilidad] = []
    recursos: list[PublicRecurso] = []


class PublicCatalogoResponse(BaseModel):
    categorias: list[PublicCategoria]
    tallas: list[PublicOpcion] = []
    colores: list[PublicOpcion] = []
    temporadas: list[PublicOpcion] = []
    colecciones: list[PublicOpcion] = []
    sucursales: list[PublicOpcion] = []
