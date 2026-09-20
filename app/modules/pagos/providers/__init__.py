"""Providers (adaptadores) de pasarelas de pago para CU22.

Mantienen la integracion Stripe desacoplada del service de negocio: el service
habla con ``ProveedorPagoBase`` y no con el SDK directamente.
"""

from app.modules.pagos.providers.base import IntencionPago, ProveedorPagoBase
from app.modules.pagos.providers.stripe import StripeProveedor

__all__ = ["IntencionPago", "ProveedorPagoBase", "StripeProveedor"]
