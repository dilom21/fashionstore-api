"""Contratos Pydantic de Asistencia Inteligente.

Separacion de responsabilidades:

- ``RecomendacionRequest``: entrada del cliente (texto no confiable).
- ``RespuestaIAValidada``: forma minima que se acepta del modelo. Solo admite
  ``inventario_id`` y ``motivo``; cualquier dato comercial que intente aportar
  el modelo se ignora.
- ``RecomendacionesResponse``: respuesta final, reconstruida desde PostgreSQL.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecomendacionRequest(BaseModel):
    """Solicitud de recomendaciones. Solo ``consulta`` es obligatoria."""

    # extra="forbid": rechaza campos no contratados, en particular cliente_id.
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(min_length=1, max_length=500)
    talla: str | None = Field(default=None, max_length=30)
    color: str | None = Field(default=None, max_length=60)
    presupuesto_max: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    sucursal_id: int | None = Field(default=None, gt=0)
    limite: int = Field(default=4, ge=1, le=6)

    @field_validator("consulta")
    @classmethod
    def _consulta_no_vacia(cls, valor: str) -> str:
        limpio = " ".join(str(valor).split())
        if not limpio:
            raise ValueError("La consulta no puede estar vacia")
        return limpio

    @field_validator("talla", "color")
    @classmethod
    def _opcional_limpio(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpio = " ".join(str(valor).split())
        return limpio or None


class RecomendacionIAItem(BaseModel):
    """Item crudo devuelto por el modelo (se ignora todo campo extra)."""

    model_config = ConfigDict(extra="ignore")

    inventario_id: int
    motivo: str = ""


class RespuestaIAValidada(BaseModel):
    """Forma minima aceptada del modelo (se ignora todo campo extra)."""

    model_config = ConfigDict(extra="ignore")

    titulo: str = ""
    descripcion: str = ""
    recomendaciones: list[RecomendacionIAItem] = Field(default_factory=list)


class IntencionIA(BaseModel):
    """Restricciones duras extraidas de la consulta libre por el modelo.

    Es informacion NO confiable: el backend valida los tipos, la acota contra
    el catalogo real y aplica los filtros en PostgreSQL. ``preferencias`` son
    blandas (estilo, ocasion, formalidad, casual, oficina, premium, urbano) y
    NUNCA filtran candidatos.
    """

    model_config = ConfigDict(extra="ignore")

    presupuesto_max: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    talla: str | None = Field(default=None, max_length=30)
    color: str | None = Field(default=None, max_length=60)
    sucursal: str | None = Field(default=None, max_length=120)
    preferencias: list[str] = Field(default_factory=list)

    @field_validator("talla", "color", "sucursal")
    @classmethod
    def _texto_limpio(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpio = " ".join(str(valor).split())
        return limpio or None


class RecomendacionProductoResponse(BaseModel):
    """Recomendacion final con datos comerciales reconstruidos desde la BD."""

    producto_id: int
    nombre: str
    precio: Decimal
    imagen_url: str | None
    categoria: str
    variante_id: int | None
    talla: str | None
    color: str | None
    inventario_id: int | None
    sucursal_id: int | None
    sucursal: str | None
    temporada: str | None
    stock_disponible: int
    motivo: str
    requiere_seleccion: bool


class RecomendacionesResponse(BaseModel):
    """Respuesta del endpoint de Asistencia Inteligente."""

    titulo: str
    descripcion: str
    recomendaciones: list[RecomendacionProductoResponse]
