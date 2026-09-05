from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalizar_nombre_rol(value: str) -> str:
    nombre = value.strip()
    if not nombre:
        raise ValueError("el nombre del rol no puede estar vacio")
    return nombre.upper()


class RolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None
    estado: bool


class RolDetalleResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool
    es_base: bool
    usuarios_activos: int


class RolCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)

    _normalizar_nombre = field_validator("nombre")(_normalizar_nombre_rol)

    @field_validator("descripcion")
    @classmethod
    def normalizar_descripcion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        descripcion = value.strip()
        return descripcion or None


class RolUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)

    @field_validator("nombre")
    @classmethod
    def normalizar_nombre_opcional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalizar_nombre_rol(value)

    @field_validator("descripcion")
    @classmethod
    def normalizar_descripcion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        descripcion = value.strip()
        return descripcion or None

    @property
    def tiene_cambios(self) -> bool:
        return self.nombre is not None or self.descripcion is not None


class RolEstadoUpdate(BaseModel):
    estado: bool


class AccionResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None


class FuncionPermisosResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    acciones: list[AccionResponse]


class ModuloPermisosResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    funciones: list[FuncionPermisosResponse]


class PermisoAsignadoResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    otorgada: bool


class FuncionPermisosRolResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    acciones: list[PermisoAsignadoResponse]


class ModuloPermisosRolResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    funciones: list[FuncionPermisosRolResponse]


class RolPermisosResponse(BaseModel):
    rol_id: int
    rol_nombre: str
    modulos: list[ModuloPermisosRolResponse]


class PermisoItemRequest(BaseModel):
    funcion_id: int
    accion_id: int


class RolPermisosUpdate(BaseModel):
    permisos: list[PermisoItemRequest]
