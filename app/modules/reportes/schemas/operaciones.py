"""Schemas de CU28 - Reservas, devoluciones y pagos.

- Reservas: conversion medianto la relacion real ``Venta.reserva_id``.
- Devoluciones: "unidades devueltas" y "valor referencial" solo cuentan
  devoluciones ``COMPLETADA`` (producto ya reincorporado).
  ``valor_referencial = SUM(DetalleDevolucion.cantidad * DetalleVenta.precio_unitario)``.
  NO es un reembolso financiero (CU25 no lo hace).
- Pagos: son TRANSACCIONES de pago (1 venta -> N intentos). Nunca se usa
  ``COUNT(pago)`` como cantidad de ventas.
"""

from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import (
    MetadatosReporteResponse,
    SerieTemporalResponse,
)


# --- Reservas ---------------------------------------------------------------


class ReservaEstadoResponse(BaseModel):
    estado: str
    cantidad: int
    unidades: int


class ReservaSucursalResponse(BaseModel):
    sucursal_id: int
    sucursal_nombre: str
    cantidad: int
    unidades: int


class ReservaResumenResponse(BaseModel):
    total_reservas: int
    cantidad_total_unidades: int
    reservas_atendidas: int
    reservas_convertidas_en_venta: int
    tasa_conversion: Decimal


class ReporteReservasResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: ReservaResumenResponse
    reservas_por_estado: list[ReservaEstadoResponse]
    reservas_por_sucursal: list[ReservaSucursalResponse]
    evolucion: list[SerieTemporalResponse]


# --- Devoluciones -----------------------------------------------------------


class DevolucionEstadoResponse(BaseModel):
    estado: str
    cantidad: int
    unidades: int
    valor_referencial: Decimal


class DevolucionSucursalResponse(BaseModel):
    sucursal_id: int
    sucursal_nombre: str
    cantidad: int
    unidades: int


class DevolucionProductoResponse(BaseModel):
    producto_id: int
    producto_nombre: str
    unidades: int
    valor_referencial: Decimal


class DevolucionMotivoResponse(BaseModel):
    motivo: str
    cantidad_devoluciones: int
    unidades: int


class DevolucionResumenResponse(BaseModel):
    total_devoluciones: int
    completadas: int
    unidades_devueltas: int
    valor_referencial: Decimal


class ReporteDevolucionesResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: DevolucionResumenResponse
    devoluciones_por_estado: list[DevolucionEstadoResponse]
    devoluciones_por_sucursal: list[DevolucionSucursalResponse]
    evolucion: list[SerieTemporalResponse]
    productos_mas_devueltos: list[DevolucionProductoResponse]
    motivos_mas_frecuentes: list[DevolucionMotivoResponse]


# --- Pagos ------------------------------------------------------------------


class PagoEstadoResponse(BaseModel):
    estado: str
    cantidad: int
    monto: Decimal


class PagoMetodoResponse(BaseModel):
    metodo: str
    cantidad: int
    monto: Decimal


class PagoPasarelaResponse(BaseModel):
    pasarela: str
    cantidad: int
    monto: Decimal


class PagoResumenResponse(BaseModel):
    cantidad_pagos: int
    monto_total_procesado: Decimal
    monto_aprobado: Decimal
    nota: str


class ReportePagosResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: PagoResumenResponse
    pagos_por_estado: list[PagoEstadoResponse]
    pagos_por_metodo: list[PagoMetodoResponse]
    pagos_por_pasarela: list[PagoPasarelaResponse]
    evolucion: list[SerieTemporalResponse]
