"""Schemas de CU28 - Reporte de auditoria (solo ADMINISTRADOR).

Se apoya en la tabla real ``bitacora`` (id, usuario_id, fecha_hora, ip,
accion, entidad_afectada, descripcion). CU28 aporta agregados + exportacion;
no duplica la pantalla de consulta de CU05.
"""

from datetime import datetime

from pydantic import BaseModel

from app.modules.reportes.schemas.common import (
    MetadatosReporteResponse,
    PaginacionResponse,
    SerieTemporalResponse,
)


class AuditoriaConteoResponse(BaseModel):
    etiqueta: str
    cantidad: int


class AuditoriaFilaResponse(BaseModel):
    bitacora_id: int
    fecha_hora: datetime
    usuario: str | None = None
    accion: str
    entidad_afectada: str | None = None
    descripcion: str | None = None


class ReporteAuditoriaResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    total_eventos: int
    eventos_por_accion: list[AuditoriaConteoResponse]
    eventos_por_entidad: list[AuditoriaConteoResponse]
    eventos_por_usuario: list[AuditoriaConteoResponse]
    evolucion: list[SerieTemporalResponse]
    paginacion: PaginacionResponse
    items: list[AuditoriaFilaResponse]
