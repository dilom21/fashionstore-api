from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.autenticacion_seguridad.services.service import AuthService

_bearer_scheme = HTTPBearer(auto_error=False)

_CONTEXTOS_VALIDOS = ("cliente", "personal")


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
