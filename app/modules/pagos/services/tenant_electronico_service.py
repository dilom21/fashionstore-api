"""Stripe adapter for tenant customers with explicit company ownership."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import BigInteger, Boolean, String, and_, column, func, insert, select, table, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.saas_rls_assertion import aplicar_asercion, clave_disponible, es_postgres
from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.pagos.errors import StripeWebhookFirmaError
from app.modules.pagos.models.models import Pago
from app.modules.pagos.services.electronico_service import obtener_proveedor
from app.modules.pagos.repositories.repository import PagoRepository
from app.modules.ventas.models.models import DetalleVenta, Venta


empresa = table(
    "empresa", column("id", BigInteger), column("slug", String), column("estado", Boolean)
)


class PagoTenantError(Exception):
    def __init__(self, codigo: int, detalle: str):
        self.codigo, self.detalle = codigo, detalle
        super().__init__(detalle)


def _campo(objeto, nombre):
    if isinstance(objeto, dict):
        return objeto.get(nombre)
    return getattr(objeto, nombre, None)


class TenantPagoElectronicoService:
    @staticmethod
    def _cliente_id(db: Session, tenant: TenantContext) -> int:
        c = Cliente.__table__
        valor = db.scalar(select(c.c.id).where(
            c.c.empresa_id == tenant.empresa_id,
            c.c.usuario_id == tenant.usuario_id,
            c.c.estado.is_(True),
        ))
        if valor is None:
            raise PagoTenantError(403, "No existe cliente activo en esta empresa")
        return int(valor)

    @classmethod
    def crear_intencion(
        cls, db: Session, *, tenant: TenantContext, venta_id: int, operacion_id: str,
    ) -> dict:
        cliente_id = cls._cliente_id(db, tenant)
        v, p = Venta.__table__, Pago.__table__
        venta = db.execute(select(v).where(
            v.c.id == venta_id, v.c.empresa_id == tenant.empresa_id,
            v.c.cliente_id == cliente_id,
        ).with_for_update()).mappings().first()
        if venta is None:
            raise PagoTenantError(404, "Venta no encontrada")
        if venta["canal"] not in ("WEB", "MOVIL") or venta["estado"] != "PENDIENTE":
            raise PagoTenantError(409, "La venta no está pendiente de pago electrónico")
        if not db.scalar(select(DetalleVenta.id).where(
            DetalleVenta.empresa_id == tenant.empresa_id,
            DetalleVenta.venta_id == venta_id,
        ).limit(1)):
            raise PagoTenantError(409, "La venta no contiene líneas")

        previo = db.execute(select(p).where(
            p.c.empresa_id == tenant.empresa_id, p.c.venta_id == venta_id,
            p.c.pasarela == "STRIPE", p.c.estado == "PENDIENTE",
        ).order_by(p.c.id.desc())).mappings().first()
        proveedor = obtener_proveedor()
        if previo is not None and previo["referencia_transaccion"]:
            intento = proveedor.recuperar_intencion(previo["referencia_transaccion"])
            if intento.estado not in ("canceled", "succeeded"):
                db.rollback()
                return cls._respuesta(venta, previo, intento)

        monto = Decimal(str(venta["total"]))
        moneda = (settings.stripe_currency or "bob").strip().lower()
        intento = proveedor.crear_intencion(
            monto=int((monto * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
            moneda=moneda,
            metadata={
                "empresa_id": str(tenant.empresa_id),
                "empresa_slug": tenant.empresa_slug,
                "usuario_id": str(tenant.usuario_id),
                "cliente_id": str(cliente_id),
                "venta_id": str(venta_id),
                "canal": str(venta["canal"]),
            },
            idempotency_key=f"tenant-{tenant.empresa_id}-pago-{operacion_id}",
        )
        valores_pago = dict(
            empresa_id=tenant.empresa_id, venta_id=venta_id,
            fecha_hora=datetime.now(timezone.utc), monto=monto,
            metodo="TARJETA", estado="PENDIENTE",
            referencia_transaccion=intento.id, pasarela="STRIPE",
        )
        if db.get_bind().dialect.name == "sqlite":
            valores_pago["id"] = int(
                db.scalar(select(func.coalesce(func.max(p.c.id), 0) + 1)) or 1
            )
        pago_id = int(db.scalar(insert(p).values(**valores_pago).returning(p.c.id)))
        db.commit()
        pago = {"id": pago_id, "monto": monto, "estado": "PENDIENTE"}
        return cls._respuesta(venta, pago, intento)

    @staticmethod
    def _respuesta(venta, pago, intento) -> dict:
        return {
            "venta_id": int(venta["id"]), "pago_id": int(pago["id"]),
            "payment_intent_id": intento.id, "client_secret": intento.client_secret,
            "monto": Decimal(str(pago["monto"])), "moneda": intento.moneda,
            "estado_pago": pago["estado"],
        }

    @classmethod
    def estado(cls, db: Session, *, tenant: TenantContext, venta_id: int) -> dict:
        cliente_id = cls._cliente_id(db, tenant)
        v, p = Venta.__table__, Pago.__table__
        venta = db.execute(select(v.c.id, v.c.estado).where(
            v.c.id == venta_id, v.c.empresa_id == tenant.empresa_id,
            v.c.cliente_id == cliente_id,
        )).mappings().first()
        if venta is None:
            raise PagoTenantError(404, "Venta no encontrada")
        pago = db.execute(select(p.c.id, p.c.estado, p.c.referencia_transaccion).where(
            p.c.empresa_id == tenant.empresa_id, p.c.venta_id == venta_id,
            p.c.pasarela == "STRIPE",
        ).order_by(p.c.id.desc())).mappings().first()
        return {
            "venta_id": venta_id, "estado_venta": venta["estado"],
            "pago_id": int(pago["id"]) if pago else None,
            "estado_pago": pago["estado"] if pago else None,
            "payment_intent_id": pago["referencia_transaccion"] if pago else None,
            "compensacion_estado": None,
        }

    @staticmethod
    def procesar_webhook(
        db: Session, *, empresa_slug: str, payload: bytes, firma: str,
    ) -> dict:
        if not firma:
            raise StripeWebhookFirmaError("Falta Stripe-Signature")
        evento = obtener_proveedor().verificar_webhook(payload, firma)
        tipo = str(_campo(evento, "type") or "")
        objeto = _campo(_campo(evento, "data"), "object")
        metadata = _campo(objeto, "metadata") or {}
        try:
            empresa_id = int(_campo(metadata, "empresa_id"))
            usuario_id = int(_campo(metadata, "usuario_id"))
            cliente_id = int(_campo(metadata, "cliente_id"))
            venta_id = int(_campo(metadata, "venta_id"))
        except (TypeError, ValueError):
            raise PagoTenantError(400, "Metadata empresarial inválida")
        if _campo(metadata, "empresa_slug") != empresa_slug:
            raise PagoTenantError(400, "Webhook no corresponde a la empresa")
        empresa_real = db.execute(select(empresa.c.id).where(
            empresa.c.id == empresa_id, empresa.c.slug == empresa_slug,
            empresa.c.estado.is_(True),
        )).first()
        if empresa_real is None:
            raise PagoTenantError(404, "Empresa no encontrada")

        if es_postgres(db):
            db.execute(text("SELECT set_config('app.usuario_id', :v, true)"), {"v": str(usuario_id)})
            db.execute(text("SELECT set_config('app.empresa_id', :v, true)"), {"v": str(empresa_id)})
            if not clave_disponible() or not aplicar_asercion(db, empresa_id, usuario_id):
                raise PagoTenantError(503, "Aislamiento SQL no disponible")

        pi_id = str(_campo(objeto, "id") or "")
        p, v, c = Pago.__table__, Venta.__table__, Cliente.__table__
        fila = db.execute(select(
            p.c.id.label("pago_id"), p.c.estado.label("pago_estado"), p.c.monto,
            v.c.id.label("venta_id"), v.c.estado.label("venta_estado"), v.c.total,
        ).join(v, and_(v.c.id == p.c.venta_id, v.c.empresa_id == empresa_id)).join(
            c, and_(c.c.id == v.c.cliente_id, c.c.empresa_id == empresa_id,
                    c.c.id == cliente_id, c.c.usuario_id == usuario_id)
        ).where(
            p.c.empresa_id == empresa_id, p.c.referencia_transaccion == pi_id,
            v.c.id == venta_id, v.c.canal.in_(("WEB", "MOVIL")),
        ).with_for_update(of=p)).mappings().first()
        if fila is None:
            raise PagoTenantError(404, "Pago empresarial no encontrado")

        if tipo == "payment_intent.succeeded":
            recibido = int(_campo(objeto, "amount_received") or _campo(objeto, "amount") or 0)
            esperado = int((Decimal(str(fila["total"])) * 100).quantize(Decimal("1")))
            if recibido != esperado or str(_campo(objeto, "currency") or "").lower() != (settings.stripe_currency or "bob").lower():
                raise PagoTenantError(409, "Monto o moneda no coincide")
            if fila["pago_estado"] == "APROBADO" and fila["venta_estado"] == "COMPLETADA":
                db.rollback()
                return {"recibido": True, "evento": tipo, "procesado": True}
            try:
                db.execute(update(p).where(
                    p.c.id == fila["pago_id"], p.c.empresa_id == empresa_id,
                ).values(estado="APROBADO"))
                db.flush()
                PagoRepository.confirmar_venta(db, venta_id, usuario_id)
                db.commit()
            except DBAPIError:
                db.rollback()
                raise PagoTenantError(503, "Confirmación de venta pendiente de reintento")
        elif tipo == "payment_intent.payment_failed":
            if fila["pago_estado"] == "PENDIENTE":
                db.execute(update(p).where(
                    p.c.id == fila["pago_id"], p.c.empresa_id == empresa_id,
                ).values(estado="RECHAZADO"))
                db.commit()
            else:
                db.rollback()
        elif tipo == "payment_intent.canceled":
            if fila["pago_estado"] == "PENDIENTE":
                db.execute(update(p).where(
                    p.c.id == fila["pago_id"], p.c.empresa_id == empresa_id,
                ).values(estado="ANULADO"))
                db.commit()
            else:
                db.rollback()
        else:
            db.rollback()
            return {"recibido": True, "evento": tipo, "procesado": False}
        return {"recibido": True, "evento": tipo, "procesado": True}
