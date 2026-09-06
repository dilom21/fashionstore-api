from pydantic import BaseModel, ConfigDict, Field


class CiudadResumenResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class SucursalResponse(BaseModel):
    """Contrato estable de CU03 para GET /sucursales y GET /sucursales/{id}.

    No modificar su estructura: es consumida por el listado de sucursales
    activas de CU03 (Usuarios) sin cambios.
    """

    id: int
    nombre: str
    direccion: str
    telefono: str | None
    estado: bool
    ciudad: CiudadResumenResponse

    model_config = ConfigDict(from_attributes=True)


class SucursalDetalleResponse(SucursalResponse):
    """Detalle de CU06: agrega ciudad_id al contrato base de CU03."""

    ciudad_id: int

    model_config = ConfigDict(from_attributes=True)


class CiudadResponse(BaseModel):
    id: int
    nombre: str
    estado: bool
    sucursales_total: int
    sucursales_activas: int

    model_config = ConfigDict(from_attributes=True)


class CiudadCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)


class CiudadUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)


class CiudadEstadoUpdate(BaseModel):
    estado: bool


class SucursalCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    ciudad_id: int = Field(gt=0)
    direccion: str = Field(min_length=1, max_length=255)
    telefono: str | None = Field(default=None, max_length=30)


class SucursalUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    ciudad_id: int | None = Field(default=None, gt=0)
    direccion: str | None = Field(default=None, min_length=1, max_length=255)
    telefono: str | None = Field(default=None, max_length=30)


class SucursalEstadoUpdate(BaseModel):
    estado: bool
