"""Schemas de CU25 - Devoluciones.

CU25 es devolucion FISICA de producto + reingreso a inventario. NO hay
reembolso financiero: por eso los importes se llaman ``precio_unitario`` y
``subtotal_referencial`` (nunca ``monto_reembolsado``).

No se inventan campos: ``devolucion`` no tiene ``motivo_rechazo`` y el codigo
visual (DEV-00012) es responsabilidad del frontend; el backend devuelve
``devolucion_id``.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class EstadoDevolucionFiltro(str, Enum):
    """Estados reales de ``ck_devolucion_estado``."""

    SOLICITADA = "SOLICITADA"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    COMPLETADA = "COMPLETADA"


class DevolucionSucursalResponse(BaseModel):
    id: int
    nombre: str
    direccion: str


class DevolucionClienteResponse(BaseModel):
    id: int
    nombre: str
    apellido: str


class DevolucionVentaResumenResponse(BaseModel):
    """Datos basicos de la venta original dentro de la devolucion."""

    venta_id: int
    fecha_hora: datetime
    canal: str
    total: Decimal


class DisponibilidadItemResponse(BaseModel):
    """Linea vendida con su disponibilidad real para devolucion."""

    detalle_venta_id: int
    inventario_id: int
    producto_id: int
    producto_nombre: str
    sku: str
    talla_nombre: str
    color_nombre: str
    cantidad_vendida: int
    cantidad_comprometida: int
    cantidad_disponible: int
    precio_unitario: Decimal


class DisponibilidadVentaResponse(BaseModel):
    """Venta COMPLETADA lista para registrar una devolucion."""

    venta_id: int
    fecha_hora: datetime
    estado: str
    canal: str
    total: Decimal
    sucursal: DevolucionSucursalResponse
    cliente: DevolucionClienteResponse | None = None
    items: list[DisponibilidadItemResponse]
    cantidad_total_unidades: int


class RegistrarDevolucionItemRequest(BaseModel):
    """Solo se confia en el detalle de venta y la cantidad."""

    detalle_venta_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    motivo: str | None = Field(default=None, max_length=255)


class RegistrarDevolucionRequest(BaseModel):
    """El estado, la sucursal y los precios NO vienen del frontend."""

    venta_id: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)
    observacion: str | None = Field(default=None, max_length=1000)
    items: list[RegistrarDevolucionItemRequest] = Field(min_length=1)

    @field_validator("motivo")
    @classmethod
    def _motivo_no_vacio(cls, valor: str) -> str:
        limpio = valor.strip()
        if not limpio:
            raise ValueError("El motivo no puede estar vacio")
        return limpio

    @field_validator("observacion")
    @classmethod
    def _observacion_limpia(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpio = valor.strip()
        return limpio or None


class DevolucionItemResponse(BaseModel):
    detalle_devolucion_id: int
    detalle_venta_id: int
    producto_id: int
    producto_nombre: str
    sku: str
    talla_nombre: str
    color_nombre: str
    cantidad_vendida: int
    cantidad_solicitada: int
    precio_unitario: Decimal
    subtotal_referencial: Decimal
    motivo: str | None = None


class DevolucionDetalleResponse(BaseModel):
    """Detalle completo de la devolucion (respuesta de crear/aprobar/...)."""

    devolucion_id: int
    venta_id: int
    fecha_hora: datetime
    motivo: str
    observacion: str | None = None
    estado: str
    venta: DevolucionVentaResumenResponse
    sucursal: DevolucionSucursalResponse
    cliente: DevolucionClienteResponse | None = None
    items: list[DevolucionItemResponse]
    cantidad_total_unidades: int


class DevolucionResumenResponse(BaseModel):
    """Fila del listado (sin cargar relaciones innecesarias)."""

    devolucion_id: int
    venta_id: int
    fecha_hora: datetime
    estado: str
    motivo: str
    sucursal_id: int
    sucursal_nombre: str
    cantidad_lineas: int
    cantidad_total_unidades: int


class DevolucionesListaResponse(BaseModel):
    items: list[DevolucionResumenResponse]
    pagina: int
    tamano_pagina: int
    total_registros: int
    total_paginas: int
