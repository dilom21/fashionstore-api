"""GET de CU11: contrato HTTP aislado, sin acceso a Supabase."""
import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./cu11_read_test.db")
os.environ.setdefault("SECRET_KEY", "clave-ficticia-cu11-read")
os.environ.setdefault("SAAS_AUTH_ENABLED", "true")
os.environ.setdefault("SAAS_RESERVAS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import BloqueoRutasLegacySaaS, _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.reservas.api import tenant_reserva_write_router as modulo
from app.modules.reservas.repositories.tenant_read_repository import (
    ReservaTenantReadError,
)


class FakeDB:
    pass


def contexto_cliente() -> TenantContext:
    return TenantContext(
        usuario_id=42,
        empresa_id=3,
        empresa_slug="elegancia-sur",
        empresa_nombre="Elegancia Sur",
        rol_id=7,
        rol_nombre="CLIENTE",
        contexto="cliente",
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


@pytest.fixture()
def escenario(monkeypatch):
    app = FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)
    app.include_router(modulo.router)
    fake_db = FakeDB()

    def db():
        yield fake_db

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_tenant_context] = contexto_cliente
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_reservas_enabled", True)
    with TestClient(app) as cliente:
        yield cliente, fake_db


def resumen():
    ahora = datetime.now(timezone.utc).isoformat()
    return {
        "reserva_id": 91,
        "carrito_id": 15,
        "sucursal_id": 8,
        "sucursal_nombre": "Central",
        "fecha_reserva": ahora,
        "fecha_atencion": ahora,
        "estado": "PENDIENTE",
        "observacion": None,
        "cantidad_lineas": 1,
        "cantidad_unidades": 2,
    }


def test_listado_usa_identidad_jwt_y_paginacion(escenario, monkeypatch):
    cliente, fake_db = escenario
    llamadas = []

    def listar(db, **kwargs):
        llamadas.append((db, kwargs))
        return {"items": [resumen()], "total": 1, "limit": 10, "offset": 5}

    monkeypatch.setattr(modulo.ReservaTenantReadRepository, "listar", listar)
    respuesta = cliente.get(
        "/tenant/reservas?estado=PENDIENTE&limit=10&offset=5"
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["total"] == 1
    assert llamadas == [(
        fake_db,
        {
            "empresa_id": 3,
            "usuario_id": 42,
            "estado": "PENDIENTE",
            "limit": 10,
            "offset": 5,
        },
    )]


def test_detalle_propio_con_lineas(escenario, monkeypatch):
    cliente, _ = escenario
    dato = resumen() | {
        "items": [{
            "detalle_id": 1,
            "inventario_id": 4,
            "producto_id": 5,
            "producto_nombre": "Camisa",
            "imagen_principal_url": None,
            "variante_producto_id": 6,
            "sku": "SKU-6",
            "talla_id": 2,
            "talla_nombre": "M",
            "color_id": 3,
            "color_nombre": "Azul",
            "temporada_id": 7,
            "temporada_nombre": "Verano",
            "cantidad": 2,
        }]
    }
    monkeypatch.setattr(
        modulo.ReservaTenantReadRepository, "obtener", lambda *a, **k: dato
    )
    respuesta = cliente.get("/tenant/reservas/91")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["items"][0]["sku"] == "SKU-6"


def test_reserva_ajena_es_404_uniforme(escenario, monkeypatch):
    cliente, _ = escenario

    def no_encontrada(*args, **kwargs):
        raise ReservaTenantReadError(404, "Reserva no encontrada")

    monkeypatch.setattr(modulo.ReservaTenantReadRepository, "obtener", no_encontrada)
    respuesta = cliente.get("/tenant/reservas/999")
    assert respuesta.status_code == 404
    assert respuesta.json() == {"detail": "Reserva no encontrada"}


def test_flag_apagado_cierra_listado_y_detalle(escenario, monkeypatch):
    cliente, _ = escenario
    monkeypatch.setattr(settings, "saas_reservas_enabled", False)
    assert cliente.get("/tenant/reservas").status_code == 503
    assert cliente.get("/tenant/reservas/1").status_code == 503


def test_guard_solo_abre_get_exactos_con_flag(monkeypatch):
    monkeypatch.setattr(settings, "saas_reservas_enabled", True)
    assert _ruta_saas_permitida("GET", "/tenant/reservas")
    assert _ruta_saas_permitida("GET", "/tenant/reservas/1")
    assert not _ruta_saas_permitida("GET", "/tenant/reservas/0")
    assert not _ruta_saas_permitida("GET", "/tenant/reservas/1/otro")
    assert not _ruta_saas_permitida("POST", "/tenant/reservas/1")


def test_personal_no_supera_dependencia_cliente(escenario):
    cliente, _ = escenario
    # La dependencia real valida contexto=cliente. Se prueba directamente para
    # no sustituir la autenticacion JWT/RLS por una simulacion permisiva.
    from app.core.tenant_dependencies import get_current_tenant_cliente

    personal = contexto_cliente()
    object.__setattr__(personal, "contexto", "personal")
    with pytest.raises(Exception) as error:
        get_current_tenant_cliente(personal)
    assert getattr(error.value, "status_code", None) == 403
