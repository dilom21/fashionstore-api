"""T03: tenant isolation, RBAC and branch scope for audit/Kardex reads."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.autenticacion_seguridad.models.models import (
    Accion,
    Bitacora,
    Empleado,
    Funcion,
    Modulo,
    Rol,
    RolFuncion,
    Usuario,
)
from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Ciudad, Sucursal

EMPRESA_A, EMPRESA_B = 100, 200
ADMIN, ENCARGADO, CLIENTE = 1, 2, 3


def tenant(
    empresa_id=EMPRESA_A,
    *,
    usuario_id=11,
    rol_id=ADMIN,
    rol_nombre="ADMINISTRADOR",
    contexto="personal",
):
    return TenantContext(
        usuario_id=usuario_id,
        empresa_id=empresa_id,
        empresa_slug="empresa-a" if empresa_id == EMPRESA_A else "empresa-b",
        empresa_nombre="Empresa A" if empresa_id == EMPRESA_A else "Empresa B",
        rol_id=rol_id,
        rol_nombre=rol_nombre,
        contexto=contexto,
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


def _sembrar(conn):
    conn.execute(insert(Rol.__table__), [
        {"id": ADMIN, "nombre": "ADMINISTRADOR", "descripcion": None, "estado": True},
        {"id": ENCARGADO, "nombre": "ENCARGADO_SUCURSAL", "descripcion": None, "estado": True},
        {"id": CLIENTE, "nombre": "CLIENTE", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Modulo.__table__), [
        {"id": 1, "nombre": "SEGURIDAD", "descripcion": None, "estado": True},
        {"id": 2, "nombre": "COMERCIAL", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Funcion.__table__), [
        {"id": 1, "modulo_id": 1, "nombre": "PERSONAL", "descripcion": None, "estado": True},
        {"id": 2, "modulo_id": 2, "nombre": "VENTAS", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Accion.__table__), [
        {"id": 1, "nombre": "CONSULTAR", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(RolFuncion.__table__), [
        {"rol_id": ADMIN, "funcion_id": 1, "accion_id": 1, "descripcion": None},
        {"rol_id": ADMIN, "funcion_id": 2, "accion_id": 1, "descripcion": None},
        {"rol_id": ENCARGADO, "funcion_id": 2, "accion_id": 1, "descripcion": None},
    ])
    fecha = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    conn.execute(insert(Usuario.__table__), [
        {"id": 11, "rol_id": ADMIN, "correo": "admin-a@example.test", "password_hash": "x", "fecha_creacion": fecha, "estado": True},
        {"id": 12, "rol_id": ENCARGADO, "correo": "encargado-a@example.test", "password_hash": "x", "fecha_creacion": fecha, "estado": True},
        {"id": 21, "rol_id": ADMIN, "correo": "admin-b@example.test", "password_hash": "x", "fecha_creacion": fecha, "estado": True},
    ])
    conn.exec_driver_sql(
        "INSERT INTO usuario_empresa (id, usuario_id, empresa_id, rol_id, estado) VALUES "
        "(1,11,100,1,1),(2,12,100,2,1),(3,21,200,1,1)"
    )
    conn.execute(insert(Ciudad.__table__), [
        {"id": 1, "nombre": "Alfa", "estado": True},
        {"id": 2, "nombre": "Beta", "estado": True},
    ])
    conn.execute(insert(Sucursal.__table__), [
        {"id": 501, "empresa_id": EMPRESA_A, "ciudad_id": 1, "nombre": "A Centro", "direccion": "A", "telefono": None, "estado": True},
        {"id": 502, "empresa_id": EMPRESA_A, "ciudad_id": 1, "nombre": "A Norte", "direccion": "B", "telefono": None, "estado": True},
        {"id": 601, "empresa_id": EMPRESA_B, "ciudad_id": 2, "nombre": "B Centro", "direccion": "C", "telefono": None, "estado": True},
    ])
    conn.execute(insert(Empleado.__table__), [{
        "id": 41, "empresa_id": EMPRESA_A, "usuario_id": 12, "sucursal_id": 501,
        "nombres": "Eva", "apellidos": "Prueba", "ci": "A-12", "telefono": None,
        "fecha_contratacion": date(2026, 1, 1), "estado": True,
    }])
    conn.execute(insert(Categoria.__table__), [
        {"id": 101, "empresa_id": EMPRESA_A, "nombre": "A", "descripcion": None, "estado": True},
        {"id": 201, "empresa_id": EMPRESA_B, "nombre": "B", "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Talla.__table__), [{"id": 1, "nombre": "M", "estado": True}])
    conn.execute(insert(Color.__table__), [{"id": 1, "nombre": "Negro", "estado": True}])
    conn.execute(insert(Producto.__table__), [
        {"id": 1001, "empresa_id": EMPRESA_A, "categoria_id": 101, "nombre": "Producto A", "descripcion": None, "precio": Decimal("50"), "estado": True},
        {"id": 2001, "empresa_id": EMPRESA_B, "categoria_id": 201, "nombre": "Producto B", "descripcion": None, "precio": Decimal("70"), "estado": True},
    ])
    conn.execute(insert(VarianteProducto.__table__), [
        {"id": 1101, "empresa_id": EMPRESA_A, "producto_id": 1001, "talla_id": 1, "color_id": 1, "sku": "A-1", "estado": True},
        {"id": 2101, "empresa_id": EMPRESA_B, "producto_id": 2001, "talla_id": 1, "color_id": 1, "sku": "B-1", "estado": True},
    ])
    conn.execute(insert(Temporada.__table__), [
        {"id": 301, "empresa_id": EMPRESA_A, "nombre": "A 2026", "fecha_inicio": date(2026, 1, 1), "fecha_fin": date(2026, 12, 31), "estado": True},
        {"id": 401, "empresa_id": EMPRESA_B, "nombre": "B 2026", "fecha_inicio": date(2026, 1, 1), "fecha_fin": date(2026, 12, 31), "estado": True},
    ])
    conn.execute(insert(Inventario.__table__), [
        {"id": 701, "empresa_id": EMPRESA_A, "sucursal_id": 501, "variante_producto_id": 1101, "temporada_id": 301, "stock_actual": 10, "stock_reservado": 0, "fecha_actualizacion": fecha},
        {"id": 702, "empresa_id": EMPRESA_A, "sucursal_id": 502, "variante_producto_id": 1101, "temporada_id": 301, "stock_actual": 5, "stock_reservado": 0, "fecha_actualizacion": fecha},
        {"id": 801, "empresa_id": EMPRESA_B, "sucursal_id": 601, "variante_producto_id": 2101, "temporada_id": 401, "stock_actual": 7, "stock_reservado": 0, "fecha_actualizacion": fecha},
    ])
    conn.execute(insert(MovimientoInventario.__table__), [
        {"id": 9001, "empresa_id": EMPRESA_A, "inventario_id": 701, "usuario_id": 12, "tipo": "ENTRADA_COMPRA", "cantidad": 10, "fecha_hora": fecha, "observaciones": "A1", "referencia_tipo": "ORDEN_COMPRA", "referencia_id": 1},
        {"id": 9002, "empresa_id": EMPRESA_A, "inventario_id": 702, "usuario_id": 11, "tipo": "SALIDA_VENTA", "cantidad": 2, "fecha_hora": fecha, "observaciones": "A2", "referencia_tipo": "VENTA", "referencia_id": 2},
        {"id": 9901, "empresa_id": EMPRESA_B, "inventario_id": 801, "usuario_id": 21, "tipo": "ENTRADA_COMPRA", "cantidad": 7, "fecha_hora": fecha, "observaciones": "B", "referencia_tipo": "ORDEN_COMPRA", "referencia_id": 3},
    ])
    conn.execute(insert(Bitacora.__table__), [
        {"id": 3001, "empresa_id": EMPRESA_A, "usuario_id": 11, "fecha_hora": fecha, "ip": "127.0.0.1", "accion": "CREAR", "entidad_afectada": "producto", "descripcion": "Evento A"},
        {"id": 3002, "empresa_id": EMPRESA_A, "usuario_id": 12, "fecha_hora": fecha, "ip": None, "accion": "ACTUALIZAR", "entidad_afectada": "inventario", "descripcion": "Evento A2"},
        {"id": 4001, "empresa_id": EMPRESA_B, "usuario_id": 21, "fecha_hora": fecha, "ip": None, "accion": "CREAR", "entidad_afectada": "producto", "descripcion": "Evento B"},
    ])


@pytest.fixture()
def db_falsa():
    motor = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(motor)
    with motor.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE usuario_empresa (id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL, empresa_id INTEGER NOT NULL, rol_id INTEGER NOT NULL, estado BOOLEAN NOT NULL)"
        )
        _sembrar(conn)
    fabrica = sessionmaker(bind=motor, expire_on_commit=False)
    try:
        yield fabrica
    finally:
        motor.dispose()


@pytest.fixture()
def cliente(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    actual = {"tenant": tenant()}

    def obtener_db():
        with db_falsa() as db:
            yield db

    app.dependency_overrides[get_db] = obtener_db
    app.dependency_overrides[get_current_tenant_context] = lambda: actual["tenant"]
    try:
        with TestClient(app) as client:
            yield client, actual
    finally:
        app.dependency_overrides.clear()


def test_bitacora_lista_detalle_catalogos_y_aislamiento_ab(cliente):
    client, actual = cliente
    lista = client.get("/tenant/bitacora")
    assert lista.status_code == 200
    assert [item["id"] for item in lista.json()["items"]] == [3002, 3001]
    assert lista.json()["items"][0]["usuario"]["rol"] == "ENCARGADO_SUCURSAL"
    assert "empresa_id" not in lista.text
    filtrada = client.get("/tenant/bitacora", params={"buscar": "Evento A2", "limit": 1})
    assert filtrada.json()["total"] == 1
    assert [item["id"] for item in filtrada.json()["items"]] == [3002]
    assert client.get(
        "/tenant/bitacora",
        params={"fecha_desde": "2026-10-02", "fecha_hasta": "2026-10-01"},
    ).status_code == 422
    assert client.get("/tenant/bitacora/4001").status_code == 404
    catalogos = client.get("/tenant/bitacora/catalogos").json()
    assert catalogos["acciones"] == ["ACTUALIZAR", "CREAR"]
    assert {u["correo"] for u in catalogos["usuarios"]} == {
        "admin-a@example.test", "encargado-a@example.test"
    }
    actual["tenant"] = tenant(EMPRESA_B, usuario_id=21)
    assert [item["id"] for item in client.get("/tenant/bitacora").json()["items"]] == [4001]


def test_kardex_admin_filtra_detalle_y_aisla_empresa(cliente):
    client, actual = cliente
    respuesta = client.get("/tenant/movimientos-inventario", params={"tipo": "ENTRADA_COMPRA"})
    assert respuesta.status_code == 200
    assert [item["movimiento_id"] for item in respuesta.json()["items"]] == [9001]
    assert respuesta.json()["items"][0]["usuario_correo"] == "encargado-a@example.test"
    paginada = client.get(
        "/tenant/movimientos-inventario", params={"producto": "producto a", "limit": 1}
    ).json()
    assert paginada["total"] == 2
    assert len(paginada["items"]) == 1
    assert client.get(
        "/tenant/movimientos-inventario",
        params={"fecha_desde": "2026-10-02", "fecha_hasta": "2026-10-01"},
    ).status_code == 422
    assert client.get("/tenant/movimientos-inventario/9901").status_code == 404
    actual["tenant"] = tenant(EMPRESA_B, usuario_id=21)
    detalle = client.get("/tenant/movimientos-inventario/9901")
    assert detalle.status_code == 200
    assert detalle.json()["sku"] == "B-1"


def test_kardex_encargado_limitado_a_su_sucursal(cliente):
    client, actual = cliente
    actual["tenant"] = tenant(
        usuario_id=12, rol_id=ENCARGADO, rol_nombre="ENCARGADO_SUCURSAL"
    )
    lista = client.get("/tenant/movimientos-inventario")
    assert [item["movimiento_id"] for item in lista.json()["items"]] == [9001]
    assert client.get("/tenant/movimientos-inventario", params={"sucursal_id": 502}).status_code == 403
    assert client.get("/tenant/movimientos-inventario/9002").status_code == 404


def test_rbac_niega_bitacora_a_no_admin_y_rutas_exigen_personal(cliente):
    client, actual = cliente
    actual["tenant"] = tenant(
        usuario_id=12, rol_id=ENCARGADO, rol_nombre="ENCARGADO_SUCURSAL"
    )
    assert client.get("/tenant/bitacora").status_code == 403
    actual["tenant"] = tenant(rol_id=CLIENTE, rol_nombre="CLIENTE", contexto="cliente")
    assert client.get("/tenant/movimientos-inventario").status_code == 403


def test_gate_es_exacto_y_sin_jwt_responde_401(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)

    def obtener_db():
        with db_falsa() as db:
            yield db

    app.dependency_overrides[get_db] = obtener_db
    try:
        with TestClient(app) as client:
            assert client.get("/tenant/bitacora").status_code == 401
            assert client.get("/tenant/movimientos-inventario").status_code == 401
            assert client.get("/tenant/bitacora/").status_code == 503
            assert client.get("/tenant/movimientos-inventario/0").status_code == 503
    finally:
        app.dependency_overrides.clear()
