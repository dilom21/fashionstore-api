from datetime import date, datetime
from decimal import Decimal
from enum import Enum

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


class DisponibilidadFiltro(str, Enum):
    """Filtro de disponibilidad de CU13."""

    TODOS = "TODOS"
    CON_STOCK = "CON_STOCK"
    SIN_STOCK = "SIN_STOCK"
    STOCK_BAJO = "STOCK_BAJO"


class InventarioItemResponse(BaseModel):
    """Registro de inventario listo para mostrar en el frontend (CU13).

    stock_disponible se calcula como stock_actual - stock_reservado.
    """

    inventario_id: int
    sucursal_id: int
    sucursal_nombre: str
    producto_id: int
    producto_nombre: str
    precio: Decimal
    categoria_id: int
    categoria_nombre: str
    variante_producto_id: int
    sku: str
    talla_id: int
    talla_nombre: str
    color_id: int
    color_nombre: str
    temporada_id: int
    temporada_nombre: str
    stock_actual: int
    stock_reservado: int
    stock_disponible: int
    fecha_actualizacion: datetime


class InventarioConsultaResponse(BaseModel):
    """Respuesta paginada de CU13 (patron items/total/limit/offset)."""

    items: list[InventarioItemResponse]
    total: int
    limit: int
    offset: int


class TipoMovimiento(str, Enum):
    """Tipos de movimiento visibles en CU14 (Kardex)."""

    ENTRADA_COMPRA = "ENTRADA_COMPRA"
    SALIDA_VENTA = "SALIDA_VENTA"
    RESERVA = "RESERVA"
    LIBERACION_RESERVA = "LIBERACION_RESERVA"
    DEVOLUCION = "DEVOLUCION"


class MovimientoInventarioItemResponse(BaseModel):
    """Movimiento de inventario enriquecido para el Kardex (CU14)."""

    movimiento_id: int
    inventario_id: int
    usuario_id: int | None
    usuario_correo: str | None
    tipo: str
    cantidad: int
    fecha_hora: datetime
    observaciones: str | None
    referencia_tipo: str | None
    referencia_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    producto_id: int
    producto_nombre: str
    variante_producto_id: int
    sku: str
    talla_id: int
    talla_nombre: str
    color_id: int
    color_nombre: str
    temporada_id: int
    temporada_nombre: str


class MovimientoInventarioConsultaResponse(BaseModel):
    """Respuesta paginada de CU14 (patron items/total/limit/offset)."""

    items: list[MovimientoInventarioItemResponse]
    total: int
    limit: int
    offset: int


