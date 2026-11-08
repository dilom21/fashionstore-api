"""CU07: lecturas autorizadas por el catalogo RBAC real.

No instala SQL ni prueba escrituras. La membresia ya validada se representa con
TenantContext y se verifica que la consulta de permiso recibe su rol_id
empresarial.
"""

import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "true"
os.environ["LEGACY_SURFACE_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.core.database import get_db
from app.main import app
from app.modules.compras.repositories.tenant_orden_compra_repository import (
    TenantOrdenCompraRepository,
)
from app.modules.roles.repositories.repository import RolRepository


class _DbFalsa:
    pass


def _personal() -> TenantContext:
    return TenantContext(
        usuario_id=701,
        empresa_id=77,
        empresa_slug="empresa-77",
        empresa_nombre="Empresa 77",
        rol_id=912,
        rol_nombre="CAJERO",
        contexto="personal",
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


def _cliente() -> TenantContext:
    tenant = _personal()
    return TenantContext(
        usuario_id=tenant.usuario_id,
        empresa_id=tenant.empresa_id,
        empresa_slug=tenant.empresa_slug,
        empresa_nombre=tenant.empresa_nombre,
        rol_id=tenant.rol_id,
        rol_nombre="CLIENTE",
        contexto="cliente",
        plan_codigo=tenant.plan_codigo,
        permite_reportes_ia=tenant.permite_reportes_ia,
    )


def _setup(monkeypatch, tenant, permitido=True):
    llamadas = []
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_compras_enabled", True)
    monkeypatch.setattr(
        RolRepository,
        "tiene_permiso",
        lambda db, rol_id, funcion, accion: llamadas.append(
            (db, rol_id, funcion, accion)
        )
        or permitido,
    )
    monkeypatch.setattr(
        TenantOrdenCompraRepository,
        "listar",
        lambda *args, **kwargs: [],
    )
    app.dependency_overrides[get_current_tenant_context] = lambda: tenant
    app.dependency_overrides[get_db] = lambda: _DbFalsa()
    return llamadas


def teardown_function():
    app.dependency_overrides.clear()


def test_get_ordenes_usa_rol_empresarial_y_funcion_real(monkeypatch):
    llamadas = _setup(monkeypatch, _personal(), permitido=True)
    with TestClient(app) as client:
        respuesta = client.get("/tenant/ordenes-compra")

    assert respuesta.status_code == 200, respuesta.text
    assert len(llamadas) == 1
    assert llamadas[0][1:] == (912, "GESTIONAR_ORDENES_COMPRA", "CONSULTAR")


def test_get_ordenes_sin_permiso_es_403_y_no_consulta_repositorio(monkeypatch):
    _setup(monkeypatch, _personal(), permitido=False)
    consultado = []
    monkeypatch.setattr(
        TenantOrdenCompraRepository,
        "listar",
        lambda *args, **kwargs: consultado.append(True),
    )
    with TestClient(app) as client:
        respuesta = client.get("/tenant/ordenes-compra")

    assert respuesta.status_code == 403
    assert not consultado


def test_get_ordenes_cliente_es_403(monkeypatch):
    _setup(monkeypatch, _cliente(), permitido=True)
    with TestClient(app) as client:
        respuesta = client.get("/tenant/ordenes-compra")

    assert respuesta.status_code == 403
