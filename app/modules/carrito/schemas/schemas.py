from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class AgregarItemCarritoRequest(BaseModel):
    sucursal_id: int = Field(gt=0)
    inventario_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)


class ActualizarCantidadRequest(BaseModel):
    cantidad: int = Field(gt=0)


class CarritoResumenResponse(BaseModel):
    """Resumen de un carrito activo (panel 'Tus carritos')."""

    carrito_id: int
    sucursal_id: int
    sucursal_nombre: str
    cantidad_lineas: int
    cantidad_unidades: int
    subtotal: Decimal
    fecha_actualizacion: datetime


class CarritoListaResponse(BaseModel):
    items: list[CarritoResumenResponse]
    total_carritos_activos: int


class CarritoItemResponse(BaseModel):
    detalle_id: int
    inventario_id: int
    producto_id: int
    producto_nombre: str
    precio_unitario: Decimal
    imagen_principal: str | None
    variante_producto_id: int
    sku: str
    talla_id: int
    talla_nombre: str
    color_id: int
    color_nombre: str
    temporada_id: int
    temporada_nombre: str
    cantidad: int
    stock_disponible: int
    subtotal_linea: Decimal


class CarritoDetalleResponse(BaseModel):
    carrito_id: int
    sucursal_id: int
    sucursal_nombre: str
    estado: str
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    items: list[CarritoItemResponse]
    cantidad_total_unidades: int
    subtotal_carrito: Decimal
