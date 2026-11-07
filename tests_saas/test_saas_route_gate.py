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
from fastapi import FastAPI, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.saas_route_gate import BloqueoRutasLegacySaaS


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

    @app.post("/auth/login")
    def login_legado():
        return {"datos": "LOGIN LEGADO"}

    @app.post("/auth/tenant/login")
    def tenant_login():
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    @app.get("/auth/tenant/me")
    def tenant_me():
        raise HTTPException(status_code=401, detail="Sin JWT")

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
        ("POST", "/auth/login"),
        ("GET", "/auth/tenant/me/extra"),
        ("GET", "/auth/tenant/login"),
        ("POST", "/auth/tenant/me"),
        ("GET", "/productos?empresa_id=1"),
    ],
)
def test_modo_saas_bloquea_rutas_legacy_y_falsos_prefijos(
    monkeypatch, metodo, ruta
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    with TestClient(_app_ficticia()) as client:
        respuesta = client.request(metodo, ruta)
        assert respuesta.status_code == 503
        assert respuesta.json()["detail"].startswith("Ruta no disponible")


@pytest.mark.parametrize(
    "metodo,ruta,esperado",
    [
        ("GET", "/", 200),
        ("GET", "/health", 200),
        ("GET", "/docs", 200),
        ("GET", "/openapi.json", 200),
        ("GET", "/redoc", 200),
        ("POST", "/auth/tenant/login", 401),
        ("GET", "/auth/tenant/me", 401),
    ],
)
def test_modo_saas_deja_solo_allowlist_exacta(
    monkeypatch, metodo, ruta, esperado
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    with TestClient(_app_ficticia()) as client:
        assert client.request(metodo, ruta).status_code == esperado


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
    with TestClient(app) as client:
        for ruta in ("/productos", "/categorias", "/health/db"):
            respuesta = client.get(ruta)
            assert respuesta.status_code == 503, ruta
        assert client.post("/auth/login").status_code == 503
        assert client.get("/docs").status_code == 200
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        assert openapi.json()["openapi"].startswith("3.")
        assert client.get("/redoc").status_code == 200
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_documentacion_saas_no_depende_del_entorno(monkeypatch, app_env):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setenv("APP_ENV", app_env)
    with TestClient(_app_ficticia()) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/redoc").status_code == 200


def test_modo_saas_bloquea_websocket_legacy(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
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
