"""Endpoints del dominio Ventas.

- CU19 (CLIENTE): POST /ventas/digital convierte el carrito ACTIVO en una venta
  PENDIENTE lista para que CU22 procese el pago electronico.
- CU20 (PERSONAL): POST /ventas/presencial registra una venta de tienda (directa
  o proveniente de CU18) en estado PENDIENTE, lista para que CU21 registre el
  pago. No se procesa pago, no se confirma la venta y no se toca el inventario.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente, require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.carrito.services.service import (
    CarritoAjenoError,
    CarritoError,
    CarritoNoActivoError,
    CarritoNoEncontradoError,
)
from app.modules.ventas.schemas.schemas import (
    RealizarCompraDigitalRequest,
    RegistrarVentaPresencialRequest,
    VentaDigitalResponse,
    VentaPresencialResponse,
)
from app.modules.ventas.services.presencial_service import (
    ClienteInactivoError,
    ClienteNoEncontradoError,
    EmpleadoNoValidoError,
    InventarioDuplicadoError,
    InventarioNoEncontradoError,
    InventarioSucursalInvalidaError,
    ProductoNoDisponibleError,
    RegistroVentaInvalidoError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    SeleccionReservaInvalidaError,
    StockInsuficienteError,
    VentaPresencialError,
    VentaPresencialScopeError,
    VentaPresencialService,
    VentaReservaDuplicadaError,
    VentaSinItemsError,
)
from app.modules.ventas.services.service import (
    CarritoVacioError,
    InventarioNoDisponibleVentaError,
    InventarioSucursalVentaError,
    StockInsuficienteVentaError,
    VentaCarritoDuplicadaError,
    VentaError,
    VentaRegistroInvalidoError,
    VentaService,
)

# RBAC real del proyecto (modulo VENTAS): ADMINISTRADOR, ENCARGADO_SUCURSAL y
# CAJERO tienen GESTIONAR_VENTAS/CREAR; CLIENTE no. El service refuerza la
# allowlist de roles y el alcance por sucursal.
FUNCION_VENTAS = "GESTIONAR_VENTAS"

router = APIRouter(prefix="/ventas", tags=["Ventas"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio (CU19 y carrito de CU15) a HTTP.

    Nunca se devuelve SQL, nombre de constraint ni mensaje del motor: todos los
    mensajes son de negocio.
    """
    if isinstance(exc, CarritoNoEncontradoError):
        return _error("Carrito no encontrado", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, CarritoAjenoError):
        return _error(
            "No autorizado: el carrito pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, CarritoNoActivoError):
        return _error(
            "El carrito no esta activo para comprar",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, CarritoVacioError):
        return _error(
            "El carrito no contiene prendas para comprar",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, VentaCarritoDuplicadaError):
        return _error(
            "El carrito ya fue convertido en una venta",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, StockInsuficienteVentaError):
        return _error(
            "Stock insuficiente para completar la compra",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, InventarioNoDisponibleVentaError):
        return _error(
            "Uno de los productos del carrito no esta disponible",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, InventarioSucursalVentaError):
        return _error(
            "El inventario no pertenece a la sucursal de la venta",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, VentaRegistroInvalidoError):
        return _error(
            "No fue posible procesar la compra: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post(
    "/digital",
    response_model=VentaDigitalResponse,
    status_code=status.HTTP_201_CREATED,
)
def realizar_compra_digital(
    datos: RealizarCompraDigitalRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU19 - Realizar compra digital a partir del carrito ACTIVO.

    El cuerpo solo indica carrito_id y canal: el cliente, la sucursal, las
    prendas, las cantidades y los precios salen de la base de datos.
    """
    try:
        return VentaService.realizar_compra_digital(db, cliente, datos)
    except (VentaError, CarritoError) as exc:
        raise _map_error(exc)


def _map_presencial_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU20 a HTTP (mensajes de negocio)."""
    if isinstance(exc, VentaPresencialScopeError):
        return _error(
            "No autorizado: la operacion esta fuera de su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, EmpleadoNoValidoError):
        return _error(
            "No autorizado: el usuario no tiene un empleado activo",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, (InventarioNoEncontradoError, ClienteNoEncontradoError)):
        return _error(
            "Uno de los recursos de la venta no existe",
            status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, ReservaNoEncontradaError):
        return _error("Reserva no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, VentaSinItemsError):
        return _error(
            "La venta debe incluir al menos una prenda",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, SeleccionReservaInvalidaError):
        return _error(
            "La seleccion de compra no coincide con la reserva",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(
        exc,
        (
            StockInsuficienteError,
            ProductoNoDisponibleError,
            InventarioSucursalInvalidaError,
            InventarioDuplicadoError,
            ReservaEstadoInvalidoError,
            VentaReservaDuplicadaError,
            ClienteInactivoError,
            RegistroVentaInvalidoError,
        ),
    ):
        return _error(
            "No fue posible registrar la venta presencial",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, VentaPresencialError):
        return _error(
            "No fue posible registrar la venta presencial",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post(
    "/presencial",
    response_model=VentaPresencialResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_venta_presencial(
    datos: RegistrarVentaPresencialRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_permission(FUNCION_VENTAS, "CREAR")),
):
    """CU20 - Registrar una venta presencial (PENDIENTE).

    Directa: el personal envia inventario_id + cantidad; la sucursal sale de las
    filas de inventario y el empleado de la sesion. Desde reserva: se envia
    reserva_id + las prendas compradas y se reutiliza la validacion de CU18.

    No registra pago, no confirma la venta y no modifica inventario ni reserva:
    eso corresponde a CU21. Devuelve venta_id/total/estado para CU21.
    """
    try:
        return VentaPresencialService.registrar(db, usuario, datos)
    except VentaPresencialError as exc:
        raise _map_presencial_error(exc)
