from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field


class TipoOperacion(str, Enum):
    CAMBIO = "CAMBIO"
    SIN_CAMBIO = "SIN_CAMBIO"


class CondicionFisica(str, Enum):
    APTO = "APTO"
    NO_APTO = "NO_APTO"
    CUARENTENA = "CUARENTENA"


class ItemCU15(BaseModel):
    detalle_venta_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)
    variante_nueva_id: int | None = Field(default=None, gt=0)


class SolicitudCU15(BaseModel):
    venta_id: int = Field(gt=0)
    tipo: TipoOperacion
    sucursal_recepcion_id: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)
    observacion: str | None = Field(default=None, max_length=1000)
    items: list[ItemCU15] = Field(min_length=1)
    idempotency_key: UUID


class InspeccionCU15(BaseModel):
    condicion: CondicionFisica | None = None
    restock_autorizado: bool = False
    observacion: str | None = Field(default=None, max_length=1000)
    items: list["InspeccionItemCU15"] | None = Field(default=None, min_length=1)


class InspeccionItemCU15(BaseModel):
    item_id: int = Field(gt=0)
    condicion: CondicionFisica
    restock_autorizado: bool = False
    observacion: str | None = Field(default=None, max_length=1000)


class RechazoCU15(BaseModel):
    motivo: str = Field(min_length=1, max_length=500)


class ConfirmarReembolsoCU15(BaseModel):
    comprobante: str = Field(min_length=1, max_length=255)
    metodo: str = Field(min_length=1, max_length=80)


class ConfirmarDiferenciaCU15(BaseModel):
    """Comprobante de cobro emitido por la autoridad de pagos autorizada."""
    comprobante: str = Field(min_length=1, max_length=255)
    medio: str = Field(min_length=1, max_length=80)


class ElegibilidadCU15(BaseModel):
    venta_id: int
    fecha_compra: datetime
    fecha_limite_reclamo: datetime
    es_elegible: bool
    motivo: str | None = None
    items: list[dict] = []


class NotaCU15(BaseModel):
    nota_id: int
    numero: str
    tipo: TipoOperacion
    estado: str
    venta_id: int
    fecha_hora: datetime
    importe_origen: Decimal
    importe_sustituto: Decimal
    diferencia: Decimal
    importe_reembolsable: Decimal | None = None
    importe_retenido: Decimal | None = None
    reembolso_estado: str | None = None
    items: list[dict] = []
    outbox_pendiente: bool = True
