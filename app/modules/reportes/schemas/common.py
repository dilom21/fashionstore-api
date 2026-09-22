"""Schemas comunes de CU28 - Reportes.

Convenciones:

- Montos: ``Decimal`` en JSON (nunca float como autoridad). El formateo con
  "Bs" solo ocurre en los exportadores PDF/XLSX/CSV.
- Fechas: intervalo semiabierto ``[desde, hasta_exclusivo)`` sobre
  ``fecha_hora``/``fecha_orden``/``fecha_hora`` segun la entidad real.
- ``es_global`` reemplaza a ``global`` (palabra reservada de Python).
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class PeriodoReporteResponse(BaseModel):
    """Rango efectivo aplicado (None = sin filtro de fechas)."""

    fecha_desde: date | None = None
    fecha_hasta: date | None = None


class AlcanceReporteResponse(BaseModel):
    """Alcance real aplicado: global (ADMIN) o sucursal concreta."""

    es_global: bool
    sucursal_id: int | None = None
    sucursal_nombre: str | None = None


class PaginacionResponse(BaseModel):
    pagina: int
    tamano_pagina: int
    total_registros: int
    total_paginas: int


class MetadatosReporteResponse(BaseModel):
    """Cabecera comun a todos los reportes (vista previa y exportacion)."""

    tipo: str
    periodo: PeriodoReporteResponse
    alcance: AlcanceReporteResponse
    generado_en: datetime


class SerieTemporalResponse(BaseModel):
    """Punto de una evolucion agrupada en SQL (agrupacion DIA o MES)."""

    periodo: str
    cantidad: int
    monto: Decimal | None = None
    unidades: int | None = None


class MontoFilaResponse(BaseModel):
    """Fila generica de ranking/agrupacion con etiqueta y monto."""

    etiqueta: str
    cantidad: int
    unidades: int | None = None
    monto: Decimal | None = None
