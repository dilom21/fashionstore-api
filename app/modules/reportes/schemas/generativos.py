"""Contratos estrictos de CU23 (reportes generativos).

La intención es la única parte que puede proponer la IA. Las filas siempre se
reconstruyen desde los servicios de reportes existentes.
"""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TipoReporteGenerativo = Literal[
    "VENTAS_SUCURSAL",
    "PRODUCTOS_TOP",
    "EVOLUCION_VENTAS",
    "INVENTARIO_BAJO",
]


class ReporteGenerativoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(min_length=1, max_length=500)

    @field_validator("consulta")
    @classmethod
    def consulta_limpia(cls, valor: str) -> str:
        limpio = " ".join(valor.split())
        if not limpio:
            raise ValueError("La consulta no puede estar vacia")
        return limpio


class IntencionReporteGenerativo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tipo: TipoReporteGenerativo
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    sucursal_id: int | None = Field(default=None, ge=1)
    top: int = Field(default=10, ge=1, le=50)
    agrupacion: Literal["DIA", "MES"] = "DIA"
    umbral_stock_bajo: int = Field(default=5, ge=0, le=1000)


class ReporteGenerativoFila(BaseModel):
    etiqueta: str
    valores: dict[str, str | int | Decimal | None]


class ReporteGenerativoGrafico(BaseModel):
    tipo: Literal["BAR", "LINE"]
    etiqueta: str
    categorias: list[str]
    valores: list[Decimal | int]


class ReporteGenerativoResponse(BaseModel):
    consulta: str
    tipo: TipoReporteGenerativo
    titulo: str
    resumen: str | None
    filtros_aplicados: IntencionReporteGenerativo
    fuente: str
    proveedor_ia: str
    sin_resultados: bool
    filas: list[ReporteGenerativoFila]
    grafico: ReporteGenerativoGrafico | None
