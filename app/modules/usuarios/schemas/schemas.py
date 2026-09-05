from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalizar_correo(value: str) -> str:
    correo = value.strip().lower()
    if "@" not in correo or correo.startswith("@") or correo.endswith("@"):
        raise ValueError("correo invalido")
    return correo


class RolResumenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class SucursalResumenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class EmpleadoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombres: str
    apellidos: str
    ci: str
    telefono: str | None
    fecha_contratacion: date
    sucursal: SucursalResumenResponse


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    correo: str
    estado: bool
    fecha_creacion: datetime
    rol: RolResumenResponse
    empleado: EmpleadoResponse | None = None


class UsuarioListResponse(BaseModel):
    items: list[UsuarioResponse]


class EmpleadoCreate(BaseModel):
    nombres: str = Field(min_length=2, max_length=100)
    apellidos: str = Field(min_length=2, max_length=100)
    ci: str = Field(min_length=3, max_length=30)
    telefono: str | None = Field(default=None, max_length=30)
    sucursal_id: int
    fecha_contratacion: date = Field(default_factory=date.today)


class EmpleadoUpdate(BaseModel):
    nombres: str | None = Field(default=None, min_length=2, max_length=100)
    apellidos: str | None = Field(default=None, min_length=2, max_length=100)
    ci: str | None = Field(default=None, min_length=3, max_length=30)
    telefono: str | None = Field(default=None, max_length=30)
    sucursal_id: int | None = None
    fecha_contratacion: date | None = None

    @property
    def tiene_datos(self) -> bool:
        return any(
            value is not None
            for value in (
                self.nombres,
                self.apellidos,
                self.ci,
                self.telefono,
                self.sucursal_id,
                self.fecha_contratacion,
            )
        )


class UsuarioCreate(BaseModel):
    correo: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=8, max_length=128)
    rol_id: int
    empleado: EmpleadoCreate

    _normalizar_correo = field_validator("correo")(_normalizar_correo)


class UsuarioUpdate(BaseModel):
    correo: str | None = Field(default=None, min_length=5, max_length=150)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    rol_id: int | None = None
    empleado: EmpleadoUpdate | None = None

    @field_validator("correo")
    @classmethod
    def normalizar_correo_opcional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalizar_correo(value)

    @property
    def tiene_datos(self) -> bool:
        return any(
            value is not None
            for value in (self.correo, self.password, self.rol_id, self.empleado)
        )


class UsuarioEstadoUpdate(BaseModel):
    estado: bool
