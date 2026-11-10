"""Contratos estrictos para operaciones offline CU19/CU20.

Angular solo aporta el UUID de operacion, el tipo, la sucursal y las lineas.
La empresa, el usuario, el rol y el cliente se derivan del JWT tenant; nunca
se aceptan en el body. Los importes de la venta se recalculan en PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TipoOperacionOffline = Literal["RESERVA", "VENTA_CONTADO"]
EstadoTransporte = Literal[
    "PENDIENTE",
    "ENVIANDO",
    "ACEPTADA",
    "CONFLICTO",
    "RECHAZADA",
    "ERROR_REINTENTABLE",
]


class LineaOffline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventario_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=1000)


class OperacionOfflineRequest(BaseModel):
    """Payload comun; las reglas por tipo se validan abajo."""

    model_config = ConfigDict(extra="forbid")

    operacion_uuid: UUID
    tipo: TipoOperacionOffline
    sucursal_id: int = Field(gt=0)
    lineas: list[LineaOffline] = Field(min_length=1, max_length=100)
    fecha_atencion: datetime | None = None
    observacion: str | None = Field(default=None, max_length=500)
    efectivo_recibido: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    # Solo VENTA_CONTADO y solo si el POS identifica un cliente de la empresa.
    # PostgreSQL vuelve a comprobar pertenencia; no autoriza por si solo.
    cliente_id: int | None = Field(default=None, gt=0)

    @field_validator("lineas")
    @classmethod
    def lineas_sin_duplicados(cls, lineas: list[LineaOffline]) -> list[LineaOffline]:
        vistos = set()
        for linea in lineas:
            if linea.inventario_id in vistos:
                raise ValueError("inventario_id duplicado en la operacion")
            vistos.add(linea.inventario_id)
        return lineas

    @model_validator(mode="after")
    def reglas_por_tipo(self) -> "OperacionOfflineRequest":
        if self.tipo == "RESERVA":
            if self.fecha_atencion is None:
                raise ValueError("RESERVA requiere fecha_atencion")
            if self.fecha_atencion.tzinfo is None or self.fecha_atencion.utcoffset() is None:
                raise ValueError("fecha_atencion debe incluir zona horaria")
            if self.fecha_atencion.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                raise ValueError("fecha_atencion debe ser futura")
            if self.efectivo_recibido is not None or self.cliente_id is not None:
                raise ValueError("RESERVA no acepta efectivo_recibido ni cliente_id")
        else:
            if self.fecha_atencion is not None:
                raise ValueError("VENTA_CONTADO no acepta fecha_atencion")
        return self


class OperacionOfflineResponse(BaseModel):
    """Resultado de transporte, separado del estado comercial definitivo."""

    model_config = ConfigDict(extra="forbid")

    operacion_uuid: UUID
    tipo: TipoOperacionOffline
    estado: EstadoTransporte
    id_definitivo: int | None = None
    resultado: dict | None = None
    error_codigo: str | None = None
    error_mensaje: str | None = None
    reintentable: bool = False
