"""CU08 SaaS aislado: adapta CU13/CU14 sin conectar a Supabase."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.saas_route_gate import BloqueoRutasLegacySaaS, _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    PerfilPersonalTenant,
    TenantAuthRepository,
)
from app.modules.inventario.api.tenant_router import router
from app.modules.inventario.repositories.repository import InventarioRepository
from app.modules.inventario.services.service import (
    InventarioService,
    RolInventarioTenantError,
    SucursalNoEncontradaError,
    SucursalScopeError,
)


class FakeDB:
    info = {}


def tenant(rol="ADMINISTRADOR", contexto="personal", empresa_id=15):
    return TenantContext(
        usuario_id=42, empresa_id=empresa_id, empresa_slug="empresa-a",
        empresa_nombre="Empresa A", rol_id=3, rol_nombre=rol,
        contexto=contexto, plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


def inventario_demo(*, empresa_id=15, sucursal_id=7):
    categoria = SimpleNamespace(id=2, nombre="Camisas")
    producto = SimpleNamespace(
        id=3, nombre="Camisa", precio=Decimal("120.00"), categoria=categoria
    )
    variante = SimpleNamespace(
        id=4, sku="CAM-AZ-M", producto=producto,
        talla=SimpleNamespace(id=5, nombre="M"),
        color=SimpleNamespace(id=6, nombre="Azul"),
    )
    return SimpleNamespace(
        id=8, empresa_id=empresa_id, sucursal_id=sucursal_id,
        sucursal=SimpleNamespace(id=sucursal_id, nombre="Central"),
        variante_producto=variante, temporada_id=9,
        temporada=SimpleNamespace(id=9, nombre="Verano"),
        stock_actual=12, stock_reservado=5,
        fecha_actualizacion=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def movimiento_demo(inventario, usuario=None):
    movimiento = SimpleNamespace(
        id=20, inventario_id=inventario.id, usuario_id=99,
        tipo="ENTRADA_COMPRA", cantidad=4,
        fecha_hora=datetime(2026, 11, 1, tzinfo=timezone.utc),
        observaciones="Recepcion", referencia_tipo="ORDEN_COMPRA",
        referencia_id=30,
    )
    variante = inventario.variante_producto
    return (
        movimiento, inventario, inventario.sucursal, variante,
        variante.producto, variante.talla, variante.color,
        inventario.temporada, usuario,
    )


@pytest.fixture(autouse=True)
def perfil_encargado(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_perfil_personal",
        lambda db, **kw: PerfilPersonalTenant(
            correo="encargado@example.test", empleado_id=11,
            nombre="Eva", apellido="Paz", sucursal_id=7,
        ),
    )


def test_admin_inventario_reutiliza_calculo_filtros_paginacion_y_empresa(monkeypatch):
    capturado = {}
    item = inventario_demo()

    def consultar(db, **kwargs):
        capturado.update(kwargs)
        return [item], 1

    monkeypatch.setattr(InventarioRepository, "consultar", consultar)
    respuesta = InventarioService.consultar_inventario_tenant(
        FakeDB(), tenant(), producto="cam", categoria_id=2,
        talla_id=5, color_id=6, temporada_id=9,
        disponibilidad="CON_STOCK", limit=20, offset=0,
        sucursal_id=None, producto_id=None,
    )
    assert respuesta.total == 1 and respuesta.limit == 20
    assert respuesta.items[0].stock_disponible == 7
    assert capturado["empresa_id"] == 15
    assert capturado["sucursal_id"] is None
    assert capturado["categoria_id"] == 2
    assert capturado["disponibilidad"] == "CON_STOCK"


def test_encargado_solo_consulta_su_sucursal(monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        InventarioRepository, "consultar",
        lambda db, **kw: (capturado.update(kw) or ([], 0)),
    )
    respuesta = InventarioService.consultar_inventario_tenant(
        FakeDB(), tenant("ENCARGADO_SUCURSAL"), sucursal_id=None,
        producto=None, producto_id=None, categoria_id=None, talla_id=None,
        color_id=None, temporada_id=None, disponibilidad=None,
        limit=50, offset=0,
    )
    assert respuesta.total == 0
    assert capturado["empresa_id"] == 15
    assert capturado["sucursal_id"] == 7
    with pytest.raises(SucursalScopeError):
        InventarioService.consultar_inventario_tenant(
            FakeDB(), tenant("ENCARGADO_SUCURSAL"), sucursal_id=8,
            producto=None, producto_id=None, categoria_id=None, talla_id=None,
            color_id=None, temporada_id=None, disponibilidad=None,
            limit=50, offset=0,
        )


def test_admin_no_puede_seleccionar_sucursal_de_otra_empresa(monkeypatch):
    monkeypatch.setattr(
        InventarioRepository, "obtener_sucursal_activa", lambda *a, **kw: None
    )
    with pytest.raises(SucursalNoEncontradaError):
        InventarioService.consultar_inventario_tenant(
            FakeDB(), tenant(), sucursal_id=999,
            producto=None, producto_id=None, categoria_id=None, talla_id=None,
            color_id=None, temporada_id=None, disponibilidad=None,
            limit=50, offset=0,
        )


@pytest.mark.parametrize(
    "rol,contexto", [("CAJERO", "personal"), ("CLIENTE", "cliente")]
)
def test_cajero_y_cliente_no_acceden(rol, contexto):
    with pytest.raises(RolInventarioTenantError):
        InventarioService.consultar_inventario_tenant(
            FakeDB(), tenant(rol, contexto), sucursal_id=None,
            producto=None, producto_id=None, categoria_id=None, talla_id=None,
            color_id=None, temporada_id=None, disponibilidad=None,
            limit=50, offset=0,
        )


def test_kardex_conserva_filtros_total_detalle_y_autor_oculto(monkeypatch):
    capturado = {}
    inv = inventario_demo()

    def consultar(db, **kwargs):
        capturado.update(kwargs)
        return [movimiento_demo(inv, usuario=None)], 1

    monkeypatch.setattr(InventarioRepository, "consultar_movimientos", consultar)
    respuesta = InventarioService.consultar_movimientos_tenant(
        FakeDB(), tenant(), sucursal_id=None, tipo="ENTRADA_COMPRA",
        producto="cam", producto_id=3, variante_producto_id=4,
        temporada_id=9, usuario_id=99, referencia_tipo="ORDEN_COMPRA",
        fecha_desde=date(2026, 11, 1), fecha_hasta=date(2026, 11, 2),
        limit=10, offset=0,
    )
    assert respuesta.total == 1
    assert respuesta.items[0].usuario_id == 99
    assert respuesta.items[0].usuario_correo is None
    assert respuesta.items[0].referencia_tipo == "ORDEN_COMPRA"
    assert capturado["empresa_id"] == 15
    assert capturado["fecha_desde"].date() == date(2026, 11, 1)
    assert capturado["fecha_hasta"].date() == date(2026, 11, 2)


def test_repositorio_agrega_empresa_a_listado_conteo_y_kardex():
    inventario = " ".join(map(str, InventarioRepository._condiciones_consulta(
        empresa_id=15, producto_id=3
    )))
    movimientos = " ".join(map(str, InventarioRepository._condiciones_movimientos(
        empresa_id=15, producto_id=3
    )))
    assert "inventario.empresa_id" in inventario
    assert "variante_producto.empresa_id" in inventario
    assert "producto.empresa_id" in inventario
    assert "movimiento_inventario.empresa_id" in movimientos
    assert "inventario.empresa_id" in movimientos
    assert "variante_producto.empresa_id" in movimientos
    assert "producto.empresa_id" in movimientos


def test_filtros_auxiliares_respetan_alcance(monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        InventarioRepository, "listar_filtros_tenant",
        lambda db, **kw: capturado.update(kw) or {
            "tallas": [{"id": 1, "nombre": "M"}],
            "colores": [{"id": 2, "nombre": "Azul"}],
            "temporadas": [{"id": 3, "nombre": "Verano"}],
        },
    )
    respuesta = InventarioService.filtros_tenant(
        FakeDB(), tenant("ENCARGADO_SUCURSAL")
    )
    assert capturado == {"empresa_id": 15, "sucursal_id": 7}
    assert respuesta.tallas[0].nombre == "M"


def test_http_sin_jwt_o_invalido_401_y_swagger_tag(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    app = FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/tenant/inventario").status_code == 401
        assert client.get(
            "/tenant/inventario/movimientos",
            headers={"Authorization": "Bearer invalido"},
        ).status_code == 401
        tags = client.get("/openapi.json").json()["paths"][
            "/tenant/inventario"
        ]["get"]["tags"]
        assert tags == ["Inventario SaaS"]


def test_gate_abre_solo_get_exactos_y_mantiene_legacy_cerrado(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    for ruta in (
        "/tenant/inventario", "/tenant/inventario/movimientos",
        "/tenant/inventario/filtros",
    ):
        assert _ruta_saas_permitida("GET", ruta)
    assert not _ruta_saas_permitida("POST", "/tenant/inventario")
    assert not _ruta_saas_permitida("GET", "/tenant/inventario/otro")
    assert not _ruta_saas_permitida("GET", "/inventario")
    assert not _ruta_saas_permitida("GET", "/movimientos-inventario")
