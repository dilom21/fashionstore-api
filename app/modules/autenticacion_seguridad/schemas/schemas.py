from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SexoCliente(str, Enum):
    """Valores reales admitidos por la BD (sin crear enum en la base)."""

    MASCULINO = "MASCULINO"
    FEMENINO = "FEMENINO"
    OTRO = "OTRO"
    NO_ESPECIFICA = "NO_ESPECIFICA"


class ClienteRegistroRequest(BaseModel):
    """Registro publico de cuenta de CLIENTE (sin JWT previo).

    No admite rol, estado, ids ni contexto: cualquier campo extra se rechaza
    con 422 (``extra="forbid"``), de modo que un intento de escalamiento de
    privilegios falla en la validacion.

    La politica de contrasena y la coincidencia de las dos contrasenas se
    validan en el servicio (autoridad del backend) para devolver el mensaje
    de dominio controlado.

    Las contrasenas NO se recortan: se comparan y se hashean tal como llegan
    (solo se validan).
    """

    model_config = ConfigDict(extra="forbid")

    correo: str = Field(min_length=5, max_length=150)
    password: str
    password_confirmacion: str
    nombre: str = Field(min_length=2, max_length=100)
    apellido: str = Field(min_length=2, max_length=100)
    ci: str = Field(min_length=3, max_length=30)
    telefono: str = Field(min_length=1, max_length=30)
    sexo: SexoCliente
    fecha_nacimiento: date

    @field_validator("correo", mode="before")
    @classmethod
    def normalizar_correo(cls, value):
        if not isinstance(value, str):
            raise ValueError("correo invalido")
        correo = value.strip().lower()
        if "@" not in correo or correo.startswith("@") or correo.endswith("@"):
            raise ValueError("correo invalido")
        return correo

    @field_validator("nombre", "apellido", "ci", "telefono", mode="before")
    @classmethod
    def limpiar_texto(cls, value):
        if not isinstance(value, str):
            raise ValueError("valor invalido")
        return value.strip()

    @field_validator("sexo", mode="before")
    @classmethod
    def normalizar_sexo(cls, value):
        if not isinstance(value, str):
            raise ValueError("sexo invalido")
        return value.strip().upper()

    @field_validator("fecha_nacimiento")
    @classmethod
    def validar_fecha_nacimiento(cls, value: date) -> date:
        if value > date.today():
            raise ValueError(
                "la fecha de nacimiento no puede estar en el futuro"
            )
        return value


class ClienteRegistroResponse(BaseModel):
    """Respuesta del registro publico: sin token y sin hash de contrasena."""

    mensaje: str
    usuario_id: int
    cliente_id: int
    correo: str
    nombre: str
    apellido: str


class ClientePerfilResponse(BaseModel):
    cliente_id: int
    usuario_id: int
    correo: str
    rol: str
    nombre: str
    apellido: str
    ci: str | None
    telefono: str | None
    sexo: str | None
    fecha_nacimiento: date | None
    estado: bool
    fecha_creacion: datetime


class ClientePerfilUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = None
    apellido: str | None = None
    telefono: str | None = None
    sexo: SexoCliente | None = None
    fecha_nacimiento: date | None = None

    @field_validator("nombre", "apellido", mode="before")
    @classmethod
    def validar_nombre(cls, value):
        if not isinstance(value, str):
            raise ValueError("nombre o apellido invalido")
        value = value.strip()
        if not 2 <= len(value) <= 100:
            raise ValueError("nombre y apellido deben tener entre 2 y 100 caracteres")
        return value

    @field_validator("telefono", mode="before")
    @classmethod
    def validar_telefono(cls, value):
        if not isinstance(value, str):
            raise ValueError("telefono invalido")
        value = value.strip()
        if not 1 <= len(value) <= 30:
            raise ValueError("telefono debe tener entre 1 y 30 caracteres")
        return value

    @field_validator("sexo", mode="before")
    @classmethod
    def validar_sexo(cls, value):
        if not isinstance(value, str):
            raise ValueError("sexo invalido")
        return value.strip().upper()

    @field_validator("fecha_nacimiento", mode="before")
    @classmethod
    def validar_fecha_nacimiento(cls, value):
        if value is None:
            raise ValueError("fecha de nacimiento invalida")
        return value

    @field_validator("fecha_nacimiento")
    @classmethod
    def validar_fecha_no_futura(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("la fecha de nacimiento no puede estar en el futuro")
        return value
