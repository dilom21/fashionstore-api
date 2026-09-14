from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente
from app.modules.carrito.schemas.schemas import (
    ActualizarCantidadRequest,
    AgregarItemCarritoRequest,
    CarritoDetalleResponse,
    CarritoListaResponse,
)
from app.modules.carrito.services.service import (
    CarritoAjenoError,
    CarritoDuplicadoError,
    CarritoError,
    CarritoNoActivoError,
    CarritoNoEncontradoError,
    CarritoRegistroInvalidoError,
    CarritoService,
    DetalleNoEncontradoError,
    InventarioNoEncontradoError,
    InventarioSucursalInvalidaError,
    ProductoNoDisponibleError,
    StockInsuficienteError,
)

router = APIRouter(prefix="/carritos", tags=["Carrito"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: CarritoError) -> HTTPException:
    """Traduce errores de negocio de CU15 a respuestas HTTP del proyecto."""
    if isinstance(exc, (CarritoNoEncontradoError,)):
        return _error("Carrito no encontrado", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, DetalleNoEncontradoError):
        return _error("Linea de carrito no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, InventarioNoEncontradoError):
        return _error("Inventario no encontrado", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, CarritoAjenoError):
        return _error(
            "No autorizado: el carrito pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, CarritoNoActivoError):
        return _error(
            "El carrito no esta activo", status.HTTP_409_CONFLICT
        )
    if isinstance(exc, InventarioSucursalInvalidaError):
        return _error(
            "El inventario no pertenece a la sucursal indicada",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ProductoNoDisponibleError):
        return _error(
            "El producto no esta disponible", status.HTTP_409_CONFLICT
        )
    if isinstance(exc, StockInsuficienteError):
        return _error(
            "Stock insuficiente para la cantidad solicitada",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, CarritoDuplicadoError):
        return _error(
            "Ya existe un carrito activo para esta sucursal",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, CarritoRegistroInvalidoError):
        return _error(
            "No fue posible guardar el carrito: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post("/items", response_model=CarritoDetalleResponse)
def agregar_item(
    datos: AgregarItemCarritoRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Agregar prenda al carrito (crea el carrito si no existe)."""
    try:
        return CarritoService.agregar_item(db, cliente, datos)
    except CarritoError as exc:
        raise _map_error(exc)


@router.get("", response_model=CarritoListaResponse)
def listar_carritos(
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Carritos activos del cliente (incluye contador de la bolsa)."""
    try:
        return CarritoService.listar_carritos(db, cliente)
    except CarritoError as exc:
        raise _map_error(exc)


@router.get("/{carrito_id}", response_model=CarritoDetalleResponse)
def obtener_carrito(
    carrito_id: int,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Detalle de un carrito del cliente autenticado."""
    try:
        return CarritoService.obtener_carrito(db, cliente, carrito_id)
    except CarritoError as exc:
        raise _map_error(exc)


@router.patch(
    "/{carrito_id}/items/{detalle_id}", response_model=CarritoDetalleResponse
)
def actualizar_cantidad(
    carrito_id: int,
    detalle_id: int,
    datos: ActualizarCantidadRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Modificar la cantidad de una linea del carrito."""
    try:
        return CarritoService.actualizar_cantidad(
            db, cliente, carrito_id, detalle_id, datos.cantidad
        )
    except CarritoError as exc:
        raise _map_error(exc)


@router.delete(
    "/{carrito_id}/items/{detalle_id}", response_model=CarritoDetalleResponse
)
def eliminar_detalle(
    carrito_id: int,
    detalle_id: int,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Eliminar una linea (si el carrito queda vacio pasa a ELIMINADO)."""
    try:
        return CarritoService.eliminar_detalle(
            db, cliente, carrito_id, detalle_id
        )
    except CarritoError as exc:
        raise _map_error(exc)


@router.delete("/{carrito_id}", response_model=CarritoDetalleResponse)
def eliminar_carrito(
    carrito_id: int,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU15 - Eliminar carrito (logico: estado = ELIMINADO)."""
    try:
        return CarritoService.eliminar_carrito(db, cliente, carrito_id)
    except CarritoError as exc:
        raise _map_error(exc)
