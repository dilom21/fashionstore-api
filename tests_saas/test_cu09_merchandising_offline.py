"""Pruebas CU09 offline: no usan Supabase ni credenciales reales."""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ["SAAS_AUTH_ENABLED"] = "true"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.catalogo.api.tenant_merchandising_router import (
    router as merchandising_router,
)
from app.modules.catalogo.models.models import Categoria, Producto, Temporada
from app.modules.promociones.models.models import Promocion, PromocionProducto
from app.modules.roles.repositories.repository import RolRepository
from app.modules.temporadas_colecciones.models.models import Coleccion, ProductoColeccion


def _tenant(empresa_id: int, contexto: str = "personal") -> TenantContext:
    return TenantContext(
        usuario_id=10 + empresa_id,
        empresa_id=empresa_id,
        empresa_slug=f"empresa-{empresa_id}",
        empresa_nombre=f"Empresa {empresa_id}",
        rol_id=1,
        rol_nombre="ADMINISTRADOR" if contexto == "personal" else "CLIENTE",
        contexto=contexto,
        plan_codigo="PRO",
        permite_reportes_ia=True,
    )


@pytest.fixture()
def cu09_client(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    with Session(engine) as db:
        db.add_all([
            Categoria(id=1, empresa_id=1, nombre="A", descripcion=None, estado=True),
            Categoria(id=2, empresa_id=2, nombre="B", descripcion=None, estado=True),
            Producto(id=1, empresa_id=1, categoria_id=1, nombre="A Shirt", descripcion=None,
                     precio=Decimal("100.00"), estado=True),
            Producto(id=2, empresa_id=1, categoria_id=1, nombre="A Pants", descripcion=None,
                     precio=Decimal("100.00"), estado=True),
            Producto(id=4, empresa_id=1, categoria_id=1, nombre="A Tee", descripcion=None,
                     precio=Decimal("100.00"), estado=True),
            Producto(id=3, empresa_id=2, categoria_id=2, nombre="B Shirt", descripcion=None,
                     precio=Decimal("200.00"), estado=True),
            Temporada(id=1, empresa_id=1, nombre="A Season", fecha_inicio=ahora.date(),
                      fecha_fin=(ahora + timedelta(days=30)).date(), estado=True),
            Temporada(id=2, empresa_id=2, nombre="B Season", fecha_inicio=ahora.date(),
                      fecha_fin=(ahora + timedelta(days=30)).date(), estado=True),
            Coleccion(id=1, empresa_id=1, temporada_id=1, nombre="A Collection",
                      descripcion=None, estado=True),
            Coleccion(id=2, empresa_id=2, temporada_id=2, nombre="B Collection",
                      descripcion=None, estado=True),
        ])
        db.flush()
        db.add_all([
            ProductoColeccion(empresa_id=1, producto_id=1, coleccion_id=1),
            ProductoColeccion(empresa_id=2, producto_id=3, coleccion_id=2),
            # One percentage and one amount promotion overlap for product 1.
            Promocion(id=1, empresa_id=1, nombre="A Percent", descripcion=None,
                      tipo_descuento="PORCENTAJE", valor_descuento=Decimal("25"),
                      fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
                      estado=True),
            Promocion(id=2, empresa_id=1, nombre="A Amount", descripcion=None,
                      tipo_descuento="MONTO", valor_descuento=Decimal("150"),
                      fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
                      estado=True),
            Promocion(id=3, empresa_id=1, nombre="A Future", descripcion=None,
                      tipo_descuento="MONTO", valor_descuento=Decimal("10"),
                      fecha_inicio=ahora + timedelta(days=2), fecha_fin=ahora + timedelta(days=3),
                      estado=True),
            Promocion(id=4, empresa_id=1, nombre="A Expired", descripcion=None,
                      tipo_descuento="MONTO", valor_descuento=Decimal("10"),
                      fecha_inicio=ahora - timedelta(days=3), fecha_fin=ahora - timedelta(days=2),
                      estado=True),
            Promocion(id=5, empresa_id=2, nombre="B Promotion", descripcion=None,
                      tipo_descuento="MONTO", valor_descuento=Decimal("10"),
                      fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
                      estado=True),
            Promocion(id=6, empresa_id=1, nombre="A Amount Active", descripcion=None,
                      tipo_descuento="MONTO", valor_descuento=Decimal("150"),
                      fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
                      estado=True),
            Promocion(id=7, empresa_id=1, nombre="A Percent Single", descripcion=None,
                      tipo_descuento="PORCENTAJE", valor_descuento=Decimal("25"),
                      fecha_inicio=ahora - timedelta(days=1), fecha_fin=ahora + timedelta(days=1),
                      estado=True),
        ])
        db.flush()
        db.add_all([
            PromocionProducto(empresa_id=1, promocion_id=1, producto_id=1),
            PromocionProducto(empresa_id=1, promocion_id=2, producto_id=1),
            PromocionProducto(empresa_id=1, promocion_id=3, producto_id=2),
            PromocionProducto(empresa_id=1, promocion_id=4, producto_id=2),
            PromocionProducto(empresa_id=2, promocion_id=5, producto_id=3),
            PromocionProducto(empresa_id=1, promocion_id=6, producto_id=2),
            PromocionProducto(empresa_id=1, promocion_id=7, producto_id=4),
        ])
        db.commit()

    actual = {
        "tenant": _tenant(1),
        "permisos": {
            ("CONSULTAR_CATALOGO", "CONSULTAR"),
            ("GESTIONAR_PRODUCTOS", "CREAR"),
            ("GESTIONAR_PRODUCTOS", "EDITAR"),
        },
        "consultas_permiso": [],
    }
    monkeypatch.setattr(settings, "saas_auth_enabled", True)

    def tiene_permiso(_db, _rol_id, funcion, accion):
        actual["consultas_permiso"].append((funcion, accion))
        return (funcion, accion) in actual["permisos"]

    monkeypatch.setattr(
        RolRepository,
        "tiene_permiso",
        staticmethod(tiene_permiso),
    )

    def override_db():
        with Session(engine) as db:
            yield db

    def override_tenant():
        return actual["tenant"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_tenant_context] = override_tenant
    try:
        with TestClient(app) as client:
            yield client, actual
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_cu09_a_b_filters_details_and_pagination(cu09_client):
    client, actual = cu09_client
    assert [item["id"] for item in client.get("/tenant/merchandising/temporadas").json()] == [1]
    assert client.get("/tenant/merchandising/temporadas/2").status_code == 404
    assert client.get("/tenant/merchandising/colecciones/2").status_code == 404
    assert client.get("/tenant/merchandising/promociones/5").status_code == 404
    assert client.get("/tenant/merchandising/colecciones", params={"buscar": "Collection", "limit": 1}).json()[0]["id"] == 1
    assert client.get("/tenant/merchandising/promociones", params={"vigentes": True, "limit": 1}).json()[0]["id"] in {1, 2, 6, 7}
    assert client.get("/tenant/merchandising/colecciones/1/productos").json()["productos"][0]["id"] == 1
    actual["tenant"] = _tenant(2)
    assert [item["id"] for item in client.get("/tenant/merchandising/temporadas").json()] == [2]


def test_cu09_vigencia_descuentos_y_solapamiento(cu09_client):
    client, _ = cu09_client
    overlap = client.get("/tenant/merchandising/productos/1/precio-promocional").json()
    assert overlap["precio_promocional"] is None
    assert overlap["precio_definitivo"] is False
    one = client.get("/tenant/merchandising/productos/2/precio-promocional").json()
    assert Decimal(one["precio_promocional"]) == Decimal("0")
    assert one["precio_definitivo"] is True
    percentage = client.get("/tenant/merchandising/productos/4/precio-promocional").json()
    assert Decimal(percentage["precio_promocional"]) == Decimal("75.00")
    assert percentage["precio_definitivo"] is True
    promotions = client.get("/tenant/merchandising/promociones", params={"vigentes": True}).json()
    assert {item["id"] for item in promotions} == {1, 2, 6, 7}
    assert client.get("/tenant/merchandising/productos/999/precio-promocional").status_code == 404


def test_cu09_sin_permiso_cliente_y_escrituras_autorizadas(cu09_client):
    client, actual = cu09_client
    actual["permisos"].remove(("CONSULTAR_CATALOGO", "CONSULTAR"))
    assert client.get("/tenant/merchandising/temporadas").status_code == 403
    actual["permisos"].add(("CONSULTAR_CATALOGO", "CONSULTAR"))
    actual["tenant"] = _tenant(1, contexto="cliente")
    assert client.get("/tenant/merchandising/temporadas").status_code == 403
    assert client.post("/tenant/merchandising/temporadas", json={
        "nombre": "Cliente rechazado",
        "fecha_inicio": "2026-01-01",
        "fecha_fin": "2026-02-01",
    }).status_code == 403
    actual["tenant"] = _tenant(1)
    creada = client.post("/tenant/merchandising/temporadas", json={
        "nombre": "No DML", "fecha_inicio": "2026-01-01", "fecha_fin": "2026-02-01"
    })
    assert creada.status_code == 201
    temporada_id = creada.json()["id"]
    assert client.patch(f"/tenant/merchandising/temporadas/{temporada_id}", json={"nombre": "CU09 editada"}).status_code == 200
    assert client.patch(f"/tenant/merchandising/temporadas/{temporada_id}/estado", json={"estado": False}).status_code == 200
    assert client.put("/tenant/merchandising/colecciones/1/productos", json={"producto_ids": [1]}).status_code == 200
    assert ("CONSULTAR_CATALOGO", "CONSULTAR") in actual["consultas_permiso"]
    assert ("GESTIONAR_PRODUCTOS", "CREAR") in actual["consultas_permiso"]
    assert ("GESTIONAR_PRODUCTOS", "EDITAR") in actual["consultas_permiso"]
    assert ("GESTIONAR_PRODUCTOS", "EJECUTAR") not in actual["consultas_permiso"]


def test_cu09_consultar_catalogo_no_concede_escrituras(cu09_client):
    client, actual = cu09_client
    actual["permisos"] = {("CONSULTAR_CATALOGO", "CONSULTAR")}
    assert client.get("/tenant/merchandising/temporadas").status_code == 200
    assert client.post("/tenant/merchandising/temporadas", json={
        "nombre": "No autorizada",
        "fecha_inicio": "2026-01-01",
        "fecha_fin": "2026-02-01",
    }).status_code == 403
    assert client.patch(
        "/tenant/merchandising/temporadas/1", json={"nombre": "No autorizada"}
    ).status_code == 403


def test_cu09_rutas_registradas_una_vez_y_allowlist_exacta():
    inclusiones = [
        route for route in app.routes
        if getattr(route, "original_router", None) is merchandising_router
    ]
    assert len(inclusiones) == 1
    rutas_cu09 = [
        (route.path, method)
        for route in merchandising_router.routes
        for method in route.methods
        if method != "HEAD"
    ]
    esperadas = {
        ("/tenant/merchandising/temporadas", "GET"),
        ("/tenant/merchandising/temporadas", "POST"),
        ("/tenant/merchandising/temporadas/{item_id}", "GET"),
        ("/tenant/merchandising/temporadas/{item_id}", "PATCH"),
        ("/tenant/merchandising/temporadas/{item_id}/estado", "PATCH"),
        ("/tenant/merchandising/colecciones", "GET"),
        ("/tenant/merchandising/colecciones", "POST"),
        ("/tenant/merchandising/colecciones/{item_id}", "GET"),
        ("/tenant/merchandising/colecciones/{item_id}", "PATCH"),
        ("/tenant/merchandising/colecciones/{item_id}/estado", "PATCH"),
        ("/tenant/merchandising/colecciones/{item_id}/productos", "GET"),
        ("/tenant/merchandising/colecciones/{item_id}/productos", "PUT"),
        ("/tenant/merchandising/promociones", "GET"),
        ("/tenant/merchandising/promociones", "POST"),
        ("/tenant/merchandising/promociones/{item_id}", "GET"),
        ("/tenant/merchandising/promociones/{item_id}", "PATCH"),
        ("/tenant/merchandising/promociones/{item_id}/estado", "PATCH"),
        ("/tenant/merchandising/promociones/{item_id}/productos", "GET"),
        ("/tenant/merchandising/promociones/{item_id}/productos", "PUT"),
        ("/tenant/merchandising/productos/{producto_id}/precio-promocional", "GET"),
    }
    assert set(rutas_cu09) == esperadas
    assert len(rutas_cu09) == len(esperadas)
    for ruta, metodo in esperadas:
        ruta_con_id = ruta.replace("{item_id}", "1").replace("{producto_id}", "1")
        assert _ruta_saas_permitida(metodo, ruta_con_id), (metodo, ruta_con_id)

    concretas = {
        ruta.replace("{item_id}", "1").replace("{producto_id}", "1")
        for ruta, _ in esperadas
    }
    esperadas_concretas = {
        (ruta.replace("{item_id}", "1").replace("{producto_id}", "1"), metodo)
        for ruta, metodo in esperadas
    }
    for ruta in concretas:
        for metodo in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            assert _ruta_saas_permitida(metodo, ruta) is ((ruta, metodo) in esperadas_concretas)

    for metodo, ruta in {
        ("GET", "/tenant/merchandising/privado"),
        ("GET", "/tenant/merchandising/temporadas/0"),
        ("GET", "/tenant/merchandising/temporadas/-1"),
        ("GET", "/tenant/merchandising/temporadas/1/extra"),
        ("GET", "/tenant/merchandising/temporadas/"),
        ("POST", "/tenant/merchandising/promociones/1"),
        ("DELETE", "/tenant/merchandising/temporadas/1"),
    }:
        assert not _ruta_saas_permitida(metodo, ruta), (metodo, ruta)


def test_cu09_promocion_crud_estado_asociacion_y_rollback(cu09_client):
    client, _ = cu09_client
    creada = client.post("/tenant/merchandising/promociones", json={
        "nombre": "CU09 sintética", "descripcion": "prueba", "tipo_descuento": "PORCENTAJE",
        "valor_descuento": 20, "fecha_inicio": "2026-01-01T00:00:00Z",
        "fecha_fin": "2026-12-31T23:59:59Z",
    })
    assert creada.status_code == 201, creada.text
    promocion_id = creada.json()["id"]
    assert client.patch(f"/tenant/merchandising/promociones/{promocion_id}", json={"valor_descuento": 30}).status_code == 200
    assert client.patch(f"/tenant/merchandising/promociones/{promocion_id}/estado", json={"estado": False}).status_code == 200
    assert client.put(f"/tenant/merchandising/promociones/{promocion_id}/productos", json={"producto_ids": [1, 2]}).status_code == 200
    rechazado = client.put(f"/tenant/merchandising/promociones/{promocion_id}/productos", json={"producto_ids": [3]})
    assert rechazado.status_code == 404
    assert client.get(f"/tenant/merchandising/promociones/{promocion_id}/productos").json()["total"] == 2
    assert client.post("/tenant/merchandising/promociones", json={
        "nombre": "Inválida", "tipo_descuento": "PORCENTAJE", "valor_descuento": 101,
        "fecha_inicio": "2026-01-01T00:00:00Z", "fecha_fin": "2026-12-31T23:59:59Z",
    }).status_code == 422


def test_cu09_coleccion_crud_y_asociacion_del_mismo_tenant(cu09_client):
    client, _ = cu09_client
    creada = client.post("/tenant/merchandising/colecciones", json={
        "temporada_id": 1,
        "nombre": "Coleccion CU09",
        "descripcion": "Aislada",
    })
    assert creada.status_code == 201, creada.text
    coleccion_id = creada.json()["id"]
    actualizada = client.patch(
        f"/tenant/merchandising/colecciones/{coleccion_id}",
        json={"nombre": "Coleccion CU09 editada"},
    )
    assert actualizada.status_code == 200
    assert actualizada.json()["nombre"] == "Coleccion CU09 editada"
    asociados = client.put(
        f"/tenant/merchandising/colecciones/{coleccion_id}/productos",
        json={"producto_ids": [1, 2]},
    )
    assert asociados.status_code == 200
    assert asociados.json()["total"] == 2
    assert client.put(
        f"/tenant/merchandising/colecciones/{coleccion_id}/productos",
        json={"producto_ids": [3]},
    ).status_code == 404
    assert client.patch(
        f"/tenant/merchandising/colecciones/{coleccion_id}/estado",
        json={"estado": False},
    ).status_code == 200
