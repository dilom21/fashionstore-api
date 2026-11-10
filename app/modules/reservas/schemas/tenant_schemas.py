"""Contratos adicionales de CU12 SaaS (no reemplazan los legacy)."""

from pydantic import BaseModel, ConfigDict


class ReservaTenantEstadisticasResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    por_estado: dict[str, int]
    total_reservas: int
    total_unidades: int
