from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.seguridad import Usuario
from app.schemas.auth import LoginRequest, LoginResponse, UsuarioAuthResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Autenticaci?n"])


def serializar_usuario(usuario: Usuario) -> UsuarioAuthResponse:
    return UsuarioAuthResponse(
        id=usuario.id,
        correo=usuario.correo,
        rol=usuario.rol.nombre,
    )


@router.post("/login", response_model=LoginResponse)
def login(credenciales: LoginRequest, db: Session = Depends(get_db)):
    autenticacion = AuthService.autenticar(
        db,
        str(credenciales.correo).lower(),
        credenciales.password,
    )
    if autenticacion is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contrase?a inv?lidos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario, access_token = autenticacion
    return LoginResponse(access_token=access_token, usuario=serializar_usuario(usuario))


@router.get("/me", response_model=UsuarioAuthResponse)
def obtener_usuario_actual(usuario: Usuario = Depends(get_current_user)):
    return serializar_usuario(usuario)
