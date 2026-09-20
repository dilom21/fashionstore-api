"""Reglas de negocio de CU22 - Procesar pago electronico con Stripe (Test Mode).

Flujo:

    venta WEB/MOVIL PENDIENTE
    -> crear/reutilizar PaymentIntent (Stripe)
    -> persistir pago PENDIENTE / TARJETA / STRIPE (referencia = pi_id)
    -> el frontend confirma con Stripe.js
    -> webhook firmado payment_intent.succeeded
    -> pago APROBADO
    -> sp_confirmar_venta (autoridad transaccional de PostgreSQL)
    -> venta COMPLETADA + inventario actualizado

La autoridad de finalizacion es SIEMPRE el webhook firmado: este service nunca
confirma la venta al crear la intencion. Stripe y PostgreSQL no comparten
transaccion, por lo que ante un fallo de persistencia se intenta cancelar el
PaymentIntent (compensacion) sin fingir atomicidad distribuida.
"""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.pagos.models.models import Pago
from app.modules.pagos.errors import (
    ConfirmacionVentaElectronicaError,
    ConfirmacionVentaTecnicaError,
    MonedaStripeInvalidaError,
    MontoStripeInvalidoError,
    PagoElectronicoError,
    PagoElectronicoNoEncontradoError,
    PagoElectronicoYaAprobadoError,
    StripeIntentError,
    StripeRefundError,
    StripeRefundPendienteError,
    StripeWebhookFirmaError,
    VentaDigitalAjenaError,
    VentaDigitalEstadoInvalidoError,
    VentaDigitalNoEncontradaError,
)
from app.modules.pagos.providers import ProveedorPagoBase, StripeProveedor
from app.modules.pagos.providers.stripe import (
    ESTADO_SUCCEEDED,
    ESTADOS_REFUND_EXITOSO,
    ESTADOS_REFUND_PENDIENTE,
    ESTADOS_REUTILIZABLES,
)
from app.modules.pagos.repositories.repository import PagoRepository
from app.modules.pagos.repositories.stripe_repository import (
    ESTADO_ANULADO,
    ESTADO_APROBADO,
    ESTADO_PENDIENTE,
    ESTADO_RECHAZADO,
    ESTADO_REEMBOLSADO,
    PagoStripeRepository,
)
from app.modules.pagos.schemas.electronico import (
    CrearIntencionPagoRequest,
    EstadoPagoVentaResponse,
    IntencionPagoResponse,
    WebhookStripeResponse,
)

logger = logging.getLogger(__name__)

CANALES_DIGITALES = frozenset({"WEB", "MOVIL"})
ESTADO_VENTA_PENDIENTE = "PENDIENTE"
ESTADO_VENTA_CANCELADA = "CANCELADA"
ESTADOS_VENTA_CONFIRMADA = frozenset({"COMPLETADA", "PAGADA"})

EVENTO_SUCCEEDED = "payment_intent.succeeded"
EVENTO_FAILED = "payment_intent.payment_failed"
EVENTO_PROCESSING = "payment_intent.processing"
EVENTO_CANCELED = "payment_intent.canceled"
EVENTOS_MANEJADOS = frozenset(
    {EVENTO_SUCCEEDED, EVENTO_FAILED, EVENTO_PROCESSING, EVENTO_CANCELED}
)

SQLSTATE_UNIQUE_VIOLATION = "23505"
SQLSTATE_RAISE_EXCEPTION = "P0001"

# Unico SQLSTATE que se considera rechazo de dominio compensable del SP:
# ``P0001`` = RAISE EXCEPTION explicito de PL/pgSQL, que es como
# ``sp_confirmar_venta`` reporta sus validaciones de negocio (stock, reserva,
# estado). NO se compensan automaticamente 22xxx/23xxx: una violacion de
# datos/integridad inesperada es un fallo tecnico y no debe provocar refund.
SQLSTATES_DOMINIO_SP = frozenset({SQLSTATE_RAISE_EXCEPTION})


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def obtener_proveedor() -> ProveedorPagoBase:
    """Fabrica del provider real. Los tests la sustituyen por un fake."""
    return StripeProveedor()


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _campo(objeto, nombre):
    """Lee un campo de un StripeObject (dict) o de un mapping de test."""
    if objeto is None:
        return None
    if isinstance(objeto, dict):
        return objeto.get(nombre)
    return getattr(objeto, nombre, None)


def _moneda_configurada() -> str:
    return (settings.stripe_currency or "bob").strip().lower()


def _a_unidades_minimas(total: Decimal) -> int:
    """Decimal -> entero en la unidad minima (BOB: 2 decimales). Sin float."""
    return int(
        (total * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _usuario_auditoria(db: Session, venta) -> int | None:
    """Usuario para ``sp_confirmar_venta`` desde un webhook sin sesion.

    El procedimiento usa ``p_usuario_id`` para ``movimiento_inventario.usuario_id``
    (FK -> usuario.id, nullable). Se usa el usuario del cliente comprador cuando
    existe (semanticamente valido y verificable por la FK); si no, se envia NULL,
    que el procedimiento admite. No se inventa ningun usuario de servicio.
    """
    if venta.cliente_id is None:
        return None
    cliente = db.get(Cliente, int(venta.cliente_id))
    if cliente is None or cliente.usuario_id is None:
        return None
    return int(cliente.usuario_id)


def _sqlstate(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    valor = getattr(origen, "sqlstate", None)
    return str(valor) if valor else None


def _constraint_violada(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    diag = getattr(origen, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    return str(nombre) if nombre else None


def _mensaje_bd(exc: DBAPIError) -> str:
    origen = getattr(exc, "orig", None)
    return str(origen or exc).lower()


def _es_referencia_duplicada(exc: DBAPIError) -> bool:
    if _sqlstate(exc) != SQLSTATE_UNIQUE_VIOLATION:
        return False
    constraint = _constraint_violada(exc) or ""
    return "referencia" in constraint or "referencia_transaccion" in _mensaje_bd(exc)


def _traducir_error_bd(exc: DBAPIError) -> PagoElectronicoError:
    estado = _sqlstate(exc)
    mensaje = _mensaje_bd(exc)
    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "no existe" in mensaje:
            return VentaDigitalNoEncontradaError()
        if "no puede confirmarse" in mensaje or "estado" in mensaje:
            return VentaDigitalEstadoInvalidoError()
        if "no posee un pago aprobado" in mensaje:
            return ConfirmacionVentaElectronicaError()
        if "stock" in mensaje or "disponible" in mensaje:
            return ConfirmacionVentaElectronicaError()
        return ConfirmacionVentaElectronicaError()
    return ConfirmacionVentaElectronicaError()


def _es_error_dominio_sp(exc: DBAPIError) -> bool:
    """True si el fallo del SP es de dominio (determinista, no reintentable).

    La clasificacion usa el SQLSTATE del driver (``orig.sqlstate``), no el texto
    completo del mensaje. Los ``RAISE EXCEPTION`` del procedimiento llegan como
    ``P0001``; las violaciones de datos/integridad tambien son rechazos
    deterministas y por tanto se compensan en vez de reintentarse.
    """
    return _sqlstate(exc) in SQLSTATES_DOMINIO_SP


def _idempotency_key_refund(venta_id: int, payment_intent_id: str) -> str:
    """Clave determinista del refund: un solo refund logico por venta/PI."""
    return f"refund-venta-{venta_id}-pi-{payment_intent_id}"


def _idempotency_key(db: Session, venta_id: int) -> str:
    """Clave que protege retries de red sin bloquear un intento nuevo legitimo.

    Incluye el numero de intentos Stripe ya registrados: dos requests
    concurrentes del primer intento comparten clave (Stripe deduplica), pero
    tras cancelar un PaymentIntent el siguiente intento obtiene una clave
    distinta y puede crear uno nuevo.
    """
    intentos = PagoStripeRepository.contar_pagos_stripe(db, venta_id)
    return f"stripe-pi-{venta_id}-{intentos + 1}"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PagoElectronicoService:
    @staticmethod
    def crear_intencion(
        db: Session,
        cliente: Cliente,
        datos: CrearIntencionPagoRequest,
    ) -> IntencionPagoResponse:
        venta_id = int(datos.venta_id)
        try:
            # La venta se bloquea durante toda la operacion: dos clicks
            # concurrentes no pueden crear dos intentos a la vez.
            venta = PagoRepository.bloquear_venta(db, venta_id)
            if venta is None:
                raise VentaDigitalNoEncontradaError()
            if venta.cliente_id is None or int(venta.cliente_id) != int(cliente.id):
                raise VentaDigitalAjenaError()
            if venta.canal not in CANALES_DIGITALES:
                raise VentaDigitalEstadoInvalidoError()
            if venta.estado != ESTADO_VENTA_PENDIENTE:
                raise VentaDigitalEstadoInvalidoError()
            if PagoRepository.obtener_pago_aprobado(db, venta.id) is not None:
                raise PagoElectronicoYaAprobadoError()
            if PagoRepository.contar_detalles(db, venta.id) == 0:
                raise VentaDigitalEstadoInvalidoError()

            monto = Decimal(venta.total)
            if monto <= 0:
                raise VentaDigitalEstadoInvalidoError()

            reutilizada = PagoElectronicoService._intencion_reutilizable(
                db, venta
            )
            if reutilizada is not None:
                return reutilizada

            return PagoElectronicoService._crear_intencion_nueva(
                db, venta, monto
            )
        except PagoElectronicoError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

    @staticmethod
    def _intencion_reutilizable(
        db: Session, venta
    ) -> IntencionPagoResponse | None:
        """Devuelve el intento existente si su PaymentIntent sigue utilizable."""
        existente = PagoStripeRepository.obtener_ultimo_pago_stripe(
            db, int(venta.id)
        )
        if existente is None or not existente.referencia_transaccion:
            return None

        try:
            intencion = obtener_proveedor().recuperar_intencion(
                existente.referencia_transaccion
            )
        except StripeIntentError:
            # El PaymentIntent ya no es recuperable: se creara uno nuevo.
            logger.warning(
                "No se pudo recuperar el PaymentIntent %s de la venta %s",
                existente.referencia_transaccion,
                venta.id,
            )
            return None

        if intencion.estado in ESTADOS_REUTILIZABLES:
            if existente.estado != ESTADO_PENDIENTE:
                # Un intento anterior RECHAZADO vuelve a estar en curso.
                existente.estado = ESTADO_PENDIENTE
            respuesta = PagoElectronicoService._respuesta(
                venta, existente, intencion
            )
            db.commit()
            return respuesta

        if intencion.estado == ESTADO_SUCCEEDED:
            # El cobro ya se realizo en Stripe; el webhook es la autoridad de
            # confirmacion. No se crea otro PaymentIntent (evita doble cobro).
            respuesta = PagoElectronicoService._respuesta(
                venta, existente, intencion
            )
            db.rollback()
            return respuesta

        # Estado cancelado u otro no reutilizable: se permite un intento nuevo.
        db.rollback()
        return None

    @staticmethod
    def _crear_intencion_nueva(
        db: Session, venta, monto: Decimal
    ) -> IntencionPagoResponse:
        moneda = _moneda_configurada()
        metadata = {
            "venta_id": str(venta.id),
            "cliente_id": str(venta.cliente_id),
            "canal": venta.canal,
        }
        intencion = obtener_proveedor().crear_intencion(
            monto=_a_unidades_minimas(monto),
            moneda=moneda,
            metadata=metadata,
            idempotency_key=_idempotency_key(db, int(venta.id)),
        )

        try:
            pago = PagoStripeRepository.crear_pago_stripe(
                db,
                venta_id=int(venta.id),
                monto=monto,
                fecha_hora=_ahora(),
                referencia_transaccion=intencion.id,
            )
            pago_id = int(pago.id)
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            if _es_referencia_duplicada(exc):
                # Otro request ya asocio este PaymentIntent: se reutiliza.
                pago = PagoStripeRepository.obtener_ultimo_pago_stripe(
                    db, int(venta.id)
                )
                if (
                    pago is not None
                    and pago.referencia_transaccion == intencion.id
                ):
                    return PagoElectronicoService._respuesta(
                        venta, pago, intencion
                    )
            PagoElectronicoService._compensar_intencion(intencion.id)
            raise _traducir_error_bd(exc) from exc

        pago = db.get(Pago, pago_id)
        if pago is None:
            raise ConfirmacionVentaElectronicaError()
        return PagoElectronicoService._respuesta(venta, pago, intencion)

    @staticmethod
    def _compensar_intencion(intencion_id: str) -> None:
        """Intenta cancelar el PaymentIntent tras un fallo de persistencia.

        Best-effort: si la cancelacion falla, solo se registra (sin secretos).
        """
        try:
            obtener_proveedor().cancelar_intencion(intencion_id)
        except PagoElectronicoError:
            logger.error(
                "No se pudo compensar (cancelar) el PaymentIntent %s",
                intencion_id,
            )

    @staticmethod
    def _respuesta(venta, pago, intencion) -> IntencionPagoResponse:
        return IntencionPagoResponse(
            venta_id=int(venta.id),
            pago_id=int(pago.id),
            payment_intent_id=intencion.id,
            client_secret=intencion.client_secret,
            monto=Decimal(pago.monto),
            moneda=intencion.moneda or _moneda_configurada(),
            estado_pago=pago.estado,
        )

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    @staticmethod
    def procesar_webhook(
        db: Session, payload: bytes, firma: str
    ) -> WebhookStripeResponse:
        if not firma:
            raise StripeWebhookFirmaError("Falta la cabecera Stripe-Signature")

        evento = obtener_proveedor().verificar_webhook(payload, firma)
        tipo = _campo(evento, "type")
        event_id = _campo(evento, "id")
        logger.info(
            "Webhook Stripe recibido event_id=%s type=%s", event_id, tipo
        )

        if tipo not in EVENTOS_MANEJADOS:
            # Evento valido no manejado: 2xx sin efectos.
            return WebhookStripeResponse(
                recibido=True, evento=tipo, procesado=False
            )

        objeto = _campo(_campo(evento, "data"), "object")
        if tipo == EVENTO_SUCCEEDED:
            return PagoElectronicoService._procesar_succeeded(db, objeto)
        if tipo == EVENTO_FAILED:
            return PagoElectronicoService._procesar_failed(db, objeto)
        if tipo == EVENTO_PROCESSING:
            return PagoElectronicoService._procesar_processing(db, objeto)
        return PagoElectronicoService._procesar_canceled(db, objeto)

    @staticmethod
    def _procesar_succeeded(db: Session, objeto) -> WebhookStripeResponse:
        pi_id = _campo(objeto, "id")
        if not pi_id:
            raise PagoElectronicoNoEncontradoError()
        pi_id = str(pi_id)

        pago = PagoStripeRepository.bloquear_pago_por_referencia(db, pi_id)
        if pago is None:
            logger.warning(
                "Webhook succeeded sin pago asociado pi=%s", pi_id
            )
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_SUCCEEDED, procesado=False
            )

        venta = PagoRepository.obtener_venta(db, int(pago.venta_id))
        if venta is None:
            db.rollback()
            raise VentaDigitalNoEncontradaError()
        venta_id = int(venta.id)

        # --- Idempotencia: releer el estado antes de cualquier side effect ---

        if (
            pago.estado == ESTADO_REEMBOLSADO
            and venta.estado == ESTADO_VENTA_CANCELADA
        ):
            # Compensacion ya completa: no hay refund adicional ni SP.
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_SUCCEEDED, procesado=True
            )

        if (
            pago.estado == ESTADO_APROBADO
            and venta.estado in ESTADOS_VENTA_CONFIRMADA
        ):
            # Venta ya completada: no repetir SP ni refund.
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_SUCCEEDED, procesado=True
            )

        if (
            pago.estado == ESTADO_APROBADO
            and venta.estado == ESTADO_VENTA_CANCELADA
        ):
            # Compensacion pendiente: reintentar SOLO el refund, nunca el SP.
            db.rollback()
            return PagoElectronicoService._reintentar_refund(db, pi_id)

        metadata = _campo(objeto, "metadata") or {}
        meta_venta = _campo(metadata, "venta_id")
        if meta_venta is not None and str(meta_venta) != str(venta_id):
            db.rollback()
            raise VentaDigitalEstadoInvalidoError()

        try:
            PagoElectronicoService._validar_monto_moneda(objeto, venta)
        except PagoElectronicoError:
            db.rollback()
            raise

        usuario_id = _usuario_auditoria(db, venta)
        if usuario_id is not None:
            _establecer_contexto_bitacora(db, usuario_id)

        try:
            pago.estado = ESTADO_APROBADO
            # La sesion tiene autoflush desactivado: el pago APROBADO debe estar
            # persistido ANTES de que sp_confirmar_venta lo valide.
            db.flush()
            PagoRepository.confirmar_venta(db, venta_id, usuario_id)
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            if _es_error_dominio_sp(exc):
                # El SP rechazo la operacion (p. ej. stock): Stripe ya cobro,
                # por lo que se compensa en una nueva transaccion.
                logger.error(
                    "sp_confirmar_venta rechazo la venta %s por dominio "
                    "(sqlstate=%s); inicia compensacion",
                    venta_id,
                    _sqlstate(exc),
                )
                return PagoElectronicoService._compensar_venta(
                    db, pi_id, venta_id
                )
            # Fallo tecnico/transitorio: NO cancelar ni reembolsar; Stripe
            # reintentara el webhook.
            logger.error(
                "Fallo tecnico al confirmar la venta %s desde el webhook "
                "(sqlstate=%s); sin compensacion",
                venta_id,
                _sqlstate(exc),
            )
            raise ConfirmacionVentaTecnicaError(
                "Fallo tecnico al confirmar la venta"
            ) from exc

        logger.info(
            "Pago %s APROBADO y venta %s confirmada (pi=%s)",
            pago.id,
            venta_id,
            pi_id,
        )
        return WebhookStripeResponse(
            recibido=True, evento=EVENTO_SUCCEEDED, procesado=True
        )

    @staticmethod
    def _compensar_venta(
        db: Session, pi_id: str, venta_id: int
    ) -> WebhookStripeResponse:
        """Persiste ``pago=APROBADO`` + ``venta=CANCELADA`` y reembolsa.

        La venta CANCELADA es la marca persistente que impide que un retry
        posterior vuelva a ejecutar el SP si el stock reaparece.
        """
        try:
            pago = PagoStripeRepository.bloquear_pago_por_referencia(db, pi_id)
            venta = PagoRepository.bloquear_venta(db, venta_id)
            if pago is None or venta is None:
                db.rollback()
                raise VentaDigitalNoEncontradaError()

            pago.estado = ESTADO_APROBADO
            venta.estado = ESTADO_VENTA_CANCELADA
            pago_id = int(pago.id)
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            logger.error(
                "No se pudo persistir la compensacion de la venta %s "
                "(pi=%s); sin refund",
                venta_id,
                pi_id,
            )
            raise ConfirmacionVentaTecnicaError(
                "Fallo tecnico al persistir la compensacion"
            ) from exc

        logger.warning(
            "Venta %s CANCELADA por rechazo de dominio del SP; pago %s "
            "APROBADO y compensacion pendiente (pi=%s)",
            venta_id,
            pago_id,
            pi_id,
        )
        return PagoElectronicoService._reintentar_refund(db, pi_id)

    @staticmethod
    def _reintentar_refund(db: Session, pi_id: str) -> WebhookStripeResponse:
        """Ejecuta/reintenta el refund de una venta ya cancelada.

        Nunca ejecuta el SP. Usa una idempotency key determinista para que un
        retry (incluido "refund externo OK + fallo de persistencia") reutilice
        el mismo refund logico en Stripe y converja a REEMBOLSADO.
        """
        pago = PagoStripeRepository.bloquear_pago_por_referencia(db, pi_id)
        if pago is None:
            db.rollback()
            raise VentaDigitalNoEncontradaError()

        if pago.estado == ESTADO_REEMBOLSADO:
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_SUCCEEDED, procesado=True
            )
        if pago.estado != ESTADO_APROBADO:
            db.rollback()
            raise VentaDigitalEstadoInvalidoError()

        venta_id = int(pago.venta_id)
        # Se libera el lock antes de la llamada externa (Stripe no comparte
        # transaccion con PostgreSQL).
        db.rollback()

        reembolso = obtener_proveedor().reembolsar_intencion(
            pi_id,
            idempotency_key=_idempotency_key_refund(venta_id, pi_id),
        )

        # Un objeto Refund NO implica exito: se inspecciona el estado real.
        # Solo ``succeeded`` confirma la compensacion; ``pending`` /
        # ``requires_action`` quedan como compensacion pendiente y se
        # reconcilian con la misma idempotency key.
        if reembolso.estado in ESTADOS_REFUND_PENDIENTE:
            logger.warning(
                "Refund %s en estado %s (pi=%s); compensacion pendiente, "
                "se reintentara con la misma idempotency key",
                reembolso.id,
                reembolso.estado,
                pi_id,
            )
            raise StripeRefundPendienteError(
                "El reembolso aun no esta confirmado por Stripe"
            )
        if reembolso.estado not in ESTADOS_REFUND_EXITOSO:
            logger.error(
                "Refund %s devolvio estado no exitoso %s (pi=%s); "
                "compensacion pendiente",
                reembolso.id,
                reembolso.estado,
                pi_id,
            )
            raise StripeRefundError(
                "Stripe no confirmo el reembolso"
            )

        try:
            pago = PagoStripeRepository.bloquear_pago_por_referencia(db, pi_id)
            if pago is None:
                db.rollback()
                raise VentaDigitalNoEncontradaError()
            if pago.estado != ESTADO_REEMBOLSADO:
                pago.estado = ESTADO_REEMBOLSADO
                db.commit()
            else:
                db.rollback()
        except DBAPIError as exc:
            db.rollback()
            logger.error(
                "Refund %s confirmado pero fallo la persistencia local (pi=%s); "
                "un retry reutilizara la misma idempotency key",
                reembolso.id,
                pi_id,
            )
            raise ConfirmacionVentaTecnicaError(
                "Fallo tecnico al persistir el reembolso"
            ) from exc

        logger.info(
            "Pago %s REEMBOLSADO (refund=%s, pi=%s)",
            pago.id,
            reembolso.id,
            pi_id,
        )
        return WebhookStripeResponse(
            recibido=True, evento=EVENTO_SUCCEEDED, procesado=True
        )

    @staticmethod
    def _validar_monto_moneda(objeto, venta) -> None:
        esperado = _a_unidades_minimas(Decimal(venta.total))
        recibido = _campo(objeto, "amount_received")
        if not recibido:
            recibido = _campo(objeto, "amount")
        try:
            recibido_int = int(recibido)
        except (TypeError, ValueError):
            raise MontoStripeInvalidoError()
        if recibido_int != esperado:
            logger.error(
                "Monto Stripe no coincide: venta=%s esperado=%s recibido=%s",
                venta.id,
                esperado,
                recibido_int,
            )
            raise MontoStripeInvalidoError()

        moneda = str(_campo(objeto, "currency") or "").lower()
        esperada = _moneda_configurada()
        if moneda != esperada:
            logger.error(
                "Moneda Stripe no coincide: venta=%s esperada=%s recibida=%s",
                venta.id,
                esperada,
                moneda,
            )
            raise MonedaStripeInvalidaError()

    @staticmethod
    def _procesar_failed(db: Session, objeto) -> WebhookStripeResponse:
        pi_id = _campo(objeto, "id")
        pago = PagoStripeRepository.bloquear_pago_por_referencia(
            db, str(pi_id)
        )
        if pago is None:
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_FAILED, procesado=False
            )

        # Nunca se degrada un pago APROBADO.
        if pago.estado in (ESTADO_APROBADO, ESTADO_RECHAZADO, ESTADO_ANULADO):
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_FAILED, procesado=True
            )

        pago.estado = ESTADO_RECHAZADO
        db.commit()
        logger.info(
            "Pago %s RECHAZADO (pi=%s); la venta sigue PENDIENTE",
            pago.id,
            pi_id,
        )
        return WebhookStripeResponse(
            recibido=True, evento=EVENTO_FAILED, procesado=True
        )

    @staticmethod
    def _procesar_processing(db: Session, objeto) -> WebhookStripeResponse:
        pi_id = _campo(objeto, "id")
        pago = PagoStripeRepository.bloquear_pago_por_referencia(
            db, str(pi_id)
        )
        # Pago y venta siguen PENDIENTE: no se confirma inventario.
        db.rollback()
        return WebhookStripeResponse(
            recibido=True,
            evento=EVENTO_PROCESSING,
            procesado=pago is not None,
        )

    @staticmethod
    def _procesar_canceled(db: Session, objeto) -> WebhookStripeResponse:
        pi_id = _campo(objeto, "id")
        pago = PagoStripeRepository.bloquear_pago_por_referencia(
            db, str(pi_id)
        )
        if pago is None:
            db.rollback()
            return WebhookStripeResponse(
                recibido=True, evento=EVENTO_CANCELED, procesado=False
            )

        # El CHECK real admite ANULADO. Un pago PENDIENTE cuyo PaymentIntent fue
        # cancelado queda ANULADO y habilita crear un nuevo PaymentIntent.
        if pago.estado == ESTADO_PENDIENTE:
            pago.estado = ESTADO_ANULADO
            db.commit()
        else:
            db.rollback()
        return WebhookStripeResponse(
            recibido=True, evento=EVENTO_CANCELED, procesado=True
        )

    # ------------------------------------------------------------------
    # Consulta de estado
    # ------------------------------------------------------------------

    @staticmethod
    def consultar_estado(
        db: Session, cliente: Cliente, venta_id: int
    ) -> EstadoPagoVentaResponse:
        venta = PagoRepository.obtener_venta(db, int(venta_id))
        if venta is None:
            raise VentaDigitalNoEncontradaError()
        if venta.cliente_id is None or int(venta.cliente_id) != int(cliente.id):
            raise VentaDigitalAjenaError()

        pago = PagoStripeRepository.obtener_ultimo_pago_stripe(db, int(venta.id))

        # Estado explicito de compensacion: una venta CANCELADA con pago
        # APROBADO/REEMBOLSADO no es una compra exitosa. Se expone sin romper
        # el contrato crudo (estado_venta/estado_pago siguen iguales).
        compensacion_estado = None
        if (
            venta.estado == ESTADO_VENTA_CANCELADA
            and pago is not None
            and pago.estado in (ESTADO_APROBADO, ESTADO_REEMBOLSADO)
        ):
            compensacion_estado = (
                "PENDIENTE" if pago.estado == ESTADO_APROBADO else "REEMBOLSADO"
            )

        return EstadoPagoVentaResponse(
            venta_id=int(venta.id),
            estado_venta=venta.estado,
            pago_id=int(pago.id) if pago is not None else None,
            estado_pago=pago.estado if pago is not None else None,
            payment_intent_id=(
                pago.referencia_transaccion if pago is not None else None
            ),
            compensacion_estado=compensacion_estado,
        )
