"""API tenant CU19/CU20: sincronizacion offline idempotente."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.offline.schemas import (
    OperacionOfflineRequest, OperacionOfflineResponse,
)
from app.modules.offline.service import (
    OfflineNoEncontrada, OfflineService,
)

router = APIRouter(prefix="/tenant/offline", tags=["CU19/CU20 Offline"])


def _comprobar_habilitado() -> None:
    if not settings.saas_auth_enabled or not settings.saas_offline_enabled:
        raise HTTPException(
            status_code=503,
            detail="Sincronizacion offline no habilitada",
        )


@router.post(
    "/operaciones",
    response_model=OperacionOfflineResponse,
    responses={200: {"model": OperacionOfflineResponse}},
)
def crear_operacion_offline(
    datos: OperacionOfflineRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tenant: TenantContext = Depends(get_current_tenant_context),
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    respuesta, codigo = OfflineService.ejecutar(
        db, tenant, datos, idempotency_key=idempotency_key
    )
    return JSONResponse(
        status_code=codigo,
        content=respuesta.model_dump(mode="json"),
    )


@router.get(
    "/operaciones/{operacion_uuid}",
    response_model=OperacionOfflineResponse,
)
def consultar_operacion_offline(
    operacion_uuid: UUID,
    tenant: TenantContext = Depends(get_current_tenant_context),
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        respuesta = OfflineService.consultar(db, tenant, operacion_uuid)
    except OfflineNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=exc.mensaje) from exc
    return respuesta
