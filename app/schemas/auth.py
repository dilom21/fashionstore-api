from pydantic import BaseModel, ConfigDict, field_validator


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
    id: int
    correo: str
    rol: str

    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioAuthResponse
