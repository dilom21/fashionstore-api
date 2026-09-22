"""Endpoints de CU26 - Vestidor Virtual (AR).

Todos requieren JWT de contexto CLIENTE. La app movil (ProductoDetalle /
Asistencia Inteligente) consulta la configuracion AR y luego abre el motor
local de Harold con el DTO ``VestidorConfig``.

Endpoints:
  GET   /vestidor-virtual/productos/{producto_id}/configuraciones
  POST  /vestidor-virtual/sesiones
  POST  /vestidor-virtual/sesiones/{sesion_id}/pruebas
  PATCH /vestidor-virtual/pruebas/{prueba_id}/finalizar
  PATCH /vestidor-virtual/sesiones/{sesion_id}/finalizar
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente
from app.modules.vestidor_virtual.schemas.schemas import (
    ConfiguracionesProductoResponse,
    CrearSesionRequest,
    FinalizarPruebaRequest,
    FinalizarSesionRequest,
    IniciarPruebaRequest,
    PruebaARResponse,
    SesionARResponse,
)
from app.modules.vestidor_virtual.services.service import (
    ConfiguracionNoActivaError,
    ConfiguracionNoEncontradaError,
    EstadoInvalidoError,
    PruebaAjenaError,
    PruebaNoEncontradaError,
    ProductoNoEncontradoError,
    RegistroInvalidoError,
    SesionAjenaError,
    SesionNoActivaError,
    SesionNoEncontradaError,
    VarianteIncompatibleError,
    VestidorError,
    VestidorVirtualService,
)

router = APIRouter(prefix="/vestidor-virtual", tags=["Vestidor Virtual"])


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: VestidorError) -> HTTPException:
    """Traduce errores de negocio de CU26 a respuestas HTTP del proyecto."""
    if isinstance(exc, ProductoNoEncontradoError):
        return _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ConfiguracionNoEncontradaError):
        return _error(
            "Configuracion de vestidor no encontrada",
            status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, ConfiguracionNoActivaError):
        return _error(
            "La configuracion de vestidor no esta activa",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, SesionNoEncontradaError):
        return _error(
            "Sesion de vestidor no encontrada", status.HTTP_404_NOT_FOUND
        )
    if isinstance(exc, PruebaNoEncontradaError):
        return _error(
            "Prueba de vestidor no encontrada", status.HTTP_404_NOT_FOUND
        )
    if isinstance(exc, SesionAjenaError):
        return _error(
            "No autorizado: la sesion pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, PruebaAjenaError):
        return _error(
            "No autorizado: la prueba pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, SesionNoActivaError):
        return _error(
            "La sesion de vestidor no esta activa",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, VarianteIncompatibleError):
        return _error(
            "La variante no pertenece al producto configurado",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, EstadoInvalidoError):
        return _error(
            "Transicion de estado no permitida",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, RegistroInvalidoError):
        return _error(
            "No fue posible guardar la operacion: datos inconsistentes",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.get(
    "/productos/{producto_id}/configuraciones",
    response_model=ConfiguracionesProductoResponse,
)
def obtener_configuraciones(
    producto_id: int,
    variante_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    _cliente=Depends(get_current_cliente),
):
    """CU26 - Configuraciones AR compatibles de un producto real."""
    try:
        return VestidorVirtualService.obtener_configuraciones(
            db,
            producto_id,
            variante_id=variante_id,
            color_id=color_id,
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.post(
    "/sesiones",
    response_model=SesionARResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_sesion(
    datos: CrearSesionRequest | None = None,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU26 - Crea (o reutiliza) la sesion ACTIVA del cliente autenticado."""
    try:
        return VestidorVirtualService.crear_sesion(db, cliente)
    except VestidorError as exc:
        raise _map_error(exc)


@router.post(
    "/sesiones/{sesion_id}/pruebas",
    response_model=PruebaARResponse,
    status_code=status.HTTP_201_CREATED,
)
def iniciar_prueba(
    sesion_id: int,
    datos: IniciarPruebaRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU26 - Registra el inicio de una prueba de prenda."""
    try:
        return VestidorVirtualService.iniciar_prueba(
            db, cliente, sesion_id, datos
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.patch("/pruebas/{prueba_id}/finalizar", response_model=PruebaARResponse)
def finalizar_prueba(
    prueba_id: int,
    datos: FinalizarPruebaRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU26 - Finaliza una prueba (idempotente con el mismo estado final)."""
    try:
        return VestidorVirtualService.finalizar_prueba(
            db, cliente, prueba_id, datos.estado
        )
    except VestidorError as exc:
        raise _map_error(exc)


@router.patch("/sesiones/{sesion_id}/finalizar", response_model=SesionARResponse)
def finalizar_sesion(
    sesion_id: int,
    datos: FinalizarSesionRequest,
    db: Session = Depends(get_db),
    cliente=Depends(get_current_cliente),
):
    """CU26 - Finaliza una sesion (idempotente con el mismo estado final)."""
    try:
        return VestidorVirtualService.finalizar_sesion(
            db, cliente, sesion_id, datos.estado
        )
    except VestidorError as exc:
        raise _map_error(exc)
