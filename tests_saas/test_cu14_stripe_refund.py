"""Pruebas dirigidas del adaptador Stripe SaaS para reembolsos CU14.

No contactan Stripe: sustituyen el proveedor y la sesión SQL por fakes. La
autoridad de negocio (importe total, moneda, idempotencia y estados) vive en
``fashionstore_private.sp_evento_stripe_tenant`` y se cubre además en el
validador PostgreSQL 17 aislado.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.pagos.providers.base import Reembolso
from app.modules.pagos.services import tenant_stripe_service as svc


class _PgError(Exception):
    def __init__(self, sqlstate: str):
        super().__init__("error simulado")
        self.sqlstate = sqlstate


def _dbapi(sqlstate: str) -> DBAPIError:
    return DBAPIError("stmt", {}, _PgError(sqlstate))


class _Result:
    def __init__(self, *, scalar=None, mapping=None):
        self._scalar = scalar
        self._mapping = mapping

    def scalar_one_or_none(self):
        return self._scalar

    def mappings(self):
        return self

    def one(self):
        return self._mapping


class _FakeDB:
    def __init__(self, procedure_errors=None, procesado=1):
        self.procedure_errors = list(procedure_errors or [])
        self.procesado = procesado
        self.procedure_calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query, params=None):
        sql = str(query)
        if "pg_roles" in sql:
            return _Result(scalar=True)
        self.procedure_calls.append((sql, dict(params or {})))
        if self.procedure_errors:
            raise self.procedure_errors.pop(0)
        return _Result(mapping={"p_procesado": self.procesado})

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeProveedor:
    def __init__(self, evento: dict, estado_refund: str = "succeeded"):
        self.evento = evento
        self.estado_refund = estado_refund
        self.refunds: list[dict] = []

    def verificar_webhook(self, payload: bytes, firma: str) -> dict:
        return self.evento

    def reembolsar_intencion(self, intencion_id: str, *, idempotency_key: str):
        self.refunds.append({"pi": intencion_id, "key": idempotency_key})
        return Reembolso(id="re_test", estado=self.estado_refund)


def _evento(tipo: str, objeto: dict) -> dict:
    return {"id": "evt_test", "type": tipo, "data": {"object": objeto}}


class _EventoStripe:
    """Imita stripe.Event: no implementa .get(), solo .to_dict()."""

    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


def _proveedor(monkeypatch, evento: dict, **kwargs) -> _FakeProveedor:
    fake = _FakeProveedor(evento, **kwargs)
    monkeypatch.setattr(svc, "StripeProveedor", lambda: fake)
    return fake


def _webhook(db: _FakeDB):
    return svc.PagoStripeTenantService.webhook(db, b"{}", "firma-test")


def test_webhook_normaliza_stripe_event_a_dict(monkeypatch):
    """El SDK real devuelve stripe.Event; debe normalizarse antes de .get()."""
    db = _FakeDB(procesado=0)
    fake = _FakeProveedor(_EventoStripe(_evento(
        "payment_intent.payment_failed",
        {"id": "pi_unknown", "amount": 1000, "currency": "bob"},
    )))
    monkeypatch.setattr(svc, "StripeProveedor", lambda: fake)

    respuesta = _webhook(db)

    assert respuesta.evento == "payment_intent.payment_failed"
    assert respuesta.procesado is False
    assert db.procedure_calls[0][1] == {
        "pi": "pi_unknown",
        "evento": "payment_intent.payment_failed",
        "monto": 1000,
        "moneda": "bob",
    }


def test_charge_refunded_total_usa_evento_interno(monkeypatch):
    db = _FakeDB()
    fake = _proveedor(monkeypatch, _evento("charge.refunded", {
        "id": "ch_1", "payment_intent": "pi_total", "amount": 10000,
        "amount_refunded": 10000, "refunded": True, "currency": "bob",
    }))

    respuesta = _webhook(db)

    assert respuesta.evento == "charge.refunded"
    assert respuesta.procesado is True
    assert len(db.procedure_calls) == 1
    _, params = db.procedure_calls[0]
    assert params == {
        "pi": "pi_total", "evento": "payment_intent.refunded",
        "monto": 10000, "moneda": "bob",
    }
    assert fake.refunds == []


def test_charge_refunded_parcial_no_toca_sql(monkeypatch):
    db = _FakeDB()
    _proveedor(monkeypatch, _evento("charge.refunded", {
        "id": "ch_1", "payment_intent": "pi_total", "amount": 10000,
        "amount_refunded": 5000, "refunded": False, "currency": "bob",
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is False
    assert db.procedure_calls == []
    assert db.commits == 0


@pytest.mark.parametrize("tipo", ["refund.created", "refund.updated"])
@pytest.mark.parametrize(
    "estado", ["pending", "requires_action", "failed", "canceled"]
)
def test_refund_no_exitoso_no_toca_sql(monkeypatch, tipo, estado):
    db = _FakeDB()
    _proveedor(monkeypatch, _evento(tipo, {
        "id": "re_1", "payment_intent": "pi_total", "amount": 10000,
        "currency": "bob", "status": estado,
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is False
    assert db.procedure_calls == []


@pytest.mark.parametrize("tipo", ["refund.created", "refund.updated"])
def test_refund_exitoso_total_usa_evento_interno(monkeypatch, tipo):
    db = _FakeDB()
    _proveedor(monkeypatch, _evento(tipo, {
        "id": "re_1", "payment_intent": "pi_total", "amount": 10000,
        "currency": "bob", "status": "succeeded",
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is True
    assert db.procedure_calls[0][1] == {
        "pi": "pi_total", "evento": "payment_intent.refunded",
        "monto": 10000, "moneda": "bob",
    }


@pytest.mark.parametrize("tipo", ["refund.created", "refund.updated"])
def test_refund_exitoso_parcial_es_rechazado_sin_marcar(monkeypatch, tipo):
    db = _FakeDB(procedure_errors=[_dbapi("23514")])
    _proveedor(monkeypatch, _evento(tipo, {
        "id": "re_1", "payment_intent": "pi_total", "amount": 5000,
        "currency": "bob", "status": "succeeded",
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is False
    assert db.rollbacks == 1
    assert db.commits == 0
    assert len(db.procedure_calls) == 1


def test_succeeded_con_fallo_de_dominio_compensa_refund_total(monkeypatch):
    db = _FakeDB(procedure_errors=[_dbapi("23514")])
    fake = _proveedor(monkeypatch, _evento("payment_intent.succeeded", {
        "id": "pi_total", "amount": 10000, "amount_received": 10000,
        "currency": "bob",
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is True
    assert fake.refunds == [{"pi": "pi_total", "key": "refund-pi_total"}]
    assert len(db.procedure_calls) == 2
    assert db.procedure_calls[1][1] == {
        "pi": "pi_total", "evento": "payment_intent.refunded",
        "monto": 10000, "moneda": "bob",
    }
    assert db.commits == 1


def test_succeeded_con_refund_pendiente_no_marca_reembolsado(monkeypatch):
    db = _FakeDB(procedure_errors=[_dbapi("23514")])
    fake = _proveedor(
        monkeypatch,
        _evento("payment_intent.succeeded", {
            "id": "pi_total", "amount": 10000, "amount_received": 10000,
            "currency": "bob",
        }),
        estado_refund="pending",
    )

    with pytest.raises(DBAPIError):
        _webhook(db)

    assert len(fake.refunds) == 1
    assert len(db.procedure_calls) == 1
    assert db.commits == 0


def test_refund_duplicado_no_duplica_reembolso(monkeypatch):
    db = _FakeDB()
    fake = _proveedor(monkeypatch, _evento("charge.refunded", {
        "id": "ch_1", "payment_intent": "pi_total", "amount": 10000,
        "amount_refunded": 10000, "refunded": True, "currency": "bob",
    }))

    assert _webhook(db).procesado is True
    assert _webhook(db).procesado is True

    assert fake.refunds == []
    assert len(db.procedure_calls) == 2
    assert all(
        params["evento"] == "payment_intent.refunded"
        for _, params in db.procedure_calls
    )


def test_evento_no_soportado_no_toca_sql(monkeypatch):
    db = _FakeDB()
    _proveedor(monkeypatch, _evento("payment_intent.created", {
        "id": "pi_total", "amount": 10000, "currency": "bob",
    }))

    respuesta = _webhook(db)

    assert respuesta.procesado is False
    assert db.procedure_calls == []
