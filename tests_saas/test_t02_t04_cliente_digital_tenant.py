"""T02/T04 customer flow: ownership, idempotency, payment and history."""
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.autenticacion_seguridad.models.models import Cliente, Rol, Usuario
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import Categoria, Color, Producto, Talla, Temporada, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.pagos.models.models import Pago
from app.modules.pagos.providers.base import IntencionPago, Reembolso
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.ventas.models.models import Venta

EMPRESA_A, EMPRESA_B = 100, 200


def tenant(empresa=EMPRESA_A, usuario=11):
    return TenantContext(
        usuario_id=usuario, empresa_id=empresa,
        empresa_slug="empresa-a" if empresa == EMPRESA_A else "empresa-b",
        empresa_nombre="Empresa A" if empresa == EMPRESA_A else "Empresa B",
        rol_id=1, rol_nombre="CLIENTE", contexto="cliente",
        plan_codigo="PROFESIONAL", permite_reportes_ia=True,
    )


class StripeFalso:
    def __init__(self):
        self.creadas = []
        self.evento = None

    def crear_intencion(self, *, monto, moneda, metadata, idempotency_key):
        self.creadas.append((monto, moneda, metadata, idempotency_key))
        return IntencionPago("pi_tenant_1", "secret_test", "requires_payment_method", monto, moneda)

    def recuperar_intencion(self, intencion_id):
        return IntencionPago(intencion_id, "secret_test", "requires_payment_method", 10000, "bob")

    def cancelar_intencion(self, intencion_id):
        return None

    def reembolsar_intencion(self, intencion_id, *, idempotency_key):
        return Reembolso("re_1", "succeeded")

    def verificar_webhook(self, payload, firma):
        return self.evento


def _sembrar(conn):
    ahora = datetime.now(timezone.utc)
    conn.exec_driver_sql(
        "INSERT INTO empresa (id,slug,nombre_comercial,estado) VALUES "
        "(100,'empresa-a','Empresa A',1),(200,'empresa-b','Empresa B',1)"
    )
    conn.execute(insert(Rol.__table__), [{"id": 1, "nombre": "CLIENTE", "descripcion": None, "estado": True}])
    conn.execute(insert(Usuario.__table__), [
        {"id": 11, "rol_id": 1, "correo": "a@example.test", "password_hash": "x", "fecha_creacion": ahora, "estado": True},
        {"id": 21, "rol_id": 1, "correo": "b@example.test", "password_hash": "x", "fecha_creacion": ahora, "estado": True},
    ])
    conn.execute(insert(Cliente.__table__), [
        {"id": 101, "empresa_id": 100, "usuario_id": 11, "nombre": "Ana", "apellido": "A", "telefono": None, "sexo": None, "ci": "A1", "fecha_nacimiento": None, "estado": True},
        {"id": 201, "empresa_id": 200, "usuario_id": 21, "nombre": "Bea", "apellido": "B", "telefono": None, "sexo": None, "ci": "B1", "fecha_nacimiento": None, "estado": True},
    ])
    conn.execute(insert(Ciudad.__table__), [{"id": 1, "nombre": "Ciudad", "estado": True}])
    conn.execute(insert(Sucursal.__table__), [
        {"id": 501, "empresa_id": 100, "ciudad_id": 1, "nombre": "Sucursal A", "direccion": "A", "telefono": None, "estado": True},
        {"id": 601, "empresa_id": 200, "ciudad_id": 1, "nombre": "Sucursal B", "direccion": "B", "telefono": None, "estado": True},
    ])
    conn.execute(insert(Categoria.__table__), [
        {"id": 301, "empresa_id": 100, "nombre": "Cat A", "descripcion": None, "estado": True},
        {"id": 401, "empresa_id": 200, "nombre": "Cat B", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Talla.__table__), [{"id": 1, "nombre": "M", "estado": True}])
    conn.execute(insert(Color.__table__), [{"id": 1, "nombre": "Negro", "estado": True}])
    conn.execute(insert(Producto.__table__), [
        {"id": 1001, "empresa_id": 100, "categoria_id": 301, "nombre": "Producto A", "descripcion": None, "precio": Decimal("50.00"), "estado": True},
        {"id": 2001, "empresa_id": 200, "categoria_id": 401, "nombre": "Producto B", "descripcion": None, "precio": Decimal("70.00"), "estado": True},
    ])
    conn.execute(insert(VarianteProducto.__table__), [
        {"id": 1101, "empresa_id": 100, "producto_id": 1001, "talla_id": 1, "color_id": 1, "sku": "A-1", "estado": True},
        {"id": 2101, "empresa_id": 200, "producto_id": 2001, "talla_id": 1, "color_id": 1, "sku": "B-1", "estado": True},
    ])
    conn.execute(insert(Temporada.__table__), [
        {"id": 701, "empresa_id": 100, "nombre": "2026 A", "fecha_inicio": date(2026, 1, 1), "fecha_fin": date(2026, 12, 31), "estado": True},
        {"id": 801, "empresa_id": 200, "nombre": "2026 B", "fecha_inicio": date(2026, 1, 1), "fecha_fin": date(2026, 12, 31), "estado": True},
    ])
    conn.execute(insert(Inventario.__table__), [
        {"id": 901, "empresa_id": 100, "sucursal_id": 501, "variante_producto_id": 1101, "temporada_id": 701, "stock_actual": 10, "stock_reservado": 0, "fecha_actualizacion": ahora},
        {"id": 902, "empresa_id": 200, "sucursal_id": 601, "variante_producto_id": 2101, "temporada_id": 801, "stock_actual": 10, "stock_reservado": 0, "fecha_actualizacion": ahora},
    ])
    conn.execute(insert(Carrito.__table__), [
        {"id": 10001, "empresa_id": 100, "cliente_id": 101, "sucursal_id": 501, "fecha_creacion": ahora, "fecha_actualizacion": ahora, "estado": "ACTIVO"},
        {"id": 20001, "empresa_id": 200, "cliente_id": 201, "sucursal_id": 601, "fecha_creacion": ahora, "fecha_actualizacion": ahora, "estado": "ACTIVO"},
    ])
    conn.execute(insert(DetalleCarrito.__table__), [
        {"id": 11001, "empresa_id": 100, "carrito_id": 10001, "inventario_id": 901, "cantidad": 2},
        {"id": 21001, "empresa_id": 200, "carrito_id": 20001, "inventario_id": 902, "cantidad": 1},
    ])
    conn.execute(insert(Reserva.__table__), [
        {"id": 12001, "empresa_id": 100, "cliente_id": 101, "sucursal_id": 501, "carrito_id": None, "fecha_reserva": ahora, "fecha_atencion": ahora + timedelta(days=1), "estado": "PENDIENTE", "observacion": "A"},
        {"id": 22001, "empresa_id": 200, "cliente_id": 201, "sucursal_id": 601, "carrito_id": None, "fecha_reserva": ahora, "fecha_atencion": ahora + timedelta(days=1), "estado": "PENDIENTE", "observacion": "B"},
    ])
    conn.execute(insert(DetalleReserva.__table__), [
        {"id": 13001, "empresa_id": 100, "reserva_id": 12001, "inventario_id": 901, "cantidad": 1},
        {"id": 23001, "empresa_id": 200, "reserva_id": 22001, "inventario_id": 902, "cantidad": 1},
    ])


@pytest.fixture()
def entorno(monkeypatch):
    motor = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(motor)
    with motor.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE empresa (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, nombre_comercial TEXT, estado BOOLEAN)")
        _sembrar(conn)
    fabrica = sessionmaker(bind=motor, expire_on_commit=False)
    actual = {"tenant": tenant()}
    stripe = StripeFalso()
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    monkeypatch.setattr("app.modules.pagos.services.tenant_electronico_service.obtener_proveedor", lambda: stripe)
    monkeypatch.setattr(
        "app.modules.pagos.services.tenant_electronico_service.PagoRepository.confirmar_venta",
        lambda db, venta_id, _usuario_id: db.execute(
            update(Venta.__table__).where(Venta.id == venta_id).values(estado="COMPLETADA")
        ),
    )

    def db():
        with fabrica() as sesion:
            yield sesion

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_tenant_context] = lambda: actual["tenant"]
    try:
        with TestClient(app) as client:
            yield client, actual, stripe, fabrica
    finally:
        app.dependency_overrides.clear()
        motor.dispose()


def test_reservas_cliente_aisladas_por_identidad_tenant(entorno):
    client, actual, _stripe, _db = entorno
    assert [x["reserva_id"] for x in client.get("/tenant/cliente/reservas").json()] == [12001]
    actual["tenant"] = tenant(EMPRESA_B, 21)
    assert [x["reserva_id"] for x in client.get("/tenant/cliente/reservas").json()] == [22001]


def test_checkout_idempotente_y_no_acepta_carrito_ajeno(entorno):
    client, _actual, _stripe, _db = entorno
    clave = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    headers = {"Idempotency-Key": clave}
    primera = client.post("/tenant/ventas/digital", json={"carrito_id": 10001, "canal": "WEB"}, headers=headers)
    assert primera.status_code == 201, primera.text
    assert primera.json()["total"] == "100.00"
    assert primera.json()["idempotente"] is False
    replay = client.post("/tenant/ventas/digital", json={"carrito_id": 10001, "canal": "WEB"}, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["venta_id"] == primera.json()["venta_id"]
    assert replay.json()["idempotente"] is True
    conflicto = client.post("/tenant/ventas/digital", json={"carrito_id": 20001, "canal": "WEB"}, headers={"Idempotency-Key": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"})
    assert conflicto.status_code == 404
    distinta = client.post("/tenant/ventas/digital", json={"carrito_id": 10001, "canal": "MOVIL"}, headers=headers)
    assert distinta.status_code == 409


def test_intencion_stripe_mock_metadata_empresa_estado_e_historial(entorno):
    client, _actual, stripe, fabrica = entorno
    venta = client.post(
        "/tenant/ventas/digital",
        json={"carrito_id": 10001, "canal": "WEB"},
        headers={"Idempotency-Key": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
    ).json()
    pago = client.post(
        "/tenant/pagos/stripe/intencion", json={"venta_id": venta["venta_id"]},
        headers={"Idempotency-Key": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"},
    )
    assert pago.status_code == 201
    assert pago.json()["client_secret"] == "secret_test"
    metadata = stripe.creadas[0][2]
    assert metadata["empresa_id"] == "100"
    assert metadata["empresa_slug"] == "empresa-a"
    assert metadata["usuario_id"] == "11"
    estado = client.get(f"/tenant/pagos/stripe/ventas/{venta['venta_id']}/estado").json()
    assert estado["estado_pago"] == "PENDIENTE"
    assert [x["venta_id"] for x in client.get("/tenant/ventas/historial").json()["items"]] == [venta["venta_id"]]

    with fabrica.begin() as db:
        db.execute(update(Venta.__table__).where(Venta.id == venta["venta_id"]).values(estado="COMPLETADA"))
        db.execute(update(Pago.__table__).where(Pago.venta_id == venta["venta_id"]).values(estado="APROBADO"))
    comprobante = client.get(f"/tenant/ventas/{venta['venta_id']}/comprobante")
    assert comprobante.status_code == 200
    assert comprobante.json()["pago"]["estado"] == "APROBADO"


def test_webhook_firmado_rechaza_slug_de_otra_empresa(entorno):
    client, _actual, stripe, _db = entorno
    stripe.evento = {
        "id": "evt_1", "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_tenant_1", "metadata": {
            "empresa_id": "100", "empresa_slug": "empresa-a",
            "usuario_id": "11", "cliente_id": "101", "venta_id": "1",
        }}},
    }
    respuesta = client.post(
        "/tenant/public/empresa-b/pagos/stripe/webhook",
        content=b"firmado", headers={"stripe-signature": "firma-test"},
    )
    assert respuesta.status_code == 400
    assert "empresa" in respuesta.json()["detail"].lower()


def test_webhook_exitoso_es_idempotente_y_valida_monto_moneda(entorno):
    client, _actual, stripe, _db = entorno
    venta = client.post(
        "/tenant/ventas/digital",
        json={"carrito_id": 10001, "canal": "WEB"},
        headers={"Idempotency-Key": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"},
    ).json()
    client.post(
        "/tenant/pagos/stripe/intencion", json={"venta_id": venta["venta_id"]},
        headers={"Idempotency-Key": "ffffffff-ffff-4fff-8fff-ffffffffffff"},
    )
    stripe.evento = {
        "id": "evt_ok", "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": "pi_tenant_1", "amount_received": 10000, "currency": "bob",
            "metadata": {"empresa_id": "100", "empresa_slug": "empresa-a",
                         "usuario_id": "11", "cliente_id": "101",
                         "venta_id": str(venta["venta_id"])},
        }},
    }
    headers = {"stripe-signature": "firma-test"}
    primera = client.post("/tenant/public/empresa-a/pagos/stripe/webhook", content=b"ok", headers=headers)
    assert primera.status_code == 200, primera.text
    assert primera.json()["procesado"] is True
    replay = client.post("/tenant/public/empresa-a/pagos/stripe/webhook", content=b"ok", headers=headers)
    assert replay.status_code == 200
    estado = client.get(f"/tenant/pagos/stripe/ventas/{venta['venta_id']}/estado").json()
    assert estado["estado_pago"] == "APROBADO"
    assert estado["estado_venta"] == "COMPLETADA"

    stripe.evento["data"]["object"]["amount_received"] = 9999
    monto = client.post("/tenant/public/empresa-a/pagos/stripe/webhook", content=b"ok", headers=headers)
    # Incluso un payload posterior debe conservar la validación económica.
    assert monto.status_code == 409


def test_webhook_fallido_no_degrada_pago_aprobado_y_firma_es_400(entorno):
    client, _actual, stripe, fabrica = entorno
    with fabrica.begin() as db:
        db.execute(insert(Venta.__table__).values(
            id=30001, empresa_id=100, cliente_id=101, empleado_id=None,
            sucursal_id=501, reserva_id=None, carrito_id=None,
            fecha_hora=datetime.now(timezone.utc), canal="WEB",
            estado="PENDIENTE", total=Decimal("100.00"),
        ))
        db.execute(insert(Pago.__table__).values(
            id=31001, empresa_id=100, venta_id=30001,
            fecha_hora=datetime.now(timezone.utc), monto=Decimal("100.00"),
            metodo="TARJETA", estado="PENDIENTE",
            referencia_transaccion="pi_failed", pasarela="STRIPE",
        ))
    stripe.evento = {
        "id": "evt_fail", "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_failed", "metadata": {
            "empresa_id": "100", "empresa_slug": "empresa-a",
            "usuario_id": "11", "cliente_id": "101", "venta_id": "30001",
        }}},
    }
    fallo = client.post(
        "/tenant/public/empresa-a/pagos/stripe/webhook", content=b"fail",
        headers={"stripe-signature": "firma-test"},
    )
    assert fallo.status_code == 200
    with fabrica() as db:
        assert db.get(Pago, 31001).estado == "RECHAZADO"

    sin_firma = client.post("/tenant/public/empresa-a/pagos/stripe/webhook", content=b"fail")
    assert sin_firma.status_code == 400


def test_gate_y_auth_no_abren_prefijos(entorno):
    client, _actual, _stripe, _db = entorno
    assert client.get("/tenant/ventas/historial/").status_code == 503
    assert client.get("/tenant/pagos/stripe/ventas/0/estado").status_code == 503
    assert client.post("/tenant/public/Empresa_A/pagos/stripe/webhook").status_code == 503
