"""Pruebas de CU14 - Consultar movimientos de inventario (Kardex).

Todas las escrituras (categoria/producto/variante/temporada/inventario y los
propios movimiento_inventario usados como datos de prueba) ocurren dentro de la
transaccion revertida del fixture db_session, por lo que no quedan datos
temporales en la base real. CU14 es SOLO lectura: la API no inserta, actualiza
ni elimina movimientos. Tampoco ejecuta procedimientos almacenados.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Rol, Usuario
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario

_UTC = timezone.utc

CAMPOS_ITEM = {
    "movimiento_id",
    "inventario_id",
    "usuario_id",
    "usuario_correo",
    "tipo",
    "cantidad",
    "fecha_hora",
    "observaciones",
    "referencia_tipo",
    "referencia_id",
    "sucursal_id",
    "sucursal_nombre",
    "producto_id",
    "producto_nombre",
    "variante_producto_id",
    "sku",
    "talla_id",
    "talla_nombre",
    "color_id",
    "color_nombre",
    "temporada_id",
    "temporada_nombre",
}


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers):
    resp = client.post(
        "/categorias",
        json={"nombre": f"ZZCU14CAT_{_suf()}", "descripcion": "cat CU14"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": f"ZZCU14PROD_{_suf()}",
            "descripcion": "producto CU14",
            "precio": "19.90",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers):
    resp = client.post(
        "/tallas", json={"nombre": f"ZZCU14T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers):
    resp = client.post(
        "/colores", json={"nombre": f"ZZCU14C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU14SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": f"ZZCU14TEMP_{_suf()}",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_base(client, headers):
    categoria = _crear_categoria(client, headers)
    producto = _crear_producto(client, headers, categoria["id"])
    talla = _crear_talla(client, headers)
    color = _crear_color(client, headers)
    variante = _crear_variante(
        client, headers, producto["id"], talla["id"], color["id"]
    )
    temporada = _crear_temporada(client, headers)
    return {
        "producto": producto,
        "variante": variante,
        "temporada": temporada,
    }


def _sucursales_activas(db_session) -> list[int]:
    from app.modules.sucursales.models.models import Sucursal

    return list(
        db_session.scalars(
            select(Sucursal.id)
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.id)
        ).all()
    )


def _segunda_sucursal(db_session) -> int:
    ids = _sucursales_activas(db_session)
    assert len(ids) >= 2, "CU14 requiere al menos dos sucursales activas"
    return ids[1]


def _add_inventario(db_session, *, sucursal_id, variante_id, temporada_id):
    inventario = Inventario(
        sucursal_id=sucursal_id,
        variante_producto_id=variante_id,
        temporada_id=temporada_id,
        stock_actual=0,
        stock_reservado=0,
        fecha_actualizacion=datetime.now(_UTC),
    )
    db_session.add(inventario)
    db_session.flush()
    return inventario


def _add_movimiento(
    db_session,
    *,
    inventario_id,
    tipo,
    cantidad,
    fecha_hora=None,
    usuario_id=None,
    referencia_tipo=None,
    referencia_id=None,
    observaciones=None,
) -> MovimientoInventario:
    movimiento = MovimientoInventario(
        inventario_id=inventario_id,
        usuario_id=usuario_id,
        tipo=tipo,
        cantidad=cantidad,
        fecha_hora=fecha_hora or datetime.now(_UTC),
        observaciones=observaciones,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
    )
    db_session.add(movimiento)
    db_session.flush()
    return movimiento


def _usuario_headers(db_session, rol_nombre: str):
    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu14.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    token = create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": rol.nombre,
            "contexto": "personal",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _consultar(client, headers, **params):
    resp = client.get("/movimientos-inventario", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert body["total"] >= len(body["items"])
    return body


def _movimientos_de(db_session, *, inventario_ids):
    return list(
        db_session.scalars(
            select(MovimientoInventario).where(
                MovimientoInventario.inventario_id.in_(inventario_ids)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Caso 1: sin token -> 401
# ---------------------------------------------------------------------------


def test_sin_token_401(client):
    assert client.get("/movimientos-inventario").status_code == 401


# ---------------------------------------------------------------------------
# Caso 2: Administrador, envelope y valores por defecto (tabla vacia hoy)
# ---------------------------------------------------------------------------


def test_admin_envelope_y_paginacion_por_defecto(client, admin_headers):
    body = _consultar(client, admin_headers)
    assert body["limit"] == 10
    assert body["offset"] == 0
    if body["total"] == 0:
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Caso 3: Administrador ve todas las sucursales
# ---------------------------------------------------------------------------


def test_admin_ve_todas_las_sucursales(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    sucursal_uno = _sucursales_activas(db_session)[0]
    sucursal_dos = _segunda_sucursal(db_session)
    for sucursal_id in (sucursal_uno, sucursal_dos):
        inventario = _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
        )
        _add_movimiento(
            db_session,
            inventario_id=inventario.id,
            tipo="ENTRADA_COMPRA",
            cantidad=20,
            referencia_tipo="ORDEN_COMPRA",
            referencia_id=15,
        )

    body = _consultar(
        client, admin_headers, producto=base["producto"]["nombre"], limit=100
    )
    assert body["total"] == 2
    assert {item["sucursal_id"] for item in body["items"]} == {
        sucursal_uno,
        sucursal_dos,
    }
    for item in body["items"]:
        assert CAMPOS_ITEM.issubset(item.keys())
        assert item["tipo"] == "ENTRADA_COMPRA"
        assert item["cantidad"] == 20
        assert item["referencia_tipo"] == "ORDEN_COMPRA"
        assert item["referencia_id"] == 15
        assert item["producto_nombre"] == base["producto"]["nombre"]
        assert item["variante_producto_id"] == base["variante"]["id"]


# ---------------------------------------------------------------------------
# Caso 4: Administrador filtra por sucursal
# ---------------------------------------------------------------------------


def test_admin_filtra_sucursal(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    sucursal_uno = _sucursales_activas(db_session)[0]
    sucursal_dos = _segunda_sucursal(db_session)
    for sucursal_id in (sucursal_uno, sucursal_dos):
        inventario = _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
        )
        _add_movimiento(
            db_session,
            inventario_id=inventario.id,
            tipo="SALIDA_VENTA",
            cantidad=1,
        )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        sucursal_id=sucursal_uno,
    )
    assert body["total"] == 1
    assert [item["sucursal_id"] for item in body["items"]] == [sucursal_uno]


# ---------------------------------------------------------------------------
# Caso 5: Encargado solo ve movimientos de SU sucursal
# ---------------------------------------------------------------------------


def test_encargado_solo_su_sucursal(
    client, admin_headers, encargado_headers, encargado_usuario, db_session
):
    base = _crear_base(client, admin_headers)
    sucursal_encargado = encargado_usuario.empleado.sucursal_id
    sucursal_ajena = _segunda_sucursal(db_session)
    assert sucursal_ajena != sucursal_encargado
    for sucursal_id in (sucursal_encargado, sucursal_ajena):
        inventario = _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
        )
        _add_movimiento(
            db_session,
            inventario_id=inventario.id,
            tipo="RESERVA",
            cantidad=2,
        )

    body = _consultar(
        client,
        encargado_headers,
        producto=base["producto"]["nombre"],
        limit=100,
    )
    assert body["total"] == 1
    assert {item["sucursal_id"] for item in body["items"]} == {
        sucursal_encargado
    }


# ---------------------------------------------------------------------------
# Caso 6: Encargado intenta otra sucursal -> 403
# ---------------------------------------------------------------------------


def test_encargado_otra_sucursal_403(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_ajena = _segunda_sucursal(db_session)
    assert sucursal_ajena != encargado_usuario.empleado.sucursal_id
    resp = client.get(
        "/movimientos-inventario",
        headers=encargado_headers,
        params={"sucursal_id": sucursal_ajena},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Caso 7: Cajero -> 403
# ---------------------------------------------------------------------------


def test_cajero_403(client, cajero_headers):
    resp = client.get("/movimientos-inventario", headers=cajero_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Caso 8: Cliente -> 403
# ---------------------------------------------------------------------------


def test_cliente_403(client, db_session):
    headers = _usuario_headers(db_session, "CLIENTE")
    resp = client.get("/movimientos-inventario", headers=headers)
    assert resp.status_code == 403


def _preparar_movimientos(client, admin_headers, db_session):
    """Crea un producto con inventario y devuelve el contexto reutilizable."""
    base = _crear_base(client, admin_headers)
    inventario = _add_inventario(
        db_session,
        sucursal_id=_sucursales_activas(db_session)[0],
        variante_id=base["variante"]["id"],
        temporada_id=base["temporada"]["id"],
    )
    base["inventario"] = inventario
    return base


# ---------------------------------------------------------------------------
# Caso 9: filtro tipo
# ---------------------------------------------------------------------------


def test_filtro_tipo(client, admin_headers, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=20,
    )
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="RESERVA",
        cantidad=2,
    )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        tipo="ENTRADA_COMPRA",
    )
    assert body["total"] == 1
    assert body["items"][0]["tipo"] == "ENTRADA_COMPRA"


# ---------------------------------------------------------------------------
# Caso 10: filtro producto parcial case-insensitive
# ---------------------------------------------------------------------------


def test_filtro_producto_parcial_case_insensitive(
    client, admin_headers, db_session
):
    base = _preparar_movimientos(client, admin_headers, db_session)
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="SALIDA_VENTA",
        cantidad=1,
    )
    fragmento = base["producto"]["nombre"][:12].upper()

    body = _consultar(client, admin_headers, producto=fragmento, limit=100)
    assert body["total"] >= 1
    assert any(
        item["variante_producto_id"] == base["variante"]["id"]
        for item in body["items"]
    )


# ---------------------------------------------------------------------------
# Caso 11: filtro fecha_desde / fecha_hasta
# ---------------------------------------------------------------------------


def test_filtro_fechas(client, admin_headers, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    fechas = (
        datetime(2026, 8, 15, 10, 0, tzinfo=_UTC),
        datetime(2026, 9, 10, 10, 0, tzinfo=_UTC),
        datetime(2026, 10, 5, 10, 0, tzinfo=_UTC),
    )
    for fecha in fechas:
        _add_movimiento(
            db_session,
            inventario_id=base["inventario"].id,
            tipo="ENTRADA_COMPRA",
            cantidad=5,
            fecha_hora=fecha,
        )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        fecha_desde="2026-09-01",
        fecha_hasta="2026-09-30",
    )
    assert body["total"] == 1
    assert body["items"][0]["fecha_hora"].startswith("2026-09-10")

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        fecha_desde="2026-09-11",
    )
    assert body["total"] == 1
    assert body["items"][0]["fecha_hora"].startswith("2026-10-05")


# ---------------------------------------------------------------------------
# Caso 12: filtro temporada
# ---------------------------------------------------------------------------


def test_filtro_temporada(client, admin_headers, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="DEVOLUCION",
        cantidad=1,
    )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        temporada_id=base["temporada"]["id"],
    )
    assert body["total"] == 1
    assert body["items"][0]["temporada_id"] == base["temporada"]["id"]
    assert body["items"][0]["temporada_nombre"] == base["temporada"]["nombre"]


# ---------------------------------------------------------------------------
# Usuario que origino la operacion y referencia
# ---------------------------------------------------------------------------


def test_admin_usuario_y_referencia(client, admin_headers, admin_usuario, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=20,
        usuario_id=admin_usuario.id,
        referencia_tipo="ORDEN_COMPRA",
        referencia_id=15,
        observaciones="recepcion de prueba",
    )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        referencia_tipo="ORDEN_COMPRA",
    )
    assert body["total"] == 1
    item = body["items"][0]
    assert item["usuario_id"] == admin_usuario.id
    assert item["usuario_correo"] == admin_usuario.correo
    assert item["referencia_tipo"] == "ORDEN_COMPRA"
    assert item["referencia_id"] == 15
    assert item["observaciones"] == "recepcion de prueba"


# ---------------------------------------------------------------------------
# Casos 13 y 14: paginacion (limit/offset) y segunda pagina
# ---------------------------------------------------------------------------


def test_paginacion(client, admin_headers, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    for indice in range(3):
        _add_movimiento(
            db_session,
            inventario_id=base["inventario"].id,
            tipo="ENTRADA_COMPRA",
            cantidad=indice + 1,
            fecha_hora=datetime(2026, 9, 1 + indice, 10, 0, tzinfo=_UTC),
        )
    nombre = base["producto"]["nombre"]

    pagina_uno = _consultar(client, admin_headers, producto=nombre, limit=10, offset=0)
    assert pagina_uno["limit"] == 10 and pagina_uno["offset"] == 0
    assert pagina_uno["total"] == 3
    assert len(pagina_uno["items"]) == 3

    recorte_uno = _consultar(client, admin_headers, producto=nombre, limit=2, offset=0)
    recorte_dos = _consultar(client, admin_headers, producto=nombre, limit=2, offset=2)
    assert recorte_uno["total"] == recorte_dos["total"] == 3
    assert len(recorte_uno["items"]) == 2
    assert len(recorte_dos["items"]) == 1
    ids_uno = {item["movimiento_id"] for item in recorte_uno["items"]}
    ids_dos = {item["movimiento_id"] for item in recorte_dos["items"]}
    assert ids_uno.isdisjoint(ids_dos)


# ---------------------------------------------------------------------------
# Caso 15: sin coincidencias -> 200 con lista vacia
# ---------------------------------------------------------------------------


def test_sin_coincidencias(client, admin_headers):
    body = _consultar(
        client, admin_headers, producto=f"ZZCU14_NOEXISTE_{_suf()}"
    )
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Caso 16: tipo invalido -> 422
# ---------------------------------------------------------------------------


def test_tipo_invalido_422(client, admin_headers):
    resp = client.get(
        "/movimientos-inventario",
        headers=admin_headers,
        params={"tipo": "AJUSTE_ENTRADA"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Caso 17: fechas invalidas -> 422
# ---------------------------------------------------------------------------


def test_fechas_invalidas_422(client, admin_headers):
    rango_invertido = client.get(
        "/movimientos-inventario",
        headers=admin_headers,
        params={"fecha_desde": "2026-10-01", "fecha_hasta": "2026-09-01"},
    )
    assert rango_invertido.status_code == 422

    formato_invalido = client.get(
        "/movimientos-inventario",
        headers=admin_headers,
        params={"fecha_desde": "no-es-fecha"},
    )
    assert formato_invalido.status_code == 422


# ---------------------------------------------------------------------------
# Caso 18: orden fecha_hora DESC (id DESC como desempate)
# ---------------------------------------------------------------------------


def test_orden_descendente(client, admin_headers, db_session):
    base = _preparar_movimientos(client, admin_headers, db_session)
    primero = _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=1,
        fecha_hora=datetime(2026, 9, 1, 10, 0, tzinfo=_UTC),
    )
    segundo = _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=2,
        fecha_hora=datetime(2026, 9, 2, 10, 0, tzinfo=_UTC),
    )
    tercero = _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=3,
        fecha_hora=datetime(2026, 9, 3, 10, 0, tzinfo=_UTC),
    )

    body = _consultar(
        client, admin_headers, producto=base["producto"]["nombre"], limit=100
    )
    ids = [item["movimiento_id"] for item in body["items"]]
    assert ids == [tercero.id, segundo.id, primero.id]


# ---------------------------------------------------------------------------
# Caso 19: CU14 es solo lectura (no INSERT/UPDATE/DELETE)
# ---------------------------------------------------------------------------


def test_no_modifica_movimientos(client, admin_headers, db_session):
    from sqlalchemy import func

    base = _preparar_movimientos(client, admin_headers, db_session)
    _add_movimiento(
        db_session,
        inventario_id=base["inventario"].id,
        tipo="ENTRADA_COMPRA",
        cantidad=4,
    )
    antes = db_session.scalar(select(func.count()).select_from(MovimientoInventario))

    _consultar(client, admin_headers, producto=base["producto"]["nombre"])

    db_session.expire_all()
    despues = db_session.scalar(
        select(func.count()).select_from(MovimientoInventario)
    )
    assert despues == antes


# ---------------------------------------------------------------------------
# Caso 20: CU13 sigue funcionando (no se rompio)
# ---------------------------------------------------------------------------


def test_cu13_sigue_funcionando(client, admin_headers):
    resp = client.get(
        "/inventario", headers=admin_headers, params={"limit": 1, "offset": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
