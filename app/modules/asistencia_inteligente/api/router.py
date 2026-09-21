"""Endpoints de Asistencia Inteligente (CLIENTE).

POST /asistencia-inteligente/recomendaciones

Requiere JWT de contexto cliente. La IA solo selecciona/rankea candidatos
reales; el backend revalida y reconstruye la respuesta desde PostgreSQL.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente
from app.modules.asistencia_inteligente.providers.base import (
    AsistenteConfiguracionError,
    AsistenteProveedorError,
    AsistenteRespuestaInvalidaError,
    AsistenteTimeoutError,
)
from app.modules.asistencia_inteligente.schemas.schemas import (
    RecomendacionRequest,
    RecomendacionesResponse,
)
from app.modules.asistencia_inteligente.services.service import (
    AsistenciaInteligenteService,
)

router = APIRouter(
    prefix="/asistencia-inteligente", tags=["Asistencia Inteligente"]
)


@router.post("/recomendaciones", response_model=RecomendacionesResponse)
def recomendar(
    datos: RecomendacionRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """Recomienda prendas a partir del catalogo e inventario REALES."""
    try:
        return AsistenciaInteligenteService.recomendar(db, datos)
    except AsistenteConfiguracionError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El asistente no esta configurado",
        )
    except AsistenteTimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="El asistente no respondio a tiempo",
        )
    except (AsistenteProveedorError, AsistenteRespuestaInvalidaError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El asistente no pudo generar recomendaciones",
        )
