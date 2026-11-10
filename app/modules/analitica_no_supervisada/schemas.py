from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PatronesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodo_dias: Literal[30, 90] = 30
    sucursal_id: int | None = Field(default=None, ge=1)
    max_productos: int = Field(default=100, ge=3, le=250)


class ClusterResumen(BaseModel):
    cluster_id: int
    etiqueta: str
    productos: int
    unidades_promedio: float
    ingresos_promedio: float
    stock_promedio: float


class ProductoCluster(BaseModel):
    producto_id: int
    producto_nombre: str
    cluster_id: int
    etiqueta: str
    unidades_vendidas: float
    ingresos: float
    dias_con_ventas: int
    stock_disponible: float


class PatronesResponse(BaseModel):
    tipo: Literal["PATRONES_NO_SUPERVISADOS"]
    analisis_id: str
    generado_en: datetime
    periodo_dias: Literal[30, 90]
    fecha_desde: date
    fecha_hasta: date
    sucursal_id: int | None
    sucursal_nombre: str | None
    modelo: str
    version_modelo: str
    procedencia_entrenamiento: Literal["SINTETICO"]
    procedencia_datos: Literal["REALES_TENANT"]
    features: list[str]
    observaciones: int
    metricas_evaluacion: dict[str, float | int | None]
    clusters: list[ClusterResumen]
    productos: list[ProductoCluster]
    advertencias: list[str]
    estado: Literal["OK", "SIN_DATOS", "NO_DISPONIBLE"]
