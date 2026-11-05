"""Tenant-owned virtual fitting-room metadata and lifecycle endpoints.

Camera frames, landmarks and user images are never accepted by this API; the
MediaPipe try-on pipeline remains entirely on the client device.
"""
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.ventas.repositories.tenant_cliente_repository import (
    ClienteDigitalError, TenantClienteRepository,
)
from app.modules.vestidor_virtual.api.router import _map_error
from app.modules.vestidor_virtual.schemas.schemas import (
    ConfiguracionesProductoResponse, CrearSesionRequest, FinalizarPruebaRequest,
    FinalizarSesionRequest, IniciarPruebaRequest, PruebaARResponse,
    SesionARResponse,
)
from app.modules.vestidor_virtual.services.service import VestidorError, VestidorVirtualService

router = APIRouter(prefix="/tenant/vestidor-virtual", tags=["Vestidor virtual tenant"])
ClienteTenant = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


def _cliente(db: Session, tenant: TenantContext) -> SimpleNamespace:
    try:
        cliente_id = TenantClienteRepository.cliente_id(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
        )
    except ClienteDigitalError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    return SimpleNamespace(id=cliente_id)


@router.get(
    "/productos/{producto_id}/configuraciones",
    response_model=ConfiguracionesProductoResponse,
)
def obtener_configuraciones(
    producto_id: int,
    tenant: ClienteTenant,
    variante_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    try:
        return VestidorVirtualService.obtener_configuraciones(
            db, producto_id, variante_id=variante_id, color_id=color_id,
            empresa_id=tenant.empresa_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.post("/sesiones", response_model=SesionARResponse, status_code=status.HTTP_201_CREATED)
def crear_sesion(
    tenant: ClienteTenant,
    _datos: CrearSesionRequest | None = None,
    db: Session = Depends(get_db),
):
    try:
        return VestidorVirtualService.crear_sesion(
            db, _cliente(db, tenant), empresa_id=tenant.empresa_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.post(
    "/sesiones/{sesion_id}/pruebas",
    response_model=PruebaARResponse,
    status_code=status.HTTP_201_CREATED,
)
def iniciar_prueba(
    sesion_id: int, datos: IniciarPruebaRequest, tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    try:
        return VestidorVirtualService.iniciar_prueba(
            db, _cliente(db, tenant), sesion_id, datos,
            empresa_id=tenant.empresa_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.patch("/pruebas/{prueba_id}/finalizar", response_model=PruebaARResponse)
def finalizar_prueba(
    prueba_id: int, datos: FinalizarPruebaRequest, tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    try:
        return VestidorVirtualService.finalizar_prueba(
            db, _cliente(db, tenant), prueba_id, datos.estado,
            empresa_id=tenant.empresa_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.patch("/sesiones/{sesion_id}/finalizar", response_model=SesionARResponse)
def finalizar_sesion(
    sesion_id: int, datos: FinalizarSesionRequest, tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    try:
        return VestidorVirtualService.finalizar_sesion(
            db, _cliente(db, tenant), sesion_id, datos.estado,
            empresa_id=tenant.empresa_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)
