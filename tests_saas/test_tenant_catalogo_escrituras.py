"""Bloque 3 / Paso 4: pruebas HTTP de escrituras aisladas.

100 % ficticias en SQLite EN MEMORIA. No usan credenciales, no realizan
INSERT/UPDATE en Supabase ni en el PostgreSQL local de cinco empresas.
Los usuarios y tenants se simulan solo para verificar aislamiento comercial;
las pruebas JWT reales están en test_auth_tenant_unit.py.
"""
import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, func, insert, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.autenticacion_seguridad.models.models import (
    Bitacora, Rol, Usuario,
)
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, VarianteProducto,
)


@compiles(BigInteger, "sqlite")
def _bigint_como_integer_sqlite(tipo, compilador, **kw):
    # PostgreSQL soporta BIGINT IDENTITY. SQLite necesita INTEGER PRIMARY KEY
    # para simular PK autogenerada en la prueba aislada en memoria.
    return "INTEGER"


@pytest.fixture()
def escenario(monkeypatch):
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(motor, tables=[
        Rol.__table__, Usuario.__table__, Bitacora.__table__,
        Categoria.__table__, Producto.__table__, Talla.__table__,
        Color.__table__, VarianteProducto.__table__,
    ])
    with motor.begin() as conn:
        conn.execute(insert(Rol.__table__), [
            {"id": 1, "nombre": "ADMINISTRADOR", "estado": True},
        ])
        conn.execute(insert(Usuario.__table__), [
            {"id": 99, "rol_id": 1, "correo": "ficticio@demo.invalid",
             "password_hash": "inutilizable", "fecha_creacion": datetime.now(timezone.utc),
             "estado": True},
        ])
        conn.execute(insert(Categoria.__table__), [
            {"id": 101, "empresa_id": 10, "nombre": "Urbana", "descripcion": None, "estado": True},
            {"id": 202, "empresa_id": 20, "nombre": "Hombre", "descripcion": None, "estado": True},
        ])
        conn.execute(insert(Producto.__table__), [
            {"id": 1001, "empresa_id": 10, "categoria_id": 101, "nombre": "Polera U",
             "precio": Decimal("100.00"), "descripcion": None, "estado": True},
            {"id": 2002, "empresa_id": 20, "categoria_id": 202, "nombre": "Camisa V",
             "precio": Decimal("200.00"), "descripcion": None, "estado": True},
        ])
        conn.execute(insert(Talla.__table__), [
            {"id": 1, "nombre": "M", "estado": True},
            {"id": 2, "nombre": "L", "estado": True},
        ])
        conn.execute(insert(Color.__table__), [
            {"id": 1, "nombre": "Negro", "estado": True},
            {"id": 2, "nombre": "Azul", "estado": True},
        ])
        conn.execute(insert(VarianteProducto.__table__), [
            {"id": 5001, "empresa_id": 10, "producto_id": 1001, "talla_id": 1,
             "color_id": 1, "sku": "U-001", "estado": True},
            {"id": 6002, "empresa_id": 20, "producto_id": 2002, "talla_id": 1,
             "color_id": 1, "sku": "V-002", "estado": True},
        ])

    fabricar = sessionmaker(bind=motor, expire_on_commit=False)
    actual = {"empresa_id": 10, "rol": "ADMINISTRADOR", "contexto": "personal"}
    monkeypatch.setattr(settings, "saas_auth_enabled", True)

    def obtener_db():
        with fabricar() as db:
            yield db

    def obtener_tenant():
        return TenantContext(
            usuario_id=99,
            empresa_id=actual["empresa_id"],
            empresa_slug="urbanwear" if actual["empresa_id"] == 10 else "vanter-men",
            empresa_nombre="UrbanWear" if actual["empresa_id"] == 10 else "Vanter Men",
            rol_id=1, rol_nombre=actual["rol"],
            contexto=actual["contexto"], plan_codigo="PROFESIONAL",
            permite_reportes_ia=True,
        )

    app.dependency_overrides[get_db] = obtener_db
    app.dependency_overrides[get_current_tenant_context] = obtener_tenant
    try:
        with TestClient(app) as client:
            yield client, actual, fabricar
    finally:
        app.dependency_overrides.clear()
        motor.dispose()


def _cantidad(fabricar, tabla, empresa_id=None):
    with fabricar() as db:
        stmt = select(func.count()).select_from(tabla)
        if empresa_id is not None:
            stmt = stmt.where(tabla.c.empresa_id == empresa_id)
        return db.scalar(stmt)


def test_crear_categoria_auditada_en_tenant_y_editar(escenario):
    client, _, fabricar = escenario
    r = client.post("/tenant/catalogo/categorias", json={
        "nombre": "  Accesorios Urbanos  ", "descripcion": "Ficticia",
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["nombre"] == "Accesorios Urbanos"
    assert client.patch(f"/tenant/catalogo/categorias/{cid}", json={
        "descripcion": "Para UrbanWear",
    }).status_code == 200
    assert client.get(f"/tenant/catalogo/categorias/{cid}").json()["descripcion"] == "Para UrbanWear"
    assert _cantidad(fabricar, Categoria.__table__, 20) == 1
    with fabricar() as db:
        bitacora = db.execute(
            select(Bitacora.empresa_id, Bitacora.accion).order_by(Bitacora.id)
        ).all()
    assert bitacora == [(10, "CREAR"), (10, "MODIFICAR")]


def test_crear_producto_y_variante_en_empresa_propia(escenario):
    client, _, fabricar = escenario
    prod = client.post("/tenant/catalogo/productos", json={
        "categoria_id": 101, "nombre": "  Pantalón Urbano ",
        "precio": "175.50", "descripcion": "Ficticio",
    })
    assert prod.status_code == 201, prod.text
    pid = prod.json()["id"]
    assert prod.json()["nombre"] == "Pantalón Urbano"
    assert client.patch(f"/tenant/catalogo/productos/{pid}", json={
        "precio": "180.00",
    }).status_code == 200
    v = client.post(f"/tenant/catalogo/productos/{pid}/variantes", json={
        "talla_id": 2, "color_id": 2, "sku": "  U-002  ",
    })
    assert v.status_code == 201, v.text
    vid = v.json()["id"]
    assert v.json()["sku"] == "U-002"
    assert client.patch(f"/tenant/catalogo/variantes/{vid}", json={
        "sku": "U-003",
    }).status_code == 200
    assert client.get(f"/tenant/catalogo/variantes/{vid}").json()["sku"] == "U-003"
    assert _cantidad(fabricar, Producto.__table__, 20) == 1
    assert _cantidad(fabricar, VarianteProducto.__table__, 20) == 1


@pytest.mark.parametrize("metodo,ruta,datos", [
    ("PATCH", "/tenant/catalogo/categorias/202", {"nombre": "ROBADO"}),
    ("PATCH", "/tenant/catalogo/productos/2002", {"nombre": "ROBADO"}),
    ("PATCH", "/tenant/catalogo/variantes/6002", {"sku": "ROBADO"}),
    ("POST", "/tenant/catalogo/productos", {
        "categoria_id": 202, "nombre": "Cruce", "precio": "1.00",
    }),
    ("POST", "/tenant/catalogo/productos/2002/variantes", {
        "sku": "CRUCE", "talla_id": 1, "color_id": 1,
    }),
    ("PATCH", "/tenant/catalogo/productos/1001", {"categoria_id": 202}),
])
def test_escritura_cruzada_rechazada_404_sin_cambios(escenario, metodo, ruta, datos):
    client, _, fabricar = escenario
    r = client.request(metodo, ruta, json=datos)
    assert r.status_code == 404, r.text
    assert _cantidad(fabricar, Categoria.__table__, 20) == 1
    assert _cantidad(fabricar, Producto.__table__, 20) == 1
    assert _cantidad(fabricar, VarianteProducto.__table__, 20) == 1
    with fabricar() as db:
        assert db.scalar(select(Producto.nombre).where(Producto.id == 2002)) == "Camisa V"
        assert db.scalar(select(Categoria.nombre).where(Categoria.id == 202)) == "Hombre"
        assert db.scalar(select(VarianteProducto.sku).where(VarianteProducto.id == 6002)) == "V-002"


def test_tenant_en_payload_no_sobrescribe_identidad(escenario):
    client, _, fabricar = escenario
    r = client.post("/tenant/catalogo/categorias", json={
        "nombre": "Suplantado", "empresa_id": 20,
    })
    assert r.status_code == 422
    r = client.post("/tenant/catalogo/productos", json={
        "categoria_id": 101, "nombre": "Suplantado", "precio": "22", "empresa_id": 20,
    })
    assert r.status_code == 422
    assert _cantidad(fabricar, Categoria.__table__, 20) == 1
    assert _cantidad(fabricar, Producto.__table__, 20) == 1


@pytest.mark.parametrize("datos", [
    {},
    {"nombre": None},
    {"nombre": "   "},
    {"precio": -1},
    {"categoria_id": 0},
    {"empresa_id": 20},
])
def test_patch_producto_invalido_rechazado_422(escenario, datos):
    client, _, _ = escenario
    assert client.patch("/tenant/catalogo/productos/1001", json=datos).status_code == 422


def test_unicidad_nombre_categoria_por_empresa(escenario):
    client, actual, fabricar = escenario
    r = client.post("/tenant/catalogo/categorias", json={"nombre": "Urbana"})
    assert r.status_code == 409
    assert _cantidad(fabricar, Bitacora.__table__) == 0
    actual["empresa_id"] = 20
    r = client.post("/tenant/catalogo/categorias", json={"nombre": "Urbana"})
    assert r.status_code == 201
    assert _cantidad(fabricar, Categoria.__table__, 20) == 2


def test_unicidad_sku_dentro_empresa_y_rollback(escenario):
    client, actual, fabricar = escenario
    r = client.post("/tenant/catalogo/productos/1001/variantes", json={
        "talla_id": 2, "color_id": 2, "sku": "U-001",
    })
    assert r.status_code == 409
    assert _cantidad(fabricar, VarianteProducto.__table__, 10) == 1
    actual["empresa_id"] = 20
    r = client.patch("/tenant/catalogo/variantes/6002", json={"sku": "U-001"})
    assert r.status_code == 200
    assert _cantidad(fabricar, VarianteProducto.__table__, 20) == 1


def test_rol_insuficiente_o_contexto_cliente_no_escribe(escenario):
    client, actual, fabricar = escenario
    actual["rol"] = "CAJERO"
    assert client.patch("/tenant/catalogo/productos/1001", json={"nombre": "X"}).status_code == 403
    actual["rol"] = "ADMINISTRADOR"
    actual["contexto"] = "cliente"
    assert client.post("/tenant/catalogo/categorias", json={"nombre": "X"}).status_code == 403
    assert _cantidad(fabricar, Categoria.__table__, 10) == 1


def test_no_se_abren_metodos_legacy_ni_rutas_no_auditadas(escenario):
    client, _, _ = escenario
    assert client.post("/productos", json={}).status_code == 503
    assert client.put("/tenant/catalogo/productos", json={}).status_code == 503
    assert client.delete("/tenant/catalogo/productos/1001").status_code == 503
    assert client.patch("/tenant/catalogo/variantes/0", json={"sku": "X"}).status_code == 503
    assert client.post("/tenant/catalogo/productos/2002/variantes/otra", json={}).status_code == 503


def test_modo_saas_inactivo_no_habilita_escritura_tenant(escenario, monkeypatch):
    client, _, _ = escenario
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    app.dependency_overrides.pop(get_current_tenant_context)
    assert client.post("/tenant/catalogo/categorias", json={"nombre": "No"}).status_code == 503


def test_sin_token_empresarial_rechaza_antes_de_escribir(escenario):
    client, _, fabricar = escenario
    app.dependency_overrides.pop(get_current_tenant_context)
    assert client.post("/tenant/catalogo/categorias", json={"nombre": "No"}).status_code == 401
    assert _cantidad(fabricar, Categoria.__table__, 10) == 1
