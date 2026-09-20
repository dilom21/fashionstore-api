"""Adaptador Stripe (Test Mode) para CU22.

Todo el contacto con el SDK oficial vive aqui. El service de negocio no importa
``stripe`` y los tests pueden sustituir este provider por un fake sin tocar la
red. Nunca se loggean ni persisten secretos (sk_test, whsec, client_secret).
"""

import logging

import stripe as stripe_sdk

from app.core.config import settings
from app.modules.pagos.errors import (
    StripeConfiguracionError,
    StripeIntentError,
    StripeRefundError,
    StripeWebhookFirmaError,
)
from app.modules.pagos.providers.base import (
    IntencionPago,
    ProveedorPagoBase,
    Reembolso,
)

logger = logging.getLogger(__name__)

PASARELA_STRIPE = "STRIPE"

# Estados de PaymentIntent en los que el intent sigue siendo utilizable para
# reintentar el cobro sin crear uno nuevo (principio: un PaymentIntent por
# orden/sesion). ``succeeded`` y ``canceled`` son terminales.
ESTADOS_REUTILIZABLES = frozenset(
    {
        "requires_payment_method",
        "requires_confirmation",
        "requires_action",
        "requires_capture",
        "processing",
    }
)
ESTADO_SUCCEEDED = "succeeded"
ESTADO_CANCELED = "canceled"

# Estados reales de un Refund de Stripe. Solo ``succeeded`` confirma que el
# dinero fue devuelto; ``pending``/``requires_action`` siguen en proceso.
ESTADO_REFUND_SUCCEEDED = "succeeded"
ESTADO_REFUND_PENDING = "pending"
ESTADO_REFUND_REQUIRES_ACTION = "requires_action"
ESTADO_REFUND_FAILED = "failed"
ESTADO_REFUND_CANCELED = "canceled"
ESTADOS_REFUND_EXITOSO = frozenset({ESTADO_REFUND_SUCCEEDED})
ESTADOS_REFUND_PENDIENTE = frozenset(
    {ESTADO_REFUND_PENDING, ESTADO_REFUND_REQUIRES_ACTION}
)


def _campo(objeto, nombre):
    """Lee un campo de un StripeObject (dict) o de un mapping de test."""
    if objeto is None:
        return None
    if isinstance(objeto, dict):
        return objeto.get(nombre)
    return getattr(objeto, nombre, None)


class StripeProveedor(ProveedorPagoBase):
    """Implementacion real sobre el SDK ``stripe`` (Test Mode)."""

    def __init__(self) -> None:
        self._configurar()

    @staticmethod
    def _configurar() -> None:
        if not settings.stripe_secret_key:
            raise StripeConfiguracionError(
                "Stripe no esta configurado: falta STRIPE_SECRET_KEY"
            )
        stripe_sdk.api_key = settings.stripe_secret_key

    @staticmethod
    def _traducir_intencion(intent) -> IntencionPago:
        return IntencionPago(
            id=str(_campo(intent, "id") or ""),
            client_secret=_campo(intent, "client_secret"),
            estado=str(_campo(intent, "status") or ""),
            monto=int(_campo(intent, "amount") or 0),
            moneda=str(_campo(intent, "currency") or "").lower(),
        )

    def crear_intencion(
        self,
        *,
        monto: int,
        moneda: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> IntencionPago:
        try:
            intent = stripe_sdk.PaymentIntent.create(
                amount=monto,
                currency=moneda,
                automatic_payment_methods={"enabled": True},
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
        except stripe_sdk.error.StripeError as exc:
            # Solo el tipo de error; jamas el payload (puede traer secretos).
            logger.error(
                "Stripe fallo al crear PaymentIntent: %s", type(exc).__name__
            )
            raise StripeIntentError(
                "Stripe no pudo crear la intencion de pago"
            ) from exc
        return self._traducir_intencion(intent)

    def recuperar_intencion(self, intencion_id: str) -> IntencionPago:
        try:
            intent = stripe_sdk.PaymentIntent.retrieve(intencion_id)
        except stripe_sdk.error.StripeError as exc:
            logger.error(
                "Stripe fallo al recuperar PaymentIntent: %s",
                type(exc).__name__,
            )
            raise StripeIntentError(
                "Stripe no pudo recuperar la intencion de pago"
            ) from exc
        return self._traducir_intencion(intent)

    def cancelar_intencion(self, intencion_id: str) -> None:
        try:
            intent = stripe_sdk.PaymentIntent.retrieve(intencion_id)
            intent.cancel()
        except stripe_sdk.error.StripeError as exc:
            logger.error(
                "Stripe fallo al cancelar PaymentIntent: %s",
                type(exc).__name__,
            )
            raise StripeIntentError(
                "Stripe no pudo cancelar la intencion de pago"
            ) from exc

    def reembolsar_intencion(
        self, intencion_id: str, *, idempotency_key: str
    ) -> Reembolso:
        try:
            refund = stripe_sdk.Refund.create(
                payment_intent=intencion_id,
                idempotency_key=idempotency_key,
            )
        except stripe_sdk.error.StripeError as exc:
            logger.error(
                "Stripe fallo al reembolsar PaymentIntent: %s",
                type(exc).__name__,
            )
            raise StripeRefundError(
                "Stripe no pudo reembolsar el pago"
            ) from exc
        # Se inspecciona el estado real: un objeto Refund no implica exito.
        return Reembolso(
            id=str(_campo(refund, "id") or ""),
            estado=str(_campo(refund, "status") or "").lower(),
        )

    def verificar_webhook(self, payload: bytes, firma: str) -> dict:
        if not settings.stripe_webhook_secret:
            raise StripeConfiguracionError(
                "Stripe no esta configurado: falta STRIPE_WEBHOOK_SECRET"
            )
        try:
            event = stripe_sdk.Webhook.construct_event(
                payload, firma, settings.stripe_webhook_secret
            )
        except (stripe_sdk.error.SignatureVerificationError, ValueError) as exc:
            raise StripeWebhookFirmaError(
                "La firma del webhook de Stripe no es valida"
            ) from exc
        return event
