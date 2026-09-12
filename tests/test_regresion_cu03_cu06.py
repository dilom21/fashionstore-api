from sqlalchemy import select

from app.modules.catalogo.models.models import Producto


def test_regresion_endpoints_publicos(client, db_session):
    assert client.get("/health").status_code == 200
    assert client.get("/health/db").status_code == 200
    assert client.get("/").status_code == 200

    sucursales = client.get("/sucursales")
    assert sucursales.status_code == 200
    assert isinstance(sucursales.json(), list)

    categorias = client.get("/categorias")
    assert categorias.status_code == 200
    assert isinstance(categorias.json(), list)

    productos = client.get("/productos")
    assert productos.status_code == 200
    assert isinstance(productos.json(), list)

    producto = db_session.scalar(
        select(Producto).where(Producto.estado.is_(True)).order_by(Producto.id)
    )
    assert producto is not None
    assert client.get(f"/productos/{producto.id}").status_code == 200
    assert (
        client.get(f"/productos/{producto.id}/disponibilidad").status_code == 200
    )


def test_regresion_inventario_requiere_autenticacion(client, admin_headers):
    assert client.get("/inventario").status_code == 401
    assert client.get("/inventario", headers=admin_headers).status_code == 200


def test_regresion_cu03_usuarios(client, admin_headers, cajero_headers):
    assert client.get("/usuarios", headers=admin_headers).status_code == 200
    assert client.get("/usuarios", headers=cajero_headers).status_code == 403


def test_regresion_cu04_roles(client, admin_headers, cajero_headers):
    assert client.get("/roles", headers=admin_headers).status_code == 200
    assert client.get("/roles", headers=cajero_headers).status_code == 403
    assert (
        client.get("/roles/catalogo-permisos", headers=admin_headers).status_code
        == 200
    )


def test_regresion_cu05_bitacora(client, admin_headers):
    assert client.get("/bitacora", headers=admin_headers).status_code == 200
    assert (
        client.get("/bitacora/catalogos", headers=admin_headers).status_code == 200
    )


def test_regresion_cu06_sucursales_ciudades(client, admin_headers, cajero_headers):
    assert client.get("/ciudades", headers=admin_headers).status_code == 200
    assert client.get("/ciudades", headers=cajero_headers).status_code == 403
    assert client.get("/sucursales").status_code == 200
