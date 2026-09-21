from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_auth_context, get_current_cliente, get_current_user
from app.core.password_policy import MENSAJE_POLITICA
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)
from app.modules.autenticacion_seguridad.schemas.schemas import (
    ClienteAuthResponse,
    ClienteLoginResponse,
    ClientePerfilResponse,
    ClientePerfilUpdateRequest,
    ClienteRegistroRequest,
    ClienteRegistroResponse,
    LoginRequest,
    PersonalAuthResponse,
    PersonalLoginResponse,
    UsuarioAuthResponse,
)
from app.modules.autenticacion_seguridad.services.service import (
    AuthService,
    CiClienteDuplicadoError,
    CorreoClienteDuplicadoError,
    MENSAJE_CUENTA_CREADA,
    PasswordInseguraError,
    PasswordsNoCoincidenError,
    PerfilClienteVacioError,
    RegistroClienteError,
    RolClienteNoDisponibleError,
)

router = APIRouter(prefix="/auth", tags=["Autenticacion"])

_MENSAJE_CREDENCIALES_INVALIDAS = "Credenciales inválidas o acceso no autorizado"


def _error_credenciales() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_MENSAJE_CREDENCIALES_INVALIDAS,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _error(detalle: str, codigo: int) -> HTTPException:
    """Error HTTP con mensaje controlado (nunca SQL ni stacktrace)."""
    return HTTPException(status_code=codigo, detail=detalle)


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


@router.post(
    "/clientes/registro",
    response_model=ClienteRegistroResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_cliente(
    datos: ClienteRegistroRequest, db: Session = Depends(get_db)
):
    """Registro publico de cuenta de CLIENTE (no requiere JWT).

    Crea Usuario + Cliente en UNA sola transaccion, con el rol CLIENTE
    resuelto por nombre en la base (el frontend nunca envia rol). No emite
    token: el cliente inicia sesion despues con ``POST /auth/clientes/login``.

    Codigos: 201 creado; 409 correo o CI duplicado; 422 contrasena insegura,
    contrasenas distintas, sexo invalido, fecha futura o datos invalidos;
    500 configuracion interna (rol CLIENTE inexistente/inactivo).
    """
    try:
        usuario, cliente = AuthService.registrar_cliente(db, datos)
    except CorreoClienteDuplicadoError:
        raise _error(
            "Ya existe una cuenta registrada con este correo.",
            status.HTTP_409_CONFLICT,
        )
    except CiClienteDuplicadoError:
        raise _error(
            "Ya existe un cliente registrado con este documento.",
            status.HTTP_409_CONFLICT,
        )
    except PasswordsNoCoincidenError:
        raise _error(
            "Las contraseñas no coinciden.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except PasswordInseguraError as exc:
        raise _error(
            str(exc) or MENSAJE_POLITICA,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except RolClienteNoDisponibleError:
        raise _error(
            "No fue posible completar el registro. Intente más tarde.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except RegistroClienteError:
        raise _error(
            "No fue posible completar el registro.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return ClienteRegistroResponse(
        mensaje=MENSAJE_CUENTA_CREADA,
        usuario_id=usuario.id,
        cliente_id=cliente.id,
        correo=usuario.correo,
        nombre=cliente.nombre,
        apellido=cliente.apellido,
    )


@router.get("/clientes/me/perfil", response_model=ClientePerfilResponse)
def obtener_perfil_cliente(
    cliente: Cliente = Depends(get_current_cliente),
    usuario: Usuario = Depends(get_current_user),
):
    return AuthService.obtener_perfil_cliente(cliente, usuario)


@router.patch("/clientes/me/perfil", response_model=ClientePerfilResponse)
def actualizar_perfil_cliente(
    datos: ClientePerfilUpdateRequest,
    cliente: Cliente = Depends(get_current_cliente),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return AuthService.actualizar_perfil_cliente(db, cliente, usuario, datos)
    except PerfilClienteVacioError:
        raise _error("No se proporcionaron datos para actualizar.", status.HTTP_422_UNPROCESSABLE_ENTITY)


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
