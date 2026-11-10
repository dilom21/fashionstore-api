"""CU14 Stripe SaaS: JWT para iniciar/consultar; pool dedicado para webhook."""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.pagos.models.models import Pago
from app.modules.pagos.providers.stripe import (
    ESTADO_SUCCEEDED, ESTADOS_REUTILIZABLES, StripeProveedor,
)
from app.modules.pagos.schemas.electronico import (
    CrearIntencionPagoRequest, EstadoPagoVentaResponse, IntencionPagoResponse,
    WebhookStripeResponse,
)
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.services.tenant_transaccional import verificar_principal_ventas


logger = logging.getLogger(__name__)

# Eventos PaymentIntent que el procedimiento SQL conoce y puede procesar
# directamente. El evento interno ``payment_intent.refunded`` no lo emite
# Stripe: lo sintetiza este adaptador tras un reembolso total exitoso.
EVENTOS_PAYMENT_INTENT = frozenset({
    "payment_intent.succeeded",
    "payment_intent.processing",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
})
EVENTOS_REFUND_STRIPE = frozenset({
    "charge.refunded",
    "refund.created",
    "refund.updated",
})
EVENTO_INTERNO_REFUND = "payment_intent.refunded"


class StripeTenantError(Exception):
    pass


class StripeTenantConflicto(StripeTenantError):
    pass


def _centavos(monto: Decimal) -> int:
    return int((monto * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _refund_total_exitoso(tipo: str, objeto: dict) -> tuple[str, int, str] | None:
    """Normaliza un evento real de refund solo si es total y exitoso.

    ``charge.refunded`` se emite también para reembolsos parciales: sólo es
    total cuando ``refunded`` es verdadero y ``amount_refunded`` cubre el
    importe completo del cargo. ``refund.created``/``refund.updated`` sólo
    cuentan cuando el Refund está en ``succeeded``; el importe se envía al
    procedimiento, que además exige que coincida con el total de la venta.
    Un refund parcial, pendiente o fallido devuelve ``None`` y no toca SQL.
    """
    if tipo == "charge.refunded":
        pi = str(objeto.get("payment_intent") or "")
        total = int(objeto.get("amount") or 0)
        reembolsado = int(objeto.get("amount_refunded") or 0)
        moneda = str(objeto.get("currency") or settings.stripe_currency).lower()
        if (not pi or total <= 0 or reembolsado < total
                or objeto.get("refunded") is not True):
            return None
        return pi, reembolsado, moneda
    if tipo in {"refund.created", "refund.updated"}:
        pi = str(objeto.get("payment_intent") or "")
        monto = int(objeto.get("amount") or 0)
        moneda = str(objeto.get("currency") or settings.stripe_currency).lower()
        if (not pi or monto <= 0
                or str(objeto.get("status") or "").lower() != "succeeded"):
            return None
        return pi, monto, moneda
    return None


def _cliente(db: Session, tenant: TenantContext) -> Cliente:
    cliente = db.scalars(select(Cliente).where(
        Cliente.empresa_id == tenant.empresa_id,
        Cliente.usuario_id == tenant.usuario_id,
        Cliente.estado.is_(True),
    )).first()
    if cliente is None:
        raise StripeTenantConflicto()
    return cliente


def _ultimo_pago(db: Session, empresa_id: int, venta_id: int) -> Pago | None:
    return db.scalars(select(Pago).where(
        Pago.empresa_id == empresa_id, Pago.venta_id == venta_id,
        Pago.pasarela == "STRIPE",
    ).order_by(Pago.id.desc()).limit(1)).first()


class PagoStripeTenantService:
    @staticmethod
    def crear_intencion(db: Session, tenant: TenantContext, datos: CrearIntencionPagoRequest):
        cliente = _cliente(db, tenant)
        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=int(datos.venta_id)
        )
        if (venta is None or venta.cliente_id != int(cliente.id)
                or venta.canal not in {"WEB", "MOVIL"} or venta.estado != "PENDIENTE"
                or Decimal(venta.total) <= 0):
            raise StripeTenantConflicto()
        existente = _ultimo_pago(db, tenant.empresa_id, int(venta.id))
        proveedor = StripeProveedor()
        if existente is not None and existente.referencia_transaccion:
            intent = proveedor.recuperar_intencion(existente.referencia_transaccion)
            if intent.estado in ESTADOS_REUTILIZABLES or intent.estado == ESTADO_SUCCEEDED:
                return IntencionPagoResponse(
                    venta_id=int(venta.id), pago_id=int(existente.id),
                    payment_intent_id=intent.id, client_secret=intent.client_secret,
                    monto=Decimal(existente.monto), moneda=intent.moneda,
                    estado_pago=existente.estado,
                )
        verificar_principal_ventas(db, tenant, "sp_registrar_intencion_stripe_tenant")
        monto = Decimal(venta.total)
        intentos = int(db.scalar(select(func.count()).select_from(Pago).where(
            Pago.empresa_id == tenant.empresa_id,
            Pago.venta_id == int(venta.id), Pago.pasarela == "STRIPE",
        )) or 0)
        intent = proveedor.crear_intencion(
            monto=_centavos(monto), moneda=settings.stripe_currency.lower(),
            metadata={"venta_id": str(venta.id)},
            idempotency_key=f"tenant-{tenant.empresa_id}-venta-{venta.id}-stripe-{intentos + 1}",
        )
        try:
            fila = db.execute(text(
                "CALL public.sp_registrar_intencion_stripe_tenant("
                ":empresa,:usuario,:venta,:pi,:moneda,NULL)"
            ), {"empresa": tenant.empresa_id, "usuario": tenant.usuario_id,
                "venta": int(venta.id), "pi": intent.id,
                "moneda": settings.stripe_currency.lower()}).mappings().one()
            db.commit()
        except DBAPIError:
            db.rollback()
            proveedor.cancelar_intencion(intent.id)
            raise
        return IntencionPagoResponse(
            venta_id=int(venta.id), pago_id=int(fila["p_pago_id"]),
            payment_intent_id=intent.id, client_secret=intent.client_secret,
            monto=monto, moneda=intent.moneda, estado_pago="PENDIENTE",
        )

    @staticmethod
    def estado(db: Session, tenant: TenantContext, venta_id: int):
        cliente = _cliente(db, tenant)
        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=venta_id
        )
        if venta is None or venta.cliente_id != int(cliente.id):
            raise StripeTenantConflicto()
        pago = _ultimo_pago(db, tenant.empresa_id, venta_id)
        return EstadoPagoVentaResponse(
            venta_id=venta_id, estado_venta=venta.estado,
            pago_id=int(pago.id) if pago else None,
            estado_pago=pago.estado if pago else None,
            payment_intent_id=pago.referencia_transaccion if pago else None,
            compensacion_estado=("REEMBOLSADO" if pago and pago.estado == "REEMBOLSADO" else None),
        )

    @staticmethod
    def webhook(db: Session, payload: bytes, firma: str):
        evento = StripeProveedor().verificar_webhook(payload, firma)
        # Stripe SDK devuelve un stripe.Event (StripeObject), no un dict.
        # Se normaliza para que el resto del adaptador use .get() de forma
        # uniforme; los fakes de pruebas pueden seguir devolviendo dict.
        if not isinstance(evento, dict):
            evento = evento.to_dict()
        principal = db.execute(text("""
          SELECT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
             AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND r.rolinherit
             AND pg_has_role(r.rolname,'fashionstore_webhook','USAGE')
             AND has_function_privilege(r.rolname,
               'fashionstore_private.sp_evento_stripe_tenant(text,text,bigint,text,bigint)',
               'EXECUTE') AS seguro
          FROM pg_roles r WHERE r.rolname=session_user
        """)).scalar_one_or_none()
        if principal is not True:
            raise StripeTenantError("Principal SQL webhook no disponible")
        tipo = str(evento.get("type") or "")
        objeto = (evento.get("data") or {}).get("object") or {}
        if tipo in EVENTOS_REFUND_STRIPE:
            normalizado = _refund_total_exitoso(tipo, objeto)
            if normalizado is None:
                # Refund parcial, pendiente o fallido: evento válido, sin
                # efecto sobre la venta ni sobre el stock.
                return WebhookStripeResponse(evento=tipo, procesado=False)
            pi, monto, moneda = normalizado
            evento_sql = EVENTO_INTERNO_REFUND
        elif tipo in EVENTOS_PAYMENT_INTENT:
            pi = str(objeto.get("id") or "")
            if not pi:
                return WebhookStripeResponse(evento=tipo, procesado=False)
            monto = int(objeto.get("amount_received") or objeto.get("amount") or 0)
            moneda = str(
                objeto.get("currency") or settings.stripe_currency
            ).lower()
            evento_sql = tipo
        else:
            return WebhookStripeResponse(evento=tipo, procesado=False)

        try:
            fila = db.execute(text(
                "CALL fashionstore_private.sp_evento_stripe_tenant("
                ":pi,:evento,:monto,:moneda,NULL)"
            ), {"pi": pi, "evento": evento_sql, "monto": monto,
                "moneda": moneda}).mappings().one()
            db.commit()
            return WebhookStripeResponse(evento=tipo, procesado=bool(fila["p_procesado"]))
        except DBAPIError as exc:
            db.rollback()
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if tipo == "payment_intent.succeeded" and sqlstate == "23514":
                reembolso = StripeProveedor().reembolsar_intencion(
                    pi, idempotency_key=f"refund-{pi}"
                )
                if reembolso.estado != "succeeded":
                    raise
                fila = db.execute(text(
                    "CALL fashionstore_private.sp_evento_stripe_tenant("
                    ":pi,:evento,:monto,:moneda,NULL)"
                ), {"pi": pi, "evento": EVENTO_INTERNO_REFUND,
                    "monto": monto, "moneda": moneda}).mappings().one()
                db.commit()
                return WebhookStripeResponse(evento=tipo, procesado=bool(fila["p_procesado"]))
            if tipo in EVENTOS_REFUND_STRIPE and sqlstate == "23514":
                # El procedimiento exige reembolso total y moneda exacta. Un
                # refund parcial o inconsistente no debe reintentarse como si
                # fuera un fallo técnico.
                logger.warning(
                    "Reembolso Stripe no aplicado evento=%s sqlstate=%s",
                    tipo, sqlstate,
                )
                return WebhookStripeResponse(evento=tipo, procesado=False)
            raise
