"""CU12 SaaS - gestion de reservas de sucursal (empresa desde JWT)."""
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
    CancelarReservaSucursalRequest,
    EstadoReservaFiltro,
    ReservaSucursalDetalleResponse,
    ReservaSucursalListaResponse,
)
from app.modules.reservas.schemas.tenant_schemas import (
    ReservaTenantEstadisticasResponse,
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
    ReservaTenantSucursalNoEncontradaError,
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
    prefix="/tenant/reservas-sucursal", tags=["Reservas SaaS CU12"]
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
            detail="No autorizado: se requiere ADMINISTRADOR o ENCARGADO_SUCURSAL",
        )
    if isinstance(exc, (ReservaTenantScopeError,)):
        return HTTPException(
            status_code=403,
            detail="No autorizado: la reserva o sucursal esta fuera de su alcance",
        )
    if isinstance(exc, (ReservaTenantNoEncontradaError, ReservaTenantSucursalNoEncontradaError)):
        return HTTPException(status_code=404, detail="Reserva no encontrada")
    if isinstance(exc, ReservaTenantEstadoError):
        return HTTPException(
            status_code=409,
            detail="La reserva no permite esa transicion en su estado actual",
        )
    if isinstance(exc, ReservaTenantRangoError):
        return HTTPException(
            status_code=422,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )
    if isinstance(exc, (ReservaTenantRegistroError, ReservaTenantIntegridadError)):
        return HTTPException(
            status_code=409, detail="No fue posible completar la operacion"
        )
    if isinstance(exc, ReservaTenantDeshabilitada):
        return HTTPException(
            status_code=503,
            detail="Procedimiento CU12 o aislamiento SQL no disponible",
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Reserva no disponible")
    raise exc


@router.get("", response_model=ReservaSucursalListaResponse)
def listar_reservas_gestion(
    tenant: TenantLectura,
    db: Session = Depends(get_db),
    sucursal_id: int | None = Query(default=None, gt=0),
    estado: EstadoReservaFiltro | None = Query(default=None),
    cliente_id: int | None = Query(default=None, gt=0),
    buscar: str | None = Query(default=None, min_length=1, max_length=100),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    limit: int = Query(default=LIMITE_POR_DEFECTO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(default=0, ge=0),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.listar_gestion(
            db,
            tenant,
            sucursal_id=sucursal_id,
            estado=estado.value if estado is not None else None,
            cliente_id=cliente_id,
            buscar=buscar,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.get("/estadisticas", response_model=ReservaTenantEstadisticasResponse)
def estadisticas_reservas_gestion(
    tenant: TenantLectura,
    db: Session = Depends(get_db),
    sucursal_id: int | None = Query(default=None, gt=0),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.estadisticas(
            db,
            tenant,
            sucursal_id=sucursal_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.get("/{reserva_id}", response_model=ReservaSucursalDetalleResponse)
def obtener_reserva_gestion(
    reserva_id: int,
    tenant: TenantLectura,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.obtener_gestion(
            db, tenant, reserva_id
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.patch("/{reserva_id}/confirmar", response_model=ReservaSucursalDetalleResponse)
def confirmar_reserva_gestion(
    reserva_id: int,
    tenant: TenantEjecutar,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.confirmar(db, tenant, reserva_id)
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc


@router.patch("/{reserva_id}/cancelar", response_model=ReservaSucursalDetalleResponse)
def cancelar_reserva_gestion(
    reserva_id: int,
    tenant: TenantEjecutar,
    datos: CancelarReservaSucursalRequest | None = None,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ReservaTenantGestionService.cancelar(
            db, tenant, reserva_id, datos
        )
    except ReservaTenantError as exc:
        raise _map_error(exc) from exc
