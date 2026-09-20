"""Endpoints de CU18 - Atender reserva de prendas.

Alcance: ADMINISTRADOR (todas las sucursales; puede filtrar por sucursal_id y
abrir/atender cualquiera), ENCARGADO_SUCURSAL y CAJERO (unicamente su sucursal:
un sucursal_id distinto responde 403). CLIENTE queda fuera aunque el RBAC le
conceda permisos de reservas: el service aplica una allowlist explicita de roles.

- preparar-venta: SOLO valida/normaliza la seleccion (no crea venta, no toca
  inventario, no cambia la reserva).
- finalizar-sin-compra: delega en sp_finalizar_reserva_sin_compra.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reservas.schemas.schemas import (
    AtencionReservaDetalleResponse,
    AtencionReservaListaResponse,
    FinalizarSinCompraRequest,
    PrepararVentaRequest,
    PrepararVentaResponse,
)
from app.modules.reservas.services.atencion_service import (
    AtencionReservaScopeError,
    AtencionReservaService,
    ReservaConVentaAsociadaError,
    RolAtencionReservaNoAutorizadoError,
    SeleccionVentaInvalidaError,
)
from app.modules.reservas.services.gestion_service import (
    LIMITE_MAXIMO,
    LIMITE_POR_DEFECTO,
    RangoFechasInvalidoError,
)
from app.modules.reservas.services.service import (
    ReservaError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    ReservaRegistroInvalidoError,
)

FUNCION_GESTIONAR_RESERVAS = "GESTIONAR_RESERVAS"


def _perm_consultar():
    """Permiso RBAC de lectura (CU04); la allowlist de roles va en el service."""
    return require_permission(FUNCION_GESTIONAR_RESERVAS, "CONSULTAR")


def _perm_ejecutar():
    """Permiso RBAC de ejecucion (CU04); la allowlist de roles va en el service."""
    return require_permission(FUNCION_GESTIONAR_RESERVAS, "EJECUTAR")


router = APIRouter(prefix="/atencion-reservas", tags=["Atencion Reservas"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: Exception) -> HTTPException:
    """Errores de dominio de CU18 (y los reutilizados de CU16/CU17) a HTTP."""
    if isinstance(exc, RolAtencionReservaNoAutorizadoError):
        return _error(
            "No autorizado: se requiere rol ENCARGADO_SUCURSAL o CAJERO",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, AtencionReservaScopeError):
        return _error(
            "No autorizado: la reserva esta fuera de la sucursal del empleado",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, ReservaNoEncontradaError):
        return _error("Reserva no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ReservaEstadoInvalidoError):
        return _error(
            "La reserva no esta CONFIRMADA o ya fue atendida",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ReservaConVentaAsociadaError):
        return _error(
            "La reserva ya tiene una venta asociada y no puede finalizarse "
            "sin compra.",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, SeleccionVentaInvalidaError):
        return _error(
            "Seleccion de compra invalida para la reserva",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, RangoFechasInvalidoError):
        return _error(
            "fecha_desde no puede ser posterior a fecha_hasta",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, ReservaRegistroInvalidoError):
        return _error(
            "No fue posible completar la atencion: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.get("", response_model=AtencionReservaListaResponse)
def listar_reservas_para_atencion(
    sucursal_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Solo ADMINISTRADOR: limita el listado a esa sucursal. "
            "ENCARGADO_SUCURSAL/CAJERO solo pueden usar la suya (otra -> 403)."
        ),
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
    """CU18 - Reservas CONFIRMADA atendibles (proximas primero).

    ADMINISTRADOR: todas las sucursales (o la indicada en sucursal_id).
    ENCARGADO_SUCURSAL/CAJERO: solo la de su empleado; ampliar con otro
    sucursal_id responde 403.
    """
    try:
        return AtencionReservaService.listar(
            db,
            usuario,
            sucursal_id=sucursal_id,
            buscar=buscar,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except ReservaError as exc:
        raise _map_error(exc)


@router.get("/{reserva_id}", response_model=AtencionReservaDetalleResponse)
def obtener_reserva_para_atencion(
    reserva_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_consultar()),
):
    """CU18 - Detalle operativo de una reserva CONFIRMADA de su sucursal.

    404 si no existe, 403 si es de otra sucursal, 409 si no esta CONFIRMADA.
    """
    try:
        return AtencionReservaService.obtener(db, usuario, reserva_id)
    except ReservaError as exc:
        raise _map_error(exc)


@router.post("/{reserva_id}/preparar-venta", response_model=PrepararVentaResponse)
def preparar_venta_reserva(
    reserva_id: int,
    datos: PrepararVentaRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_ejecutar()),
):
    """CU18 - Validar/normalizar la seleccion de compra.

    NO crea venta, NO procesa pago, NO modifica la reserva, NO toca inventario
    ni genera movimientos: la reserva sigue CONFIRMADA hasta que Venta/Pago
    complete la operacion.
    """
    try:
        return AtencionReservaService.preparar_venta(db, usuario, reserva_id, datos)
    except ReservaError as exc:
        raise _map_error(exc)


@router.post(
    "/{reserva_id}/finalizar-sin-compra",
    response_model=AtencionReservaDetalleResponse,
)
def finalizar_atencion_sin_compra(
    reserva_id: int,
    datos: FinalizarSinCompraRequest | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm_ejecutar()),
):
    """CU18 - Finalizar la atencion sin compra (CONFIRMADA -> ATENDIDA).

    Libera unicamente stock_reservado mediante sp_finalizar_reserva_sin_compra;
    stock_actual no cambia y el carrito original sigue CONVERTIDO.
    """
    try:
        return AtencionReservaService.finalizar_sin_compra(
            db, usuario, reserva_id, datos
        )
    except ReservaError as exc:
        raise _map_error(exc)
