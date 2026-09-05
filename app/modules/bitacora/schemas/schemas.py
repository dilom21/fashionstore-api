from datetime import datetime

from pydantic import BaseModel


class BitacoraUsuarioResumen(BaseModel):
    """Proyeccion segura del usuario responsable de un evento.

    Solo expone identificador, correo y rol; nunca password_hash ni datos
    personales del empleado/cliente asociado.
    """

    id: int
    correo: str
    rol: str


class BitacoraUsuarioFiltroResponse(BaseModel):
    """Usuario seguro para poblar el filtro de usuarios del listado."""

    id: int
    correo: str


class BitacoraResponse(BaseModel):
    id: int
    fecha_hora: datetime
    ip: str | None
    accion: str
    entidad_afectada: str | None
    descripcion: str | None
    usuario: BitacoraUsuarioResumen | None


class BitacoraDetalleResponse(BitacoraResponse):
    """Detalle de un evento; reutiliza la proyeccion segura del listado."""


class BitacoraListResponse(BaseModel):
    items: list[BitacoraResponse]
    total: int
    limit: int
    offset: int


class BitacoraCatalogosResponse(BaseModel):
    acciones: list[str]
    entidades: list[str]
    usuarios: list[BitacoraUsuarioFiltroResponse]
