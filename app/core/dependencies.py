from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.autenticacion_seguridad.services.service import AuthService
from app.modules.roles.services.service import RolService

_bearer_scheme = HTTPBearer(auto_error=False)

_CONTEXTOS_VALIDOS = ("cliente", "personal")

_ROL_ADMINISTRADOR = "ADMINISTRADOR"


def _error_no_autorizado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> Usuario:
    if credentials is None:
        raise _error_no_autorizado()

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _error_no_autorizado()

    try:
        usuario_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _error_no_autorizado()

    usuario = AuthService.obtener_usuario_activo(db, usuario_id)
    if usuario is None:
        raise _error_no_autorizado()

    return usuario


def get_current_auth_context(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> str:
    """Devuelve el contexto (cliente|personal) almacenado en el JWT actual.

    Es una dependencia adicional: get_current_user no se modifica y sigue
    siendo la unica validacion de usuario para los demas modulos.
    """
    if credentials is None:
        raise _error_no_autorizado()

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _error_no_autorizado()

    contexto = payload.get("contexto")
    if contexto not in _CONTEXTOS_VALIDOS:
        raise _error_no_autorizado()

    return contexto


def get_current_admin(
    usuario: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    """Requerimiento de autorizacion para operaciones exclusivas de administracion.

    Depende de get_current_user (JWT valido + usuario activo) y verifica que el
    rol asignado sea ADMINISTRADOR. Reutilizable por otros CU administrativos.
    """
    if usuario.rol.nombre != _ROL_ADMINISTRADOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado: se requiere rol ADMINISTRADOR",
        )
    return usuario


def require_permission(funcion: str, accion: str):
    """Fabrica una dependencia de autorizacion granular basada en la base de datos.

    Valida la cadena Usuario -> Rol -> RolFuncion -> Funcion -> Modulo -> Accion
    considerando unicamente registros activos. Reutiliza get_current_user y no
    duplica la logica de JWT.

    Sin permiso se responde 403 Forbidden.
    """

    def _verificador(
        usuario: Annotated[Usuario, Depends(get_current_user)],
        db: Session = Depends(get_db),
    ) -> Usuario:
        tiene_permiso = RolService.tiene_permiso(
            db, usuario.rol_id, funcion, accion
        )
        if not tiene_permiso:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No autorizado: se requiere permiso {funcion} / {accion}",
            )
        return usuario

    return _verificador
