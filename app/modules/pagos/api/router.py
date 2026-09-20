"""Endpoints del dominio Pagos.

- CU21 (PERSONAL): POST /pagos/presencial registra el pago APROBADO de una
  venta PRESENCIAL PENDIENTE y confirma la venta con sp_confirmar_venta de
  forma atomica. No usa Stripe.
- CU22 (CLIENTE): POST /pagos/stripe/intencion crea/reutiliza el PaymentIntent
  de una venta WEB/MOVIL PENDIENTE; POST /pagos/stripe/webhook recibe los
  eventos firmados de Stripe (sin JWT) y es la unica autoridad de confirmacion;
  GET /pagos/stripe/ventas/{venta_id}/estado consulta el resultado final.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_cliente, require_permission
from app.modules.autenticacion_seguridad.models.models import Cliente, Usuario
from app.modules.pagos.errors import (
    ConfirmacionVentaElectronicaError,
    ConfirmacionVentaTecnicaError,
    MonedaStripeInvalidaError,
    MontoStripeInvalidoError,
    PagoElectronicoError,
    PagoElectronicoNoEncontradoError,
    PagoElectronicoYaAprobadoError,
    StripeConfiguracionError,
    StripeIntentError,
    StripeRefundError,
    StripeRefundPendienteError,
    StripeWebhookFirmaError,
    VentaDigitalAjenaError,
    VentaDigitalEstadoInvalidoError,
    VentaDigitalNoEncontradaError,
)
from app.modules.pagos.schemas.electronico import (
    CrearIntencionPagoRequest,
    EstadoPagoVentaResponse,
    IntencionPagoResponse,
    WebhookStripeResponse,
)
from app.modules.pagos.schemas.schemas import (
    PagoPresencialResponse,
    RegistrarPagoPresencialRequest,
)
from app.modules.pagos.services.electronico_service import PagoElectronicoService
from app.modules.pagos.services.service import (
    ConfirmacionVentaError,
    MetodoPagoInvalidoError,
    PagoError,
    PagoPresencialService,
    PagoRegistroInvalidoError,
    PagoYaAprobadoError,
    StockConfirmacionInsuficienteError,
    VentaEstadoInvalidoError,
    VentaFueraDeSucursalError,
    VentaNoEncontradaError,
    VentaNoPresencialError,
)

# RBAC real del proyecto (modulo VENTAS): ADMINISTRADOR, ENCARGADO_SUCURSAL y
# CAJERO tienen GESTIONAR_PAGOS/CREAR; CLIENTE no. El service refuerza la
# allowlist de roles y el alcance por sucursal.
FUNCION_PAGOS = "GESTIONAR_PAGOS"

router = APIRouter(prefix="/pagos", tags=["Pagos"])


def _error(detalle: str, codigo: int) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _map_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU21 a HTTP (mensajes de negocio)."""
    if isinstance(exc, VentaNoEncontradaError):
        return _error("Venta no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, VentaFueraDeSucursalError):
        return _error(
            "No autorizado: la operacion esta fuera de su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, MetodoPagoInvalidoError):
        return _error(
            "Metodo de pago no permitido",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, VentaNoPresencialError):
        return _error(
            "La venta no es presencial: no corresponde a este endpoint",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, VentaEstadoInvalidoError):
        return _error(
            "La venta no esta pendiente de pago",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, PagoYaAprobadoError):
        return _error(
            "La venta ya tiene un pago aprobado",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, StockConfirmacionInsuficienteError):
        return _error(
            "No hay stock suficiente para confirmar la venta",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, ConfirmacionVentaError):
        return _error(
            "No fue posible confirmar la venta",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, PagoRegistroInvalidoError):
        return _error(
            "No fue posible registrar el pago",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, PagoError):
        return _error(
            "No fue posible registrar el pago presencial",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post(
    "/presencial",
    response_model=PagoPresencialResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_pago_presencial(
    datos: RegistrarPagoPresencialRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_permission(FUNCION_PAGOS, "CREAR")),
):
    """CU21 - Registrar pago presencial y confirmar la venta.

    El cuerpo solo indica venta_id y metodo. El monto (venta.total), el estado
    (APROBADO), la sucursal y el empleado se determinan en el backend. El pago
    aprobado y la confirmacion via sp_confirmar_venta son una sola transaccion.
    """
    try:
        return PagoPresencialService.registrar_presencial(db, usuario, datos)
    except PagoError as exc:
        raise _map_error(exc)


# ---------------------------------------------------------------------------
# CU22 - Pago electronico con Stripe (Test Mode)
# ---------------------------------------------------------------------------


def _map_electronico_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio de CU22 a HTTP (sin exponer Stripe crudo)."""
    if isinstance(exc, VentaDigitalNoEncontradaError):
        return _error("Venta no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(exc, PagoElectronicoNoEncontradoError):
        return _error(
            "No existe un pago electronico para la venta",
            status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, VentaDigitalAjenaError):
        return _error(
            "No autorizado: la venta pertenece a otro cliente",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, StripeWebhookFirmaError):
        return _error(
            "Firma de webhook invalida",
            status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(
        exc,
        (
            VentaDigitalEstadoInvalidoError,
            PagoElectronicoYaAprobadoError,
            MontoStripeInvalidoError,
            MonedaStripeInvalidaError,
            ConfirmacionVentaElectronicaError,
        ),
    ):
        return _error(
            "No fue posible procesar el pago electronico",
            status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, StripeConfiguracionError):
        return _error(
            "Stripe no esta configurado en el servidor",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if isinstance(exc, StripeRefundPendienteError):
        # Refund aun no confirmado (pending/requires_action): la venta queda
        # CANCELADA/APROBADO y Stripe reintenta el webhook (5xx).
        return _error(
            "El reembolso aun no esta confirmado",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if isinstance(exc, StripeRefundError):
        # Fallo temporal del refund: la venta queda CANCELADA/APROBADO y
        # Stripe reintenta el webhook (5xx).
        return _error(
            "No fue posible procesar el reembolso",
            status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, ConfirmacionVentaTecnicaError):
        # Fallo tecnico/transitorio: no se compensa; Stripe reintenta (5xx).
        return _error(
            "No fue posible confirmar la venta, reintente",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if isinstance(exc, StripeIntentError):
        return _error(
            "La pasarela de pago no esta disponible",
            status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, PagoElectronicoError):
        return _error(
            "No fue posible procesar el pago electronico",
            status.HTTP_409_CONFLICT,
        )
    raise exc


@router.post(
    "/stripe/intencion",
    response_model=IntencionPagoResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_intencion_pago_stripe(
    datos: CrearIntencionPagoRequest,
    db: Session = Depends(get_db),
    cliente: Cliente = Depends(get_current_cliente),
):
    """CU22 - Crear/reutilizar el PaymentIntent de una venta WEB/MOVIL.

    El cliente solo envia ``venta_id``; el monto, la moneda y la metadata se
    derivan de la venta y de la configuracion. No confirma la venta.
    """
    try:
        return PagoElectronicoService.crear_intencion(db, cliente, datos)
    except PagoElectronicoError as exc:
        raise _map_electronico_error(exc)


@router.post("/stripe/webhook", response_model=WebhookStripeResponse)
async def webhook_stripe(
    request: Request,
    db: Session = Depends(get_db),
):
    """CU22 - Webhook firmado de Stripe (sin JWT).

    Verifica ``Stripe-Signature`` con el payload RAW. Es la unica autoridad que
    confirma la venta (``payment_intent.succeeded``). Firma invalida: 400.
    """
    payload = await request.body()
    firma = request.headers.get("stripe-signature", "")
    try:
        return PagoElectronicoService.procesar_webhook(db, payload, firma)
    except PagoElectronicoError as exc:
        raise _map_electronico_error(exc)


@router.get(
    "/stripe/ventas/{venta_id}/estado",
    response_model=EstadoPagoVentaResponse,
)
def consultar_estado_pago_stripe(
    venta_id: int,
    db: Session = Depends(get_db),
    cliente: Cliente = Depends(get_current_cliente),
):
    """CU22 - Estado backend de la venta y de su pago electronico.

    Permite al frontend/movil esperar la confirmacion del webhook sin confiar
    en el callback del cliente. Solo el cliente dueno puede consultarla.
    """
    try:
        return PagoElectronicoService.consultar_estado(db, cliente, venta_id)
    except PagoElectronicoError as exc:
        raise _map_electronico_error(exc)
