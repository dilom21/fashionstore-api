from typing import Literal

from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    correo: str
    password: str

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, value: str) -> str:
        correo = value.strip().lower()
        if "@" not in correo or correo.startswith("@") or correo.endswith("@"):
            raise ValueError("correo invalido")
        return correo


class UsuarioAuthResponse(BaseModel):
    """Respuesta basica del usuario autenticado (GET /auth/me).

    contexto indica como inicio sesion y se lee del JWT actual.
    """

    id: int
    correo: str
    rol: str
    contexto: Literal["cliente", "personal"]


class ClienteAuthResponse(UsuarioAuthResponse):
    cliente_id: int
    nombre: str
    apellido: str


class PersonalAuthResponse(UsuarioAuthResponse):
    empleado_id: int
    nombre: str
    apellido: str
    sucursal_id: int


class LoginResponseBase(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ClienteLoginResponse(LoginResponseBase):
    usuario: ClienteAuthResponse


class PersonalLoginResponse(LoginResponseBase):
    usuario: PersonalAuthResponse
