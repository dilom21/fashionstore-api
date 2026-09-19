"""Endpoints de CU16 - Gestionar reserva de prendas (CLIENTE).

Un cliente autenticado (JWT contexto "cliente") convierte su carrito ACTIVO en
una reserva PENDIENTE. La reserva no es una venta: no crea Venta/Pago ni baja
stock_actual; solo incrementa stock_reservado via procedimiento en PostgreSQL.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente
from app.modules.carrito.services.service import (
    CarritoAjenoError,
    CarritoError,
    CarritoNoActivoError,
    CarritoNoEncontradoError,
)
from app.modules.reservas.schemas.schemas import (
    CancelarReservaRequest,
    CrearReservaRequest,
    EstadoReservaFiltro,
    ReservaDetalleResponse,
    ReservaListaResponse,
)
from app.modules.reservas.services.service import (
    CarritoVacioError,
    FechaAtencionInvalidaError,
    ReservaAjenaError,
    ReservaDuplicadaError,
    ReservaError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    ReservaRegistroInvalidoError,
    ReservaService,
    StockInsuficienteReservaError,
)

router = APIRouter(prefix="/reservas", tags=["Reservas"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio (CU16 y carrito de CU15) a HTTP.

    Nunca se devuelve SQL, nombre de constraint ni mensaje del motor: todos los
    mensajes son de negocio.
    """
    if isinstance(exc, CarritoNoEncontradoError):
        return _error("Carrito no encontrado", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ReservaNoEncontradaError):
        return _error("Reserva no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, CarritoAjenoError):
        return _error(
            "No autorizado: el carrito pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, ReservaAjenaError):
        return _error(
            "No autorizado: la reserva pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, CarritoNoActivoError):
        return _error(
            "El carrito no esta activo para reservar",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, CarritoVacioError):
        return _error(
            "El carrito no contiene prendas para reservar",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, StockInsuficienteReservaError):
        return _error(
            "Stock insuficiente para reservar las prendas del carrito",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ReservaDuplicadaError):
        return _error(
            "El carrito ya fue convertido en una reserva",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ReservaEstadoInvalidoError):
        return _error(
            "La reserva no puede cancelarse en su estado actual",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, FechaAtencionInvalidaError):
        return _error(
            "La fecha de atencion debe ser posterior a la fecha actual",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, ReservaRegistroInvalidoError):
        return _error(
            "No fue posible procesar la reserva: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post(
    "",
    response_model=ReservaDetalleResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_reserva(
    datos: CrearReservaRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU16 - Crear reserva a partir del carrito ACTIVO del cliente.

    El cuerpo solo indica carrito_id, fecha_atencion y observacion: el cliente,
    la sucursal, las prendas y las cantidades salen del carrito almacenado.
    """
    try:
        return ReservaService.crear(db, cliente, datos)
    except (ReservaError, CarritoError) as exc:
        raise _map_error(exc)


@router.get("", response_model=ReservaListaResponse)
def listar_reservas(
    estado: EstadoReservaFiltro | None = Query(
        default=None,
        description="Filtro opcional por estado real de la reserva.",
    ),
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU16 - Reservas del cliente autenticado (nunca las de otros clientes)."""
    try:
        return ReservaService.listar(
            db, cliente, estado=estado.value if estado is not None else None
        )
    except (ReservaError, CarritoError) as exc:
        raise _map_error(exc)


@router.get("/{reserva_id}", response_model=ReservaDetalleResponse)
def obtener_reserva(
    reserva_id: int,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU16 - Detalle de una reserva propia (404 si no existe, 403 si es ajena)."""
    try:
        return ReservaService.obtener(db, cliente, reserva_id)
    except (ReservaError, CarritoError) as exc:
        raise _map_error(exc)


@router.patch("/{reserva_id}/cancelar", response_model=ReservaDetalleResponse)
def cancelar_reserva(
    reserva_id: int,
    datos: CancelarReservaRequest | None = None,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU16 - Cancelar una reserva propia (PENDIENTE/CONFIRMADA).

    Libera unicamente stock_reservado. El carrito permanece CONVERTIDO.
    """
    try:
        return ReservaService.cancelar(db, cliente, reserva_id, datos)
    except (ReservaError, CarritoError) as exc:
        raise _map_error(exc)
