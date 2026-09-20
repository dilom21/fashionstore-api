"""Errores de dominio de CU22 - Procesar pago electronico con Stripe.

Se mantienen separados de los errores de CU21 (``services/service.py``) para
que el pago presencial y el pago electronico no compartan semantica. El router
los traduce a HTTP; nunca se expone una excepcion cruda del SDK de Stripe.
"""


class PagoElectronicoError(Exception):
    """Base de errores de negocio de CU22 (mapeada a HTTP en el router)."""


# ---------------------------------------------------------------------------
# Configuracion / integracion Stripe
# ---------------------------------------------------------------------------


class StripeConfiguracionError(PagoElectronicoError):
    """Faltan credenciales o parametros de Stripe (Test Mode)."""


class StripeIntentError(PagoElectronicoError):
    """Stripe no pudo crear/recuperar/cancelar el PaymentIntent."""


class StripeWebhookFirmaError(PagoElectronicoError):
    """La firma ``Stripe-Signature`` no es valida para el payload recibido."""


class StripeRefundError(PagoElectronicoError):
    """Stripe no pudo crear/procesar el refund (fallo o estado no exitoso).

    Es reintentable: la venta queda CANCELADA con el pago APROBADO y el
    siguiente webhook vuelve a intentar el refund con la misma idempotency key.
    """


class StripeRefundPendienteError(PagoElectronicoError):
    """El refund existe pero aun NO esta confirmado como exitoso.

    Stripe devolvio ``pending`` o ``requires_action``: la compensacion sigue
    pendiente (venta CANCELADA + pago APROBADO) y se debe reconciliar/reintentar
    con la misma idempotency key hasta que el refund sea ``succeeded``.
    """


# ---------------------------------------------------------------------------
# Venta digital
# ---------------------------------------------------------------------------


class VentaDigitalNoEncontradaError(PagoElectronicoError):
    """La venta indicada no existe."""


class VentaDigitalAjenaError(PagoElectronicoError):
    """La venta pertenece a otro cliente."""


class VentaDigitalEstadoInvalidoError(PagoElectronicoError):
    """La venta no es digital (WEB/MOVIL) o no esta PENDIENTE."""


class PagoElectronicoNoEncontradoError(PagoElectronicoError):
    """No existe un pago electronico asociado a la venta/PaymentIntent."""


class PagoElectronicoYaAprobadoError(PagoElectronicoError):
    """La venta ya tiene un pago APROBADO."""


# ---------------------------------------------------------------------------
# Validaciones del webhook
# ---------------------------------------------------------------------------


class MontoStripeInvalidoError(PagoElectronicoError):
    """El monto informado por Stripe no coincide con el total de la venta."""


class MonedaStripeInvalidaError(PagoElectronicoError):
    """La moneda informada por Stripe no coincide con la configurada."""


class ConfirmacionVentaElectronicaError(PagoElectronicoError):
    """``sp_confirmar_venta`` no pudo confirmar la venta desde el webhook."""


class ConfirmacionVentaTecnicaError(PagoElectronicoError):
    """Fallo tecnico/transitorio al confirmar o persistir (NO se compensa).

    Conexion, timeout o error inesperado del driver: se hace rollback sin
    cancelar la venta ni emitir refund, se responde 5xx y Stripe reintenta.
    """
