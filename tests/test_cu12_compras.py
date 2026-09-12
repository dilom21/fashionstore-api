"""Pruebas de CU12 - Gestionar compras a proveedores.

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session, por lo que no quedan ordenes, inventario, movimientos ni
bitacora temporal en la base de datos real.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import (
    Producto,
    Temporada,
    VarianteProducto,
)
from app.modules.compras.models.models import (
    MovimientoInventario,
    OrdenCompra,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _hoy() -> date:
    return datetime.now(_UTC).date()


# ---------------------------------------------------------------------------
# Helpers de datos
# ---------------------------------------------------------------------------


def _sucursales_activas(db_session) -> list[int]:
    return list(
        db_session.scalars(
            select(Sucursal.id)
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.id)
        ).all()
    )


def _sucursal_activa(db_session, *, excluir: int | None = None) -> int:
    for sucursal_id in _sucursales_activas(db_session):
        if sucursal_id != excluir:
            return sucursal_id
    raise AssertionError("No hay sucursal activa disponible")


def _sucursal_inactiva(db_session) -> int:
    sucursal_id = db_session.scalar(
        select(Sucursal.id)
        .where(Sucursal.estado.is_(False))
        .order_by(Sucursal.id)
        .limit(1)
    )
    if sucursal_id is None:
        sucursal_id = _sucursal_activa(db_session)
        db_session.get(Sucursal, sucursal_id).estado = False
        db_session.flush()
    return sucursal_id


def _temporada_activa(db_session) -> int:
    temporada_id = db_session.scalar(
        select(Temporada.id)
        .where(Temporada.estado.is_(True))
        .order_by(Temporada.id)
        .limit(1)
    )
    assert temporada_id is not None
    return temporada_id


def _variante_producto_activo(db_session) -> tuple[int, int]:
    row = db_session.execute(
        select(VarianteProducto.id, VarianteProducto.producto_id)
        .join(Producto, Producto.id == VarianteProducto.producto_id)
        .where(
            VarianteProducto.estado.is_(True),
            Producto.estado.is_(True),
        )
        .order_by(VarianteProducto.id)
        .limit(1)
    ).first()
    assert row is not None
    return int(row[0]), int(row[1])


def _variantes_dos_productos(
    db_session,
) -> tuple[tuple[int, int], tuple[int, int]]:
    rows = db_session.execute(
        select(VarianteProducto.id, VarianteProducto.producto_id)
        .join(Producto, Producto.id == VarianteProducto.producto_id)
        .where(
            VarianteProducto.estado.is_(True),
            Producto.estado.is_(True),
        )
        .order_by(VarianteProducto.id)
    ).all()
    assert len(rows) >= 2
    primera = (int(rows[0][0]), int(rows[0][1]))
    for row in rows[1:]:
        if int(row[1]) != primera[1]:
            return primera, (int(row[0]), int(row[1]))
    raise AssertionError("No hay dos variantes de productos distintos")


def _crear_proveedor_con_producto(
    client, headers, producto_id: int
) -> int:
    resp = client.post(
        "/proveedores",
        json={"razon_social": f"ZZCU12PROV_{_suf()}"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    proveedor_id = resp.json()["id"]

    resp = client.put(
        f"/proveedores/{proveedor_id}/productos",
        json={
            "productos": [
                {
                    "producto_id": producto_id,
                    "costo_referencia": "1.00",
                    "estado": True,
                }
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return proveedor_id


def _crear_proveedor_inactivo(client, admin_headers, db_session) -> int:
    resp = client.post(
        "/proveedores",
        json={"razon_social": f"ZZCU12PROVIN_{_suf()}"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    proveedor_id = resp.json()["id"]
    resp = client.patch(
        f"/proveedores/{proveedor_id}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return proveedor_id


def _payload_detalle(
    variante_id: int,
    temporada_id: int,
    *,
    cantidad: int = 5,
    costo: str = "10.00",
) -> dict:
    return {
        "variante_producto_id": variante_id,
        "temporada_id": temporada_id,
        "cantidad": cantidad,
        "costo_unitario": costo,
    }


def _crear_orden(
    client,
    headers,
    proveedor_id: int,
    sucursal_id: int,
    *,
    observacion: str | None = None,
    fecha_estimada: str | None = None,
):
    payload = {
        "proveedor_id": proveedor_id,
        "sucursal_id": sucursal_id,
    }
    if observacion is not None:
        payload["observacion"] = observacion
    if fecha_estimada is not None:
        payload["fecha_estimada"] = fecha_estimada
    return client.post("/ordenes-compra", json=payload, headers=headers)


def _orden_borrador_con_detalle(
    client, headers, db_session, *, sucursal_id=None, cantidad=5, costo="10.00"
):
    variante_id, producto_id = _variante_producto_activo(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, headers, producto_id
    )
    sucursal_id = sucursal_id or _sucursal_activa(db_session)

    resp = _crear_orden(client, headers, proveedor_id, sucursal_id)
    assert resp.status_code == 201, resp.text
    orden_id = resp.json()["id"]

    resp = client.put(
        f"/ordenes-compra/{orden_id}/detalles",
        json={
            "detalles": [
                _payload_detalle(
                    variante_id, temporada_id, cantidad=cantidad, costo=costo
                )
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return {
        "orden_id": orden_id,
        "proveedor_id": proveedor_id,
        "variante_id": variante_id,
        "temporada_id": temporada_id,
        "sucursal_id": sucursal_id,
        "cantidad": cantidad,
    }


def _forzar_estado(db_session, orden_id: int, estado: str) -> None:
    orden = db_session.get(OrdenCompra, orden_id)
    assert orden is not None
    orden.estado = estado
    db_session.flush()


def _eventos_orden(db_session, orden_id: int) -> list[Bitacora]:
    return list(
        db_session.scalars(
            select(Bitacora).where(
                Bitacora.entidad_afectada == "orden_compra",
                Bitacora.descripcion.ilike(f"%ID {orden_id}%"),
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# SEGURIDAD
# ---------------------------------------------------------------------------


def test_seguridad_401_sin_token(client):
    assert client.get("/ordenes-compra").status_code == 401
    assert client.get("/ordenes-compra/1").status_code == 401
    assert client.get("/ordenes-compra/1/detalles").status_code == 401
    assert (
        client.post(
            "/ordenes-compra",
            json={"proveedor_id": 1, "sucursal_id": 1},
        ).status_code
        == 401
    )
    assert (
        client.patch(
            "/ordenes-compra/1", json={"observacion": "x"}
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/ordenes-compra/1/detalles", json={"detalles": []}
        ).status_code
        == 401
    )
    assert client.patch("/ordenes-compra/1/enviar").status_code == 401
    assert client.patch("/ordenes-compra/1/cancelar").status_code == 401
    assert client.post("/ordenes-compra/1/recibir").status_code == 401


def test_seguridad_403_cajero(client, cajero_headers):
    assert (
        client.get("/ordenes-compra", headers=cajero_headers).status_code
        == 403
    )
    assert (
        client.get("/ordenes-compra/1", headers=cajero_headers).status_code
        == 403
    )
    assert (
        client.get(
            "/ordenes-compra/1/detalles", headers=cajero_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/ordenes-compra",
            json={"proveedor_id": 1, "sucursal_id": 1},
            headers=cajero_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/ordenes-compra/1",
            json={"observacion": "x"},
            headers=cajero_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/ordenes-compra/1/detalles",
            json={"detalles": []},
            headers=cajero_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/ordenes-compra/1/enviar", headers=cajero_headers
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/ordenes-compra/1/cancelar", headers=cajero_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/ordenes-compra/1/recibir", headers=cajero_headers
        ).status_code
        == 403
    )


def test_admin_puede_operar(client, admin_headers, db_session):
    assert (
        client.get("/ordenes-compra", headers=admin_headers).status_code
        == 200
    )
    data = _orden_borrador_con_detalle(
        client, admin_headers, db_session
    )
    assert (
        client.get(
            f"/ordenes-compra/{data['orden_id']}", headers=admin_headers
        ).status_code
        == 200
    )


def test_encargado_puede_crear_consultar_editar(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_id = encargado_usuario.empleado.sucursal_id
    variante_id, producto_id = _variante_producto_activo(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, encargado_headers, producto_id
    )

    resp = _crear_orden(
        client, encargado_headers, proveedor_id, sucursal_id
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["empleado_id"] == encargado_usuario.empleado.id

    orden_id = body["id"]
    assert (
        client.get(
            "/ordenes-compra", headers=encargado_headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/ordenes-compra/{orden_id}", headers=encargado_headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/ordenes-compra/{orden_id}",
            json={"observacion": "editada por encargado"},
            headers=encargado_headers,
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/ordenes-compra/{orden_id}/detalles",
            json={"detalles": [_payload_detalle(variante_id, temporada_id)]},
            headers=encargado_headers,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/ordenes-compra/{orden_id}/enviar", headers=encargado_headers
        ).status_code
        == 200
    )


def test_encargado_no_puede_cancelar(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_id = encargado_usuario.empleado.sucursal_id
    data = _orden_borrador_con_detalle(
        client, encargado_headers, db_session, sucursal_id=sucursal_id
    )
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar",
        headers=encargado_headers,
    )
    assert resp.status_code == 403


def test_admin_puede_cancelar(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CANCELADA"


def test_encargado_bloqueado_en_sucursal_ajena(
    client, admin_headers, encargado_headers, encargado_usuario, db_session
):
    sucursal_encargado = encargado_usuario.empleado.sucursal_id
    sucursal_ajena = _sucursal_activa(
        db_session, excluir=sucursal_encargado
    )

    variante_id, producto_id = _variante_producto_activo(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )

    # Crear en sucursal ajena -> 403.
    resp = _crear_orden(
        client, encargado_headers, proveedor_id, sucursal_ajena
    )
    assert resp.status_code == 403, resp.text

    # Orden creada por el admin en sucursal ajena: el encargado no la ve.
    resp = _crear_orden(
        client, admin_headers, proveedor_id, sucursal_ajena
    )
    assert resp.status_code == 201, resp.text
    orden_ajena = resp.json()["id"]
    client.put(
        f"/ordenes-compra/{orden_ajena}/detalles",
        json={"detalles": [_payload_detalle(variante_id, temporada_id)]},
        headers=admin_headers,
    )

    assert (
        client.get(
            f"/ordenes-compra/{orden_ajena}", headers=encargado_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/ordenes-compra/{orden_ajena}/detalles",
            headers=encargado_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/ordenes-compra/{orden_ajena}",
            json={"observacion": "x"},
            headers=encargado_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/ordenes-compra",
            params={"sucursal_id": sucursal_ajena},
            headers=encargado_headers,
        ).status_code
        == 403
    )

    # La lista del encargado solo contiene ordenes de su sucursal.
    lista = client.get(
        "/ordenes-compra", headers=encargado_headers
    ).json()
    assert all(item["sucursal_id"] == sucursal_encargado for item in lista)


# ---------------------------------------------------------------------------
# ORDENES
# ---------------------------------------------------------------------------


def test_listar_ordenes(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.get("/ordenes-compra", headers=admin_headers)
    assert resp.status_code == 200
    assert any(item["id"] == data["orden_id"] for item in resp.json())


def test_filtros(client, admin_headers, db_session):
    variante_id, producto_id = _variante_producto_activo(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    sucursal_id = _sucursal_activa(db_session)
    marca = f"ZZCU12OBS_{_suf()}"

    resp = _crear_orden(
        client,
        admin_headers,
        proveedor_id,
        sucursal_id,
        observacion=marca,
    )
    assert resp.status_code == 201, resp.text
    orden_id = resp.json()["id"]

    assert (
        client.get(
            "/ordenes-compra",
            params={"buscar": marca},
            headers=admin_headers,
        ).json()
    )
    assert any(
        item["id"] == orden_id
        for item in client.get(
            "/ordenes-compra",
            params={"proveedor_id": proveedor_id},
            headers=admin_headers,
        ).json()
    )
    assert any(
        item["id"] == orden_id
        for item in client.get(
            "/ordenes-compra",
            params={"sucursal_id": sucursal_id},
            headers=admin_headers,
        ).json()
    )
    assert any(
        item["id"] == orden_id
        for item in client.get(
            "/ordenes-compra",
            params={"estado": "BORRADOR"},
            headers=admin_headers,
        ).json()
    )
    assert all(
        item["id"] != orden_id
        for item in client.get(
            "/ordenes-compra",
            params={"estado": "RECIBIDA"},
            headers=admin_headers,
        ).json()
    )

    hoy = _hoy()
    assert any(
        item["id"] == orden_id
        for item in client.get(
            "/ordenes-compra",
            params={
                "fecha_desde": str(hoy - timedelta(days=1)),
                "fecha_hasta": str(hoy + timedelta(days=1)),
            },
            headers=admin_headers,
        ).json()
    )
    assert all(
        item["id"] != orden_id
        for item in client.get(
            "/ordenes-compra",
            params={"fecha_desde": str(hoy + timedelta(days=1))},
            headers=admin_headers,
        ).json()
    )

    assert (
        client.get(
            "/ordenes-compra",
            params={"estado": "INVALIDO"},
            headers=admin_headers,
        ).status_code
        == 400
    )


def test_crear_borrador_deriva_empleado(client, admin_headers, db_session):
    variante_id, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    sucursal_id = _sucursal_activa(db_session)
    fecha_estimada = (
        datetime.now(_UTC) + timedelta(days=2)
    ).isoformat()

    resp = _crear_orden(
        client,
        admin_headers,
        proveedor_id,
        sucursal_id,
        observacion="  compra de temporada  ",
        fecha_estimada=fecha_estimada,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["estado"] == "BORRADOR"
    assert body["fecha_recepcion"] is None
    assert body["observacion"] == "compra de temporada"
    assert body["proveedor_razon_social"] is not None
    assert body["sucursal_nombre"] is not None
    assert body["total_detalles"] == 0


def test_crear_proveedor_inactivo(
    client, admin_headers, db_session
):
    proveedor_id = _crear_proveedor_inactivo(
        client, admin_headers, db_session
    )
    resp = _crear_orden(
        client, admin_headers, proveedor_id, _sucursal_activa(db_session)
    )
    assert resp.status_code == 409


def test_crear_sucursal_inactiva(client, admin_headers, db_session):
    _, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    resp = _crear_orden(
        client, admin_headers, proveedor_id, _sucursal_inactiva(db_session)
    )
    assert resp.status_code == 409


def test_crear_proveedor_inexistente(client, admin_headers, db_session):
    resp = _crear_orden(
        client, admin_headers, 999999999, _sucursal_activa(db_session)
    )
    assert resp.status_code == 404


def test_crear_sucursal_inexistente(client, admin_headers, db_session):
    _, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    resp = _crear_orden(
        client, admin_headers, proveedor_id, 999999999
    )
    assert resp.status_code == 404


def test_crear_fecha_estimada_invalida(client, admin_headers, db_session):
    _, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    ayer = (datetime.now(_UTC) - timedelta(days=1)).isoformat()
    resp = _crear_orden(
        client,
        admin_headers,
        proveedor_id,
        _sucursal_activa(db_session),
        fecha_estimada=ayer,
    )
    assert resp.status_code == 400


def test_editar_orden(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    fecha = (datetime.now(_UTC) + timedelta(days=5)).isoformat()
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}",
        json={"observacion": "nueva observacion", "fecha_estimada": fecha},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["observacion"] == "nueva observacion"
    assert resp.json()["fecha_estimada"] is not None


def test_editar_orden_cancelada(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar", headers=admin_headers
    )
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}",
        json={"observacion": "no permitido"},
        headers=admin_headers,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DETALLES
# ---------------------------------------------------------------------------


def test_get_detalles(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.get(
        f"/ordenes-compra/{data['orden_id']}/detalles", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["orden_compra_id"] == data["orden_id"]
    assert body["total"] == 1
    detalle = body["detalles"][0]
    assert detalle["variante_producto_id"] == data["variante_id"]
    assert detalle["temporada_id"] == data["temporada_id"]
    assert detalle["cantidad"] == data["cantidad"]
    assert detalle["sku"] is not None
    assert detalle["producto_nombre"] is not None
    assert detalle["temporada_nombre"] is not None


def test_put_detalles_reemplaza(client, admin_headers, db_session):
    variante_id, producto_id = _variante_producto_activo(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    sucursal_id = _sucursal_activa(db_session)
    orden_id = _crear_orden(
        client, admin_headers, proveedor_id, sucursal_id
    ).json()["id"]

    resp = client.put(
        f"/ordenes-compra/{orden_id}/detalles",
        json={
            "detalles": [
                _payload_detalle(
                    variante_id, temporada_id, cantidad=3, costo="1.50"
                ),
                _payload_detalle(
                    variante_id, temporada_id, cantidad=9, costo="2.50"
                ),
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Deduplicacion por variante+temporada conservando la ultima.
    assert body["total"] == 1
    assert body["detalles"][0]["cantidad"] == 9
    assert Decimal(body["detalles"][0]["costo_unitario"]) == Decimal("2.50")

    # Reemplazo por lista vacia permitido en BORRADOR.
    resp = client.put(
        f"/ordenes-compra/{orden_id}/detalles",
        json={"detalles": []},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_put_detalles_cantidad_invalida(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(
                    data["variante_id"], data["temporada_id"], cantidad=0
                )
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_put_detalles_costo_negativo(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(
                    data["variante_id"],
                    data["temporada_id"],
                    costo="-1",
                )
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_put_detalles_variante_inexistente(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(999999999, data["temporada_id"])
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_put_detalles_temporada_inexistente(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(data["variante_id"], 999999999)
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_put_detalles_variante_inactiva(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    variante = db_session.get(VarianteProducto, data["variante_id"])
    variante.estado = False
    db_session.flush()
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(data["variante_id"], data["temporada_id"])
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_detalles_temporada_inactiva(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    temporada = db_session.get(Temporada, data["temporada_id"])
    temporada.estado = False
    db_session.flush()
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={
            "detalles": [
                _payload_detalle(data["variante_id"], data["temporada_id"])
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_detalles_producto_no_asociado(
    client, admin_headers, db_session
):
    (var_a, prod_a), (var_b, _prod_b) = _variantes_dos_productos(db_session)
    temporada_id = _temporada_activa(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, prod_a
    )
    orden_id = _crear_orden(
        client, admin_headers, proveedor_id, _sucursal_activa(db_session)
    ).json()["id"]

    resp = client.put(
        f"/ordenes-compra/{orden_id}/detalles",
        json={"detalles": [_payload_detalle(var_b, temporada_id)]},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_detalles_idempotente_no_duplica_auditoria(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    payload = {
        "detalles": [
            _payload_detalle(
                data["variante_id"],
                data["temporada_id"],
                cantidad=7,
                costo="3.30",
            )
        ]
    }

    def _eventos() -> int:
        return len(
            db_session.scalars(
                select(Bitacora).where(
                    Bitacora.entidad_afectada == "detalle_orden_compra",
                    Bitacora.descripcion.ilike(
                        f"%Orden {data['orden_id']}:%"
                    ),
                )
            ).all()
        )

    primera = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json=payload,
        headers=admin_headers,
    )
    assert primera.status_code == 200, primera.text
    eventos_primera = _eventos()

    segunda = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json=payload,
        headers=admin_headers,
    )
    assert segunda.status_code == 200
    assert segunda.json() == primera.json()
    assert _eventos() == eventos_primera


def test_put_detalles_no_editar_enviada(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={"detalles": []},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_detalles_no_editar_recibida(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={"detalles": []},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_put_detalles_no_editar_cancelada(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar", headers=admin_headers
    )
    resp = client.put(
        f"/ordenes-compra/{data['orden_id']}/detalles",
        json={"detalles": []},
        headers=admin_headers,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# ENVIAR
# ---------------------------------------------------------------------------


def test_enviar_borrador_a_enviada(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "ENVIADA"


def test_enviar_sin_detalles(client, admin_headers, db_session):
    _, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    orden_id = _crear_orden(
        client, admin_headers, proveedor_id, _sucursal_activa(db_session)
    ).json()["id"]
    resp = client.patch(
        f"/ordenes-compra/{orden_id}/enviar", headers=admin_headers
    )
    assert resp.status_code == 409


def test_enviar_transicion_invalida(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# CANCELAR
# ---------------------------------------------------------------------------


def test_cancelar_enviada(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CANCELADA"


def test_cancelar_idempotente(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    for _ in range(2):
        resp = client.patch(
            f"/ordenes-compra/{data['orden_id']}/cancelar",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "CANCELADA"


def test_cancelar_recibida(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar", headers=admin_headers
    )
    assert resp.status_code == 409


def test_cancelar_parcial_no_permitido(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    _forzar_estado(db_session, data["orden_id"], "PARCIAL")
    resp = client.patch(
        f"/ordenes-compra/{data['orden_id']}/cancelar", headers=admin_headers
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# RECIBIR
# ---------------------------------------------------------------------------


def _inventario(
    db_session, sucursal_id: int, variante_id: int, temporada_id: int
):
    return db_session.scalar(
        select(Inventario).where(
            Inventario.sucursal_id == sucursal_id,
            Inventario.variante_producto_id == variante_id,
            Inventario.temporada_id == temporada_id,
        )
    )


def test_recibir_enviada_a_recibida_e_inventario(
    client, admin_headers, admin_usuario, db_session
):
    data = _orden_borrador_con_detalle(
        client, admin_headers, db_session, cantidad=6
    )
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )

    db_session.expire_all()
    inventario_antes = _inventario(
        db_session,
        data["sucursal_id"],
        data["variante_id"],
        data["temporada_id"],
    )
    stock_antes = (
        inventario_antes.stock_actual if inventario_antes is not None else 0
    )

    resp = client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "RECIBIDA"
    assert body["fecha_recepcion"] is not None

    db_session.expire_all()
    inventario_despues = _inventario(
        db_session,
        data["sucursal_id"],
        data["variante_id"],
        data["temporada_id"],
    )
    assert inventario_despues is not None
    assert inventario_despues.stock_actual == stock_antes + data["cantidad"]

    movimientos = list(
        db_session.scalars(
            select(MovimientoInventario).where(
                MovimientoInventario.referencia_tipo == "ORDEN_COMPRA",
                MovimientoInventario.referencia_id == data["orden_id"],
            )
        ).all()
    )
    assert len(movimientos) == 1
    movimiento = movimientos[0]
    assert movimiento.tipo == "ENTRADA_COMPRA"
    assert movimiento.cantidad == data["cantidad"]
    assert movimiento.usuario_id == admin_usuario.id


def test_recibir_doble_recepcion(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    assert (
        client.post(
            f"/ordenes-compra/{data['orden_id']}/recibir",
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/ordenes-compra/{data['orden_id']}/recibir",
            headers=admin_headers,
        ).status_code
        == 409
    )


def test_recibir_borrador_rechazado(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    resp = client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    assert resp.status_code == 409


def test_recibir_sin_detalles(client, admin_headers, db_session):
    _, producto_id = _variante_producto_activo(db_session)
    proveedor_id = _crear_proveedor_con_producto(
        client, admin_headers, producto_id
    )
    orden_id = _crear_orden(
        client, admin_headers, proveedor_id, _sucursal_activa(db_session)
    ).json()["id"]
    _forzar_estado(db_session, orden_id, "ENVIADA")
    resp = client.post(
        f"/ordenes-compra/{orden_id}/recibir", headers=admin_headers
    )
    assert resp.status_code == 409


def test_recibir_parcial_segun_sp(client, admin_headers, db_session):
    data = _orden_borrador_con_detalle(
        client, admin_headers, db_session, cantidad=4
    )
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    _forzar_estado(db_session, data["orden_id"], "PARCIAL")

    resp = client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "RECIBIDA"


def test_recibir_encargado_actor_correcto(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_id = encargado_usuario.empleado.sucursal_id
    data = _orden_borrador_con_detalle(
        client, encargado_headers, db_session, sucursal_id=sucursal_id
    )
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar",
        headers=encargado_headers,
    )
    resp = client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir",
        headers=encargado_headers,
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    movimientos = list(
        db_session.scalars(
            select(MovimientoInventario).where(
                MovimientoInventario.referencia_tipo == "ORDEN_COMPRA",
                MovimientoInventario.referencia_id == data["orden_id"],
            )
        ).all()
    )
    assert movimientos
    assert all(
        movimiento.usuario_id == encargado_usuario.id
        for movimiento in movimientos
    )


# ---------------------------------------------------------------------------
# AUDITORIA
# ---------------------------------------------------------------------------


def test_auditoria_orden_sin_duplicado_manual(
    client, admin_headers, admin_usuario, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    eventos = _eventos_orden(db_session, data["orden_id"])
    crear = [
        evento
        for evento in eventos
        if evento.accion == "CREAR"
        and evento.usuario_id == admin_usuario.id
    ]
    assert len(crear) == 1


def test_auditoria_detalle_manual(
    client, admin_headers, admin_usuario, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    eventos = list(
        db_session.scalars(
            select(Bitacora).where(
                Bitacora.entidad_afectada == "detalle_orden_compra",
                Bitacora.descripcion.ilike(f"%Orden {data['orden_id']}:%"),
            )
        ).all()
    )
    assert any(
        evento.accion == "MODIFICAR"
        and evento.usuario_id == admin_usuario.id
        for evento in eventos
    )


def test_auditoria_actor_encargado(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_id = encargado_usuario.empleado.sucursal_id
    data = _orden_borrador_con_detalle(
        client, encargado_headers, db_session, sucursal_id=sucursal_id
    )
    eventos = _eventos_orden(db_session, data["orden_id"])
    assert any(
        evento.accion == "CREAR"
        and evento.usuario_id == encargado_usuario.id
        for evento in eventos
    )


def test_recepcion_no_duplica_movimientos(
    client, admin_headers, db_session
):
    data = _orden_borrador_con_detalle(client, admin_headers, db_session)
    client.patch(
        f"/ordenes-compra/{data['orden_id']}/enviar", headers=admin_headers
    )
    client.post(
        f"/ordenes-compra/{data['orden_id']}/recibir", headers=admin_headers
    )
    db_session.expire_all()
    total = len(
        db_session.scalars(
            select(MovimientoInventario).where(
                MovimientoInventario.referencia_tipo == "ORDEN_COMPRA",
                MovimientoInventario.referencia_id == data["orden_id"],
            )
        ).all()
    )
    assert total == 1


# ---------------------------------------------------------------------------
# REGRESION CU03-CU11
# ---------------------------------------------------------------------------


def test_regresion_cu03_cu11(client, admin_headers, db_session):
    # Publicos / CU06 / CU07 / CU08 / CU09
    assert client.get("/health").status_code == 200
    assert client.get("/health/db").status_code == 200
    assert client.get("/sucursales").status_code == 200
    assert client.get("/categorias").status_code == 200
    assert client.get("/productos").status_code == 200
    producto = db_session.scalar(
        select(Producto).where(Producto.estado.is_(True)).order_by(Producto.id)
    )
    assert producto is not None
    assert (
        client.get(f"/productos/{producto.id}/disponibilidad").status_code
        == 200
    )
    assert client.get("/temporadas", headers=admin_headers).status_code == 200

    # Autenticados CU03/CU04/CU05/CU10/CU11/inventario
    assert client.get("/usuarios", headers=admin_headers).status_code == 200
    assert client.get("/roles", headers=admin_headers).status_code == 200
    assert client.get("/bitacora", headers=admin_headers).status_code == 200
    assert client.get("/inventario", headers=admin_headers).status_code == 200
    assert (
        client.get("/promociones", headers=admin_headers).status_code == 200
    )
    assert (
        client.get("/proveedores", headers=admin_headers).status_code == 200
    )
