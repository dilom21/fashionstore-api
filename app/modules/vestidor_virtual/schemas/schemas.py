"""Contratos Pydantic de CU26 - Vestidor Virtual (AR).

El backend NO procesa frames, landmarks ni imagenes de camara: la deteccion
corporal es 100% local en el telefono (motor AR de Harold). Aqui solo viajan
metadatos de configuracion y el ciclo de vida de sesiones/pruebas.

Reglas de entrada:
- Los cuerpos usan ``extra="forbid"``: datos como ``cliente_id``, ``estado``
  inicial, ``fecha_inicio`` o ``asset_url`` NUNCA se aceptan desde el cliente.
- ``cliente_id`` y las fechas los deriva el backend (JWT / servidor).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfiguracionARResponse(BaseModel):
    """Configuracion AR de un producto lista para el motor de vestidor."""

    configuracion_id: int
    recurso_producto_id: int
    asset_url: str
    color_id: int | None
    color: str | None
    zona_cuerpo: str
    tipo_asset: str
    factor_ancho: Decimal
    factor_alto: Decimal
    offset_x: Decimal
    offset_y: Decimal
    rotacion_offset: Decimal
    orden_capa: int
    opacidad: Decimal


class ConfiguracionesProductoResponse(BaseModel):
    """Respuesta de configuraciones compatibles de un producto.

    ``compatible`` es ``False`` cuando el producto existe pero no tiene ninguna
    configuracion AR activa; en ese caso ``configuraciones`` va vacia. Un
    producto inexistente responde 404.
    """

    producto_id: int
    compatible: bool
    configuraciones: list[ConfiguracionARResponse]


class CrearSesionRequest(BaseModel):
    """Cuerpo opcional de ``POST /sesiones``.

    No define campos: existe para rechazar (422) cualquier dato no contratado
    como ``cliente_id``, ``estado`` o fechas enviadas por el cliente.
    """

    model_config = ConfigDict(extra="forbid")


class SesionARResponse(BaseModel):
    """Sesion de vestidor del cliente autenticado."""

    sesion_id: int
    cliente_id: int
    estado: str
    fecha_inicio: datetime
    fecha_fin: datetime | None


class IniciarPruebaRequest(BaseModel):
    """Inicio de una prueba de prenda dentro de una sesion ACTIVA."""

    model_config = ConfigDict(extra="forbid")

    configuracion_id: int = Field(gt=0)
    variante_producto_id: int | None = Field(default=None, gt=0)


class FinalizarPruebaRequest(BaseModel):
    """Estado final de una prueba."""

    model_config = ConfigDict(extra="forbid")

    estado: Literal["COMPLETADA", "CANCELADA", "ERROR"]


class FinalizarSesionRequest(BaseModel):
    """Estado final de una sesion."""

    model_config = ConfigDict(extra="forbid")

    estado: Literal["FINALIZADA", "CANCELADA"]


class PruebaARResponse(BaseModel):
    """Prueba de una prenda dentro de una sesion de vestidor."""

    prueba_id: int
    sesion_vestidor_ar_id: int
    configuracion_id: int
    variante_producto_id: int | None
    estado: str
    fecha_inicio: datetime
    fecha_fin: datetime | None
