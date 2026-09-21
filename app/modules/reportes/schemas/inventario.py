"""Schemas de CU28 - Reporte de inventario.

- ``stock_disponible = stock_actual - stock_reservado`` (nunca negativo).
- "stock bajo" es solo un criterio de REPORTE:
  ``0 < stock_disponible <= umbral_stock_bajo``. La tabla ``inventario`` no
  tiene ``stock_minimo``, no se inventa esa columna.
- Los movimientos se agrupan por ``movimiento_inventario.tipo`` real.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import (
    MetadatosReporteResponse,
    PaginacionResponse,
)


class InventarioResumenResponse(BaseModel):
    stock_actual_total: int
    stock_reservado_total: int
    stock_disponible_total: int
    variantes_agotadas: int
    variantes_stock_bajo: int
    umbral_stock_bajo: int


class InventarioMovimientoTipoResponse(BaseModel):
    tipo: str
    cantidad_movimientos: int
    unidades: int


class InventarioTablaItemResponse(BaseModel):
    inventario_id: int
    sucursal: str
    producto: str
    sku: str
    talla: str
    color: str
    temporada: str
    stock_actual: int
    stock_reservado: int
    stock_disponible: int
    fecha_actualizacion: datetime


class ReporteInventarioResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: InventarioResumenResponse
    movimientos_por_tipo: list[InventarioMovimientoTipoResponse]
    paginacion: PaginacionResponse
    items: list[InventarioTablaItemResponse]
