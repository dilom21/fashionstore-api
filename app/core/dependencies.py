from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.seguridad import Usuario
from app.services.auth_service import AuthService

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> Usuario:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise unauthorized

    try:
        usuario_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized

    usuario = AuthService.obtener_usuario_activo(db, usuario_id)
    if usuario is None:
        raise unauthorized

    return usuario
