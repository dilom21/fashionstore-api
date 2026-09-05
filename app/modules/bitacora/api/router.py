from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.bitacora.schemas.schemas import (
    BitacoraCatalogosResponse,
    BitacoraDetalleResponse,
    BitacoraListResponse,
)
from app.modules.bitacora.services.service import (
    PaginacionInvalidaError,
    RangoFechasInvalidoError,
    BitacoraService,
)

router = APIRouter(prefix="/bitacora", tags=["Bitacora"])

FUNCION_CONSULTAR_BITACORA = "CONSULTAR_BITACORA"


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


@router.get("", response_model=BitacoraListResponse)
def listar_bitacora(
    buscar: str | None = None,
    usuario_id: int | None = None,
    accion: str | None = None,
    entidad_afectada: str | None = None,
    fecha_desde: datetime | None = None,
    fecha_hasta: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_CONSULTAR_BITACORA, "CONSULTAR")
    ),
):
    """Listado paginado y filtrable de eventos de bitacora (solo lectura)."""
    try:
        return BitacoraService.listar(
            db,
            buscar=buscar,
            usuario_id=usuario_id,
            accion=accion,
            entidad_afectada=entidad_afectada,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except RangoFechasInvalidoError:
        raise _error("fecha_desde no puede ser posterior a fecha_hasta")
    except PaginacionInvalidaError:
        raise _error(
            "limit debe estar entre 1 y 100 y offset no puede ser negativo"
        )


@router.get("/catalogos", response_model=BitacoraCatalogosResponse)
def catalogos_bitacora(
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_CONSULTAR_BITACORA, "CONSULTAR")
    ),
):
    """Catalogos reales de BD (acciones, entidades, usuarios) para filtros."""
    return BitacoraService.catalogos(db)


@router.get("/{bitacora_id}", response_model=BitacoraDetalleResponse)
def obtener_evento_bitacora(
    bitacora_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_CONSULTAR_BITACORA, "CONSULTAR")
    ),
):
    detalle = BitacoraService.obtener(db, bitacora_id)
    if detalle is None:
        raise _error("Evento de bitacora no encontrado", status.HTTP_404_NOT_FOUND)
    return detalle
