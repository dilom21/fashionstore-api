"""Schemas de CU28 - Reporte de ventas.

Las metricas monetarias (monto, unidades, ticket) usan solo ventas
``COMPLETADA``; el desglose por estado muestra todos los estados reales.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import (
    MetadatosReporteResponse,
    PaginacionResponse,
    SerieTemporalResponse,
)


class VentaResumenKpisResponse(BaseModel):
    ventas_completadas: int
    monto_total: Decimal
    unidades_vendidas: int
    ticket_promedio: Decimal


class VentaEstadoResponse(BaseModel):
    estado: str
    cantidad: int
    monto: Decimal


class VentaCanalResponse(BaseModel):
    canal: str
    cantidad: int
    monto: Decimal
    unidades: int


class VentaSucursalResponse(BaseModel):
    sucursal_id: int
    sucursal_nombre: str
    cantidad: int
    monto: Decimal
    unidades: int
    ticket_promedio: Decimal


class VentaEmpleadoResponse(BaseModel):
    empleado_id: int
    empleado_nombre: str
    cantidad: int
    monto: Decimal


class VentaTablaItemResponse(BaseModel):
    """Fila de la tabla paginada (sin CI ni telefono del cliente)."""

    venta_id: int
    fecha_hora: datetime
    sucursal: str
    canal: str
    estado: str
    cliente_nombre: str | None = None
    empleado_nombre: str | None = None
    cantidad_unidades: int
    total: Decimal


class ReporteVentasResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: VentaResumenKpisResponse
    ventas_por_estado: list[VentaEstadoResponse]
    ventas_por_canal: list[VentaCanalResponse]
    ventas_por_sucursal: list[VentaSucursalResponse]
    ventas_por_empleado: list[VentaEmpleadoResponse]
    evolucion: list[SerieTemporalResponse]
    paginacion: PaginacionResponse
    items: list[VentaTablaItemResponse]
