from pydantic import BaseModel, ConfigDict


class CategoriaResponse(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)
