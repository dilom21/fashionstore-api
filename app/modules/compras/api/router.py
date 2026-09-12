from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.compras.schemas.schemas import (
    DetallesOrdenCompraResponse,
    DetallesOrdenCompraUpdate,
    OrdenCompraCreate,
    OrdenCompraResponse,
    OrdenCompraUpdate,
)
from app.modules.compras.services.service import (
    DetalleCantidadInvalidaError,
    DetalleCostoInvalidoError,
    DetalleEstadoInvalidoError,
    DetalleProductoNoAsociadoError,
    DetalleRegistroInvalidoError,
    DetalleTemporadaInactivaError,
    DetalleTemporadaNoEncontradaError,
    DetalleVarianteInactivaError,
    DetalleVarianteNoEncontradaError,
    EstadoOrdenInvalidoError,
    FechaEstimadaInvalidaError,
    OrdenCompraNoEncontradaError,
    OrdenCompraService,
    OrdenRegistroInvalidoError,
    ProveedorInactivoError,
    ProveedorNoEncontradoError,
    RecepcionInvalidaError,
    SinDetallesError,
    SucursalInactivaError,
    SucursalNoEncontradaError,
    SucursalScopeError,
    TransicionInvalidaError,
)

FUNCION = "GESTIONAR_ORDENES_COMPRA"

ESTADOS_VALIDOS = frozenset(
    {"BORRADOR", "ENVIADA", "PARCIAL", "RECIBIDA", "CANCELADA"}
)


def _perm(accion: str):
    return require_permission(FUNCION, accion)


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST):
    return HTTPException(status_code=codigo, detail=detalle)


router = APIRouter(prefix="/ordenes-compra", tags=["Compras"])


@router.get("", response_model=list[OrdenCompraResponse])
def listar_ordenes(
    buscar: str | None = None,
    proveedor_id: int | None = None,
    sucursal_id: int | None = None,
    estado: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise _error("Estado de orden de compra invalido")
    try:
        return OrdenCompraService.listar(
            db,
            usuario,
            buscar=buscar,
            proveedor_id=proveedor_id,
            sucursal_id=sucursal_id,
            estado=estado,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la sucursal no corresponde a su alcance",
            status.HTTP_403_FORBIDDEN,
        )


@router.post(
    "",
    response_model=OrdenCompraResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_orden(
    datos: OrdenCompraCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("CREAR")),
):
    try:
        return OrdenCompraService.crear(db, datos, usuario)
    except ProveedorNoEncontradoError:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    except ProveedorInactivoError:
        raise _error(
            "El proveedor esta inactivo", status.HTTP_409_CONFLICT
        )
    except SucursalInactivaError:
        raise _error(
            "La sucursal esta inactiva", status.HTTP_409_CONFLICT
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: solo puede operar en su sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except FechaEstimadaInvalidaError:
        raise _error(
            "La fecha estimada debe ser nula o mayor o igual a la "
            "fecha de la orden"
        )
    except OrdenRegistroInvalidoError:
        raise _error(
            "No fue posible registrar la orden de compra",
            status.HTTP_409_CONFLICT,
        )


@router.get("/{orden_id}", response_model=OrdenCompraResponse)
def obtener_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    try:
        return OrdenCompraService.obtener(db, orden_id, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )


@router.patch("/{orden_id}", response_model=OrdenCompraResponse)
def actualizar_orden(
    orden_id: int,
    datos: OrdenCompraUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return OrdenCompraService.actualizar(db, orden_id, datos, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except FechaEstimadaInvalidaError:
        raise _error(
            "La fecha estimada debe ser nula o mayor o igual a la "
            "fecha de la orden"
        )
    except EstadoOrdenInvalidoError:
        raise _error(
            "La orden no puede editarse en su estado actual",
            status.HTTP_409_CONFLICT,
        )
    except OrdenRegistroInvalidoError:
        raise _error(
            "No fue posible actualizar la orden de compra",
            status.HTTP_409_CONFLICT,
        )


@router.get(
    "/{orden_id}/detalles", response_model=DetallesOrdenCompraResponse
)
def listar_detalles(
    orden_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    try:
        return OrdenCompraService.listar_detalles(db, orden_id, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )


@router.put(
    "/{orden_id}/detalles", response_model=DetallesOrdenCompraResponse
)
def reemplazar_detalles(
    orden_id: int,
    datos: DetallesOrdenCompraUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return OrdenCompraService.reemplazar_detalles(
            db, orden_id, datos, usuario
        )
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except DetalleEstadoInvalidoError:
        raise _error(
            "Solo se pueden editar los detalles de una orden en BORRADOR",
            status.HTTP_409_CONFLICT,
        )
    except DetalleCantidadInvalidaError:
        raise _error("La cantidad del detalle debe ser mayor a cero")
    except DetalleCostoInvalidoError:
        raise _error("El costo unitario no puede ser negativo")
    except DetalleVarianteNoEncontradaError:
        raise _error(
            "Una o mas variantes no existen", status.HTTP_404_NOT_FOUND
        )
    except DetalleTemporadaNoEncontradaError:
        raise _error(
            "Una o mas temporadas no existen", status.HTTP_404_NOT_FOUND
        )
    except DetalleVarianteInactivaError:
        raise _error(
            "Una o mas variantes estan inactivas",
            status.HTTP_409_CONFLICT,
        )
    except DetalleTemporadaInactivaError:
        raise _error(
            "Una o mas temporadas estan inactivas",
            status.HTTP_409_CONFLICT,
        )
    except DetalleProductoNoAsociadoError:
        raise _error(
            "El producto de una variante no esta asociado al proveedor",
            status.HTTP_409_CONFLICT,
        )
    except DetalleRegistroInvalidoError:
        raise _error(
            "No fue posible actualizar los detalles de la orden",
            status.HTTP_409_CONFLICT,
        )


@router.patch("/{orden_id}/enviar", response_model=OrdenCompraResponse)
def enviar_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return OrdenCompraService.enviar(db, orden_id, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except SinDetallesError:
        raise _error(
            "La orden debe tener al menos un detalle para enviarse",
            status.HTTP_409_CONFLICT,
        )
    except TransicionInvalidaError:
        raise _error(
            "Solo una orden en BORRADOR puede enviarse",
            status.HTTP_409_CONFLICT,
        )


@router.patch("/{orden_id}/cancelar", response_model=OrdenCompraResponse)
def cancelar_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return OrdenCompraService.cancelar(db, orden_id, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except TransicionInvalidaError:
        raise _error(
            "La orden no puede cancelarse en su estado actual",
            status.HTTP_409_CONFLICT,
        )


@router.post("/{orden_id}/recibir", response_model=OrdenCompraResponse)
def recibir_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EJECUTAR")),
):
    try:
        return OrdenCompraService.recibir(db, orden_id, usuario)
    except OrdenCompraNoEncontradaError:
        raise _error(
            "Orden de compra no encontrada", status.HTTP_404_NOT_FOUND
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la orden pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    except SinDetallesError:
        raise _error(
            "La orden no contiene productos", status.HTTP_409_CONFLICT
        )
    except RecepcionInvalidaError:
        raise _error(
            "La orden no puede recibirse en su estado actual",
            status.HTTP_409_CONFLICT,
        )
