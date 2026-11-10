from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrediccionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producto_id: int = Field(ge=1)
    sucursal_id: int | None = Field(default=None, ge=1)
    horizonte_dias: Literal[7, 30] = 7


class PrediccionResponse(BaseModel):
    tipo: Literal["DEMANDA_UNIDADES"]
    horizonte_dias: Literal[7, 30]
    unidad: Literal["UNIDADES_VENDIDAS"]
    producto_id: int
    producto_nombre: str
    sucursal_id: int | None
    sucursal_nombre: str | None
    fecha_corte_datos: date
    valor_predicho: float
    historico_dias: int
    modelo: str
    version_modelo: str
    procedencia_entrenamiento: Literal["SINTETICO"]
    metricas_validacion: dict[str, float]
    baseline_metricas_test: dict[str, float]
    aviso: str
