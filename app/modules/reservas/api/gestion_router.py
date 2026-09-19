"""Endpoints de CU17 - Gestionar reservas de sucursal (personal).

Solo ADMINISTRADOR y ENCARGADO_SUCURSAL: ademas del permiso RBAC
GESTIONAR_RESERVAS, el service valida el rol explicitamente (esa funcion tambien
esta asignada a CAJERO y CLIENTE, que deben quedar fuera de CU17).

No se implementa CU18 (transicion a ATENDIDA) ni logica de VENCIDA.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reservas.schemas.schemas import (
    CancelarReservaSucursalRequest,
    EstadoReservaFiltro,
    ReservaSucursalDetalleResponse,
    ReservaSucursalListaResponse,
)
from app.modules.reservas.services.gestion_service import (
    LIMITE_MAXIMO,
    LIMITE_POR_DEFECTO,
    RangoFechasInvalidoError,
    ReservaSucursalScopeError,
    ReservaSucursalService,
    RolGestionReservaNoAutorizadoError,
)
from app.modules.reservas.services.service import (
    ReservaError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    ReservaRegistroInvalidoError,
)

FUNCION_GESTIONAR_RESERVAS = "GESTIONAR_RESERVAS"


def _perm_consultar():
    """Permiso RBAC de lectura (CU04); el rol se valida tambien en el service."""
    return require_permission(FUNCION_GESTIONAR_RESERVAS, "CONSULTAR")


def _perm_ejecutar():
    """Permiso RBAC de ejecucion (CU04); el rol se valida tambien en el service."""
    return require_permission(FUNCION_GESTIONAR_RESERVAS, "EJECUTAR")


router = APIRouter(prefix="/reservas-sucursal", tags=["Reservas Sucursal"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: Exception) -> HTTPException:
    """Errores de dominio de CU17 (y los reutilizados de CU16) a HTTP."""
    if isinstance(exc, RolGestionReservaNoAutorizadoError):
        return _error(
            "No autorizado: se requiere rol ADMINISTRADOR o ENCARGADO_SUCURSAL",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, ReservaSucursalScopeError):
        return _error(
            "No autorizado: la reserva o sucursal esta fuera de su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, ReservaNoEncontradaError):
        return _error("Reserva no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ReservaEstadoInvalidoError):
        return _error(
            "La reserva no permite esa transicion en su estado actual",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, RangoFechasInvalidoError):
        return _error(
            "fecha_desde no puede ser posterior a fecha_hasta",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, ReservaRegistroInvalidoError):
        return _error(
            "No fue posible procesar la reserva: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.get("", response_model=ReservaSucursalListaResponse)
def listar_reservas_sucursal(
    sucursal_id: int | None = Query(
        default=None,
        gt=0,
        description="Administrador puede elegirla; el Encargado queda limitado "
        "a su sucursal (otra sucursal responde 403).",
    ),
    estado: EstadoReservaFiltro | None = Query(
        default=None, description="Filtro por estado real de la reserva."
    ),
    buscar: str | None = Query(
        default=None,
        min_length=1,
        max_length=100,
        description="Busqueda por id de reserva, nombre o apellido del cliente.",
    ),
    fecha_desde: date | None = Query(
        default=None, description="Filtra por fecha_atencion (inclusive)."
    ),
    fecha_hasta: date | None = Query(
        default=None, description="Filtra por fecha_atencion (inclusive)."
    ),
    limit: int = Query(default=LIMITE_POR_DEFECTO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_consultar()),
):
    """CU17 - Reservas de sucursal (proximas atenciones primero)."""
    try:
        return ReservaSucursalService.listar(
            db,
            usuario,
            sucursal_id=sucursal_id,
            estado=estado.value if estado is not None else None,
            buscar=buscar,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except ReservaError as exc:
        raise _map_error(exc)


@router.get("/{reserva_id}", response_model=ReservaSucursalDetalleResponse)
def obtener_reserva_sucursal(
    reserva_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_consultar()),
):
    """CU17 - Detalle de una reserva (404 si no existe, 403 si es de otra sucursal)."""
    try:
        return ReservaSucursalService.obtener(db, usuario, reserva_id)
    except ReservaError as exc:
        raise _map_error(exc)


@router.patch("/{reserva_id}/confirmar", response_model=ReservaSucursalDetalleResponse)
def confirmar_reserva_sucursal(
    reserva_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_ejecutar()),
):
    """CU17 - Confirmar una reserva PENDIENTE (no toca inventario)."""
    try:
        return ReservaSucursalService.confirmar(db, usuario, reserva_id)
    except ReservaError as exc:
        raise _map_error(exc)


@router.patch("/{reserva_id}/cancelar", response_model=ReservaSucursalDetalleResponse)
def cancelar_reserva_sucursal(
    reserva_id: int,
    datos: CancelarReservaSucursalRequest | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_ejecutar()),
):
    """CU17 - Cancelar una reserva PENDIENTE/CONFIRMADA (libera stock_reservado)."""
    try:
        return ReservaSucursalService.cancelar(db, usuario, reserva_id, datos)
    except ReservaError as exc:
        raise _map_error(exc)
