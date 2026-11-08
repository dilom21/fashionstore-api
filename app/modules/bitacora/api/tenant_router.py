"""CU05 SaaS: consulta de bitacora aislada por empresa."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import require_tenant_permission
from app.modules.bitacora.schemas.schemas import (
    BitacoraCatalogosResponse,
    BitacoraDetalleResponse,
    BitacoraListResponse,
)
from app.modules.bitacora.services.service import (
    BitacoraService,
    PaginacionInvalidaError,
    RangoFechasInvalidoError,
)

router = APIRouter(prefix="/tenant/bitacora", tags=["Bitacora SaaS"])
_permiso_bitacora = require_tenant_permission("CONSULTAR_BITACORA", "CONSULTAR")


def _personal_autorizado(
    tenant: Annotated[TenantContext, Depends(_permiso_bitacora)],
) -> TenantContext:
    if tenant.contexto != "personal":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere personal autorizado de la empresa",
        )
    return tenant


TenantBitacora = Annotated[TenantContext, Depends(_personal_autorizado)]


@router.get("", response_model=BitacoraListResponse)
def listar_bitacora_tenant(
    tenant: TenantBitacora,
    buscar: str | None = None,
    usuario_id: int | None = None,
    accion: str | None = None,
    entidad_afectada: str | None = None,
    fecha_desde: datetime | None = None,
    fecha_hasta: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    try:
        return BitacoraService.listar(
            db,
            empresa_id=tenant.empresa_id,
            buscar=buscar,
            usuario_id=usuario_id,
            accion=accion,
            entidad_afectada=entidad_afectada,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except RangoFechasInvalidoError as exc:
        raise HTTPException(400, "fecha_desde no puede ser posterior a fecha_hasta") from exc
    except PaginacionInvalidaError as exc:
        raise HTTPException(
            400, "limit debe estar entre 1 y 100 y offset no puede ser negativo"
        ) from exc


@router.get("/catalogos", response_model=BitacoraCatalogosResponse)
def catalogos_bitacora_tenant(
    tenant: TenantBitacora, db: Session = Depends(get_db)
):
    return BitacoraService.catalogos(db, empresa_id=tenant.empresa_id)


@router.get("/{bitacora_id}", response_model=BitacoraDetalleResponse)
def obtener_bitacora_tenant(
    bitacora_id: int, tenant: TenantBitacora, db: Session = Depends(get_db)
):
    detalle = BitacoraService.obtener(
        db, bitacora_id, empresa_id=tenant.empresa_id
    )
    if detalle is None:
        raise HTTPException(404, "Evento de bitacora no encontrado")
    return detalle
