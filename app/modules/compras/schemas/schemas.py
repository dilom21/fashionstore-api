from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CU12 - Ordenes de compra
# ---------------------------------------------------------------------------


class OrdenCompraCreate(BaseModel):
    proveedor_id: int = Field(gt=0)
    sucursal_id: int = Field(gt=0)
    fecha_estimada: datetime | None = None
    observacion: str | None = Field(default=None, max_length=2000)


class OrdenCompraUpdate(BaseModel):
    fecha_estimada: datetime | None = None
    observacion: str | None = Field(default=None, max_length=2000)


class OrdenCompraResponse(BaseModel):
    id: int
    proveedor_id: int
    proveedor_razon_social: str | None = None
    sucursal_id: int
    sucursal_nombre: str | None = None
    empleado_id: int | None
    fecha_orden: datetime
    fecha_estimada: datetime | None
    fecha_recepcion: datetime | None
    estado: str
    observacion: str | None
    total_detalles: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# CU12 - Detalles de la orden
# ---------------------------------------------------------------------------


class DetalleOrdenCompraInput(BaseModel):
    variante_producto_id: int = Field(gt=0)
    temporada_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    costo_unitario: Decimal = Field(ge=0)


class DetallesOrdenCompraUpdate(BaseModel):
    detalles: list[DetalleOrdenCompraInput] = Field(default_factory=list)


class DetalleOrdenCompraResponse(BaseModel):
    id: int
    variante_producto_id: int
    temporada_id: int
    cantidad: int
    costo_unitario: Decimal
    sku: str | None = None
    producto_id: int | None = None
    producto_nombre: str | None = None
    temporada_nombre: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DetallesOrdenCompraResponse(BaseModel):
    orden_compra_id: int
    total: int
    detalles: list[DetalleOrdenCompraResponse]
