"""Pruebas del cierre preventivo de rutas legacy del Bloque 3 / Paso 1.

No abre conexiones SQL ni consulta Supabase. Los endpoints de prueba son
ficticios y no necesitan credenciales ni recursos comerciales.
"""
import os

# Mismos valores ficticios que tests_saas/test_auth_tenant_unit.py.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.saas_route_gate import BloqueoRutasLegacySaaS, _ruta_saas_permitida


def _app_ficticia() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)

    @app.get("/")
    def raiz():
        return {"ok": True}

    @app.get("/health")
    def salud():
        return {"status": "ok"}

    @app.get("/health/db")
    def salud_db_legada():
        return {"datos": "NO DEBE SER VISIBLE EN SAAS"}

    @app.post("/auth/tenant/login")
    def tenant_login():
        return {"tipo": "empresa"}

    @app.get("/auth/tenant/me")
    def tenant_me():
        return {"tipo": "empresa"}

    @app.get("/productos")
    def listar_productos_legacy():
        return {"datos": "CATALOGO LEGADO SIN FILTRO"}

    @app.post("/ventas")
    def venta_legacy():
        return {"datos": "VENTA LEGADA SIN FILTRO"}

    @app.get("/auth/clientes/me/perfil")
    def perfil_legacy():
        return {"datos": "PERFIL LEGADO"}

    @app.websocket("/ws/legacy")
    async def websocket_legacy(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("MENSAJE LEGADO DE PRUEBA")

    return app


@pytest.mark.parametrize(
    "metodo,ruta",
    [
        ("GET", "/productos"),
        ("POST", "/ventas"),
        ("GET", "/auth/clientes/me/perfil"),
        ("GET", "/health/db"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/auth/tenant/me/extra"),
        ("GET", "/auth/tenant/login"),
        ("POST", "/auth/tenant/me"),
        ("GET", "/productos?empresa_id=1"),
    ],
)
def test_modo_corte_saas_bloquea_rutas_legacy_y_falsos_prefijos(
    monkeypatch, metodo, ruta
):
    # 5C1: el modo corte es SaaS con la superficie legacy apagada.
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    with TestClient(_app_ficticia()) as client:
        respuesta = client.request(metodo, ruta)
        assert respuesta.status_code == 503
        assert respuesta.json()["detail"].startswith("Ruta no disponible")


@pytest.mark.parametrize(
    "metodo,ruta",
    [
        ("GET", "/"),
        ("GET", "/health"),
        ("POST", "/auth/tenant/login"),
        ("GET", "/auth/tenant/me"),
    ],
)
def test_modo_saas_deja_solo_allowlist_exacta(monkeypatch, metodo, ruta):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    with TestClient(_app_ficticia()) as client:
        assert client.request(metodo, ruta).status_code == 200


def test_modo_legacy_no_rompe_endpoints_del_primer_parcial(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    with TestClient(_app_ficticia()) as client:
        assert client.get("/productos").status_code == 200
        assert client.post("/ventas").status_code == 200
        assert client.get("/auth/clientes/me/perfil").status_code == 200
        assert client.get("/health/db").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/no-existe").status_code == 404


def test_guard_instalado_en_app_real_y_bloqueo_sin_tocar_db(monkeypatch):
    # Importar la app real comprueba que no olvidamos registrar el middleware.
    from app.main import app

    assert any(
        middleware.cls is BloqueoRutasLegacySaaS
        for middleware in app.user_middleware
    )

    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    with TestClient(app) as client:
        for ruta in ("/productos", "/categorias", "/health/db", "/openapi.json"):
            respuesta = client.get(ruta)
            assert respuesta.status_code == 503, ruta
        assert client.get("/health").status_code == 200


def test_modo_saas_bloquea_websocket_legacy(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    with TestClient(_app_ficticia()) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/legacy") as websocket:
                websocket.receive_text()
    assert exc.value.code == 1008


def test_modo_legacy_conserva_websocket(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    with TestClient(_app_ficticia()) as client:
        with client.websocket_connect("/ws/legacy") as websocket:
            assert websocket.receive_text() == "MENSAJE LEGADO DE PRUEBA"


# ---------------------------------------------------------------------------
# 5C1: coexistencia de superficies (SaaS + legacy en el mismo proceso)
# ---------------------------------------------------------------------------


def test_coexistencia_abre_rutas_heredadas_sin_romper_allowlist_tenant(
    monkeypatch,
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", True)

    with TestClient(_app_ficticia()) as client:
        # Superficie legacy: responde como en el primer parcial.
        assert client.get("/productos").status_code == 200
        assert client.post("/ventas").status_code == 200
        assert client.get("/auth/clientes/me/perfil").status_code == 200
        assert client.get("/no-existe").status_code == 404

        # Diagnostico/documentacion siguen cerrados aunque SaaS este activo.
        for ruta in ("/health/db", "/openapi.json", "/docs"):
            assert client.get(ruta).status_code == 503, ruta

        # La allowlist tenant NO se relaja: prefijos falsos siguen en 503.
        assert client.get("/auth/tenant/me/extra").status_code == 503
        assert client.get("/auth/tenant/login").status_code == 503
        assert client.post("/auth/tenant/me").status_code == 503

        # Contratos tenant validos intactos.
        assert client.get("/auth/tenant/me").status_code == 200
        assert client.post("/auth/tenant/login").status_code == 200


def test_coexistencia_conserva_websocket_heredado(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", True)

    with TestClient(_app_ficticia()) as client:
        with client.websocket_connect("/ws/legacy") as websocket:
            assert websocket.receive_text() == "MENSAJE LEGADO DE PRUEBA"


def test_coexistencia_no_confunde_prefijo_parecido_con_superficie_tenant(
    monkeypatch,
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)

    with TestClient(_app_ficticia()) as client:
        # `/tenants-legacy` es legacy, no tenant: se bloquea por superficie.
        assert client.get("/tenants-legacy").status_code == 503


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/tenant/public/alpha/catalogo"),
        ("GET", "/tenant/public/alpha/catalogo/1"),
        ("GET", "/tenant/public/alpha/sucursales"),
        ("GET", "/tenant/reportes/resumen"),
        ("POST", "/tenant/reportes/generativo"),
        ("POST", "/tenant/asistencia-inteligente/recomendaciones"),
        ("POST", "/tenant/vestidor-virtual/sesiones"),
        ("PATCH", "/tenant/vestidor-virtual/pruebas/9/finalizar"),
        ("GET", "/tenant/merchandising/temporadas"),
        ("POST", "/tenant/merchandising/promociones"),
        ("PUT", "/tenant/merchandising/colecciones/7/productos"),
    ],
)
def test_t05_t06_allowlist_opens_only_audited_exact_routes(method, path):
    assert _ruta_saas_permitida(method, path) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/tenant/public/alpha/unknown"),
        ("GET", "/tenant/public/Alpha/catalogo"),
        ("GET", "/tenant/public/alpha/catalogo/0"),
        ("DELETE", "/tenant/merchandising/temporadas/1"),
        ("GET", "/tenant/reportes/resumen/extra"),
        ("POST", "/tenant/vestidor-virtual/sesiones/0/pruebas"),
    ],
)
def test_t05_t06_allowlist_rejects_wrong_methods_and_false_prefixes(method, path):
    assert _ruta_saas_permitida(method, path) is False
