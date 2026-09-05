from pydantic import BaseModel, ConfigDict


class CiudadResumenResponse(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class SucursalResponse(BaseModel):
    id: int
    nombre: str
    direccion: str
    telefono: str | None
    estado: bool
    ciudad: CiudadResumenResponse

    model_config = ConfigDict(from_attributes=True)
