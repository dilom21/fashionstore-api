"""Bloque 3 / Paso 3: aislamiento HTTP y SQL de lecturas del catalogo.

Las dos empresas y los recursos de este archivo son FICTICIOS y solo existen
en SQLite temporal, sin conexiones a Supabase ni PostgreSQL local.
La dependencia get_current_tenant_context se sustituye por un tenant validado
FICTICIO para aislar esta bateria del login (ya probado en tests_saas).
"""
import os
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, VarianteProducto,
)
from app.modules.catalogo.repositories.tenant_repository import TenantCatalogoRepository


def tenant_ficticio(empresa_id: int) -> TenantContext:
    return TenantContext(
        usuario_id=99,
        empresa_id=empresa_id,
        empresa_slug="urbanwear" if empresa_id == 10 else "vanter-men",
        empresa_nombre="UrbanWear" if empresa_id == 10 else "Vanter Men",
        rol_id=1,
        rol_nombre="ADMINISTRADOR",
        contexto="personal",
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


@pytest.fixture()
def db_falsa():
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tablas = [
        Categoria.__table__, Producto.__table__, Talla.__table__,
        Color.__table__, VarianteProducto.__table__,
    ]
    # Crear solo el subconjunto del catalogo aislado, sin crear empresa en DB.
    # Los IDs estan dados por el test; empresa_id permanece 100% explicito.
    from app.core.database import Base
    Base.metadata.create_all(motor, tables=tablas)
    with motor.begin() as conn:
        conn.execute(insert(Categoria.__table__), [
            {"id": 101, "empresa_id": 10, "nombre": "Urbana", "descripcion": "Urban", "estado": True},
            {"id": 202, "empresa_id": 20, "nombre": "Hombre", "descripcion": "Vanter", "estado": True},
            {"id": 103, "empresa_id": 10, "nombre": "Oculta", "descripcion": None, "estado": False},
        ])
        conn.execute(insert(Producto.__table__), [
            {"id": 1001, "empresa_id": 10, "categoria_id": 101, "nombre": "Polera U",
             "descripcion": "De UrbanWear", "precio": Decimal("120.00"), "estado": True},
            {"id": 2002, "empresa_id": 20, "categoria_id": 202, "nombre": "Camisa V",
             "descripcion": "De Vanter Men", "precio": Decimal("230.00"), "estado": True},
            {"id": 1003, "empresa_id": 10, "categoria_id": 103, "nombre": "Producto Oculto",
             "descripcion": "No exponer", "precio": Decimal("10.00"), "estado": True},
        ])
        conn.execute(insert(Talla.__table__), [{"id": 1, "nombre": "M", "estado": True}])
        conn.execute(insert(Color.__table__), [{"id": 1, "nombre": "Negro", "estado": True}])
        conn.execute(insert(VarianteProducto.__table__), [
            {"id": 5001, "empresa_id": 10, "producto_id": 1001, "talla_id": 1,
             "color_id": 1, "sku": "U-001", "estado": True},
            {"id": 6002, "empresa_id": 20, "producto_id": 2002, "talla_id": 1,
             "color_id": 1, "sku": "V-002", "estado": True},
        ])
    SessionTest = sessionmaker(bind=motor, expire_on_commit=False)
    try:
        yield SessionTest
    finally:
        motor.dispose()


@pytest.fixture()
def cliente_tenant(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    actual = {"empresa_id": 10}

    def obtener_db():
        with db_falsa() as db:
            yield db

    def obtener_tenant_ficticio():
        return tenant_ficticio(actual["empresa_id"])

    app.dependency_overrides[get_db] = obtener_db
    app.dependency_overrides[get_current_tenant_context] = obtener_tenant_ficticio
    try:
        with TestClient(app) as client:
            yield client, actual
    finally:
        app.dependency_overrides.clear()


def test_empresa_urbana_lista_exclusivamente_su_producto_y_categoria(cliente_tenant):
    client, _ = cliente_tenant
    categorias = client.get("/tenant/catalogo/categorias")
    productos = client.get("/tenant/catalogo/productos")
    assert categorias.status_code == productos.status_code == 200
    assert [c["id"] for c in categorias.json()] == [101]
    assert [p["id"] for p in productos.json()] == [1001]
    assert "Vanter" not in str(productos.json())


def test_empresa_vanter_ve_sus_propios_datos_y_no_urbanwear(cliente_tenant):
    client, actual = cliente_tenant
    actual["empresa_id"] = 20
    assert [x["id"] for x in client.get("/tenant/catalogo/categorias").json()] == [202]
    assert [x["id"] for x in client.get("/tenant/catalogo/productos").json()] == [2002]
    assert [x["id"] for x in client.get("/tenant/catalogo/productos/2002/variantes").json()] == [6002]


@pytest.mark.parametrize("ruta", [
    "/tenant/catalogo/categorias/202",
    "/tenant/catalogo/productos/2002",
    "/tenant/catalogo/productos/2002/variantes",
    "/tenant/catalogo/variantes/6002",
    "/tenant/catalogo/productos/1003",
])
def test_acceso_a_id_ajeno_o_inactivo_responde_404(cliente_tenant, ruta):
    client, _ = cliente_tenant
    respuesta = client.get(ruta)
    assert respuesta.status_code == 404
    assert respuesta.json() == {"detail": "Recurso no encontrado"}


def test_variantes_del_propio_producto_visibles_sin_pasar_empresa_id(cliente_tenant):
    client, _ = cliente_tenant
    assert [x["id"] for x in client.get("/tenant/catalogo/productos/1001/variantes").json()] == [5001]
    assert client.get("/tenant/catalogo/variantes/5001").json()["sku"] == "U-001"


def test_filtro_categoria_ajena_retorna_vacio_sin_filtrar(cliente_tenant):
    client, _ = cliente_tenant
    r = client.get("/tenant/catalogo/productos", params={"categoria_id": 202})
    assert r.status_code == 200
    assert r.json() == []


def test_empresa_inyectada_por_query_param_no_cambia_tenant(cliente_tenant):
    client, _ = cliente_tenant
    r = client.get("/tenant/catalogo/productos", params={"empresa_id": 20})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [1001]
    assert client.get("/tenant/catalogo/productos/2002", params={"empresa_id": 20}).status_code == 404


def test_paginacion_controlada(cliente_tenant):
    client, _ = cliente_tenant
    assert client.get("/tenant/catalogo/productos", params={"limit": 0}).status_code == 422
    assert client.get("/tenant/catalogo/productos", params={"limit": 101}).status_code == 422
    assert client.get("/tenant/catalogo/productos", params={"offset": -1}).status_code == 422
    assert client.get("/tenant/catalogo/productos", params={"offset": 1}).json() == []


def test_modo_saas_bloquea_urls_legacy_y_metodos_no_migrados(cliente_tenant):
    client, _ = cliente_tenant
    for ruta in ("/productos", "/categorias", "/productos/2002", "/openapi.json"):
        assert client.get(ruta).status_code == 503
    assert client.post("/tenant/catalogo/productos", json={}).status_code == 503
    assert client.get("/tenant/catalogo/productos/2002/extra").status_code == 503


def test_repo_exige_tenant_positivo(db_falsa):
    with db_falsa() as db:
        for valor in (0, -10, None, True, "20"):
            with pytest.raises(ValueError):
                TenantCatalogoRepository.listar_productos(
                    db, empresa_id=valor,
                )


def test_modo_saas_inactivo_no_ejecuta_consulta_tenant(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    def obtener_db():
        with db_falsa() as db:
            yield db
    app.dependency_overrides[get_db] = obtener_db
    try:
        with TestClient(app) as client:
            assert client.get("/tenant/catalogo/productos").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_token_ausente_no_hace_consulta_tenant(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    def obtener_db():
        with db_falsa() as db:
            yield db
    app.dependency_overrides[get_db] = obtener_db
    try:
        with TestClient(app) as client:
            assert client.get("/tenant/catalogo/productos").status_code == 401
    finally:
        app.dependency_overrides.clear()
