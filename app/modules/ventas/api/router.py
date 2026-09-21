"""Endpoints del dominio Ventas.

- CU19 (CLIENTE): POST /ventas/digital convierte el carrito ACTIVO en una venta
  PENDIENTE lista para que CU22 procese el pago electronico.
- CU20 (PERSONAL): POST /ventas/presencial registra una venta de tienda (directa
  o proveniente de CU18) en estado PENDIENTE, lista para que CU21 registre el
  pago. No se procesa pago, no se confirma la venta y no se toca el inventario.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import (
    get_current_auth_context,
    get_current_cliente,
    get_current_user,
    require_permission,
)
from app.modules.autenticacion_seguridad.models.models import Cliente, Usuario
from app.modules.autenticacion_seguridad.services.service import AuthService
from app.modules.roles.services.service import RolService
from app.modules.ventas.schemas.comprobante import ComprobanteVentaResponse
from app.modules.ventas.schemas.historial import (
    CanalHistorialFiltro,
    EstadoHistorialFiltro,
    HistorialCompraDetalleResponse,
    HistorialComprasResponse,
)
from app.modules.ventas.services.comprobante_service import (
    CONTEXTO_CLIENTE,
    ComprobanteNoAutorizadoError,
    ComprobanteVentaError,
    ComprobanteVentaService,
    PagoAprobadoNoEncontradoError,
    VentaComprobanteNoEncontradaError,
    VentaNoCompletadaError,
)
from app.modules.ventas.services.historial_service import (
    PAGINA_POR_DEFECTO,
    TAMANO_PAGINA_MAXIMO,
    TAMANO_PAGINA_POR_DEFECTO,
    CompraAunNoHistoricaError,
    CompraHistorialNoAutorizadaError,
    CompraHistorialNoEncontradaError,
    HistorialComprasError,
    HistorialComprasService,
    RangoFechasHistorialError,
)
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


# ---------------------------------------------------------------------------
# CU23 - Emitir comprobante de venta (GET, solo lectura)
# ---------------------------------------------------------------------------


def _autorizar_comprobante(
    contexto: Annotated[str, Depends(get_current_auth_context)],
    usuario: Annotated[Usuario, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> Usuario:
    """Autoriza CU23 reutilizando las dependencias existentes (sin otro auth).

    - contexto "cliente": exige Cliente activo (el service limita a su venta).
    - contexto "personal": exige el RBAC GESTIONAR_VENTAS / CONSULTAR (el
      service refuerza la allowlist de roles y el alcance por sucursal).
    """
    if contexto == CONTEXTO_CLIENTE:
        if AuthService.obtener_cliente_activo(db, usuario.id) is None:
            raise _error(
                "No autorizado: el usuario no tiene perfil de cliente activo",
                status.HTTP_403_FORBIDDEN,
            )
        return usuario

    if not RolService.tiene_permiso(
        db, usuario.rol_id, FUNCION_VENTAS, "CONSULTAR"
    ):
        raise _error(
            f"No autorizado: se requiere permiso {FUNCION_VENTAS} / CONSULTAR",
            status.HTTP_403_FORBIDDEN,
        )
    return usuario


def _map_comprobante_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU23 a HTTP (nunca SQL crudo)."""
    if isinstance(exc, VentaComprobanteNoEncontradaError):
        return _error("Venta no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ComprobanteNoAutorizadoError):
        return _error(
            "No autorizado: la venta no esta a su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, VentaNoCompletadaError):
        return _error(
            "La venta aun no esta COMPLETADA",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, PagoAprobadoNoEncontradoError):
        return _error(
            "La venta no tiene un pago APROBADO",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.get("/{venta_id}/comprobante", response_model=ComprobanteVentaResponse)
def obtener_comprobante_venta(
    venta_id: int,
    contexto: Annotated[str, Depends(get_current_auth_context)],
    usuario: Annotated[Usuario, Depends(_autorizar_comprobante)],
    db: Session = Depends(get_db),
):
    """CU23 - Comprobante de una venta COMPLETADA con pago APROBADO.

    Sirve al CLIENTE (solo su propia venta) y al PERSONAL (ADMINISTRADOR en
    cualquier sucursal; ENCARGADO_SUCURSAL/CAJERO solo la suya). Es solo
    lectura: no persiste el comprobante, no crea filas y no modifica venta,
    pago, reserva ni inventario. Valido para WEB, MOVIL, PRESENCIAL directa y
    PRESENCIAL desde reserva.

    Codigos: 404 venta inexistente, 403 venta ajena/fuera de sucursal/rol no
    permitido, 409 venta no COMPLETADA o sin pago APROBADO.
    """
    try:
        return ComprobanteVentaService.generar_comprobante(
            db, usuario, contexto, venta_id
        )
    except ComprobanteVentaError as exc:
        raise _map_comprobante_error(exc)


# ---------------------------------------------------------------------------
# CU24 - Historial de compras del cliente autenticado (solo lectura)
# ---------------------------------------------------------------------------


def _map_historial_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU24 a HTTP (nunca SQL crudo)."""
    if isinstance(exc, CompraHistorialNoEncontradaError):
        return _error("Compra no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, CompraHistorialNoAutorizadaError):
        return _error(
            "No autorizado: la compra pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, CompraAunNoHistoricaError):
        return _error(
            "La venta aun no forma parte del historial de compras",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, RangoFechasHistorialError):
        return _error(
            "fecha_desde no puede ser posterior a fecha_hasta",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    raise exc


@router.get("/historial", response_model=HistorialComprasResponse)
def consultar_historial_compras(
    estado: EstadoHistorialFiltro | None = Query(
        default=None,
        description="Filtra por estado historico (COMPLETADA/CANCELADA/REEMBOLSADA).",
    ),
    canal: CanalHistorialFiltro | None = Query(
        default=None, description="Filtra por canal (WEB/MOVIL/PRESENCIAL)."
    ),
    fecha_desde: date | None = Query(
        default=None, description="Incluye todo el dia indicado."
    ),
    fecha_hasta: date | None = Query(
        default=None, description="Incluye todo el dia indicado."
    ),
    pagina: int = Query(default=PAGINA_POR_DEFECTO, ge=1),
    tamano_pagina: int = Query(
        default=TAMANO_PAGINA_POR_DEFECTO, ge=1, le=TAMANO_PAGINA_MAXIMO
    ),
    db: Session = Depends(get_db),
    cliente: Cliente = Depends(get_current_cliente),
):
    """CU24 - Historial de compras del cliente autenticado.

    Solo compras historicas (COMPLETADA/CANCELADA/REEMBOLSADA) del propio
    cliente: el alcance sale del JWT via ``get_current_cliente``, nunca de un
    ``cliente_id`` del request. Orden: mas reciente primero. Paginado en SQL.
    """
    try:
        return HistorialComprasService.consultar_historial(
            db,
            cliente,
            estado=estado.value if estado is not None else None,
            canal=canal.value if canal is not None else None,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            pagina=pagina,
            tamano_pagina=tamano_pagina,
        )
    except HistorialComprasError as exc:
        raise _map_historial_error(exc)


@router.get(
    "/historial/{venta_id}", response_model=HistorialCompraDetalleResponse
)
def obtener_detalle_compra(
    venta_id: int,
    db: Session = Depends(get_db),
    cliente: Cliente = Depends(get_current_cliente),
):
    """CU24 - Detalle de una compra del cliente autenticado.

    404 si la venta no existe, 403 si pertenece a otro cliente, 409 si aun no
    es historica (PENDIENTE/PAGADA). Incluye sucursal, items (producto, talla,
    color, cantidades, precios) y el pago mas reciente. Solo lectura: no
    modifica la venta ni el pago, y no genera comprobante (para eso el
    frontend usa CU23 con este ``venta_id``).
    """
    try:
        return HistorialComprasService.obtener_detalle_compra(
            db, cliente, venta_id
        )
    except HistorialComprasError as exc:
        raise _map_historial_error(exc)
