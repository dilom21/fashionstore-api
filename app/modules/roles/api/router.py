from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.roles.schemas.schemas import (
    ModuloPermisosResponse,
    RolCreate,
    RolDetalleResponse,
    RolEstadoUpdate,
    RolPermisosResponse,
    RolPermisosUpdate,
    RolResponse,
    RolUpdate,
)
from app.modules.roles.services.service import (
    PermisoReferenciaInvalidaError,
    RolAdministradorProtegidoError,
    RolBaseProtegidoError,
    RolEnUsoError,
    RolNombreDuplicadoError,
    RolNombreInvalidoError,
    RolNoEncontradoError,
    RolService,
)

router = APIRouter(prefix="/roles", tags=["Roles"])

FUNCION_GESTIONAR_ROLES = "GESTIONAR_ROLES"


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _rol_o_404(detalle: dict | None) -> dict:
    if detalle is None:
        raise _error("Rol no encontrado", status.HTTP_404_NOT_FOUND)
    return detalle


@router.get("", response_model=list[RolResponse])
def listar_roles(
    asignable_interno: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "CONSULTAR")
    ),
):
    """Lista todos los roles. Con asignable_interno=true excluye CLIENTE y
    roles deshabilitados (roles asignables a usuarios internos)."""
    return RolService.listar(db, asignable_interno=asignable_interno)


@router.get("/catalogo-permisos", response_model=list[ModuloPermisosResponse])
def catalogo_permisos(
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "CONSULTAR")
    ),
):
    """Devuelve la jerarquia Modulo -> Funcion -> Acciones activa."""
    return RolService.catalogo_permisos(db)


@router.get("/{rol_id}", response_model=RolDetalleResponse)
def obtener_rol(
    rol_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "CONSULTAR")
    ),
):
    detalle = RolService.detalle(db, rol_id)
    return _rol_o_404(detalle)


@router.get("/{rol_id}/permisos", response_model=RolPermisosResponse)
def obtener_permisos_rol(
    rol_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "CONSULTAR")
    ),
):
    try:
        return RolService.obtener_permisos_rol(db, rol_id)
    except RolNoEncontradoError:
        raise _error("Rol no encontrado", status.HTTP_404_NOT_FOUND)


@router.post(
    "",
    response_model=RolResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_rol(
    datos: RolCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "CREAR")
    ),
):
    try:
        return RolService.crear(db, datos, admin)
    except RolNombreInvalidoError:
        raise _error("El nombre del rol no es valido")
    except RolNombreDuplicadoError:
        raise _error("El nombre del rol ya existe", status.HTTP_409_CONFLICT)


@router.patch("/{rol_id}", response_model=RolResponse)
def actualizar_rol(
    rol_id: int,
    datos: RolUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "EDITAR")
    ),
):
    try:
        return RolService.actualizar(db, rol_id, datos, admin)
    except RolNoEncontradoError:
        raise _error("Rol no encontrado", status.HTTP_404_NOT_FOUND)
    except RolBaseProtegidoError:
        raise _error(
            "Los roles base no pueden renombrarse",
            status.HTTP_400_BAD_REQUEST,
        )
    except RolNombreDuplicadoError:
        raise _error("El nombre del rol ya existe", status.HTTP_409_CONFLICT)


@router.patch("/{rol_id}/estado", response_model=RolResponse)
def cambiar_estado_rol(
    rol_id: int,
    datos: RolEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "ELIMINAR")
    ),
):
    try:
        return RolService.cambiar_estado(db, rol_id, datos.estado, admin)
    except RolNoEncontradoError:
        raise _error("Rol no encontrado", status.HTTP_404_NOT_FOUND)
    except RolAdministradorProtegidoError:
        raise _error(
            "El rol ADMINISTRADOR no puede deshabilitarse",
            status.HTTP_400_BAD_REQUEST,
        )
    except RolEnUsoError:
        raise _error(
            "El rol no puede deshabilitarse porque tiene usuarios activos",
            status.HTTP_409_CONFLICT,
        )


@router.put("/{rol_id}/permisos", response_model=RolPermisosResponse)
def reemplazar_permisos_rol(
    rol_id: int,
    datos: RolPermisosUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_ROLES, "EDITAR")
    ),
):
    pares = [
        (permiso.funcion_id, permiso.accion_id) for permiso in datos.permisos
    ]
    try:
        RolService.reemplazar_permisos(db, rol_id, pares, admin)
        return RolService.obtener_permisos_rol(db, rol_id)
    except RolNoEncontradoError:
        raise _error("Rol no encontrado", status.HTTP_404_NOT_FOUND)
    except RolAdministradorProtegidoError:
        raise _error(
            "La matriz de permisos de ADMINISTRADOR es de solo lectura",
            status.HTTP_400_BAD_REQUEST,
        )
    except PermisoReferenciaInvalidaError:
        raise _error(
            "La matriz contiene funciones o acciones inexistentes o inactivas",
            status.HTTP_400_BAD_REQUEST,
        )
