"""CU13 POS: inventario mínimo para CAJERO, sin abrir funciones admin."""
import inspect
import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    PerfilPersonalTenant,
    TenantAuthRepository,
)
from app.core.database import Base
from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.modules.inventario.api import tenant_router
from app.modules.inventario.models.models import Inventario
from app.modules.inventario.repositories.repository import InventarioRepository
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.inventario.services.service import (
    InventarioService,
    RolInventarioTenantError,
    SucursalScopeError,
)


class FakeDB:
    info = {}


def tenant(rol="ADMINISTRADOR", contexto="personal", empresa_id=15):
    return TenantContext(
        usuario_id=42,
        empresa_id=empresa_id,
        empresa_slug="empresa-a",
        empresa_nombre="Empresa A",
        rol_id=3,
        rol_nombre=rol,
        contexto=contexto,
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


def inventario_demo(*, empresa_id=15, sucursal_id=7):
    categoria = SimpleNamespace(id=2, nombre="Camisas")
    producto = SimpleNamespace(
        id=3, nombre="Camisa", precio=Decimal("120.00"), categoria=categoria
    )
    variante = SimpleNamespace(
        id=4,
        sku="CAM-AZ-M",
        producto=producto,
        talla=SimpleNamespace(id=5, nombre="M"),
        color=SimpleNamespace(id=6, nombre="Azul"),
    )
    return SimpleNamespace(
        id=8,
        empresa_id=empresa_id,
        sucursal_id=sucursal_id,
        sucursal=SimpleNamespace(id=sucursal_id, nombre="Central"),
        variante_producto=variante,
        temporada_id=9,
        temporada=SimpleNamespace(id=9, nombre="Verano"),
        stock_actual=12,
        stock_reservado=5,
        fecha_actualizacion=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


@pytest.fixture(autouse=True)
def perfil_encargado(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda db, **kw: PerfilPersonalTenant(
            correo="cajero@example.test",
            empleado_id=11,
            nombre="Carla",
            apellido="Paz",
            sucursal_id=7,
        ),
    )


def test_cajero_pos_consulta_su_sucursal_y_schema_minimo(monkeypatch):
    capturado = {}
    item = inventario_demo()

    def consultar_pos(db, **kwargs):
        capturado.update(kwargs)
        return [item], 1

    monkeypatch.setattr(InventarioRepository, "consultar_pos", consultar_pos)
    respuesta = InventarioService.consultar_inventario_pos_tenant(
        FakeDB(),
        tenant("CAJERO"),
        producto="cam",
        categoria_id=2,
        talla_id=5,
        color_id=6,
        temporada_id=9,
        disponibilidad="CON_STOCK",
        limit=20,
        offset=0,
        sucursal_id=None,
        producto_id=None,
    )

    assert respuesta.total == 1 and respuesta.limit == 20
    assert capturado["empresa_id"] == 15
    assert capturado["sucursal_id"] == 7
    assert capturado["categoria_id"] == 2
    assert capturado["disponibilidad"] == "CON_STOCK"
    assert respuesta.items[0].stock_disponible == 7
    assert not hasattr(respuesta.items[0], "stock_actual")
    assert not hasattr(respuesta.items[0], "stock_reservado")


def test_cajero_pos_no_puede_pedir_otra_sucursal():
    with pytest.raises(SucursalScopeError):
        InventarioService.consultar_inventario_pos_tenant(
            FakeDB(), tenant("CAJERO"), sucursal_id=8
        )


def test_cajero_pos_sin_empleado_no_accede(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_perfil_personal", lambda *a, **k: None
    )
    with pytest.raises(SucursalScopeError):
        InventarioService.consultar_inventario_pos_tenant(
            FakeDB(), tenant("CAJERO")
        )


def test_cliente_no_accede_al_pos():
    with pytest.raises(RolInventarioTenantError):
        InventarioService.consultar_inventario_pos_tenant(
            FakeDB(), tenant("CLIENTE", contexto="cliente")
        )


def test_admin_pos_conserva_alcance_global_y_filtro(monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        InventarioRepository,
        "obtener_sucursal_activa",
        lambda db, sucursal_id, **kw: SimpleNamespace(id=sucursal_id),
    )
    monkeypatch.setattr(
        InventarioRepository,
        "consultar_pos",
        lambda db, **kw: (capturado.update(kw) or ([], 0)),
    )
    InventarioService.consultar_inventario_pos_tenant(
        FakeDB(), tenant("ADMINISTRADOR"), sucursal_id=None
    )
    assert capturado["empresa_id"] == 15
    assert capturado["sucursal_id"] is None

    InventarioService.consultar_inventario_pos_tenant(
        FakeDB(), tenant("ADMINISTRADOR"), sucursal_id=9
    )
    assert capturado["sucursal_id"] == 9


def test_encargado_pos_solo_su_sucursal(monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        InventarioRepository,
        "consultar_pos",
        lambda db, **kw: (capturado.update(kw) or ([], 0)),
    )
    InventarioService.consultar_inventario_pos_tenant(
        FakeDB(), tenant("ENCARGADO_SUCURSAL")
    )
    assert capturado["sucursal_id"] == 7
    with pytest.raises(SucursalScopeError):
        InventarioService.consultar_inventario_pos_tenant(
            FakeDB(), tenant("ENCARGADO_SUCURSAL"), sucursal_id=8
        )


def test_inventario_general_y_kardex_siguen_negando_cajero():
    with pytest.raises(RolInventarioTenantError):
        InventarioService.consultar_inventario_tenant(
            FakeDB(), tenant("CAJERO")
        )
    with pytest.raises(RolInventarioTenantError):
        InventarioService.consultar_movimientos_tenant(
            FakeDB(), tenant("CAJERO")
        )
    with pytest.raises(RolInventarioTenantError):
        InventarioService.filtros_tenant(FakeDB(), tenant("CAJERO"))


def test_repositorio_pos_filtra_entidades_activas():
    fuente = inspect.getsource(InventarioRepository.consultar_pos)
    assert "Producto.estado.is_(True)" in fuente
    assert "VarianteProducto.estado.is_(True)" in fuente
    assert "Temporada.estado.is_(True)" in fuente
    assert "Sucursal.estado.is_(True)" in fuente
    assert "Temporada.empresa_id == empresa_id" in fuente
    assert "Sucursal.empresa_id == empresa_id" in fuente
    assert "empresa_id=empresa_id" in fuente
    assert "sucursal_id=sucursal_id" in fuente


def test_consultar_pos_sqlite_filtra_empresa_sucursal_y_estados():
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        motor,
        tables=[
            Ciudad.__table__, Sucursal.__table__, Categoria.__table__,
            Producto.__table__, Talla.__table__, Color.__table__,
            Temporada.__table__, VarianteProducto.__table__,
            Inventario.__table__,
        ],
    )
    ahora = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with motor.begin() as c:
        c.execute(insert(Ciudad.__table__), [
            {"id": 1, "nombre": "Ciudad", "estado": True},
        ])
        c.execute(insert(Sucursal.__table__), [
            {"id": 7, "empresa_id": 15, "ciudad_id": 1,
             "nombre": "Central", "direccion": "Calle 1", "estado": True},
            {"id": 8, "empresa_id": 15, "ciudad_id": 1,
             "nombre": "Cerrada", "direccion": "Calle 2", "estado": False},
        ])
        c.execute(insert(Categoria.__table__), [
            {"id": 2, "empresa_id": 15, "nombre": "Camisas", "estado": True},
        ])
        c.execute(insert(Talla.__table__), [
            {"id": 5, "nombre": "M", "estado": True},
        ])
        c.execute(insert(Color.__table__), [
            {"id": 6, "nombre": "Azul", "estado": True},
        ])
        c.execute(insert(Temporada.__table__), [
            {"id": 9, "empresa_id": 15, "nombre": "Verano",
             "fecha_inicio": ahora.date(), "fecha_fin": ahora.date(),
             "estado": True},
            {"id": 10, "empresa_id": 15, "nombre": "Cerrada",
             "fecha_inicio": ahora.date(), "fecha_fin": ahora.date(),
             "estado": False},
        ])
        c.execute(insert(Producto.__table__), [
            {"id": 3, "empresa_id": 15, "categoria_id": 2,
             "nombre": "Camisa activa", "precio": Decimal("120.00"),
             "estado": True},
            {"id": 4, "empresa_id": 15, "categoria_id": 2,
             "nombre": "Camisa inactiva", "precio": Decimal("90.00"),
             "estado": False},
        ])
        c.execute(insert(VarianteProducto.__table__), [
            {"id": 41, "empresa_id": 15, "producto_id": 3,
             "talla_id": 5, "color_id": 6, "sku": "OK-1", "estado": True},
            {"id": 42, "empresa_id": 15, "producto_id": 4,
             "talla_id": 5, "color_id": 6, "sku": "BAD-1", "estado": True},
            {"id": 43, "empresa_id": 15, "producto_id": 3,
             "talla_id": 5, "color_id": 6, "sku": "BAD-2", "estado": False},
        ])
        c.execute(insert(Inventario.__table__), [
            {"id": 81, "empresa_id": 15, "sucursal_id": 7,
             "variante_producto_id": 41, "temporada_id": 9,
             "stock_actual": 12, "stock_reservado": 5,
             "fecha_actualizacion": ahora},
            {"id": 82, "empresa_id": 15, "sucursal_id": 7,
             "variante_producto_id": 42, "temporada_id": 9,
             "stock_actual": 5, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
            {"id": 83, "empresa_id": 15, "sucursal_id": 7,
             "variante_producto_id": 43, "temporada_id": 9,
             "stock_actual": 5, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
            {"id": 84, "empresa_id": 15, "sucursal_id": 8,
             "variante_producto_id": 41, "temporada_id": 9,
             "stock_actual": 5, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
            {"id": 85, "empresa_id": 15, "sucursal_id": 7,
             "variante_producto_id": 41, "temporada_id": 10,
             "stock_actual": 5, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
            {"id": 86, "empresa_id": 16, "sucursal_id": 7,
             "variante_producto_id": 41, "temporada_id": 9,
             "stock_actual": 5, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
        ])
    with Session(motor) as db:
        filas, total = InventarioRepository.consultar_pos(
            db, empresa_id=15, sucursal_id=7, limit=50, offset=0
        )
        assert total == 1
        assert [f.id for f in filas] == [81]
        assert filas[0].variante_producto.producto.nombre == "Camisa activa"

        respuesta = InventarioService.consultar_inventario_pos_tenant(
            db, tenant("CAJERO"), limit=50, offset=0
        )
    assert respuesta.total == 1
    item = respuesta.items[0]
    assert item.inventario_id == 81
    assert item.stock_disponible == 7
    assert item.precio == Decimal("120.00")
    assert item.sku == "OK-1"
    assert item.sucursal_nombre == "Central"
    assert item.categoria_nombre == "Camisas"
    assert item.temporada_nombre == "Verano"


@pytest.fixture()
def app_pos(monkeypatch):
    app = FastAPI()
    app.include_router(tenant_router.router)
    fake_db = FakeDB()
    ctx = tenant("CAJERO")
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_tenant_context] = lambda: ctx
    with TestClient(app) as cliente:
        yield cliente


def test_router_pos_devuelve_contrato_y_usa_jwt(app_pos, monkeypatch):
    llamadas = []

    def consultar(_db, tenant_ctx, **kwargs):
        llamadas.append((tenant_ctx, kwargs))
        return {
            "items": [
                {
                    "inventario_id": 8,
                    "sucursal_id": 7,
                    "sucursal_nombre": "Central",
                    "producto_id": 3,
                    "producto_nombre": "Camisa",
                    "precio": "120.00",
                    "categoria_id": 2,
                    "categoria_nombre": "Camisas",
                    "variante_producto_id": 4,
                    "sku": "CAM-AZ-M",
                    "talla_id": 5,
                    "talla_nombre": "M",
                    "color_id": 6,
                    "color_nombre": "Azul",
                    "temporada_id": 9,
                    "temporada_nombre": "Verano",
                    "stock_disponible": 7,
                }
            ],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }

    monkeypatch.setattr(
        tenant_router.InventarioService,
        "consultar_inventario_pos_tenant",
        consultar,
    )
    respuesta = app_pos.get(
        "/tenant/inventario/pos?empresa_id=999&sucursal_id=7&limit=20"
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["items"][0]["stock_disponible"] == 7
    assert "stock_actual" not in respuesta.json()["items"][0]
    tenant_ctx, kwargs = llamadas[0]
    assert tenant_ctx.empresa_id == 15
    assert kwargs["sucursal_id"] == 7


def test_gate_abre_solo_el_endpoint_pos_exacto(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    assert _ruta_saas_permitida("GET", "/tenant/inventario/pos")
    assert not _ruta_saas_permitida("GET", "/tenant/inventario/pos/extra")
    assert not _ruta_saas_permitida("POST", "/tenant/inventario/pos")
    # Las rutas administrativas siguen en la allowlist; el service sigue
    # negando CAJERO en movimientos/filtros.
    assert _ruta_saas_permitida("GET", "/tenant/inventario/movimientos")
    assert _ruta_saas_permitida("GET", "/tenant/inventario/filtros")
