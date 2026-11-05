"""5C0: alta tenant de proveedores (offline, sin PostgreSQL).

Cubre:
  * allowlist exacta del gate para POST /tenant/proveedores y
    PUT /tenant/proveedores/{id}/productos (metodo + ruta + flag de compras);
  * validacion del DTO (campos extra, razon vacia, producto_id invalido);
  * permiso COMPRAS/GESTIONAR y contexto personal;
  * traduccion de errores del repositorio (403/409/422).

Lo que exige PostgreSQL real (RLS, SP, asercion HMAC) se prueba en
`database/saas/124_proveedores_tenant_5c0.py` contra el staging local.
"""
import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.core import tenant_dependencies
from app.core.config import settings
from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.proveedores.repositories import (
    tenant_proveedor_repository as repo_module,
)
from app.modules.proveedores.repositories.tenant_proveedor_repository import (
    ProveedorTenantError,
    TenantProveedorRepository,
)

EMPRESA_A = 100


def _tenant() -> TenantContext:
    return TenantContext(
        usuario_id=99,
        empresa_id=EMPRESA_A,
        empresa_slug="empresa-a",
        empresa_nombre="Empresa A",
        rol_id=1,
        rol_nombre="ADMINISTRADOR",
        contexto="personal",
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


@pytest.fixture()
def cliente(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_compras_enabled", True)
    monkeypatch.setattr(
        tenant_dependencies, "_tiene_permiso_tenant", lambda *a, **k: True
    )
    app.dependency_overrides[get_current_tenant_context] = _tenant
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Gate: allowlist exacta
# ---------------------------------------------------------------------------
def test_gate_permite_alta_de_proveedor_con_flag(monkeypatch):
    monkeypatch.setattr(settings, "saas_compras_enabled", True)
    assert _ruta_saas_permitida("POST", "/tenant/proveedores") is True
    assert _ruta_saas_permitida(
        "PUT", "/tenant/proveedores/5/productos"
    ) is True


def test_gate_bloquea_proveedores_sin_flag(monkeypatch):
    monkeypatch.setattr(settings, "saas_compras_enabled", False)
    assert _ruta_saas_permitida("POST", "/tenant/proveedores") is False
    assert _ruta_saas_permitida(
        "PUT", "/tenant/proveedores/5/productos"
    ) is False


@pytest.mark.parametrize(
    "metodo,ruta",
    [
        ("GET", "/tenant/proveedores"),
        ("POST", "/tenant/proveedores/"),
        ("DELETE", "/tenant/proveedores/5"),
        ("POST", "/tenant/proveedores/5/productos"),
        ("GET", "/tenant/proveedores/5/productos"),
        ("PUT", "/tenant/proveedores/0/productos"),
        ("PUT", "/tenant/proveedores/5/productos/extra"),
    ],
)
def test_gate_no_abre_rutas_vecinas(monkeypatch, metodo, ruta):
    monkeypatch.setattr(settings, "saas_compras_enabled", True)
    assert _ruta_saas_permitida(metodo, ruta) is False


# ---------------------------------------------------------------------------
# API: alta
# ---------------------------------------------------------------------------
def test_crear_proveedor_201(cliente, monkeypatch):
    capturado = {}

    def falso_crear(db, **kwargs):
        capturado.update(kwargs)
        return {
            "id": 77,
            "razon_social": kwargs["razon_social"],
            "nit": kwargs["nit"],
            "correo": kwargs["correo"],
            "telefono": kwargs["telefono"],
            "direccion": kwargs["direccion"],
            "estado": True,
            "total_productos": len(kwargs["producto_ids"]),
        }

    monkeypatch.setattr(TenantProveedorRepository, "crear", falso_crear)
    respuesta = cliente.post(
        "/tenant/proveedores",
        json={
            "razon_social": "  Textiles   Andinos  ",
            "nit": " 123456 ",
            "correo": "Compras@Ejemplo.Invalid",
            "producto_ids": [3, 3, 8],
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["id"] == 77
    assert cuerpo["razon_social"] == "Textiles Andinos"
    assert cuerpo["correo"] == "compras@ejemplo.invalid"
    assert cuerpo["total_productos"] == 2
    # La empresa y el usuario NUNCA vienen del cliente.
    assert capturado["empresa_id"] == EMPRESA_A
    assert capturado["usuario_id"] == 99
    assert capturado["producto_ids"] == [3, 8]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"razon_social": "   "},
        {"razon_social": "Proveedor", "extra": "no permitido"},
        {"razon_social": "Proveedor", "producto_ids": [0]},
        {"razon_social": "Proveedor", "producto_ids": [-4]},
        {"razon_social": "Proveedor", "correo": "sin-arroba"},
        {"razon_social": "x" * 151},
    ],
)
def test_crear_proveedor_422(cliente, cuerpo):
    respuesta = cliente.post("/tenant/proveedores", json=cuerpo)
    assert respuesta.status_code == 422, respuesta.text


def test_crear_proveedor_nit_duplicado_409(cliente, monkeypatch):
    def duplicado(db, **kwargs):
        raise ProveedorTenantError(409, "Conflicto al guardar el proveedor")

    monkeypatch.setattr(TenantProveedorRepository, "crear", duplicado)
    respuesta = cliente.post(
        "/tenant/proveedores", json={"razon_social": "Proveedor"}
    )
    assert respuesta.status_code == 409


def test_crear_proveedor_sin_permiso_403(cliente, monkeypatch):
    monkeypatch.setattr(
        tenant_dependencies, "_tiene_permiso_tenant", lambda *a, **k: False
    )
    respuesta = cliente.post(
        "/tenant/proveedores", json={"razon_social": "Proveedor"}
    )
    assert respuesta.status_code == 403


def test_crear_proveedor_modulo_apagado_503(cliente, monkeypatch):
    monkeypatch.setattr(settings, "saas_compras_enabled", False)
    respuesta = cliente.post(
        "/tenant/proveedores", json={"razon_social": "Proveedor"}
    )
    assert respuesta.status_code == 503


def test_contexto_cliente_403(cliente, monkeypatch):
    app.dependency_overrides[get_current_tenant_context] = lambda: TenantContext(
        usuario_id=50,
        empresa_id=EMPRESA_A,
        empresa_slug="empresa-a",
        empresa_nombre="Empresa A",
        rol_id=4,
        rol_nombre="CLIENTE",
        contexto="cliente",
        plan_codigo="BASICO",
        permite_reportes_ia=False,
    )
    respuesta = cliente.post(
        "/tenant/proveedores", json={"razon_social": "Proveedor"}
    )
    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# API: reemplazo de productos
# ---------------------------------------------------------------------------
def test_reemplazar_productos_200(cliente, monkeypatch):
    capturado = {}

    def falso_reemplazar(db, **kwargs):
        capturado.update(kwargs)
        return {
            "proveedor_id": kwargs["proveedor_id"],
            "total_productos": len(kwargs["producto_ids"]),
        }

    monkeypatch.setattr(
        TenantProveedorRepository, "reemplazar_productos", falso_reemplazar
    )
    respuesta = cliente.put(
        "/tenant/proveedores/12/productos",
        json={"producto_ids": [5, 2, 5]},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"proveedor_id": 12, "total_productos": 2}
    assert capturado["producto_ids"] == [2, 5]


@pytest.mark.parametrize("proveedor_id", [0, -1])
def test_reemplazar_productos_id_no_positivo_503_gate(cliente, proveedor_id):
    # El gate exacto rechaza IDs no positivos ANTES del router (igual que en
    # ordenes de compra); por eso el 503 es la respuesta observable.
    respuesta = cliente.put(
        f"/tenant/proveedores/{proveedor_id}/productos",
        json={"producto_ids": []},
    )
    assert respuesta.status_code == 503


def test_verificar_id_no_positivo_devuelve_404():
    from fastapi import HTTPException

    from app.modules.proveedores.api.tenant_proveedor_router import _verificar_id

    with pytest.raises(HTTPException) as exc:
        _verificar_id(0)
    assert exc.value.status_code == 404


def test_reemplazar_productos_ajeno_403(cliente, monkeypatch):
    def ajeno(db, **kwargs):
        raise ProveedorTenantError(403, "Operacion no autorizada")

    monkeypatch.setattr(TenantProveedorRepository, "reemplazar_productos", ajeno)
    respuesta = cliente.put(
        "/tenant/proveedores/12/productos", json={"producto_ids": [1]}
    )
    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# Repositorio: traduccion de SQLSTATE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sqlstate,esperado",
    [
        ("42501", 403),
        ("23505", 409),
        ("23514", 409),
        ("22023", 422),
    ],
)
def test_traduccion_sqlstate(sqlstate, esperado):
    from sqlalchemy.exc import DBAPIError

    class _Origen:
        def __init__(self, estado):
            self.sqlstate = estado

    class _Error(DBAPIError):
        def __init__(self, estado):
            super().__init__("sentencia", {}, Exception("x"))
            self.orig = _Origen(estado)

    traducido = repo_module._traducir(_Error(sqlstate))
    assert traducido.codigo == esperado
