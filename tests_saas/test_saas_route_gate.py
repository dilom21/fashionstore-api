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
from fastapi import FastAPI
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
def test_modo_saas_bloquea_rutas_legacy_y_falsos_prefijos(
    monkeypatch, metodo, ruta
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
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
    with TestClient(app) as client:
        for ruta in ("/productos", "/categorias", "/health/db", "/openapi.json"):
            respuesta = client.get(ruta)
            assert respuesta.status_code == 503, ruta
        assert client.get("/health").status_code == 200
