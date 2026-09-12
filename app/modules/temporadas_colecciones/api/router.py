from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.temporadas_colecciones.schemas.schemas import (
    ColeccionCreate,
    ColeccionDetalleResponse,
    ColeccionEstadoUpdate,
    ColeccionProductosResponse,
    ColeccionProductosUpdate,
    ColeccionResponse,
    ColeccionUpdate,
    TemporadaCreate,
    TemporadaEstadoUpdate,
    TemporadaResponse,
    TemporadaUpdate,
)
from app.modules.temporadas_colecciones.services.service import (
    AsignacionProductoInactivoError,
    AsignacionProductoNoEncontradoError,
    AsignacionRegistroInvalidoError,
    ColeccionNombreDuplicadoError,
    ColeccionNombreInvalidoError,
    ColeccionNoEncontradaError,
    ColeccionRegistroInvalidoError,
    ColeccionService,
    ColeccionTemporadaInactivaError,
    ColeccionTemporadaNoEncontradaError,
    TemporadaConColeccionesActivasError,
    TemporadaConInventarioError,
    TemporadaFechasInvalidasError,
    TemporadaNombreDuplicadoError,
    TemporadaNombreInvalidoError,
    TemporadaNoEncontradaError,
    TemporadaRegistroInvalidoError,
    TemporadaService,
)


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _productos_response(coleccion_id: int, productos) -> ColeccionProductosResponse:
    return ColeccionProductosResponse(
        coleccion_id=coleccion_id,
        total=len(productos),
        productos=productos,
    )


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------

temporadas_router = APIRouter(prefix="/temporadas", tags=["Temporadas"])


@temporadas_router.get("", response_model=list[TemporadaResponse])
def listar_temporadas(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    return TemporadaService.listar(db, buscar=buscar, estado=estado)


@temporadas_router.post(
    "",
    response_model=TemporadaResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_temporada(
    datos: TemporadaCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return TemporadaService.crear(db, datos, admin)
    except TemporadaNombreInvalidoError:
        raise _error("El nombre de la temporada no es valido")
    except TemporadaFechasInvalidasError:
        raise _error("La fecha de inicio no puede ser posterior a la fecha de fin")
    except TemporadaNombreDuplicadoError:
        raise _error(
            "Ya existe una temporada con ese nombre",
            status.HTTP_409_CONFLICT,
        )


@temporadas_router.get("/{temporada_id}", response_model=TemporadaResponse)
def obtener_temporada(
    temporada_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    temporada = TemporadaService.obtener(db, temporada_id)
    if temporada is None:
        raise _error("Temporada no encontrada", status.HTTP_404_NOT_FOUND)
    return temporada


@temporadas_router.patch("/{temporada_id}", response_model=TemporadaResponse)
def actualizar_temporada(
    temporada_id: int,
    datos: TemporadaUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return TemporadaService.actualizar(db, temporada_id, datos, admin)
    except TemporadaNoEncontradaError:
        raise _error("Temporada no encontrada", status.HTTP_404_NOT_FOUND)
    except TemporadaNombreInvalidoError:
        raise _error("El nombre de la temporada no es valido")
    except TemporadaFechasInvalidasError:
        raise _error("La fecha de inicio no puede ser posterior a la fecha de fin")
    except TemporadaNombreDuplicadoError:
        raise _error(
            "Ya existe una temporada con ese nombre",
            status.HTTP_409_CONFLICT,
        )


@temporadas_router.patch(
    "/{temporada_id}/estado", response_model=TemporadaResponse
)
def cambiar_estado_temporada(
    temporada_id: int,
    datos: TemporadaEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return TemporadaService.cambiar_estado(db, temporada_id, datos, admin)
    except TemporadaNoEncontradaError:
        raise _error("Temporada no encontrada", status.HTTP_404_NOT_FOUND)
    except TemporadaConColeccionesActivasError:
        raise _error(
            "La temporada no puede deshabilitarse: tiene colecciones activas",
            status.HTTP_409_CONFLICT,
        )
    except TemporadaConInventarioError:
        raise _error(
            "La temporada no puede deshabilitarse: tiene inventario con stock o reservas",
            status.HTTP_409_CONFLICT,
        )
    except TemporadaRegistroInvalidoError:
        raise _error("No fue posible actualizar el estado de la temporada")


# ---------------------------------------------------------------------------
# Colecciones
# ---------------------------------------------------------------------------

colecciones_router = APIRouter(prefix="/colecciones", tags=["Colecciones"])


@colecciones_router.get("", response_model=list[ColeccionResponse])
def listar_colecciones(
    buscar: str | None = None,
    temporada_id: int | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    return ColeccionService.listar(
        db, buscar=buscar, temporada_id=temporada_id, estado=estado
    )


@colecciones_router.post(
    "",
    response_model=ColeccionResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_coleccion(
    datos: ColeccionCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return ColeccionService.crear(db, datos, admin)
    except ColeccionTemporadaNoEncontradaError:
        raise _error("La temporada no existe", status.HTTP_404_NOT_FOUND)
    except ColeccionTemporadaInactivaError:
        raise _error(
            "La coleccion solo puede asociarse a una temporada activa",
            status.HTTP_409_CONFLICT,
        )
    except ColeccionNombreInvalidoError:
        raise _error("El nombre de la coleccion no es valido")
    except ColeccionNombreDuplicadoError:
        raise _error(
            "Ya existe una coleccion con ese nombre en la temporada",
            status.HTTP_409_CONFLICT,
        )


@colecciones_router.get(
    "/{coleccion_id}", response_model=ColeccionDetalleResponse
)
def obtener_coleccion(
    coleccion_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    detalle = ColeccionService.obtener_detalle(db, coleccion_id)
    if detalle is None:
        raise _error("Coleccion no encontrada", status.HTTP_404_NOT_FOUND)
    return detalle


@colecciones_router.patch("/{coleccion_id}", response_model=ColeccionResponse)
def actualizar_coleccion(
    coleccion_id: int,
    datos: ColeccionUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return ColeccionService.actualizar(db, coleccion_id, datos, admin)
    except ColeccionNoEncontradaError:
        raise _error("Coleccion no encontrada", status.HTTP_404_NOT_FOUND)
    except ColeccionTemporadaNoEncontradaError:
        raise _error("La temporada no existe", status.HTTP_404_NOT_FOUND)
    except ColeccionTemporadaInactivaError:
        raise _error(
            "No se puede mover la coleccion a una temporada inactiva",
            status.HTTP_409_CONFLICT,
        )
    except ColeccionNombreInvalidoError:
        raise _error("El nombre de la coleccion no es valido")
    except ColeccionNombreDuplicadoError:
        raise _error(
            "Ya existe una coleccion con ese nombre en la temporada",
            status.HTTP_409_CONFLICT,
        )


@colecciones_router.patch(
    "/{coleccion_id}/estado", response_model=ColeccionResponse
)
def cambiar_estado_coleccion(
    coleccion_id: int,
    datos: ColeccionEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return ColeccionService.cambiar_estado(
            db, coleccion_id, datos, admin
        )
    except ColeccionNoEncontradaError:
        raise _error("Coleccion no encontrada", status.HTTP_404_NOT_FOUND)
    except ColeccionRegistroInvalidoError:
        raise _error("No fue posible actualizar el estado de la coleccion")


@colecciones_router.get(
    "/{coleccion_id}/productos", response_model=ColeccionProductosResponse
)
def listar_productos_coleccion(
    coleccion_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    productos = ColeccionService.listar_productos(db, coleccion_id)
    if productos is None:
        raise _error("Coleccion no encontrada", status.HTTP_404_NOT_FOUND)
    return _productos_response(coleccion_id, productos)


@colecciones_router.put(
    "/{coleccion_id}/productos", response_model=ColeccionProductosResponse
)
def reemplazar_productos_coleccion(
    coleccion_id: int,
    datos: ColeccionProductosUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        productos = ColeccionService.reemplazar_productos(
            db, coleccion_id, datos.producto_ids, admin
        )
    except ColeccionNoEncontradaError:
        raise _error("Coleccion no encontrada", status.HTTP_404_NOT_FOUND)
    except AsignacionProductoNoEncontradoError:
        raise _error("Uno o mas productos no existen", status.HTTP_404_NOT_FOUND)
    except AsignacionProductoInactivoError:
        raise _error(
            "Solo se pueden asignar productos activos",
            status.HTTP_409_CONFLICT,
        )
    except AsignacionRegistroInvalidoError:
        raise _error("No fue posible actualizar los productos de la coleccion")
    return _productos_response(coleccion_id, productos)


# ---------------------------------------------------------------------------
# Router del modulo CU08
# ---------------------------------------------------------------------------

router = APIRouter()
router.include_router(temporadas_router)
router.include_router(colecciones_router)
