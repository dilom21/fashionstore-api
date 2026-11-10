"""Pruebas HTTP aisladas de la superficie tenant CU14/CU23/CU24."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db, get_webhook_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.main import app
from app.modules.ventas.services.historial_service import HistorialComprasService
from app.modules.ventas.services.comprobante_service import ComprobanteVentaService
from app.modules.ventas.services.tenant_digital_service import VentaDigitalTenantService
from app.modules.pagos.services.tenant_stripe_service import PagoStripeTenantService


TENANT = TenantContext(
    usuario_id=71, empresa_id=9, empresa_slug="empresa-nueve",
    empresa_nombre="Empresa Nueve", rol_id=4, rol_nombre="CLIENTE",
    contexto="cliente", plan_codigo="PRO", permite_reportes_ia=False,
)


def _db():
    yield SimpleNamespace()


def _cliente():
    return TENANT


def _client(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_tenant_cliente] = _cliente
    return TestClient(app)


def _cerrar():
    app.dependency_overrides.clear()


def test_compra_digital_usa_tenant_y_no_acepta_identidades(monkeypatch):
    client = _client(monkeypatch)
    visto = {}
    monkeypatch.setattr(VentaDigitalTenantService, "crear", staticmethod(
        lambda db, tenant, datos: visto.update(tenant=tenant, datos=datos) or {
            "venta_id": 3, "carrito_id": datos.carrito_id, "cliente_id": 8,
            "sucursal_id": 2, "sucursal_nombre": "Centro", "canal": "WEB",
            "estado": "PENDIENTE", "fecha_hora": "2026-11-09T12:00:00",
            "total": "50.00", "items": [], "cantidad_total_unidades": 0,
        }
    ))
    try:
        response = client.post("/tenant/ventas/digital", json={"carrito_id": 4, "canal": "WEB"})
        assert response.status_code == 201
        assert visto["tenant"] == TENANT
        assert response.json()["estado"] == "PENDIENTE"
        assert client.post("/tenant/ventas/digital", json={
            "carrito_id": 4, "canal": "WEB", "empresa_id": 999,
        }).status_code == 422
    finally:
        _cerrar()


def test_historial_y_detalle_delegan_identidad_tenant(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(HistorialComprasService, "consultar_historial_tenant", staticmethod(
        lambda db, tenant, **kw: {"items": [], "pagina": 1, "tamano_pagina": 20,
                                  "total_registros": 0, "total_paginas": 0}
    ))
    monkeypatch.setattr(HistorialComprasService, "obtener_detalle_compra_tenant", staticmethod(
        lambda db, tenant, venta_id: {
            "venta_id": venta_id, "fecha_hora": "2026-11-09T12:00:00",
            "canal": "WEB", "estado": "COMPLETADA", "total": "10.00",
            "carrito_id": 1, "reserva_id": None,
            "sucursal": {"id": 2, "nombre": "Centro", "direccion": "X", "telefono": None},
            "pago": None, "items": [], "cantidad_total_unidades": 0,
        }
    ))
    try:
        assert client.get("/tenant/ventas/historial").status_code == 200
        assert client.get("/tenant/ventas/historial/7").json()["venta_id"] == 7
    finally:
        _cerrar()


def test_comprobante_cliente_reutiliza_servicio(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(ComprobanteVentaService, "generar_comprobante_cliente_tenant", staticmethod(
        lambda db, tenant, venta_id: {
            "venta_id": venta_id, "fecha_hora": "2026-11-09T12:00:00",
            "canal": "WEB", "estado_venta": "COMPLETADA", "total": "10.00",
            "carrito_id": 1, "reserva_id": None, "cliente": None, "empleado": None,
            "sucursal": {"id": 2, "nombre": "Centro", "direccion": "X", "telefono": None},
            "pago": {"pago_id": 4, "fecha_hora": "2026-11-09T12:01:00", "monto": "10.00",
                     "metodo": "TARJETA", "estado": "APROBADO", "referencia_transaccion": "pi_test", "pasarela": "STRIPE"},
            "items": [], "cantidad_total_unidades": 0,
        }
    ))
    try:
        assert client.get("/tenant/ventas/historial/7/comprobante").status_code == 200
    finally:
        _cerrar()


def test_rutas_nuevas_fallan_cerradas_con_flag_apagado(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", False)
    client = TestClient(app)
    assert client.get("/tenant/ventas/historial").status_code == 503
    assert client.post("/tenant/ventas/digital", json={"carrito_id": 1, "canal": "WEB"}).status_code == 503


def test_cliente_sin_jwt_no_accede(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    assert TestClient(app).get("/tenant/ventas/historial").status_code == 401


def test_stripe_intencion_y_estado_exigen_cliente_tenant(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(PagoStripeTenantService, "crear_intencion", staticmethod(
        lambda db, tenant, datos: {"venta_id": datos.venta_id, "pago_id": 8,
          "payment_intent_id": "pi_test", "client_secret": "pi_test_secret_x",
          "monto": "25.00", "moneda": "bob", "estado_pago": "PENDIENTE"}
    ))
    monkeypatch.setattr(PagoStripeTenantService, "estado", staticmethod(
        lambda db, tenant, venta_id: {"venta_id": venta_id, "estado_venta": "PENDIENTE",
          "pago_id": 8, "estado_pago": "PENDIENTE", "payment_intent_id": "pi_test",
          "compensacion_estado": None}
    ))
    try:
        assert client.post("/tenant/pagos/stripe/intencion", json={"venta_id": 4}).status_code == 200
        assert client.get("/tenant/pagos/stripe/ventas/4/estado").status_code == 200
    finally:
        _cerrar()


def test_webhook_conserva_cuerpo_raw_y_exige_firma(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    monkeypatch.setattr(settings, "database_webhook_url", "postgresql://configurado")
    app.dependency_overrides[get_webhook_db] = _db
    visto = {}
    monkeypatch.setattr(PagoStripeTenantService, "webhook", staticmethod(
        lambda db, payload, firma: visto.update(payload=payload, firma=firma) or
          {"recibido": True, "evento": "payment_intent.succeeded", "procesado": True}
    ))
    client = TestClient(app)
    try:
        assert client.post("/tenant/pagos/stripe/webhook", content=b'{"id":"evt"}').status_code == 400
        response = client.post("/tenant/pagos/stripe/webhook", content=b'{"id":"evt"}',
                               headers={"stripe-signature": "firma-test"})
        assert response.status_code == 200
        assert visto == {"payload": b'{"id":"evt"}', "firma": "firma-test"}
    finally:
        _cerrar()
