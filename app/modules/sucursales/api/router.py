from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.sucursales.schemas.schemas import (
    CiudadCreate,
    CiudadEstadoUpdate,
    CiudadResponse,
    CiudadUpdate,
    SucursalCreate,
    SucursalDetalleResponse,
    SucursalEstadoUpdate,
    SucursalResponse,
    SucursalUpdate,
)
from app.modules.sucursales.services.service import (
    CiudadEnUsoError,
    CiudadNombreDuplicadoError,
    CiudadNombreInvalidoError,
    CiudadNoEncontradaError,
    CiudadService,
    SucursalCiudadInactivaError,
    SucursalCiudadInexistenteError,
    SucursalDireccionInvalidaError,
    SucursalEnUsoError,
    SucursalNombreDuplicadoError,
    SucursalNombreInvalidoError,
    SucursalNoEncontradaError,
    SucursalService,
)

sucursales_router = APIRouter(prefix="/sucursales", tags=["Sucursales"])
ciudades_router = APIRouter(prefix="/ciudades", tags=["Ciudades"])

router = APIRouter()
router.include_router(sucursales_router)
router.include_router(ciudades_router)


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _ciudad_o_404(detalle: dict | None) -> dict:
    if detalle is None:
        raise _error("Ciudad no encontrada", status.HTTP_404_NOT_FOUND)
    return detalle


# --------------------------------------------------------------------------
# Sucursales (GET publicos conservan el contrato de CU03)
# --------------------------------------------------------------------------


@sucursales_router.get("", response_model=list[SucursalResponse])
def listar_sucursales(
    buscar: str | None = None,
    ciudad_id: int | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
):
    """Lista sucursales.

    Compatibilidad CU03: sin parametros devuelve unicamente sucursales
    activas. CU06 agrega filtros opcionales buscar/ciudad_id/estado.
    """
    return SucursalService.listar(
        db,
        buscar=buscar,
        ciudad_id=ciudad_id,
        estado=estado,
    )


@sucursales_router.get("/{sucursal_id}", response_model=SucursalResponse)
def obtener_sucursal(sucursal_id: int, db: Session = Depends(get_db)):
    """Detalle de una sucursal activa (contrato CU03)."""
    sucursal = SucursalService.obtener_sucursal(db, sucursal_id)
    if sucursal is None:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    return sucursal


@sucursales_router.post(
    "",
    response_model=SucursalDetalleResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_sucursal(
    datos: SucursalCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return SucursalService.crear(db, datos, admin)
    except SucursalNombreInvalidoError:
        raise _error("El nombre de la sucursal no es valido")
    except SucursalDireccionInvalidaError:
        raise _error("La direccion de la sucursal no es valida")
    except SucursalCiudadInexistenteError:
        raise _error("La ciudad no existe", status.HTTP_404_NOT_FOUND)
    except SucursalCiudadInactivaError:
        raise _error(
            "La sucursal solo puede crearse en una ciudad activa",
            status.HTTP_409_CONFLICT,
        )
    except SucursalNombreDuplicadoError:
        raise _error(
            "Ya existe una sucursal con ese nombre en la ciudad",
            status.HTTP_409_CONFLICT,
        )


@sucursales_router.patch("/{sucursal_id}", response_model=SucursalDetalleResponse)
def actualizar_sucursal(
    sucursal_id: int,
    datos: SucursalUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return SucursalService.actualizar(db, sucursal_id, datos, admin)
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    except SucursalNombreInvalidoError:
        raise _error("El nombre de la sucursal no es valido")
    except SucursalDireccionInvalidaError:
        raise _error("La direccion de la sucursal no es valida")
    except SucursalCiudadInexistenteError:
        raise _error("La ciudad no existe", status.HTTP_404_NOT_FOUND)
    except SucursalCiudadInactivaError:
        raise _error(
            "No se puede mover la sucursal a una ciudad inactiva",
            status.HTTP_409_CONFLICT,
        )
    except SucursalNombreDuplicadoError:
        raise _error(
            "Ya existe una sucursal con ese nombre en la ciudad",
            status.HTTP_409_CONFLICT,
        )


@sucursales_router.patch(
    "/{sucursal_id}/estado", response_model=SucursalDetalleResponse
)
def cambiar_estado_sucursal(
    sucursal_id: int,
    datos: SucursalEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return SucursalService.cambiar_estado(db, sucursal_id, datos, admin)
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    except SucursalEnUsoError:
        raise _error(
            "La sucursal no puede deshabilitarse: tiene empleados activos "
            "o inventario pendiente",
            status.HTTP_409_CONFLICT,
        )


# --------------------------------------------------------------------------
# Ciudades (CU06 - solo administrador)
# --------------------------------------------------------------------------


@ciudades_router.get("", response_model=list[CiudadResponse])
def listar_ciudades(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    return CiudadService.listar(db, buscar=buscar, estado=estado)


@ciudades_router.get("/{ciudad_id}", response_model=CiudadResponse)
def obtener_ciudad(
    ciudad_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    return _ciudad_o_404(CiudadService.obtener(db, ciudad_id))


@ciudades_router.post(
    "",
    response_model=CiudadResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_ciudad(
    datos: CiudadCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return CiudadService.crear(db, datos, admin)
    except CiudadNombreInvalidoError:
        raise _error("El nombre de la ciudad no es valido")
    except CiudadNombreDuplicadoError:
        raise _error(
            "Ya existe una ciudad con ese nombre", status.HTTP_409_CONFLICT
        )


@ciudades_router.patch("/{ciudad_id}", response_model=CiudadResponse)
def actualizar_ciudad(
    ciudad_id: int,
    datos: CiudadUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return CiudadService.actualizar(db, ciudad_id, datos, admin)
    except CiudadNoEncontradaError:
        raise _error("Ciudad no encontrada", status.HTTP_404_NOT_FOUND)
    except CiudadNombreInvalidoError:
        raise _error("El nombre de la ciudad no es valido")
    except CiudadNombreDuplicadoError:
        raise _error(
            "Ya existe una ciudad con ese nombre", status.HTTP_409_CONFLICT
        )


@ciudades_router.patch("/{ciudad_id}/estado", response_model=CiudadResponse)
def cambiar_estado_ciudad(
    ciudad_id: int,
    datos: CiudadEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return CiudadService.cambiar_estado(db, ciudad_id, datos, admin)
    except CiudadNoEncontradaError:
        raise _error("Ciudad no encontrada", status.HTTP_404_NOT_FOUND)
    except CiudadEnUsoError:
        raise _error(
            "La ciudad no puede deshabilitarse: tiene sucursales activas",
            status.HTTP_409_CONFLICT,
        )
