"""Contrato HTTP de CU21 - Registrar pago presencial (CAJERO/POS)."""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class MetodoPago(str, Enum):
    """Valores exactos del CHECK real ck_pago_metodo de la tabla pago.

    Stripe no participa en CU21: son metodos presenciales.
    """

    EFECTIVO = "EFECTIVO"
    TARJETA = "TARJETA"
    TRANSFERENCIA = "TRANSFERENCIA"
    QR = "QR"
    OTRO = "OTRO"


class RegistrarPagoPresencialRequest(BaseModel):
    """Solo venta_id y metodo.

    El backend obtiene la venta y deriva de ella el monto (venta.total), el
    estado (APROBADO) y la sucursal/empleado. No se aceptan como autoridad
    monto, estado, empleado_id, sucursal_id, canal ni total del frontend.
    """

    venta_id: int = Field(gt=0)
    metodo: MetodoPago


class PagoPresencialResponse(BaseModel):
    """Resultado del pago aprobado y de la confirmacion de la venta."""

    pago_id: int
    venta_id: int
    metodo: str
    estado_pago: str
    monto: Decimal
    fecha: datetime
    estado_venta: str
    reserva_id: int | None = None
    estado_reserva: str | None = None
