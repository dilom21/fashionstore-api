"""CU12 SaaS - atencion de reservas confirmadas (empresa desde JWT)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import require_tenant_permission
from app.modules.reservas.schemas.schemas import (
    AtencionReservaDetalleResponse,
    AtencionReservaListaResponse,
    FinalizarSinCompraRequest,
    PrepararVentaRequest,
    PrepararVentaResponse,
)
from app.modules.reservas.services.tenant_gestion_service import (
    LIMITE_MAXIMO,
    LIMITE_POR_DEFECTO,
    ReservaTenantEstadoError,
    ReservaTenantError,
    ReservaTenantNoEncontradaError,
    ReservaTenantRangoError,
    ReservaTenantRegistroError,
    ReservaTenantRolNoAutorizadoError,
    ReservaTenantScopeError,
    ReservaTenantSeleccionError,
    ReservaTenantSucursalNoEncontradaError,
    ReservaTenantVentaAsociadaError,
    ReservaTenantGestionService,
)
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    ReservaTenantDeshabilitada,
    ReservaTenantIntegridadError,
)

FUNCION = "GESTIONAR_RESERVAS"
TenantLectura = Annotated[
    TenantContext, Depends(require_tenant_permission(FUNCION, "CONSULTAR"))
]
TenantEjecutar = Annotated[
    TenantContext, Depends(require_tenant_permission(FUNCION, "EJECUTAR"))
]

router = APIRouter(
    prefix="/tenant/atencion-reservas", tags=["Atencion Reservas SaaS CU12"]
)


def _comprobar_habilitado() -> None:
    if not settings.saas_auth_enabled or not settings.saas_reservas_enabled:
        raise HTTPException(
            status_code=503, detail="Reservas SaaS aun no habilitadas"
        )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReservaTenantRolNoAutorizadoError):
        return HTTPException(
            status_code=403,
            detail="No autorizado: se requiere ADMINISTRADOR, ENCARGADO_SUCURSAL o CAJERO",
        )
    if isinstance(exc, ReservaTenantScopeError):
        return HTTPException(
            status_code=403,
            detail="No autorizado: la reserva esta fuera de su sucursal",
        )
    if isinstance(exc, (ReservaTenantNoEncontradaError, ReservaTenantSucursalNoEncontradaError)):
        return HTTPException(status_code=404, detail="Reserva no encontrada")
    if isinstance(exc, ReservaTenantEstadoError):
        return HTTPException(
            status_code=409,
            detail="La reserva no esta CONFIRMADA o ya fue atendida",
        )
    if isinstance(exc, ReservaTenantVentaAsociadaError):
        return HTTPException(
            status_code=409,
            detail="La reserva ya tiene una venta asociada",
        )
    if isinstance(exc, ReservaTenantSeleccionError):
        return HTTPException(
            status_code=422, detail="Seleccion de compra invalida para la reserva"
        )
    if isinstance(exc, ReservaTenantRangoError):
        return HTTPException(
            status_code=422,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )
    if isinstance(exc, (ReservaTenantRegistroError, ReservaTenantIntegridadError)):
        return HTTPException(
            status_code=409, detail="No fue posible completar la atencion"
        )
    if isinstance(exc, ReservaTenantDeshabilitada):
        return HTTPException(
            status_code=503,
            detail="Procedimiento CU12 o aislamiento SQL no disponible",
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Reserva no disponible")
    raise exc


@router.get("", response_model=AtencionReservaListaResponse)
def listar_reservas_atencion(
    tenant: TenantLectura,
    db: Session = Depends(get_db),
    sucursal_id: int | None = Query(default=None, gt=0),
    buscar: str | None = Query(default=None, min_length=1, max_length=100),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    limit: int = Query(default=LIMITE_POR_DEFECTO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(default=0, ge=0),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.listar_atencion(
            db,
            tenant,
            sucursal_id=sucursal_id,
            buscar=buscar,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.get("/{reserva_id}", response_model=AtencionReservaDetalleResponse)
def obtener_reserva_atencion(
    reserva_id: int,
    tenant: TenantLectura,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.obtener_atencion(
            db, tenant, reserva_id
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.post("/{reserva_id}/preparar-venta", response_model=PrepararVentaResponse)
def preparar_venta_atencion(
    reserva_id: int,
    datos: PrepararVentaRequest,
    tenant: TenantEjecutar,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.preparar_venta(
            db, tenant, reserva_id, datos
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/{reserva_id}/finalizar-sin-compra",
    response_model=AtencionReservaDetalleResponse,
)
def finalizar_sin_compra_atencion(
    reserva_id: int,
    tenant: TenantEjecutar,
    datos: FinalizarSinCompraRequest | None = None,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.finalizar_sin_compra(
            db, tenant, reserva_id, datos
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc
