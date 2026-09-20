"""Pruebas de CU22 - Procesar pago electronico con Stripe (Test Mode).

El SDK de Stripe se sustituye por un fake inyectado en
``PagoElectronicoService``: la suite NO depende de internet ni de claves reales.
Ademas se prueba el adaptador Stripe (firma con payload RAW) mockeando el SDK.

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
``db_session``, por lo que no quedan pagos, ventas ni movimientos en la BD real.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Rol,
    Usuario,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.pagos.errors import (
    StripeConfiguracionError,
    StripeIntentError,
    StripeRefundError,
    StripeWebhookFirmaError,
)
from app.modules.pagos.models.models import Pago
from app.modules.pagos.providers.base import IntencionPago, Reembolso
from app.modules.pagos.repositories.stripe_repository import PagoStripeRepository
from app.modules.pagos.services import electronico_service
from app.modules.ventas.models.models import Venta

_UTC = timezone.utc

CANAL_WEB = "WEB"
CANAL_MOVIL = "MOVIL"


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Fake del provider Stripe
# ---------------------------------------------------------------------------


class FakeStripeProveedor:
    """Provider en memoria: no toca la red y permite forzar errores."""

    def __init__(self) -> None:
        self.creados: list[dict] = []
        self.recuperados: list[str] = []
        self.cancelados: list[str] = []
        self.intenciones: dict[str, IntencionPago] = {}
        self.estado_siguiente = "requires_payment_method"
        self.crear_error: Exception | None = None
        self.recuperar_error: Exception | None = None
        self.cancelar_error: Exception | None = None
        self.verificar_error: Exception | None = None
        self.evento: dict | None = None
        self._contador = 0
        # Refunds: se emula la idempotencia de Stripe por idempotency_key.
        self.reembolsos: dict[str, str] = {}
        self.reembolsos_llamadas: list[dict] = []
        self.reembolsar_error: Exception | None = None
        self.reembolsar_estado = "succeeded"

    def crear_intencion(self, *, monto, moneda, metadata, idempotency_key):
        if self.crear_error is not None:
            raise self.crear_error
        self._contador += 1
        pi_id = f"pi_test_{self._contador:04d}"
        self.creados.append(
            {
                "id": pi_id,
                "monto": monto,
                "moneda": moneda,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            }
        )
        intencion = IntencionPago(
            id=pi_id,
            client_secret=f"{pi_id}_secret_test",
            estado=self.estado_siguiente,
            monto=monto,
            moneda=moneda,
        )
        self.intenciones[pi_id] = intencion
        return intencion

    def recuperar_intencion(self, intencion_id):
        if self.recuperar_error is not None:
            raise self.recuperar_error
        self.recuperados.append(intencion_id)
        return self.intenciones[intencion_id]

    def cancelar_intencion(self, intencion_id):
        if self.cancelar_error is not None:
            raise self.cancelar_error
        self.cancelados.append(intencion_id)

    def reembolsar_intencion(self, intencion_id, *, idempotency_key):
        if self.reembolsar_error is not None:
            raise self.reembolsar_error
        self.reembolsos_llamadas.append(
            {"pi": intencion_id, "key": idempotency_key}
        )
        if idempotency_key not in self.reembolsos:
            self.reembolsos[idempotency_key] = (
                f"re_test_{len(self.reembolsos) + 1:04d}"
            )
        return Reembolso(
            id=self.reembolsos[idempotency_key],
            estado=self.reembolsar_estado,
        )

    def verificar_webhook(self, payload, firma):
        if self.verificar_error is not None:
            raise self.verificar_error
        return self.evento


@pytest.fixture()
def stripe_fake(monkeypatch):
    fake = FakeStripeProveedor()
    monkeypatch.setattr(
        electronico_service, "obtener_proveedor", lambda: fake
    )
    return fake


def _evento(tipo, pi_id, *, amount, currency="bob", metadata=None, event_id="evt_test"):
    return {
        "id": event_id,
        "type": tipo,
        "data": {
            "object": {
                "id": pi_id,
                "amount": amount,
                "amount_received": amount,
                "currency": currency,
                "metadata": metadata or {},
            }
        },
    }


# ---------------------------------------------------------------------------
# Datos de apoyo (catalogo, inventario, cliente, venta digital)
# ---------------------------------------------------------------------------


def _crear_categoria(client, headers):
    resp = client.post(
        "/categorias",
        json={"nombre": f"ZZCU22CAT_{_suf()}", "descripcion": "cat CU22"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": f"ZZCU22PROD_{_suf()}",
            "descripcion": "producto CU22",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers):
    resp = client.post(
        "/tallas", json={"nombre": f"ZZCU22T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers):
    resp = client.post(
        "/colores", json={"nombre": f"ZZCU22C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU22SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": f"ZZCU22TEMP_{_suf()}",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _sucursales_activas(db_session) -> list[int]:
    from app.modules.sucursales.models.models import Sucursal

    return list(
        db_session.scalars(
            select(Sucursal.id)
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.id)
        ).all()
    )


def _crear_inventario(
    db_session, *, sucursal_id, variante_id, temporada_id, stock_actual=10
):
    inventario = Inventario(
        sucursal_id=sucursal_id,
        variante_producto_id=variante_id,
        temporada_id=temporada_id,
        stock_actual=stock_actual,
        stock_reservado=0,
        fecha_actualizacion=datetime.now(_UTC),
    )
    db_session.add(inventario)
    db_session.flush()
    return inventario


def _contexto(client, admin_headers, db_session, *, stock_actual=10, precio="50.00"):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"], precio=precio)
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    temporada = _crear_temporada(client, admin_headers)
    sucursal_id = _sucursales_activas(db_session)[0]
    inventario = _crear_inventario(
        db_session,
        sucursal_id=sucursal_id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=stock_actual,
    )
    return {
        "sucursal_id": sucursal_id,
        "inventario_id": inventario.id,
        "producto": producto,
    }


def _crear_cliente(db_session, etiqueta="a"):
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None, "No existe el rol CLIENTE"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu22.{etiqueta}.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    cliente = Cliente(
        usuario_id=usuario.id,
        nombre="Josias",
        apellido="Prueba",
        telefono="70000000",
        estado=True,
    )
    db_session.add(cliente)
    db_session.flush()
    token = create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": rol.nombre,
            "contexto": "cliente",
        }
    )
    return cliente, {"Authorization": f"Bearer {token}"}


def _venta_digital(
    client,
    admin_headers,
    db_session,
    *,
    canal=CANAL_WEB,
    cantidad=2,
    precio="50.00",
    stock_actual=10,
):
    """Crea una venta WEB/MOVIL PENDIENTE real usando CU19."""
    cliente, headers = _crear_cliente(db_session, canal.lower())
    ctx = _contexto(
        client, admin_headers, db_session, stock_actual=stock_actual, precio=precio
    )
    agregado = client.post(
        "/carritos/items",
        json={
            "sucursal_id": ctx["sucursal_id"],
            "inventario_id": ctx["inventario_id"],
            "cantidad": cantidad,
        },
        headers=headers,
    )
    assert agregado.status_code == 200, agregado.text
    resp = client.post(
        "/ventas/digital",
        json={"carrito_id": agregado.json()["carrito_id"], "canal": canal},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return {
        "venta_id": resp.json()["venta_id"],
        "total": Decimal(str(resp.json()["total"])),
        "inventario_id": ctx["inventario_id"],
        "cliente": cliente,
        "headers": headers,
    }


def _venta_directa(
    db_session, *, sucursal_id, cliente_id, canal="WEB", estado="PENDIENTE", total="50.00"
) -> Venta:
    venta = Venta(
        cliente_id=cliente_id,
        empleado_id=None,
        sucursal_id=sucursal_id,
        reserva_id=None,
        fecha_hora=datetime.now(_UTC),
        canal=canal,
        estado=estado,
        total=Decimal(total),
        carrito_id=None,
    )
    db_session.add(venta)
    db_session.commit()
    return venta


# ---------------------------------------------------------------------------
# Verificacion de BD
# ---------------------------------------------------------------------------


def _pagos(db_session, venta_id) -> list[Pago]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Pago).where(Pago.venta_id == venta_id).order_by(Pago.id)
        ).all()
    )


def _estado_venta(db_session, venta_id) -> str:
    db_session.expire_all()
    return db_session.get(Venta, venta_id).estado


def _stock(db_session, inventario_id) -> int:
    db_session.expire_all()
    return db_session.get(Inventario, inventario_id).stock_actual


def _movimientos(db_session, inventario_id, tipo=None):
    db_session.expire_all()
    condiciones = [MovimientoInventario.inventario_id == inventario_id]
    if tipo is not None:
        condiciones.append(MovimientoInventario.tipo == tipo)
    return list(
        db_session.scalars(select(MovimientoInventario).where(*condiciones)).all()
    )


def _intencion(client, headers, venta_id):
    return client.post(
        "/pagos/stripe/intencion",
        json={"venta_id": venta_id},
        headers=headers,
    )


def _webhook(client, payload, firma="sig_test"):
    return client.post(
        "/pagos/stripe/webhook",
        content=payload,
        headers={"stripe-signature": firma, "content-type": "application/json"},
    )


# ===========================================================================
# Crear intencion
# ===========================================================================


def test_crear_intencion_web_valida(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session, canal=CANAL_WEB)
    resp = _intencion(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["venta_id"] == venta["venta_id"]
    assert body["payment_intent_id"].startswith("pi_")
    assert body["client_secret"].endswith("_secret_test")
    assert Decimal(str(body["monto"])) == Decimal("100.00")
    assert body["moneda"] == "bob"
    assert body["estado_pago"] == "PENDIENTE"

    # Stripe recibe unidades minimas (sin float) y metadata util.
    creado = stripe_fake.creados[0]
    assert creado["monto"] == 10000
    assert creado["moneda"] == "bob"
    assert creado["metadata"]["venta_id"] == str(venta["venta_id"])
    assert creado["metadata"]["cliente_id"] == str(venta["cliente"].id)
    assert creado["metadata"]["canal"] == CANAL_WEB

    # Persistencia real del pago.
    pagos = _pagos(db_session, venta["venta_id"])
    assert len(pagos) == 1
    assert pagos[0].estado == "PENDIENTE"
    assert pagos[0].metodo == "TARJETA"
    assert pagos[0].pasarela == "STRIPE"
    assert pagos[0].referencia_transaccion == body["payment_intent_id"]
    assert Decimal(pagos[0].monto) == Decimal("100.00")

    # La intencion NO confirma la venta ni toca inventario.
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"]) == []


def test_crear_intencion_movil_valida(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session, canal=CANAL_MOVIL)
    resp = _intencion(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 201, resp.text
    assert stripe_fake.creados[0]["metadata"]["canal"] == CANAL_MOVIL


def test_venta_ajena_403(client, admin_headers, db_session, stripe_fake):
    venta = _venta_digital(client, admin_headers, db_session)
    _, otro_headers = _crear_cliente(db_session, "ajeno")
    resp = _intencion(client, otro_headers, venta["venta_id"])
    assert resp.status_code == 403, resp.text
    assert _pagos(db_session, venta["venta_id"]) == []


def test_venta_inexistente_404(client, admin_headers, db_session, stripe_fake):
    _, headers = _crear_cliente(db_session, "inexistente")
    resp = _intencion(client, headers, 999_999_999)
    assert resp.status_code == 404, resp.text


def test_venta_presencial_409(client, admin_headers, db_session, stripe_fake):
    cliente, headers = _crear_cliente(db_session, "presencial")
    sucursal = _sucursales_activas(db_session)[0]
    venta = _venta_directa(
        db_session, sucursal_id=sucursal, cliente_id=cliente.id, canal="PRESENCIAL"
    )
    resp = _intencion(client, headers, venta.id)
    assert resp.status_code == 409, resp.text
    assert stripe_fake.creados == []


def test_venta_completada_409(client, admin_headers, db_session, stripe_fake):
    cliente, headers = _crear_cliente(db_session, "completada")
    sucursal = _sucursales_activas(db_session)[0]
    venta = _venta_directa(
        db_session, sucursal_id=sucursal, cliente_id=cliente.id, estado="COMPLETADA"
    )
    resp = _intencion(client, headers, venta.id)
    assert resp.status_code == 409, resp.text
    assert stripe_fake.creados == []


def test_sin_token_401(client, admin_headers, db_session, stripe_fake):
    venta = _venta_digital(client, admin_headers, db_session)
    resp = client.post(
        "/pagos/stripe/intencion", json={"venta_id": venta["venta_id"]}
    )
    assert resp.status_code == 401, resp.text


def test_retry_reutiliza_intencion_y_pago(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session)
    primera = _intencion(client, venta["headers"], venta["venta_id"])
    assert primera.status_code == 201, primera.text

    segunda = _intencion(client, venta["headers"], venta["venta_id"])
    assert segunda.status_code == 201, segunda.text
    assert segunda.json()["payment_intent_id"] == primera.json()["payment_intent_id"]
    assert segunda.json()["pago_id"] == primera.json()["pago_id"]

    assert len(stripe_fake.creados) == 1
    assert stripe_fake.recuperados == [primera.json()["payment_intent_id"]]
    assert len(_pagos(db_session, venta["venta_id"])) == 1


def test_doble_request_no_duplica(client, admin_headers, db_session, stripe_fake):
    venta = _venta_digital(client, admin_headers, db_session)
    for _ in range(3):
        resp = _intencion(client, venta["headers"], venta["venta_id"])
        assert resp.status_code == 201, resp.text
    assert len(stripe_fake.creados) == 1
    assert len(_pagos(db_session, venta["venta_id"])) == 1


def test_error_stripe_no_crea_pago(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session)
    stripe_fake.crear_error = StripeIntentError("stripe caido")
    resp = _intencion(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 502, resp.text
    assert _pagos(db_session, venta["venta_id"]) == []


def test_error_db_tras_stripe_compensa_intencion(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    venta = _venta_digital(client, admin_headers, db_session)

    def falla_persistencia(*args, **kwargs):
        raise DBAPIError("INSERT pago ...", {}, Exception("boom"))

    monkeypatch.setattr(
        PagoStripeRepository, "crear_pago_stripe", staticmethod(falla_persistencia)
    )

    resp = _intencion(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 409, resp.text
    assert "boom" not in resp.text

    assert stripe_fake.creados, "Stripe si creo la intencion"
    pi_id = stripe_fake.creados[0]["id"]
    assert stripe_fake.cancelados == [pi_id]
    assert _pagos(db_session, venta["venta_id"]) == []


def test_intencion_usa_idempotency_key_no_fija(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session)
    _intencion(client, venta["headers"], venta["venta_id"])
    key = stripe_fake.creados[0]["idempotency_key"]
    assert str(venta["venta_id"]) in key


# ===========================================================================
# Webhook
# ===========================================================================


def _preparar_pago(client, admin_headers, db_session, stripe_fake, *, canal=CANAL_WEB):
    venta = _venta_digital(client, admin_headers, db_session, canal=canal)
    resp = _intencion(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 201, resp.text
    venta["pi_id"] = resp.json()["payment_intent_id"]
    return venta


def test_webhook_firma_invalida_400(
    client, admin_headers, db_session, stripe_fake
):
    stripe_fake.verificar_error = StripeWebhookFirmaError("firma mala")
    resp = _webhook(client, b'{"id":"evt_bad"}', firma="bad")
    assert resp.status_code == 400, resp.text


def test_webhook_sin_firma_400(client, admin_headers, db_session, stripe_fake):
    resp = client.post(
        "/pagos/stripe/webhook",
        content=b'{"id":"evt"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400, resp.text


def test_webhook_evento_desconocido_2xx(
    client, admin_headers, db_session, stripe_fake
):
    stripe_fake.evento = {
        "id": "evt_otro",
        "type": "charge.succeeded",
        "data": {"object": {}},
    }
    resp = _webhook(client, b'{"id":"evt_otro"}')
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesado"] is False


def test_webhook_succeeded_aprueba_y_confirma(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    amount = int(venta["total"] * 100)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded",
        venta["pi_id"],
        amount=amount,
        metadata={"venta_id": str(venta["venta_id"])},
    )

    resp = _webhook(client, b'{"id":"evt_succeeded"}')
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesado"] is True

    pagos = _pagos(db_session, venta["venta_id"])
    assert len(pagos) == 1
    assert pagos[0].estado == "APROBADO"

    assert _estado_venta(db_session, venta["venta_id"]) == "COMPLETADA"
    assert _stock(db_session, venta["inventario_id"]) == 8

    salidas = _movimientos(db_session, venta["inventario_id"], "SALIDA_VENTA")
    assert len(salidas) == 1 and salidas[0].cantidad == 2
    # Auditoria: el movimiento usa el usuario del cliente comprador.
    assert salidas[0].usuario_id == venta["cliente"].usuario_id


def test_webhook_succeeded_repetido_idempotente(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    amount = int(venta["total"] * 100)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded", venta["pi_id"], amount=amount
    )

    primera = _webhook(client, b'{"id":"evt_1"}')
    segunda = _webhook(client, b'{"id":"evt_2"}')
    assert primera.status_code == 200 and segunda.status_code == 200

    assert len(_pagos(db_session, venta["venta_id"])) == 1
    assert _estado_venta(db_session, venta["venta_id"]) == "COMPLETADA"
    assert _stock(db_session, venta["inventario_id"]) == 8
    assert len(_movimientos(db_session, venta["inventario_id"], "SALIDA_VENTA")) == 1


def test_webhook_amount_mismatch_no_confirma(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded", venta["pi_id"], amount=1
    )

    resp = _webhook(client, b'{"id":"evt_monto"}')
    assert resp.status_code == 409, resp.text

    assert _pagos(db_session, venta["venta_id"])[0].estado == "PENDIENTE"
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert _stock(db_session, venta["inventario_id"]) == 10


def test_webhook_currency_mismatch_no_confirma(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    amount = int(venta["total"] * 100)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded", venta["pi_id"], amount=amount, currency="usd"
    )

    resp = _webhook(client, b'{"id":"evt_moneda"}')
    assert resp.status_code == 409, resp.text
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert _stock(db_session, venta["inventario_id"]) == 10


def test_webhook_metadata_venta_inconsistente_no_confirma(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    amount = int(venta["total"] * 100)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded",
        venta["pi_id"],
        amount=amount,
        metadata={"venta_id": "999999999"},
    )
    resp = _webhook(client, b'{"id":"evt_meta"}')
    assert resp.status_code == 409, resp.text
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"


def test_webhook_payment_failed_rechaza_sin_confirmar(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    stripe_fake.evento = _evento(
        "payment_intent.payment_failed", venta["pi_id"], amount=10000
    )
    resp = _webhook(client, b'{"id":"evt_failed"}')
    assert resp.status_code == 200, resp.text

    assert _pagos(db_session, venta["venta_id"])[0].estado == "RECHAZADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"]) == []


def test_webhook_payment_failed_repetido_idempotente(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    stripe_fake.evento = _evento(
        "payment_intent.payment_failed", venta["pi_id"], amount=10000
    )
    for _ in range(2):
        resp = _webhook(client, b'{"id":"evt_failed"}')
        assert resp.status_code == 200, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "RECHAZADO"


def test_webhook_processing_mantiene_pendiente(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    stripe_fake.evento = _evento(
        "payment_intent.processing", venta["pi_id"], amount=10000
    )
    resp = _webhook(client, b'{"id":"evt_proc"}')
    assert resp.status_code == 200, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "PENDIENTE"
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"


def test_webhook_canceled_anula_y_permite_nuevo_intento(
    client, admin_headers, db_session, stripe_fake
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    stripe_fake.evento = _evento(
        "payment_intent.canceled", venta["pi_id"], amount=10000
    )
    resp = _webhook(client, b'{"id":"evt_cancel"}')
    assert resp.status_code == 200, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "ANULADO"

    # Stripe ya reporta el PaymentIntent como cancelado: no es reutilizable,
    # por lo que se crea uno nuevo (evita reutilizar un intent muerto).
    original = stripe_fake.intenciones[venta["pi_id"]]
    stripe_fake.intenciones[venta["pi_id"]] = IntencionPago(
        id=original.id,
        client_secret=original.client_secret,
        estado="canceled",
        monto=original.monto,
        moneda=original.moneda,
    )
    nueva = _intencion(client, venta["headers"], venta["venta_id"])
    assert nueva.status_code == 201, nueva.text
    assert nueva.json()["payment_intent_id"] != venta["pi_id"]
    assert len(_pagos(db_session, venta["venta_id"])) == 2


def test_webhook_sin_pago_asociado_2xx(
    client, admin_headers, db_session, stripe_fake
):
    stripe_fake.evento = _evento(
        "payment_intent.succeeded", "pi_desconocido", amount=10000
    )
    resp = _webhook(client, b'{"id":"evt_desc"}')
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesado"] is False


# ===========================================================================
# Compensacion / refund idempotente (fallo de dominio del SP)
# ===========================================================================


class _OrigenBD:
    """Origen DBAPI minimo para clasificar por SQLSTATE en tests."""

    def __init__(self, sqlstate=None, constraint_name=None, mensaje="error"):
        self.sqlstate = sqlstate
        self.diag = (
            type("_Diag", (), {"constraint_name": constraint_name})()
            if constraint_name
            else None
        )
        self._mensaje = mensaje

    def __str__(self):
        return self._mensaje


def _error_dominio_sp(mensaje="Stock fisico insuficiente para el producto"):
    return DBAPIError(
        "CALL public.sp_confirmar_venta(:venta_id, :usuario_id)",
        {},
        _OrigenBD(sqlstate="P0001", mensaje=mensaje),
    )


def _error_tecnico_sp():
    return DBAPIError(
        "CALL public.sp_confirmar_venta(:venta_id, :usuario_id)",
        {},
        _OrigenBD(sqlstate=None, mensaje="connection reset by peer"),
    )


def _error_sqlstate(sqlstate, mensaje="constraint inesperado"):
    return DBAPIError(
        "CALL public.sp_confirmar_venta(:venta_id, :usuario_id)",
        {},
        _OrigenBD(sqlstate=sqlstate, mensaje=mensaje),
    )


def _instalar_sp_dominio(monkeypatch) -> dict:
    """Hace que sp_confirmar_venta falle por dominio y cuenta sus llamadas."""
    from app.modules.pagos.repositories import repository as repo_module

    llamadas = {"n": 0}

    def confirmar_falla(db, venta_id, usuario_id):
        llamadas["n"] += 1
        raise _error_dominio_sp()

    monkeypatch.setattr(
        repo_module.PagoRepository, "confirmar_venta", confirmar_falla
    )
    return llamadas


def _preparar_succeeded(venta, stripe_fake):
    stripe_fake.evento = _evento(
        "payment_intent.succeeded",
        venta["pi_id"],
        amount=int(venta["total"] * 100),
        metadata={"venta_id": str(venta["venta_id"])},
    )


def test_webhook_succeeded_ok_no_refunda(
    client, admin_headers, db_session, stripe_fake
):
    """succeeded + SP OK -> APROBADO/COMPLETADA y ningun refund."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    resp = _webhook(client, b'{"id":"evt_ok"}')
    assert resp.status_code == 200, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "COMPLETADA"
    assert stripe_fake.reembolsos == {}
    assert stripe_fake.reembolsos_llamadas == []


def test_compensacion_fallo_dominio_refunda(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """succeeded + SP dominio -> CANCELADA/APROBADO -> refund -> REEMBOLSADO."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    llamadas = _instalar_sp_dominio(monkeypatch)

    resp = _webhook(client, b'{"id":"evt_comp"}')
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesado"] is True

    pagos = _pagos(db_session, venta["venta_id"])
    assert len(pagos) == 1
    assert pagos[0].estado == "REEMBOLSADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"

    # No se descuenta stock ni se registra SALIDA_VENTA.
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"], "SALIDA_VENTA") == []

    assert len(stripe_fake.reembolsos) == 1
    key = next(iter(stripe_fake.reembolsos))
    assert key == f"refund-venta-{venta['venta_id']}-pi-{venta['pi_id']}"
    assert stripe_fake.reembolsos_llamadas[0]["pi"] == venta["pi_id"]
    assert llamadas["n"] == 1


def test_replay_compensado_no_duplica_refund(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Replay de venta compensada: 200, sin segundo refund ni SP."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    llamadas = _instalar_sp_dominio(monkeypatch)

    primera = _webhook(client, b'{"id":"evt_1"}')
    segunda = _webhook(client, b'{"id":"evt_2"}')
    assert primera.status_code == 200 and segunda.status_code == 200

    assert len(_pagos(db_session, venta["venta_id"])) == 1
    assert _pagos(db_session, venta["venta_id"])[0].estado == "REEMBOLSADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert len(stripe_fake.reembolsos) == 1
    assert llamadas["n"] == 1
    assert _movimientos(db_session, venta["inventario_id"]) == []


def _llevar_a_cancelada_aprobado(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Deja la venta en CANCELADA + APROBADO con el refund temporal caido."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    llamadas = _instalar_sp_dominio(monkeypatch)
    stripe_fake.reembolsar_error = StripeRefundError("stripe temporal")

    resp = _webhook(client, b'{"id":"evt_tmp"}')
    assert resp.status_code == 502, resp.text

    pagos = _pagos(db_session, venta["venta_id"])
    assert pagos[0].estado == "APROBADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert stripe_fake.reembolsos == {}
    assert llamadas["n"] == 1

    stripe_fake.reembolsar_error = None
    return venta, llamadas


def test_refund_temporal_falla_mantiene_aprobado_5xx(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Fallo temporal del refund: CANCELADA/APROBADO y 5xx."""
    venta, _ = _llevar_a_cancelada_aprobado(
        client, admin_headers, db_session, stripe_fake, monkeypatch
    )
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"]) == []


def test_compensacion_pendiente_reintenta_solo_refund(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """CANCELADA/APROBADO -> retry solo refund, nunca SP."""
    venta, llamadas = _llevar_a_cancelada_aprobado(
        client, admin_headers, db_session, stripe_fake, monkeypatch
    )

    resp = _webhook(client, b'{"id":"evt_retry"}')
    assert resp.status_code == 200, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "REEMBOLSADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert len(stripe_fake.reembolsos) == 1
    assert llamadas["n"] == 1


def test_refund_ok_commit_local_falla_retry_converge(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Refund externo OK + fallo local -> retry con la misma key converge."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    llamadas = _instalar_sp_dominio(monkeypatch)

    commits = {"n": 0}
    commit_original = db_session.commit

    def commit_falla_segundo():
        commits["n"] += 1
        if commits["n"] == 2:
            raise DBAPIError("COMMIT", {}, Exception("commit boom"))
        return commit_original()

    monkeypatch.setattr(db_session, "commit", commit_falla_segundo)

    primera = _webhook(client, b'{"id":"evt_1"}')
    assert primera.status_code == 503, primera.text
    # El refund externo si ocurrio; localmente quedo CANCELADA/APROBADO.
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"
    assert len(stripe_fake.reembolsos) == 1

    monkeypatch.setattr(db_session, "commit", commit_original)
    segunda = _webhook(client, b'{"id":"evt_2"}')
    assert segunda.status_code == 200, segunda.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "REEMBOLSADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    # Un solo refund logico pese a dos llamadas a Stripe.
    assert len(stripe_fake.reembolsos) == 1
    assert len(stripe_fake.reembolsos_llamadas) == 2
    assert (
        stripe_fake.reembolsos_llamadas[0]["key"]
        == stripe_fake.reembolsos_llamadas[1]["key"]
    )
    assert llamadas["n"] == 1


def test_fallo_tecnico_sp_no_compensa_5xx(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Error tecnico del SP: sin refund, sin CANCELADA, 5xx."""
    from app.modules.pagos.repositories import repository as repo_module

    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)

    def confirmar_falla(db, venta_id, usuario_id):
        raise _error_tecnico_sp()

    monkeypatch.setattr(
        repo_module.PagoRepository, "confirmar_venta", confirmar_falla
    )

    resp = _webhook(client, b'{"id":"evt_tec"}')
    assert resp.status_code == 503, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "PENDIENTE"
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert stripe_fake.reembolsos == {}
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"]) == []


@pytest.mark.parametrize("sqlstate", ["23514", "23503", "22000", "23505"])
def test_sqlstate_datos_integridad_es_tecnico_no_compensa(
    client, admin_headers, db_session, stripe_fake, monkeypatch, sqlstate
):
    """22xxx/23xxx inesperados NO son dominio: sin refund ni CANCELADA, 5xx."""
    from app.modules.pagos.repositories import repository as repo_module

    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)

    def confirmar_falla(db, venta_id, usuario_id):
        raise _error_sqlstate(sqlstate)

    monkeypatch.setattr(
        repo_module.PagoRepository, "confirmar_venta", confirmar_falla
    )

    resp = _webhook(client, b'{"id":"evt_tec"}')
    assert resp.status_code == 503, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "PENDIENTE"
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
    assert stripe_fake.reembolsos == {}
    assert _stock(db_session, venta["inventario_id"]) == 10
    assert _movimientos(db_session, venta["inventario_id"]) == []


def test_refund_pendiente_no_marca_reembolsado(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Refund pending/requires_action: compensacion pendiente, no REEMBOLSADO."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    llamadas = _instalar_sp_dominio(monkeypatch)
    stripe_fake.reembolsar_estado = "pending"

    primera = _webhook(client, b'{"id":"evt_1"}')
    assert primera.status_code == 503, primera.text
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"

    # Al confirmarse el refund, el retry idempotente converge a REEMBOLSADO.
    stripe_fake.reembolsar_estado = "succeeded"
    segunda = _webhook(client, b'{"id":"evt_2"}')
    assert segunda.status_code == 200, segunda.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "REEMBOLSADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"
    assert len(stripe_fake.reembolsos) == 1
    assert len(stripe_fake.reembolsos_llamadas) == 2
    assert (
        stripe_fake.reembolsos_llamadas[0]["key"]
        == stripe_fake.reembolsos_llamadas[1]["key"]
    )
    assert llamadas["n"] == 1


def test_refund_requires_action_no_marca_reembolsado(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """requires_action tambien queda como compensacion pendiente."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    _instalar_sp_dominio(monkeypatch)
    stripe_fake.reembolsar_estado = "requires_action"

    resp = _webhook(client, b'{"id":"evt_ra"}')
    assert resp.status_code == 503, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"


def test_refund_estado_fallido_no_marca_reembolsado(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    """Refund failed/canceled: no se persiste REEMBOLSADO."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    _instalar_sp_dominio(monkeypatch)
    stripe_fake.reembolsar_estado = "failed"

    resp = _webhook(client, b'{"id":"evt_fail"}')
    assert resp.status_code == 502, resp.text
    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "CANCELADA"


def test_replay_completada_sin_side_effects(
    client, admin_headers, db_session, stripe_fake
):
    """COMPLETADA/APROBADO replay -> 200 sin side effects ni refund."""
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    _webhook(client, b'{"id":"evt_1"}')
    _webhook(client, b'{"id":"evt_2"}')

    assert _pagos(db_session, venta["venta_id"])[0].estado == "APROBADO"
    assert _estado_venta(db_session, venta["venta_id"]) == "COMPLETADA"
    assert _stock(db_session, venta["inventario_id"]) == 8
    assert len(_movimientos(db_session, venta["inventario_id"], "SALIDA_VENTA")) == 1
    assert stripe_fake.reembolsos == {}


# ===========================================================================
# Consulta de estado
# ===========================================================================


def test_consulta_propia_venta(client, admin_headers, db_session, stripe_fake):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    resp = client.get(
        f"/pagos/stripe/ventas/{venta['venta_id']}/estado",
        headers=venta["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["venta_id"] == venta["venta_id"]
    assert body["estado_venta"] == "PENDIENTE"
    assert body["estado_pago"] == "PENDIENTE"
    assert body["payment_intent_id"] == venta["pi_id"]
    assert "client_secret" not in body


def test_consulta_venta_ajena_403(client, admin_headers, db_session, stripe_fake):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _, otro_headers = _crear_cliente(db_session, "consulta_ajena")
    resp = client.get(
        f"/pagos/stripe/ventas/{venta['venta_id']}/estado", headers=otro_headers
    )
    assert resp.status_code == 403, resp.text


def test_consulta_refleja_transicion(client, admin_headers, db_session, stripe_fake):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    amount = int(venta["total"] * 100)
    stripe_fake.evento = _evento(
        "payment_intent.succeeded", venta["pi_id"], amount=amount
    )
    _webhook(client, b'{"id":"evt_ok"}')

    resp = client.get(
        f"/pagos/stripe/ventas/{venta['venta_id']}/estado",
        headers=venta["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado_venta"] == "COMPLETADA"
    assert body["estado_pago"] == "APROBADO"
    assert body["compensacion_estado"] is None


def test_consulta_refleja_compensacion_pendiente(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    venta, _ = _llevar_a_cancelada_aprobado(
        client, admin_headers, db_session, stripe_fake, monkeypatch
    )
    resp = client.get(
        f"/pagos/stripe/ventas/{venta['venta_id']}/estado",
        headers=venta["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado_venta"] == "CANCELADA"
    assert body["estado_pago"] == "APROBADO"
    assert body["compensacion_estado"] == "PENDIENTE"


def test_consulta_refleja_reembolsado(
    client, admin_headers, db_session, stripe_fake, monkeypatch
):
    venta = _preparar_pago(client, admin_headers, db_session, stripe_fake)
    _preparar_succeeded(venta, stripe_fake)
    _instalar_sp_dominio(monkeypatch)
    _webhook(client, b'{"id":"evt_comp"}')

    resp = client.get(
        f"/pagos/stripe/ventas/{venta['venta_id']}/estado",
        headers=venta["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado_venta"] == "CANCELADA"
    assert body["estado_pago"] == "REEMBOLSADO"
    assert body["compensacion_estado"] == "REEMBOLSADO"


# ===========================================================================
# Adaptador Stripe (firma con payload RAW) - SDK mockeado
# ===========================================================================


def test_provider_reembolso_inspecciona_status(monkeypatch):
    """El adaptador devuelve el status real del Refund, no solo su id."""
    from app.modules.pagos.providers import stripe as stripe_module

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    capturado = {}

    def fake_create(**kwargs):
        capturado.update(kwargs)
        return {"id": "re_1", "status": "pending"}

    monkeypatch.setattr(
        stripe_module.stripe_sdk.Refund, "create", staticmethod(fake_create)
    )
    proveedor = stripe_module.StripeProveedor()
    reembolso = proveedor.reembolsar_intencion(
        "pi_1", idempotency_key="refund-venta-1-pi-pi_1"
    )

    assert reembolso.id == "re_1"
    assert reembolso.estado == "pending"
    assert capturado["payment_intent"] == "pi_1"
    assert capturado["idempotency_key"] == "refund-venta-1-pi-pi_1"


def test_provider_webhook_usa_construct_event(monkeypatch):
    from app.modules.pagos.providers import stripe as stripe_module

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_dummy")
    capturado = {}

    def fake_construct(payload, sig_header, secret):
        capturado["payload"] = payload
        capturado["sig"] = sig_header
        capturado["secret"] = secret
        return {
            "id": "evt_1",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_1"}},
        }

    monkeypatch.setattr(
        stripe_module.stripe_sdk.Webhook,
        "construct_event",
        staticmethod(fake_construct),
    )
    proveedor = stripe_module.StripeProveedor()
    evento = proveedor.verificar_webhook(b'{"raw":true}', "sig123")

    assert capturado["payload"] == b'{"raw":true}'
    assert capturado["sig"] == "sig123"
    assert capturado["secret"] == "whsec_dummy"
    assert evento["type"] == "payment_intent.succeeded"


def test_provider_webhook_firma_invalida(monkeypatch):
    from app.modules.pagos.providers import stripe as stripe_module

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_dummy")

    def fake_construct(*args, **kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr(
        stripe_module.stripe_sdk.Webhook,
        "construct_event",
        staticmethod(fake_construct),
    )
    proveedor = stripe_module.StripeProveedor()
    with pytest.raises(StripeWebhookFirmaError):
        proveedor.verificar_webhook(b"{}", "firma_mala")


def test_provider_sin_secret_key_error(monkeypatch):
    from app.modules.pagos.providers import stripe as stripe_module

    monkeypatch.setattr(settings, "stripe_secret_key", None)
    with pytest.raises(StripeConfiguracionError):
        stripe_module.StripeProveedor()


# ===========================================================================
# Regresion CU19 / CU20 / CU21
# ===========================================================================


def test_regresion_cu21_rechaza_metodo_stripe(
    client, admin_headers, db_session, stripe_fake
):
    """CU21 sigue siendo exclusivo de pagos presenciales."""
    cliente, headers = _crear_cliente(db_session, "reg21")
    sucursal = _sucursales_activas(db_session)[0]
    venta = _venta_directa(
        db_session, sucursal_id=sucursal, cliente_id=cliente.id, canal="PRESENCIAL"
    )
    # El cliente no tiene permiso para CU21 (403), y STRIPE no es metodo valido.
    resp = client.post(
        "/pagos/presencial",
        json={"venta_id": venta.id, "metodo": "STRIPE"},
        headers=headers,
    )
    assert resp.status_code in (403, 422), resp.text
    assert _pagos(db_session, venta.id) == []


def test_regresion_cu19_no_crea_pago(
    client, admin_headers, db_session, stripe_fake
):
    venta = _venta_digital(client, admin_headers, db_session)
    assert _pagos(db_session, venta["venta_id"]) == []
    assert _estado_venta(db_session, venta["venta_id"]) == "PENDIENTE"
