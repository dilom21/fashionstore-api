"""Pruebas CU09 offline: no usan Supabase ni credenciales reales."""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ["SAAS_AUTH_ENABLED"] = "true"
os.environ["LEGACY_SURFACE_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
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

    actual = {"tenant": _tenant(1), "permiso": True}
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    monkeypatch.setattr(
        RolRepository,
        "tiene_permiso",
        staticmethod(lambda *_args, **_kwargs: actual["permiso"]),
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
    actual["permiso"] = False
    assert client.get("/tenant/merchandising/temporadas").status_code == 403
    actual["permiso"] = True
    actual["tenant"] = _tenant(1, contexto="cliente")
    assert client.get("/tenant/merchandising/temporadas").status_code == 403
    actual["tenant"] = _tenant(1)
    creada = client.post("/tenant/merchandising/temporadas", json={
        "nombre": "No DML", "fecha_inicio": "2026-01-01", "fecha_fin": "2026-02-01"
    })
    assert creada.status_code == 201
    temporada_id = creada.json()["id"]
    assert client.patch(f"/tenant/merchandising/temporadas/{temporada_id}", json={"nombre": "CU09 editada"}).status_code == 200
    assert client.patch(f"/tenant/merchandising/temporadas/{temporada_id}/estado", json={"estado": False}).status_code == 200
    assert client.put("/tenant/merchandising/colecciones/1/productos", json={"producto_ids": [1]}).status_code == 200


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
