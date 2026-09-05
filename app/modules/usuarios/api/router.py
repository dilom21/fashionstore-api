from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.roles.services.service import RolClienteNoAsignableError
from app.modules.usuarios.schemas.schemas import (
    UsuarioCreate,
    UsuarioEstadoUpdate,
    UsuarioListResponse,
    UsuarioResponse,
    UsuarioUpdate,
)
from app.modules.usuarios.services.service import (
    CiDuplicadoError,
    CorreoDuplicadoError,
    EmpleadoNoEncontradoError,
    RolInexistenteError,
    RolInactivoError,
    SinCambiosError,
    SucursalInvalidaError,
    UsuarioNoEncontradoError,
    UsuarioService,
)

router = APIRouter(prefix="/usuarios", tags=["Usuarios"])

FUNCION_GESTIONAR_USUARIOS = "GESTIONAR_USUARIOS"


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _usuario_o_404(usuario: Usuario | None) -> Usuario:
    if usuario is None:
        raise _error("Usuario no encontrado", status.HTTP_404_NOT_FOUND)
    return usuario


@router.get("", response_model=UsuarioListResponse)
def listar_usuarios(
    buscar: str | None = None,
    rol_id: int | None = None,
    estado: bool | None = None,
    sucursal_id: int | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_USUARIOS, "CONSULTAR")
    ),
):
    items = UsuarioService.listar(
        db,
        buscar=buscar,
        rol_id=rol_id,
        estado=estado,
        sucursal_id=sucursal_id,
    )
    return UsuarioListResponse(items=items)


@router.get("/{usuario_id}", response_model=UsuarioResponse)
def obtener_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_USUARIOS, "CONSULTAR")
    ),
):
    return _usuario_o_404(UsuarioService.obtener(db, usuario_id))


@router.post(
    "",
    response_model=UsuarioResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_usuario(
    datos: UsuarioCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_USUARIOS, "CREAR")
    ),
):
    try:
        usuario = UsuarioService.crear(db, datos, admin)
    except RolClienteNoAsignableError:
        raise _error(
            "El rol CLIENTE no puede asignarse a un usuario interno",
            status.HTTP_400_BAD_REQUEST,
        )
    except CorreoDuplicadoError:
        raise _error(
            "El correo ya se encuentra registrado", status.HTTP_409_CONFLICT
        )
    except CiDuplicadoError:
        raise _error(
            "El CI ya se encuentra registrado", status.HTTP_409_CONFLICT
        )
    except RolInexistenteError:
        raise _error("El rol no existe", status.HTTP_404_NOT_FOUND)
    except RolInactivoError:
        raise _error("El rol no está activo", status.HTTP_422_UNPROCESSABLE_ENTITY)
    except SucursalInvalidaError:
        raise _error(
            "La sucursal no existe o no está activa", status.HTTP_404_NOT_FOUND
        )
    return usuario


@router.patch("/{usuario_id}", response_model=UsuarioResponse)
def actualizar_usuario(
    usuario_id: int,
    datos: UsuarioUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_USUARIOS, "EDITAR")
    ),
):
    try:
        usuario = UsuarioService.actualizar(db, usuario_id, datos, admin)
    except RolClienteNoAsignableError:
        raise _error(
            "El rol CLIENTE no puede asignarse a un usuario interno",
            status.HTTP_400_BAD_REQUEST,
        )
    except UsuarioNoEncontradoError:
        raise _error("Usuario no encontrado", status.HTTP_404_NOT_FOUND)
    except CorreoDuplicadoError:
        raise _error(
            "El correo ya se encuentra registrado", status.HTTP_409_CONFLICT
        )
    except CiDuplicadoError:
        raise _error(
            "El CI ya se encuentra registrado", status.HTTP_409_CONFLICT
        )
    except RolInexistenteError:
        raise _error("El rol no existe", status.HTTP_404_NOT_FOUND)
    except RolInactivoError:
        raise _error("El rol no está activo", status.HTTP_422_UNPROCESSABLE_ENTITY)
    except SucursalInvalidaError:
        raise _error(
            "La sucursal no existe o no está activa", status.HTTP_404_NOT_FOUND
        )
    except EmpleadoNoEncontradoError:
        raise _error(
            "El usuario no tiene un empleado asociado",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except SinCambiosError:
        raise _error(
            "No se recibieron datos para actualizar",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return usuario


@router.patch("/{usuario_id}/estado", response_model=UsuarioResponse)
def cambiar_estado_usuario(
    usuario_id: int,
    datos: UsuarioEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(
        require_permission(FUNCION_GESTIONAR_USUARIOS, "ELIMINAR")
    ),
):
    try:
        usuario = UsuarioService.cambiar_estado(db, usuario_id, datos.estado, admin)
    except UsuarioNoEncontradoError:
        raise _error("Usuario no encontrado", status.HTTP_404_NOT_FOUND)
    return usuario
