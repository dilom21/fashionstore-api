"""Tenant-safe intelligent recommendations with backend plan enforcement."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.asistencia_inteligente.providers.base import (
    AsistenteConfiguracionError, AsistenteProveedorError,
    AsistenteRespuestaInvalidaError, AsistenteTimeoutError,
)
from app.modules.asistencia_inteligente.schemas.schemas import RecomendacionRequest, RecomendacionesResponse
from app.modules.asistencia_inteligente.services.service import AsistenciaInteligenteService

router = APIRouter(prefix="/tenant/asistencia-inteligente", tags=["Asistencia inteligente tenant"])
Cliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


@router.post("/recomendaciones", response_model=RecomendacionesResponse)
def recomendar_tenant(datos: RecomendacionRequest, cliente: Cliente, db: Session = Depends(get_db)):
    if not cliente.permite_reportes_ia:
        raise HTTPException(status_code=403, detail="El plan vigente no incluye asistencia inteligente")
    try:
        return AsistenciaInteligenteService.recomendar(db, datos, empresa_id=cliente.empresa_id)
    except AsistenteConfiguracionError as exc:
        raise HTTPException(status_code=503, detail="El asistente no está configurado") from exc
    except AsistenteTimeoutError as exc:
        raise HTTPException(status_code=504, detail="El asistente no respondió a tiempo") from exc
    except (AsistenteProveedorError, AsistenteRespuestaInvalidaError) as exc:
        raise HTTPException(status_code=502, detail="El asistente no pudo generar recomendaciones") from exc
