"""Schemas de CU28 - Dashboard general (resumen ejecutivo)."""

from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import (
    MetadatosReporteResponse,
    SerieTemporalResponse,
)


class DashboardKpisResponse(BaseModel):
    ventas_completadas: int
    total_vendido: Decimal
    unidades_vendidas: int
    ticket_promedio: Decimal
    reservas_total: int
    devoluciones_completadas: int
    unidades_devueltas: int
    stock_disponible_total: int


class VentasCanalResponse(BaseModel):
    canal: str
    cantidad_ventas: int
    monto: Decimal
    unidades: int


class TopProductoResponse(BaseModel):
    producto_id: int
    producto_nombre: str
    unidades: int
    monto: Decimal


class ConteoEstadoResponse(BaseModel):
    estado: str
    cantidad: int


class ResumenReservasResponse(BaseModel):
    total: int
    por_estado: list[ConteoEstadoResponse]


class ResumenDevolucionesResponse(BaseModel):
    total: int
    completadas: int
    unidades_devueltas: int
    valor_referencial: Decimal
    por_estado: list[ConteoEstadoResponse]


class ResumenInventarioResponse(BaseModel):
    stock_actual_total: int
    stock_reservado_total: int
    stock_disponible_total: int
    variantes_agotadas: int
    variantes_stock_bajo: int


class SucursalComparadaResponse(BaseModel):
    """Comparativo por sucursal (ADMIN: todas; ENCARGADO: solo la suya)."""

    sucursal_id: int
    sucursal_nombre: str
    ventas: int
    monto_vendido: Decimal
    unidades: int
    ticket_promedio: Decimal
    reservas: int
    devoluciones: int


class DashboardResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    kpis: DashboardKpisResponse
    ventas_evolucion: list[SerieTemporalResponse]
    ventas_por_canal: list[VentasCanalResponse]
    top_productos: list[TopProductoResponse]
    resumen_reservas: ResumenReservasResponse
    resumen_devoluciones: ResumenDevolucionesResponse
    resumen_inventario: ResumenInventarioResponse
    comparativo_sucursales: list[SucursalComparadaResponse]
