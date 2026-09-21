"""Endpoints de CU25 - Registrar devolucion de productos.

Actores: ADMINISTRADOR (cualquier sucursal) y ENCARGADO_SUCURSAL (solo su
sucursal). Se exige contexto JWT ``personal`` y el RBAC real
``GESTIONAR_DEVOLUCIONES`` (CONSULTAR / CREAR / EDITAR / EJECUTAR); CAJERO y
CLIENTE no tienen ese permiso en la base.

CU25 es devolucion fisica + reingreso a inventario: no hay reembolso
financiero, no se toca ``pago`` y no se llama a Stripe.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_auth_context, require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.devoluciones.schemas.schemas import (
    DisponibilidadVentaResponse,
    DevolucionDetalleResponse,
    DevolucionesListaResponse,
    EstadoDevolucionFiltro,
    RegistrarDevolucionRequest,
)
from app.modules.devoluciones.services.service import (
    PAGINA_POR_DEFECTO,
    TAMANO_PAGINA_MAXIMO,
    TAMANO_PAGINA_POR_DEFECTO,
    CantidadDevolucionInvalidaError,
    CantidadDevolucionNoDisponibleError,
    DetalleDevolucionDuplicadoError,
    DetalleVentaNoValidoError,
    DevolucionError,
    DevolucionNoEncontradaError,
    DevolucionRolNoAutorizadoError,
    DevolucionScopeError,
    DevolucionService,
    DevolucionSinItemsError,
    EstadoDevolucionInvalidoError,
    ProcesamientoDevolucionError,
    RangoFechasDevolucionError,
    VentaDevolucionNoEncontradaError,
    VentaNoDevolvibleError,
)

FUNCION_DEVOLUCIONES = "GESTIONAR_DEVOLUCIONES"
CONTEXTO_PERSONAL = "personal"

router = APIRouter(prefix="/devoluciones", tags=["Devoluciones"])


def _error(
    detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST
) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _contexto_personal(
    contexto: str = Depends(get_current_auth_context),
) -> str:
    """CU25 es exclusivo de PERSONAL: una sesion de cliente no entra."""
    if contexto != CONTEXTO_PERSONAL:
        raise _error(
            "No autorizado: se requiere sesion de personal",
            status.HTTP_403_FORBIDDEN,
        )
    return contexto


def _permiso(accion: str):
    """Dependencia RBAC real de CU25 (GESTIONAR_DEVOLUCIONES / accion)."""
    return require_permission(FUNCION_DEVOLUCIONES, accion)


def _map_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU25 a HTTP (nunca SQL crudo)."""
    if isinstance(exc, VentaDevolucionNoEncontradaError):
        return _error("Venta no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, DetalleVentaNoValidoError):
        return _error(
            "El detalle de venta no pertenece a la venta indicada",
            status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, DevolucionNoEncontradaError):
        return _error("Devolucion no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, DevolucionRolNoAutorizadoError):
        return _error(
            "No autorizado: se requiere rol ADMINISTRADOR o ENCARGADO_SUCURSAL",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, DevolucionScopeError):
        return _error(
            "No autorizado: la operacion esta fuera de su sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(
        exc,
        (
            DetalleDevolucionDuplicadoError,
            CantidadDevolucionInvalidaError,
            RangoFechasDevolucionError,
        ),
    ):
        return _error(
            "Solicitud de devolucion invalida",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, VentaNoDevolvibleError):
        return _error(
            "La venta no esta COMPLETADA: no admite devoluciones",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, CantidadDevolucionNoDisponibleError):
        return _error(
            "No existe cantidad disponible suficiente para la devolucion",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, EstadoDevolucionInvalidoError):
        return _error(
            "La devolucion no esta en un estado que permita esa operacion",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, DevolucionSinItemsError):
        return _error(
            "La devolucion no tiene prendas",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ProcesamientoDevolucionError):
        return _error(
            "No fue posible procesar la devolucion",
            status.HTTP_409_CONFLICT,
        )
    raise exc


# ---------------------------------------------------------------------------
# Consultas (GESTIONAR_DEVOLUCIONES / CONSULTAR)
# ---------------------------------------------------------------------------


@router.get("", response_model=DevolucionesListaResponse)
def listar_devoluciones(
    estado: EstadoDevolucionFiltro | None = Query(
        default=None, description="Filtra por estado de la devolucion."
    ),
    sucursal_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Solo ADMINISTRADOR: limita el listado a esa sucursal. "
            "ENCARGADO_SUCURSAL siempre ve la suya (otra -> 403)."
        ),
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
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """CU25 - Listado paginado de devoluciones (mas reciente primero)."""
    try:
        return DevolucionService.listar_devoluciones(
            db,
            usuario,
            estado=estado.value if estado is not None else None,
            sucursal_id=sucursal_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            pagina=pagina,
            tamano_pagina=tamano_pagina,
        )
    except DevolucionError as exc:
        raise _map_error(exc)


@router.get(
    "/ventas/{venta_id}/disponibilidad",
    response_model=DisponibilidadVentaResponse,
)
def consultar_disponibilidad_venta(
    venta_id: int,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """CU25 - Venta COMPLETADA con la cantidad disponible por linea.

    404 si la venta no existe, 403 si esta fuera de la sucursal del usuario,
    409 si la venta no esta COMPLETADA (PENDIENTE/PAGADA/CANCELADA/REEMBOLSADA).
    """
    try:
        return DevolucionService.consultar_disponibilidad_venta(
            db, usuario, venta_id
        )
    except DevolucionError as exc:
        raise _map_error(exc)


@router.get("/{devolucion_id}", response_model=DevolucionDetalleResponse)
def obtener_devolucion(
    devolucion_id: int,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """CU25 - Detalle completo de la devolucion (venta, sucursal, items)."""
    try:
        return DevolucionService.obtener_detalle(db, usuario, devolucion_id)
    except DevolucionError as exc:
        raise _map_error(exc)


# ---------------------------------------------------------------------------
# Acciones (CREAR / EDITAR / EJECUTAR)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DevolucionDetalleResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_devolucion(
    datos: RegistrarDevolucionRequest,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CREAR")),
    db: Session = Depends(get_db),
):
    """CU25 - Registrar la solicitud de devolucion (estado SOLICITADA).

    Solo se confia en ``venta_id``, ``detalle_venta_id``, ``cantidad`` y
    ``motivo``: el estado, la sucursal y los precios salen de la base. Todavia
    NO se toca el inventario (eso ocurre al procesar el SP).
    """
    try:
        return DevolucionService.registrar_devolucion(db, usuario, datos)
    except DevolucionError as exc:
        raise _map_error(exc)


@router.post(
    "/{devolucion_id}/aprobar", response_model=DevolucionDetalleResponse
)
def aprobar_devolucion(
    devolucion_id: int,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("EDITAR")),
    db: Session = Depends(get_db),
):
    """CU25 - SOLICITADA -> APROBADA (revalida disponibilidad con bloqueo)."""
    try:
        return DevolucionService.aprobar(db, usuario, devolucion_id)
    except DevolucionError as exc:
        raise _map_error(exc)


@router.post(
    "/{devolucion_id}/rechazar", response_model=DevolucionDetalleResponse
)
def rechazar_devolucion(
    devolucion_id: int,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("EDITAR")),
    db: Session = Depends(get_db),
):
    """CU25 - SOLICITADA -> RECHAZADA (sin tocar inventario ni movimientos)."""
    try:
        return DevolucionService.rechazar(db, usuario, devolucion_id)
    except DevolucionError as exc:
        raise _map_error(exc)


@router.post(
    "/{devolucion_id}/procesar", response_model=DevolucionDetalleResponse
)
def procesar_devolucion(
    devolucion_id: int,
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("EJECUTAR")),
    db: Session = Depends(get_db),
):
    """CU25 - APROBADA -> sp_registrar_devolucion -> COMPLETADA.

    El procedimiento repone ``inventario.stock_actual``, registra el
    ``MovimientoInventario`` DEVOLUCION y marca COMPLETADA. La Venta original
    no cambia de estado y no se toca ``pago`` (sin reembolso financiero).
    """
    try:
        return DevolucionService.procesar(db, usuario, devolucion_id)
    except DevolucionError as exc:
        raise _map_error(exc)
