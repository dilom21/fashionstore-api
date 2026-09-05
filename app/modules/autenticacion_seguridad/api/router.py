from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_auth_context, get_current_user
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)
from app.modules.autenticacion_seguridad.schemas.schemas import (
    ClienteAuthResponse,
    ClienteLoginResponse,
    LoginRequest,
    PersonalAuthResponse,
    PersonalLoginResponse,
    UsuarioAuthResponse,
)
from app.modules.autenticacion_seguridad.services.service import AuthService

router = APIRouter(prefix="/auth", tags=["Autenticacion"])

_MENSAJE_CREDENCIALES_INVALIDAS = "Credenciales inválidas o acceso no autorizado"


def _error_credenciales() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_MENSAJE_CREDENCIALES_INVALIDAS,
        headers={"WWW-Authenticate": "Bearer"},
    )


def serializar_usuario(
    usuario: Usuario, contexto: Literal["cliente", "personal"]
) -> UsuarioAuthResponse:
    return UsuarioAuthResponse(
        id=usuario.id,
        correo=usuario.correo,
        rol=usuario.rol.nombre,
        contexto=contexto,
    )


def serializar_cliente(usuario: Usuario, cliente: Cliente) -> ClienteAuthResponse:
    return ClienteAuthResponse(
        id=usuario.id,
        correo=usuario.correo,
        rol=usuario.rol.nombre,
        contexto="cliente",
        cliente_id=cliente.id,
        nombre=cliente.nombre,
        apellido=cliente.apellido,
    )


def serializar_personal(usuario: Usuario, empleado: Empleado) -> PersonalAuthResponse:
    return PersonalAuthResponse(
        id=usuario.id,
        correo=usuario.correo,
        rol=usuario.rol.nombre,
        contexto="personal",
        empleado_id=empleado.id,
        nombre=empleado.nombres,
        apellido=empleado.apellidos,
        sucursal_id=empleado.sucursal_id,
    )


@router.post("/clientes/login", response_model=ClienteLoginResponse)
def login_cliente(credenciales: LoginRequest, db: Session = Depends(get_db)):
    resultado = AuthService.login_cliente(
        db, credenciales.correo, credenciales.password
    )
    if resultado is None:
        raise _error_credenciales()

    usuario, cliente, access_token = resultado
    return ClienteLoginResponse(
        access_token=access_token,
        usuario=serializar_cliente(usuario, cliente),
    )


@router.post("/personal/login", response_model=PersonalLoginResponse)
def login_personal(credenciales: LoginRequest, db: Session = Depends(get_db)):
    resultado = AuthService.login_personal(
        db, credenciales.correo, credenciales.password
    )
    if resultado is None:
        raise _error_credenciales()

    usuario, empleado, access_token = resultado
    return PersonalLoginResponse(
        access_token=access_token,
        usuario=serializar_personal(usuario, empleado),
    )


@router.get("/me", response_model=UsuarioAuthResponse)
def obtener_usuario_actual(
    usuario: Usuario = Depends(get_current_user),
    contexto: Literal["cliente", "personal"] = Depends(get_current_auth_context),
):
    return serializar_usuario(usuario, contexto)
