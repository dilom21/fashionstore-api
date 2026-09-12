import uuid
from decimal import Decimal

from sqlalchemy import select

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import Producto
from app.modules.proveedores.models.models import (
    OrdenCompra,
    ProveedorProducto,
)
from app.modules.sucursales.models.models import Sucursal

PAYLOAD_PROVEEDOR = {
    "razon_social": "Proveedor de prueba",
    "nit": None,
    "correo": None,
    "telefono": None,
    "direccion": None,
}


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


def _crear_proveedor(
    client,
    headers,
    *,
    razon_social=None,
    nit=None,
    correo=None,
    telefono=None,
    direccion=None,
):
    razon_social = razon_social or f"ZZCU11PROV_{_suf()}"
    resp = client.post(
        "/proveedores",
        json={
            "razon_social": razon_social,
            "nit": nit,
            "correo": correo,
            "telefono": telefono,
            "direccion": direccion,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _producto_activo_id(db_session) -> int:
    producto_id = db_session.scalar(
        select(Producto.id)
        .where(Producto.estado.is_(True))
        .order_by(Producto.id)
        .limit(1)
    )
    assert producto_id is not None
    return producto_id


def _dos_productos_activos(db_session) -> list[int]:
    ids = list(
        db_session.scalars(
            select(Producto.id)
            .where(Producto.estado.is_(True))
            .order_by(Producto.id)
            .limit(2)
        ).all()
    )
    assert len(ids) == 2
    return ids


def _producto_inactivo_id(db_session) -> int:
    producto_id = db_session.scalar(
        select(Producto.id)
        .where(Producto.estado.is_(False))
        .order_by(Producto.id)
        .limit(1)
    )
    if producto_id is None:
        producto_id = _producto_activo_id(db_session)
        producto = db_session.get(Producto, producto_id)
        producto.estado = False
        db_session.flush()
    return producto_id


def _proveedor_con_productos(db_session) -> int:
    proveedor_id = db_session.scalar(
        select(ProveedorProducto.proveedor_id)
        .order_by(ProveedorProducto.proveedor_id)
        .limit(1)
    )
    assert proveedor_id is not None
    return proveedor_id


def _sucursal_id(db_session) -> int:
    sucursal_id = db_session.scalar(select(Sucursal.id).limit(1))
    assert sucursal_id is not None
    return sucursal_id


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------


def test_seguridad_401_sin_token(client):
    assert client.get("/proveedores").status_code == 401
    assert client.get("/proveedores/1").status_code == 401
    assert client.get("/proveedores/1/productos").status_code == 401
    assert (
        client.post("/proveedores", json=PAYLOAD_PROVEEDOR).status_code == 401
    )
    assert (
        client.patch("/proveedores/1", json={"telefono": "700"}).status_code
        == 401
    )
    assert (
        client.patch(
            "/proveedores/1/estado", json={"estado": False}
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/proveedores/1/productos", json={"productos": []}
        ).status_code
        == 401
    )


def test_seguridad_403_cajero(client, cajero_headers):
    assert (
        client.get("/proveedores", headers=cajero_headers).status_code == 403
    )
    assert (
        client.get("/proveedores/1", headers=cajero_headers).status_code
        == 403
    )
    assert (
        client.post(
            "/proveedores", json=PAYLOAD_PROVEEDOR, headers=cajero_headers
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/proveedores/1",
            json={"telefono": "700"},
            headers=cajero_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/proveedores/1/estado",
            json={"estado": False},
            headers=cajero_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/proveedores/1/productos", headers=cajero_headers
        ).status_code
        == 403
    )


def test_encargado_puede_consultar_crear_editar(
    client, encargado_headers, db_session
):
    assert (
        client.get("/proveedores", headers=encargado_headers).status_code
        == 200
    )
    proveedor = _crear_proveedor(client, encargado_headers)
    proveedor_id = proveedor["id"]

    assert (
        client.get(
            f"/proveedores/{proveedor_id}", headers=encargado_headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/proveedores/{proveedor_id}",
            json={"telefono": "70012345"},
            headers=encargado_headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/proveedores/{proveedor_id}/productos",
            headers=encargado_headers,
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/proveedores/{proveedor_id}/productos",
            json={"productos": []},
            headers=encargado_headers,
        ).status_code
        == 200
    )


def test_encargado_no_puede_cambiar_estado(
    client, encargado_headers, db_session
):
    proveedor_id = _proveedor_con_productos(db_session)
    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=encargado_headers,
    )
    assert resp.status_code == 403


def test_admin_puede_cambiar_estado(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] is False


# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------


def test_listar_proveedores(client, admin_headers):
    resp = client.get("/proveedores", headers=admin_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_buscar_por_razon_social(client, admin_headers):
    razon = f"ZZCU11RAZON_{_suf()}"
    _crear_proveedor(client, admin_headers, razon_social=razon)
    resp = client.get(
        "/proveedores", params={"buscar": razon}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert any(item["razon_social"] == razon for item in resp.json())


def test_buscar_por_nit(client, admin_headers):
    nit = f"NIT{_suf()}"
    _crear_proveedor(client, admin_headers, nit=nit)
    resp = client.get(
        "/proveedores", params={"buscar": nit}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert any(item["nit"] == nit for item in resp.json())


def test_buscar_por_correo(client, admin_headers):
    correo = f"zzcu11_{_suf()}@correo.test"
    _crear_proveedor(client, admin_headers, correo=correo)
    resp = client.get(
        "/proveedores", params={"buscar": correo}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert any(item["correo"] == correo for item in resp.json())


def test_filtro_estado(client, admin_headers):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]

    activos = client.get(
        "/proveedores", params={"estado": True}, headers=admin_headers
    ).json()
    assert any(item["id"] == proveedor_id for item in activos)

    client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )

    activos = client.get(
        "/proveedores", params={"estado": True}, headers=admin_headers
    ).json()
    assert all(item["id"] != proveedor_id for item in activos)

    inactivos = client.get(
        "/proveedores", params={"estado": False}, headers=admin_headers
    ).json()
    assert any(item["id"] == proveedor_id for item in inactivos)


def test_detalle_proveedor(client, admin_headers):
    proveedor = _crear_proveedor(client, admin_headers)
    resp = client.get(
        f"/proveedores/{proveedor['id']}", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == proveedor["id"]
    assert body["total_productos"] == 0


def test_detalle_inexistente(client, admin_headers):
    assert (
        client.get("/proveedores/999999999", headers=admin_headers).status_code
        == 404
    )


def test_crear_normaliza_y_opcionales_null(client, admin_headers):
    razon = f"  ZZCU11  Normalizada   {_suf()}  "
    resp = client.post(
        "/proveedores",
        json={
            "razon_social": razon,
            "nit": "   ",
            "correo": "  Foo.Bar@Correo.TEST  ",
            "telefono": "   ",
            "direccion": "   ",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["razon_social"] == " ".join(razon.split())
    assert body["nit"] is None
    assert body["correo"] == "foo.bar@correo.test"
    assert body["telefono"] is None
    assert body["direccion"] is None
    assert body["estado"] is True


def test_crear_correo_invalido(client, admin_headers):
    resp = client.post(
        "/proveedores",
        json={
            "razon_social": f"ZZCU11_{_suf()}",
            "correo": "correo-invalido",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_editar_proveedor(client, admin_headers):
    proveedor = _crear_proveedor(client, admin_headers, nit=f"NIT{_suf()}")
    proveedor_id = proveedor["id"]

    resp = client.patch(
        f"/proveedores/{proveedor_id}",
        json={
            "razon_social": f"ZZCU11 Editada {_suf()}",
            "correo": "Editado@Correo.TEST",
            "telefono": "70000000",
            "direccion": "Av. Siempre Viva 123",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["correo"] == "editado@correo.test"
    assert body["telefono"] == "70000000"
    assert body["direccion"] == "Av. Siempre Viva 123"

    resp = client.patch(
        f"/proveedores/{proveedor_id}",
        json={"nit": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["nit"] is None


def test_nit_duplicado(client, admin_headers):
    nit = f"NITDUP{_suf()}"
    _crear_proveedor(client, admin_headers, nit=nit)
    resp = client.post(
        "/proveedores",
        json={"razon_social": f"ZZCU11_{_suf()}", "nit": nit},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_estado_idempotente(client, admin_headers):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]

    for _ in range(2):
        resp = client.patch(
            f"/proveedores/{proveedor_id}/estado",
            json={"estado": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] is False

    for _ in range(2):
        resp = client.patch(
            f"/proveedores/{proveedor_id}/estado",
            json={"estado": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] is True


def test_dependencia_orden_compra_activa(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    db_session.add(
        OrdenCompra(
            proveedor_id=proveedor_id,
            sucursal_id=_sucursal_id(db_session),
            estado="ENVIADA",
        )
    )
    db_session.flush()

    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_orden_compra_finalizada_no_bloquea(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    db_session.add(
        OrdenCompra(
            proveedor_id=proveedor_id,
            sucursal_id=_sucursal_id(db_session),
            estado="RECIBIDA",
        )
    )
    db_session.flush()

    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] is False


def test_deshabilitar_con_productos_sin_ordenes(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)
    put = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "10.00",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert put.status_code == 200, put.text

    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] is False


# ---------------------------------------------------------------------------
# Productos del proveedor
# ---------------------------------------------------------------------------


def test_listar_productos_asociados(client, admin_headers, db_session):
    proveedor_id = _proveedor_con_productos(db_session)
    resp = client.get(
        f"/proveedores/{proveedor_id}/productos", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proveedor_id"] == proveedor_id
    assert body["total"] >= 1
    primero = body["productos"][0]
    assert "costo_referencia" in primero
    assert "estado" in primero
    assert "producto_estado" in primero


def test_put_reemplaza_y_conserva_decimal(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_a, producto_b = _dos_productos_activos(db_session)

    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_a,
                    "costo_referencia": "120.50",
                    "estado": True,
                },
                {
                    "producto_id": producto_b,
                    "costo_referencia": "0",
                    "estado": False,
                },
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    por_id = {item["producto_id"]: item for item in body["productos"]}
    assert _decimal(por_id[producto_a]["costo_referencia"]) == Decimal("120.50")
    assert por_id[producto_a]["estado"] is True
    assert por_id[producto_b]["estado"] is False

    # Reemplazo: dejar solo producto_a con otro costo.
    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_a,
                    "costo_referencia": "99.99",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["productos"][0]["producto_id"] == producto_a


def test_put_dedupe_producto_id(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)

    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "10",
                    "estado": True,
                },
                {
                    "producto_id": producto_id,
                    "costo_referencia": "20",
                    "estado": True,
                },
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert _decimal(body["productos"][0]["costo_referencia"]) == Decimal("20")


def test_put_idempotente_no_duplica_auditoria(
    client, admin_headers, db_session
):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)
    payload = {
        "productos": [
            {
                "producto_id": producto_id,
                "costo_referencia": "55.55",
                "estado": True,
            }
        ]
    }

    primera = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json=payload,
        headers=admin_headers,
    )
    assert primera.status_code == 200, primera.text

    def _eventos() -> int:
        return len(
            db_session.scalars(
                select(Bitacora).where(
                    Bitacora.entidad_afectada == "proveedor_producto",
                    Bitacora.descripcion.ilike(
                        f"%proveedor {proveedor_id} %"
                    ),
                )
            ).all()
        )

    eventos_primera = _eventos()

    segunda = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json=payload,
        headers=admin_headers,
    )
    assert segunda.status_code == 200
    assert segunda.json() == primera.json()
    assert _eventos() == eventos_primera


def test_put_lista_vacia(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)

    client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "5",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={"productos": []},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["productos"] == []


def test_put_producto_inexistente(client, admin_headers):
    proveedor = _crear_proveedor(client, admin_headers)
    resp = client.put(
        f"/proveedores/{proveedor['id']}/productos",
        json={
            "productos": [
                {
                    "producto_id": 999999999,
                    "costo_referencia": "1",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_put_proveedor_inexistente(client, admin_headers):
    resp = client.put(
        "/proveedores/999999999/productos",
        json={"productos": []},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_put_producto_inactivo_nueva_relacion(
    client, admin_headers, db_session
):
    proveedor = _crear_proveedor(client, admin_headers)
    producto_id = _producto_inactivo_id(db_session)
    resp = client.put(
        f"/proveedores/{proveedor['id']}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "1",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_producto_inactivo_ya_asociado_permitido(
    client, admin_headers, db_session
):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)

    primera = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "10",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert primera.status_code == 200

    producto = db_session.get(Producto, producto_id)
    producto.estado = False
    db_session.flush()

    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "15",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _decimal(resp.json()["productos"][0]["costo_referencia"]) == Decimal(
        "15"
    )


def test_put_costo_negativo(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    producto_id = _producto_activo_id(db_session)
    resp = client.put(
        f"/proveedores/{proveedor['id']}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "-1",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_put_estado_relacion(client, admin_headers, db_session):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)

    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "3",
                    "estado": False,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["productos"][0]["estado"] is False

    detalle = client.get(
        f"/proveedores/{proveedor_id}/productos", headers=admin_headers
    ).json()
    assert detalle["productos"][0]["estado"] is False


def test_put_no_modifica_producto_ni_inventario(
    client, admin_headers, db_session
):
    proveedor = _crear_proveedor(client, admin_headers)
    producto_id = _producto_activo_id(db_session)
    producto = db_session.get(Producto, producto_id)
    precio_antes = producto.precio

    resp = client.put(
        f"/proveedores/{proveedor['id']}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "1234.56",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    db_session.expire_all()
    producto = db_session.get(Producto, producto_id)
    assert producto.precio == precio_antes


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_proveedor(
    client, admin_headers, admin_usuario, db_session
):
    razon = f"ZZCU11AUD_{_suf()}"
    _crear_proveedor(client, admin_headers, razon_social=razon)

    eventos = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada == "proveedor",
            Bitacora.descripcion.ilike(f"%{razon}%"),
        )
    ).all()
    assert any(
        evento.accion == "CREAR" and evento.usuario_id == admin_usuario.id
        for evento in eventos
    )


def test_auditoria_proveedor_producto(
    client, admin_headers, admin_usuario, db_session
):
    proveedor = _crear_proveedor(client, admin_headers)
    proveedor_id = proveedor["id"]
    producto_id = _producto_activo_id(db_session)

    client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "7",
                    "estado": True,
                }
            ]
        },
        headers=admin_headers,
    )

    eventos = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada == "proveedor_producto",
            Bitacora.descripcion.ilike(f"%proveedor {proveedor_id} %"),
        )
    ).all()
    assert any(
        evento.accion == "MODIFICAR" and evento.usuario_id == admin_usuario.id
        for evento in eventos
    )
