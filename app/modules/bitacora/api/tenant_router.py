"""Read-only audit log endpoints scoped to the authenticated company."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_admin_con_permiso
from app.modules.bitacora.repositories.tenant_repository import TenantBitacoraRepository
from app.modules.bitacora.schemas.schemas import (
    BitacoraCatalogosResponse,
    BitacoraDetalleResponse,
    BitacoraListResponse,
)

router = APIRouter(prefix="/tenant/bitacora", tags=["Bitacora SaaS"])

AdministradorAuditoria = Annotated[
    TenantContext,
    Depends(get_current_tenant_admin_con_permiso("PERSONAL", "CONSULTAR")),
]


def _fecha_utc(valor: datetime | None) -> datetime | None:
    if valor is None or valor.tzinfo is not None:
        return valor
    return valor.replace(tzinfo=timezone.utc)


def _serializar(fila: dict) -> dict:
    usuario = None
    if fila["usuario_id"] is not None and fila["usuario_correo"] is not None:
        usuario = {
            "id": fila["usuario_id"],
            "correo": fila["usuario_correo"],
            "rol": fila["rol_nombre"] or "",
        }
    return {
        "id": fila["id"],
        "fecha_hora": fila["fecha_hora"],
        "ip": fila["ip"],
        "accion": fila["accion"],
        "entidad_afectada": fila["entidad_afectada"],
        "descripcion": fila["descripcion"],
        "usuario": usuario,
    }


@router.get("", response_model=BitacoraListResponse)
def listar_bitacora(
    tenant: AdministradorAuditoria,
    buscar: str | None = Query(default=None, max_length=120),
    usuario_id: int | None = Query(default=None, gt=0),
    accion: str | None = Query(default=None, max_length=150),
    entidad_afectada: str | None = Query(default=None, max_length=100),
    fecha_desde: datetime | None = None,
    fecha_hasta: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    desde, hasta = _fecha_utc(fecha_desde), _fecha_utc(fecha_hasta)
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )
    filtros = {
        "buscar": buscar,
        "usuario_id": usuario_id,
        "accion": accion,
        "entidad_afectada": entidad_afectada,
        "fecha_desde": desde,
        "fecha_hasta": hasta,
    }
    filas = TenantBitacoraRepository.listar(
        db, empresa_id=tenant.empresa_id, limit=limit, offset=offset, **filtros
    )
    total = TenantBitacoraRepository.contar(db, empresa_id=tenant.empresa_id, **filtros)
    return {"items": [_serializar(fila) for fila in filas], "total": total, "limit": limit, "offset": offset}


@router.get("/catalogos", response_model=BitacoraCatalogosResponse)
def catalogos_bitacora(tenant: AdministradorAuditoria, db: Session = Depends(get_db)):
    return TenantBitacoraRepository.catalogos(db, empresa_id=tenant.empresa_id)


@router.get("/{bitacora_id}", response_model=BitacoraDetalleResponse)
def obtener_bitacora(
    bitacora_id: int,
    tenant: AdministradorAuditoria,
    db: Session = Depends(get_db),
):
    fila = TenantBitacoraRepository.obtener(
        db, empresa_id=tenant.empresa_id, bitacora_id=bitacora_id
    )
    if fila is None:
        raise HTTPException(status_code=404, detail="Evento de bitacora no encontrado")
    return _serializar(fila)
