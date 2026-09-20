"""Contrato HTTP de CU22 - Procesar pago electronico con Stripe (Test Mode)."""

from decimal import Decimal

from pydantic import BaseModel, Field


class CrearIntencionPagoRequest(BaseModel):
    """Unica autoridad: la venta digital del cliente autenticado.

    No se aceptan ``amount``, ``currency``, ``cliente_id``, ``estado`` ni
    ``metodo``: el backend los deriva de la venta y de la configuracion.
    """

    venta_id: int = Field(gt=0)


class IntencionPagoResponse(BaseModel):
    """Datos necesarios para que Stripe.js/Flutter confirme el pago.

    ``client_secret`` se entrega solo al cliente dueno de la venta y no se
    persiste ni se loggea.
    """

    venta_id: int
    pago_id: int
    payment_intent_id: str
    client_secret: str | None = None
    monto: Decimal
    moneda: str
    estado_pago: str


class EstadoPagoVentaResponse(BaseModel):
    """Estado backend de la venta y de su pago electronico mas reciente."""

    venta_id: int
    estado_venta: str
    pago_id: int | None = None
    estado_pago: str | None = None
    payment_intent_id: str | None = None
    # Compensacion: PENDIENTE (pago APROBADO, refund en curso/reintentable) o
    # REEMBOLSADO (refund aplicado). None cuando no hay compensacion.
    compensacion_estado: str | None = None


class WebhookStripeResponse(BaseModel):
    """Acuse del webhook. Stripe solo necesita un 2xx."""

    recibido: bool = True
    evento: str | None = None
    procesado: bool = False
