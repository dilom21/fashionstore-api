"""Contrato del proveedor de pago electronico (CU22).

El service de negocio depende de esta abstraccion, no del SDK de Stripe. Asi
los tests pueden inyectar un fake y la suite no depende de internet.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class IntencionPago:
    """Vista normalizada y minima de un PaymentIntent de Stripe.

    Solo contiene datos no sensibles: nunca PAN, CVC ni la secret key. El
    ``client_secret`` se devuelve al cliente autenticado y no se persiste.
    """

    id: str
    client_secret: str | None
    estado: str
    monto: int
    moneda: str


@dataclass(frozen=True)
class Reembolso:
    """Vista normalizada y minima de un Refund de Stripe.

    ``estado`` refleja el ``status`` real devuelto por Stripe (succeeded,
    pending, requires_action, failed, canceled). El service solo persiste
    REEMBOLSADO cuando es ``succeeded``.
    """

    id: str
    estado: str


class ProveedorPagoBase(ABC):
    """Operaciones minimas que CU22 necesita de una pasarela."""

    @abstractmethod
    def crear_intencion(
        self,
        *,
        monto: int,
        moneda: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> IntencionPago:
        """Crea un PaymentIntent. ``monto`` va en unidades minimas (entero)."""

    @abstractmethod
    def recuperar_intencion(self, intencion_id: str) -> IntencionPago:
        """Recupera un PaymentIntent existente por su id."""

    @abstractmethod
    def cancelar_intencion(self, intencion_id: str) -> None:
        """Cancela un PaymentIntent (compensacion ante fallo de persistencia)."""

    @abstractmethod
    def reembolsar_intencion(
        self, intencion_id: str, *, idempotency_key: str
    ) -> Reembolso:
        """Reembolsa un PaymentIntent y devuelve el refund con su estado real.

        ``idempotency_key`` debe ser determinista (venta + PaymentIntent) para
        que los retries no generen un segundo refund logico en Stripe. El
        llamador decide segun ``Reembolso.estado``: solo ``succeeded`` confirma
        la compensacion.
        """

    @abstractmethod
    def verificar_webhook(self, payload: bytes, firma: str) -> dict:
        """Verifica la firma y devuelve el evento de Stripe ya parseado."""
