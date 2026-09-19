"""Pruebas de CU18 - Atender reserva de prendas (ENCARGADO_SUCURSAL / CAJERO).

Todo ocurre dentro de la transaccion revertida del fixture db_session: no
quedan reservas, stock_reservado ni movimientos en la BD real.

CU18 no vende ni cobra: preparar-venta solo valida y finalizar-sin-compra libera
unicamente stock_reservado mediante sp_finalizar_reserva_sin_compra.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Rol,
    Usuario,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU18CAT_{_suf()}", "descripcion": "cat CU18"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU18PROD_{_suf()}",
            "descripcion": "producto CU18",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU18T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU18C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU18SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU18TEMP_{_suf()}",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _sucursales_activas(db_session) -> list[int]:
    from app.modules.sucursales.models.models import Sucursal

    return list(
        db_session.scalars(
            select(Sucursal.id)
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.id)
        ).all()
    )


def _crear_inventario(
    db_session,
    *,
    sucursal_id,
    variante_id,
    temporada_id,
    stock_actual=10,
    stock_reservado=0,
) -> Inventario:
    inventario = Inventario(
        sucursal_id=sucursal_id,
        variante_producto_id=variante_id,
        temporada_id=temporada_id,
        stock_actual=stock_actual,
        stock_reservado=stock_reservado,
        fecha_actualizacion=datetime.now(_UTC),
    )
    db_session.add(inventario)
    db_session.flush()
    return inventario


def _token(usuario, rol, contexto):
    return create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": rol,
            "contexto": contexto,
        }
    )


def _crear_cliente(db_session, etiqueta="a"):
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None, "No existe el rol CLIENTE"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu18.{etiqueta}.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    cliente = Cliente(
        usuario_id=usuario.id,
        nombre="Harold",
        apellido="Prueba",
        telefono="70000000",
        estado=True,
    )
    db_session.add(cliente)
    db_session.flush()
    return cliente, {"Authorization": f"Bearer {_token(usuario, rol.nombre, 'cliente')}"}


def _crear_personal(db_session, rol_nombre, sucursal_id=None, etiqueta="p"):
    """Usuario de personal; con Empleado activo si se indica sucursal."""
    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu18.{etiqueta}.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()

    if sucursal_id is not None:
        empleado = Empleado(
            usuario_id=usuario.id,
            sucursal_id=sucursal_id,
            nombres="Empleado",
            apellidos="Prueba",
            ci=f"CI{uuid.uuid4().hex[:10]}",
            telefono=None,
            fecha_contratacion=datetime.now(_UTC).date(),
            estado=True,
        )
        db_session.add(empleado)
        db_session.flush()

    return usuario, {"Authorization": f"Bearer {_token(usuario, rol.nombre, 'personal')}"}


def _contexto(
    client,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    stock_actual=10,
    stock_reservado=0,
):
    """Catalogo + inventario nuevo en la sucursal indicada."""
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    temporada = _crear_temporada(client, admin_headers)
    inventario = _crear_inventario(
        db_session,
        sucursal_id=sucursal_id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=stock_actual,
        stock_reservado=stock_reservado,
    )
    return {
        "sucursal_id": sucursal_id,
        "inventario_id": inventario.id,
        "producto": producto,
        "variante": variante,
        "talla": talla,
        "color": color,
        "temporada": temporada,
    }


def _agregar(client, headers, sucursal_id, inventario_id, cantidad=1):
    """POST /carritos/items (CU15)."""
    return client.post(
        "/carritos/items",
        json={
            "sucursal_id": sucursal_id,
            "inventario_id": inventario_id,
            "cantidad": cantidad,
        },
        headers=headers,
    )


def _reservar(client, headers, carrito_id, *, fecha_atencion=None):
    """POST /reservas (CU16)."""
    return client.post(
        "/reservas",
        json={
            "carrito_id": carrito_id,
            "fecha_atencion": (
                fecha_atencion
                or (datetime.now(_UTC) + timedelta(days=2)).isoformat()
            ),
            "observacion": "reserva CU18",
        },
        headers=headers,
    )


def _confirmar_cu17(client, admin_headers, reserva_id):
    """PATCH /reservas-sucursal/{id}/confirmar (CU17)."""
    return client.patch(
        f"/reservas-sucursal/{reserva_id}/confirmar",
        json={},
        headers=admin_headers,
    )


def _reserva(
    client,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    etiqueta="a",
    cantidades=(2,),
    stock_actual=10,
    fecha_atencion=None,
    confirmar=True,
):
    """Reserva real (CU16) con 1..N lineas; CONFIRMADA por CU17 salvo indicacion."""
    cliente, headers = _crear_cliente(db_session, etiqueta)
    inventarios: list[int] = []
    carrito_id = None

    for cantidad in cantidades:
        ctx = _contexto(
            client, admin_headers, db_session, sucursal_id, stock_actual=stock_actual
        )
        agregado = _agregar(
            client, headers, sucursal_id, ctx["inventario_id"], cantidad
        )
        assert agregado.status_code == 200, agregado.text
        carrito_id = agregado.json()["carrito_id"]
        inventarios.append(ctx["inventario_id"])

    respuesta = _reservar(
        client, headers, carrito_id, fecha_atencion=fecha_atencion
    )
    assert respuesta.status_code == 201, respuesta.text
    reserva_id = respuesta.json()["reserva_id"]

    if confirmar:
        confirmacion = _confirmar_cu17(client, admin_headers, reserva_id)
        assert confirmacion.status_code == 200, confirmacion.text

    return {
        "reserva_id": reserva_id,
        "carrito_id": carrito_id,
        "inventarios": inventarios,
        "cantidades": list(cantidades),
        "cliente": cliente,
        "cliente_headers": headers,
        "sucursal_id": sucursal_id,
    }


def _listar_atencion(client, headers, **params):
    return client.get("/atencion-reservas", params=params, headers=headers)


def _detalle_atencion(client, headers, reserva_id):
    return client.get(f"/atencion-reservas/{reserva_id}", headers=headers)


def _preparar_venta(client, headers, reserva_id, items):
    return client.post(
        f"/atencion-reservas/{reserva_id}/preparar-venta",
        json={"items": items},
        headers=headers,
    )


def _finalizar_sin_compra(client, headers, reserva_id, observacion=None):
    return client.post(
        f"/atencion-reservas/{reserva_id}/finalizar-sin-compra",
        json={"observacion": observacion},
        headers=headers,
    )


def _stock(db_session, inventario_id) -> tuple[int, int]:
    db_session.expire_all()
    inventario = db_session.get(Inventario, inventario_id)
    return inventario.stock_actual, inventario.stock_reservado


def _movimientos(db_session, inventario_id, tipo=None):
    db_session.expire_all()
    condiciones = [MovimientoInventario.inventario_id == inventario_id]
    if tipo is not None:
        condiciones.append(MovimientoInventario.tipo == tipo)
    return list(
        db_session.scalars(select(MovimientoInventario).where(*condiciones)).all()
    )


def _contar_liberaciones(db_session, inventario_id) -> int:
    return len(_movimientos(db_session, inventario_id, "LIBERACION_RESERVA"))


def _estado(db_session, reserva_id) -> str:
    db_session.expire_all()
    return db_session.get(Reserva, reserva_id).estado


def _ids(respuesta) -> set[int]:
    return {item["reserva_id"] for item in respuesta.json()["items"]}


# ---------------------------------------------------------------------------
# A/B - ENCARGADO y CAJERO listan las CONFIRMADA de su sucursal
# ---------------------------------------------------------------------------


def test_a_encargado_lista_confirmadas_de_su_sucursal(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(2,)
    )

    resp = _listar_atencion(client, encargado)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert reserva["reserva_id"] in _ids(resp)
    assert all(item["sucursal_id"] == mia for item in body["items"])
    assert all(item["estado"] == "CONFIRMADA" for item in body["items"])
    assert body["limit"] == 20 and body["offset"] == 0
    assert body["total"] == len(body["items"])


def test_b_cajero_lista_confirmadas_de_su_sucursal(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="b", cantidades=(1,)
    )

    resp = _listar_atencion(client, cajero)
    assert resp.status_code == 200, resp.text
    assert reserva["reserva_id"] in _ids(resp)
    assert all(item["estado"] == "CONFIRMADA" for item in resp.json()["items"])


# ---------------------------------------------------------------------------
# C - ADMINISTRADOR: alcance global (ver todo, filtrar y atender cualquiera)
# ---------------------------------------------------------------------------


def test_c_administrador_lista_todas_las_sucursales(
    client, admin_headers, db_session
):
    mia, otra = _sucursales_activas(db_session)[:2]
    propia = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _listar_atencion(client, admin_headers)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert {propia["reserva_id"], ajena["reserva_id"]} <= ids
    assert all(item["estado"] == "CONFIRMADA" for item in resp.json()["items"])
    # Alcance global: se ven al menos dos sucursales distintas
    assert {item["sucursal_id"] for item in resp.json()["items"]} >= {mia, otra}


def test_c_administrador_filtra_por_sucursal(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    propia = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")

    solo_otra = _listar_atencion(client, admin_headers, sucursal_id=otra)
    assert solo_otra.status_code == 200, solo_otra.text
    assert ajena["reserva_id"] in _ids(solo_otra)
    assert propia["reserva_id"] not in _ids(solo_otra)
    assert all(item["sucursal_id"] == otra for item in solo_otra.json()["items"])

    solo_mia = _listar_atencion(client, admin_headers, sucursal_id=mia)
    assert solo_mia.status_code == 200, solo_mia.text
    assert propia["reserva_id"] in _ids(solo_mia)
    assert ajena["reserva_id"] not in _ids(solo_mia)


def test_c_administrador_atiende_cualquier_sucursal(
    client, admin_headers, db_session
):
    mia, otra = _sucursales_activas(db_session)[:2]
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")
    inventario_id = ajena["inventarios"][0]

    detalle = _detalle_atencion(client, admin_headers, ajena["reserva_id"])
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["sucursal_id"] == otra

    preparado = _preparar_venta(
        client,
        admin_headers,
        ajena["reserva_id"],
        [{"inventario_id": inventario_id, "cantidad_compra": 1}],
    )
    assert preparado.status_code == 200, preparado.text
    assert _estado(db_session, ajena["reserva_id"]) == "CONFIRMADA"

    finalizada = _finalizar_sin_compra(
        client, admin_headers, ajena["reserva_id"]
    )
    assert finalizada.status_code == 200, finalizada.text
    assert finalizada.json()["estado"] == "ATENDIDA"
    assert _estado(db_session, ajena["reserva_id"]) == "ATENDIDA"


# ---------------------------------------------------------------------------
# C2 - ENCARGADO_SUCURSAL / CAJERO no amplian alcance con sucursal_id
# ---------------------------------------------------------------------------


def test_c2_encargado_y_cajero_no_amplian_scope(
    client, admin_headers, db_session
):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    propia = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")

    for headers in (encargado, cajero):
        # Sin sucursal_id: solo la suya
        base = _listar_atencion(client, headers)
        assert base.status_code == 200, base.text
        assert propia["reserva_id"] in _ids(base)
        assert ajena["reserva_id"] not in _ids(base)

        # Con su propia sucursal: permitido y sin cambios
        suya = _listar_atencion(client, headers, sucursal_id=mia)
        assert suya.status_code == 200, suya.text
        assert ajena["reserva_id"] not in _ids(suya)
        assert all(item["sucursal_id"] == mia for item in suya.json()["items"])

        # Con otra sucursal: 403 (no puede ampliar alcance)
        intento = _listar_atencion(client, headers, sucursal_id=otra)
        assert intento.status_code == 403, intento.text

        # La reserva ajena sigue fuera de su alcance
        assert _detalle_atencion(client, headers, ajena["reserva_id"]).status_code == 403



# ---------------------------------------------------------------------------
# D - CLIENTE queda fuera de CU18 (sin romper su CU16)
# ---------------------------------------------------------------------------


def test_d_cliente_recibe_403(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="d")
    headers = reserva["cliente_headers"]

    assert _listar_atencion(client, headers).status_code == 403
    assert _detalle_atencion(client, headers, reserva["reserva_id"]).status_code == 403
    assert (
        _preparar_venta(
            client,
            headers,
            reserva["reserva_id"],
            [{"inventario_id": reserva["inventarios"][0], "cantidad_compra": 1}],
        ).status_code
        == 403
    )
    assert _finalizar_sin_compra(client, headers, reserva["reserva_id"]).status_code == 403
    assert _listar_atencion(client, headers, sucursal_id=mia).status_code == 403

    propias = client.get("/reservas", headers=headers)
    assert propias.status_code == 200, propias.text
    assert propias.json()["total_reservas"] == 1


# ---------------------------------------------------------------------------
# E - Sin token
# ---------------------------------------------------------------------------


def test_e_sin_token_401(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="e")

    assert client.get("/atencion-reservas").status_code == 401
    assert client.get(f"/atencion-reservas/{reserva['reserva_id']}").status_code == 401
    assert (
        client.post(
            f"/atencion-reservas/{reserva['reserva_id']}/preparar-venta",
            json={"items": [{"inventario_id": 1, "cantidad_compra": 1}]},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/atencion-reservas/{reserva['reserva_id']}/finalizar-sin-compra",
            json={},
        ).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# F - El listado no devuelve reservas de otra sucursal
# ---------------------------------------------------------------------------


def test_f_listado_no_devuelve_otra_sucursal(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    propia = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _listar_atencion(client, encargado)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert propia["reserva_id"] in ids
    assert ajena["reserva_id"] not in ids
    assert all(item["sucursal_id"] == mia for item in resp.json()["items"])


# ---------------------------------------------------------------------------
# G/H - Solo CONFIRMADA; PENDIENTE no aparece
# ---------------------------------------------------------------------------


def test_g_h_listado_solo_confirmadas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    confirmada = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    pendiente = _reserva(
        client, admin_headers, db_session, mia, etiqueta="b", confirmar=False
    )
    assert _estado(db_session, pendiente["reserva_id"]) == "PENDIENTE"

    resp = _listar_atencion(client, encargado)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert confirmada["reserva_id"] in ids
    assert pendiente["reserva_id"] not in ids
    assert all(item["estado"] == "CONFIRMADA" for item in resp.json()["items"])


# ---------------------------------------------------------------------------
# I - CANCELADA no aparece
# ---------------------------------------------------------------------------


def test_i_listado_no_muestra_canceladas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")

    cancelada = client.patch(
        f"/reservas-sucursal/{reserva['reserva_id']}/cancelar",
        json={},
        headers=admin_headers,
    )
    assert cancelada.status_code == 200, cancelada.text
    assert _estado(db_session, reserva["reserva_id"]) == "CANCELADA"

    resp = _listar_atencion(client, encargado)
    assert resp.status_code == 200, resp.text
    assert reserva["reserva_id"] not in _ids(resp)


# ---------------------------------------------------------------------------
# J - ATENDIDA no aparece como pendiente de atencion
# ---------------------------------------------------------------------------


def test_j_listado_no_muestra_atendidas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")

    atendida = _finalizar_sin_compra(
        client, encargado, reserva["reserva_id"]
    )
    assert atendida.status_code == 200, atendida.text
    assert _estado(db_session, reserva["reserva_id"]) == "ATENDIDA"

    resp = _listar_atencion(client, encargado)
    assert resp.status_code == 200, resp.text
    assert reserva["reserva_id"] not in _ids(resp)
    assert all(item["estado"] == "CONFIRMADA" for item in resp.json()["items"])


# ---------------------------------------------------------------------------
# K - Busqueda por id de reserva, nombre y apellido del cliente
# ---------------------------------------------------------------------------


def test_k_busqueda(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    objetivo = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    otro = _reserva(client, admin_headers, db_session, mia, etiqueta="b")

    por_id = _listar_atencion(client, encargado, buscar=str(objetivo["reserva_id"]))
    assert por_id.status_code == 200, por_id.text
    assert objetivo["reserva_id"] in _ids(por_id)
    assert otro["reserva_id"] not in _ids(por_id)

    por_nombre = _listar_atencion(client, encargado, buscar="Harold")
    assert por_nombre.status_code == 200, por_nombre.text
    assert objetivo["reserva_id"] in _ids(por_nombre)

    por_apellido = _listar_atencion(client, encargado, buscar="Prueb")
    assert por_apellido.status_code == 200, por_apellido.text
    assert objetivo["reserva_id"] in _ids(por_apellido)


# ---------------------------------------------------------------------------
# L/M - Rango de fechas de atencion
# ---------------------------------------------------------------------------


def test_l_m_rango_de_fechas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    cercana = _reserva(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=2)).isoformat(),
    )
    lejana = _reserva(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="b",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=40)).isoformat(),
    )

    desde = (datetime.now(_UTC) + timedelta(days=1)).date().isoformat()
    hasta = (datetime.now(_UTC) + timedelta(days=5)).date().isoformat()

    resp = _listar_atencion(client, encargado, fecha_desde=desde, fecha_hasta=hasta)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert cercana["reserva_id"] in ids
    assert lejana["reserva_id"] not in ids

    invalido = _listar_atencion(
        client, encargado, fecha_desde="2026-10-01", fecha_hasta="2026-09-01"
    )
    assert invalido.status_code == 422, invalido.text


# ---------------------------------------------------------------------------
# N - Paginacion
# ---------------------------------------------------------------------------


def test_n_paginacion(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    primera = _reserva(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=1)).isoformat(),
    )
    segunda = _reserva(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="b",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=2)).isoformat(),
    )

    todo = _listar_atencion(client, encargado)
    assert todo.status_code == 200, todo.text
    assert todo.json()["total"] >= 2

    primera_pagina = _listar_atencion(client, encargado, limit=1, offset=0)
    assert primera_pagina.status_code == 200, primera_pagina.text
    body = primera_pagina.json()
    assert body["limit"] == 1 and body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["total"] == todo.json()["total"]
    # Orden: fecha_atencion ASC -> la mas proxima primero
    assert body["items"][0]["reserva_id"] == primera["reserva_id"]

    segunda_pagina = _listar_atencion(client, encargado, limit=1, offset=1)
    assert segunda_pagina.status_code == 200, segunda_pagina.text
    assert segunda_pagina.json()["items"][0]["reserva_id"] == segunda["reserva_id"]

    invalido = _listar_atencion(client, encargado, limit=0)
    assert invalido.status_code == 422


# ---------------------------------------------------------------------------
# O/P - Detalle propio para ENCARGADO y CAJERO
# ---------------------------------------------------------------------------


def test_o_encargado_obtiene_detalle_propio(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3,)
    )

    resp = _detalle_atencion(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reserva_id"] == reserva["reserva_id"]
    assert body["estado"] == "CONFIRMADA"
    assert body["sucursal_id"] == mia
    assert body["cliente_id"] == reserva["cliente"].id
    assert body["cliente_nombre"] == "Harold"
    assert body["cliente_apellido"] == "Prueba"
    assert body["fecha_reserva"] is not None
    assert body["fecha_atencion"] is not None
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["inventario_id"] == reserva["inventarios"][0]
    assert item["cantidad_reservada"] == 3
    assert item["producto_nombre"]
    assert item["sku"]
    assert item["talla_nombre"]
    assert item["color_nombre"]
    assert item["temporada_nombre"]
    assert "imagen_principal" in item
    assert body["cantidad_total_unidades"] == 3


def test_p_cajero_obtiene_detalle_propio(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(2, 1)
    )

    resp = _detalle_atencion(client, cajero, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    assert sorted(item["cantidad_reservada"] for item in body["items"]) == [1, 2]
    assert body["cantidad_total_unidades"] == 3


# ---------------------------------------------------------------------------
# Q - Detalle de otra sucursal
# ---------------------------------------------------------------------------


def test_q_detalle_otra_sucursal_403(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="b")

    assert _detalle_atencion(client, encargado, ajena["reserva_id"]).status_code == 403
    assert _detalle_atencion(client, cajero, ajena["reserva_id"]).status_code == 403


# ---------------------------------------------------------------------------
# R - Reserva inexistente
# ---------------------------------------------------------------------------


def test_r_reserva_inexistente_404(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    assert _detalle_atencion(client, encargado, 999_999_999).status_code == 404
    assert (
        _preparar_venta(
            client,
            encargado,
            999_999_999,
            [{"inventario_id": 1, "cantidad_compra": 1}],
        ).status_code
        == 404
    )
    assert _finalizar_sin_compra(client, encargado, 999_999_999).status_code == 404


# ---------------------------------------------------------------------------
# S/T/U - Reserva existente pero no atendible
# ---------------------------------------------------------------------------


def test_s_pendiente_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    pendiente = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", confirmar=False
    )

    assert _detalle_atencion(client, encargado, pendiente["reserva_id"]).status_code == 409
    assert (
        _preparar_venta(
            client,
            encargado,
            pendiente["reserva_id"],
            [{"inventario_id": pendiente["inventarios"][0], "cantidad_compra": 1}],
        ).status_code
        == 409
    )
    assert (
        _finalizar_sin_compra(client, encargado, pendiente["reserva_id"]).status_code
        == 409
    )
    assert _estado(db_session, pendiente["reserva_id"]) == "PENDIENTE"


def test_t_cancelada_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")

    cancelada = client.patch(
        f"/reservas-sucursal/{reserva['reserva_id']}/cancelar",
        json={},
        headers=admin_headers,
    )
    assert cancelada.status_code == 200, cancelada.text

    assert _detalle_atencion(client, encargado, reserva["reserva_id"]).status_code == 409
    assert (
        _finalizar_sin_compra(client, encargado, reserva["reserva_id"]).status_code
        == 409
    )
    assert _estado(db_session, reserva["reserva_id"]) == "CANCELADA"


def test_u_atendida_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")

    ok = _finalizar_sin_compra(client, encargado, reserva["reserva_id"])
    assert ok.status_code == 200, ok.text

    assert _detalle_atencion(client, encargado, reserva["reserva_id"]).status_code == 409
    assert (
        _preparar_venta(
            client,
            encargado,
            reserva["reserva_id"],
            [{"inventario_id": reserva["inventarios"][0], "cantidad_compra": 1}],
        ).status_code
        == 409
    )


# ---------------------------------------------------------------------------
# V - Seleccion completa valida
# ---------------------------------------------------------------------------


def test_v_seleccion_completa_valida(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]

    resp = _preparar_venta(
        client,
        cajero,
        reserva["reserva_id"],
        [{"inventario_id": inventario_id, "cantidad_compra": 3}],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reserva_id"] == reserva["reserva_id"]
    assert body["sucursal_id"] == mia
    assert body["estado"] == "CONFIRMADA"
    assert body["venta_registrada"] is False
    assert body["reserva_modificada"] is False
    assert body["items"] == [
        {
            "inventario_id": inventario_id,
            "cantidad_reservada": 3,
            "cantidad_compra": 3,
            "cantidad_no_compra": 0,
        }
    ]
    assert body["total_unidades_reservadas"] == 3
    assert body["total_unidades_compra"] == 3
    assert body["total_unidades_no_compra"] == 0


# ---------------------------------------------------------------------------
# W/AL - Compra parcial y linea reservada no enviada
# ---------------------------------------------------------------------------


def test_w_al_compra_parcial_y_linea_no_enviada(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    # Linea A reserva 3, linea B reserva 1
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]

    resp = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": a, "cantidad_compra": 2}],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    por_inventario = {item["inventario_id"]: item for item in body["items"]}
    assert por_inventario[a] == {
        "inventario_id": a,
        "cantidad_reservada": 3,
        "cantidad_compra": 2,
        "cantidad_no_compra": 1,
    }
    # B no fue enviada -> aparece con compra 0 (respuesta normalizada completa)
    assert por_inventario[b] == {
        "inventario_id": b,
        "cantidad_reservada": 1,
        "cantidad_compra": 0,
        "cantidad_no_compra": 1,
    }
    assert body["total_unidades_reservadas"] == 4
    assert body["total_unidades_compra"] == 2
    assert body["total_unidades_no_compra"] == 2


# ---------------------------------------------------------------------------
# X - Seleccion de varias lineas
# ---------------------------------------------------------------------------


def test_x_seleccion_varias_lineas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3, 2)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]

    resp = _preparar_venta(
        client,
        cajero,
        reserva["reserva_id"],
        [
            {"inventario_id": a, "cantidad_compra": 1},
            {"inventario_id": b, "cantidad_compra": 2},
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_unidades_reservadas"] == 5
    assert body["total_unidades_compra"] == 3
    assert body["total_unidades_no_compra"] == 2


# ---------------------------------------------------------------------------
# Y/AA/Z - Cantidades invalidas
# ---------------------------------------------------------------------------


def test_y_cantidad_mayor_a_reservada_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3,)
    )

    resp = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": reserva["inventarios"][0], "cantidad_compra": 4}],
    )
    assert resp.status_code == 422, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


def test_z_aa_cantidad_cero_o_negativa_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(2,)
    )
    inventario_id = reserva["inventarios"][0]

    cero = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": inventario_id, "cantidad_compra": 0}],
    )
    assert cero.status_code == 422, cero.text

    negativa = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": inventario_id, "cantidad_compra": -1}],
    )
    assert negativa.status_code == 422, negativa.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


# ---------------------------------------------------------------------------
# AB/AC/AD - Inventario ajeno, duplicado y items vacio
# ---------------------------------------------------------------------------


def test_ab_inventario_de_otra_reserva_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    otra = _reserva(client, admin_headers, db_session, mia, etiqueta="b")

    ajeno = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": otra["inventarios"][0], "cantidad_compra": 1}],
    )
    assert ajeno.status_code == 422, ajeno.text

    # Inventario real pero sin stock reservado (nunca reservado)
    libre = _contexto(client, admin_headers, db_session, mia)["inventario_id"]
    inventado = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [{"inventario_id": libre, "cantidad_compra": 1}],
    )
    assert inventado.status_code == 422, inventado.text


def test_ac_inventario_duplicado_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]

    resp = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [
            {"inventario_id": inventario_id, "cantidad_compra": 1},
            {"inventario_id": inventario_id, "cantidad_compra": 1},
        ],
    )
    assert resp.status_code == 422, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


def test_ad_items_vacio_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")

    resp = _preparar_venta(client, encargado, reserva["reserva_id"], [])
    assert resp.status_code == 422, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


# ---------------------------------------------------------------------------
# AE/AF - Scope y estado en preparar-venta
# ---------------------------------------------------------------------------


def test_ae_af_preparar_venta_scope_y_estado(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="a")
    ajeno = _preparar_venta(
        client,
        encargado,
        ajena["reserva_id"],
        [{"inventario_id": ajena["inventarios"][0], "cantidad_compra": 1}],
    )
    assert ajeno.status_code == 403, ajeno.text
    assert _estado(db_session, ajena["reserva_id"]) == "CONFIRMADA"

    pendiente = _reserva(
        client, admin_headers, db_session, mia, etiqueta="b", confirmar=False
    )
    no_confirmada = _preparar_venta(
        client,
        encargado,
        pendiente["reserva_id"],
        [{"inventario_id": pendiente["inventarios"][0], "cantidad_compra": 1}],
    )
    assert no_confirmada.status_code == 409, no_confirmada.text


# ---------------------------------------------------------------------------
# AG/AH/AI/AJ/AK - preparar-venta no modifica la base de datos
# ---------------------------------------------------------------------------


def test_ag_aj_preparar_venta_no_modifica_nada(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(3, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]

    stock_a_antes = _stock(db_session, a)
    stock_b_antes = _stock(db_session, b)
    mov_a_antes = len(_movimientos(db_session, a))
    mov_b_antes = len(_movimientos(db_session, b))

    resp = _preparar_venta(
        client,
        encargado,
        reserva["reserva_id"],
        [
            {"inventario_id": a, "cantidad_compra": 2},
            {"inventario_id": b, "cantidad_compra": 1},
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # AG - estado intacto
    assert body["estado"] == "CONFIRMADA"
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    # AH/AI - inventario intacto (stock_actual y stock_reservado)
    assert _stock(db_session, a) == stock_a_antes
    assert _stock(db_session, b) == stock_b_antes
    # AJ - sin movimientos nuevos
    assert len(_movimientos(db_session, a)) == mov_a_antes
    assert len(_movimientos(db_session, b)) == mov_b_antes
    assert _movimientos(db_session, a, "LIBERACION_RESERVA") == []
    assert _movimientos(db_session, b, "LIBERACION_RESERVA") == []
    # AK - calculo por linea y totales
    por_inventario = {item["inventario_id"]: item for item in body["items"]}
    assert por_inventario[a]["cantidad_reservada"] == 3
    assert por_inventario[a]["cantidad_compra"] == 2
    assert por_inventario[a]["cantidad_no_compra"] == 1
    assert por_inventario[b]["cantidad_reservada"] == 1
    assert por_inventario[b]["cantidad_compra"] == 1
    assert por_inventario[b]["cantidad_no_compra"] == 0
    assert body["total_unidades_reservadas"] == 4
    assert body["total_unidades_compra"] == 3
    assert body["total_unidades_no_compra"] == 1

    # El detalle tampoco cambio
    detalle = _detalle_atencion(client, encargado, reserva["reserva_id"])
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["estado"] == "CONFIRMADA"
    cantidades = {
        item["inventario_id"]: item["cantidad_reservada"]
        for item in detalle.json()["items"]
    }
    assert cantidades == {a: 3, b: 1}


# ---------------------------------------------------------------------------
# AM..AU - Finalizar sin compra (exito)
# ---------------------------------------------------------------------------


def test_am_au_finalizar_sin_compra_exito(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    usuario, encargado = _crear_personal(
        db_session, "ENCARGADO_SUCURSAL", mia, "enc1"
    )
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(2, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]
    stock_a_antes = _stock(db_session, a)
    stock_b_antes = _stock(db_session, b)

    observacion = "Cliente fue atendido pero decidio no llevar prendas"
    resp = _finalizar_sin_compra(
        client, encargado, reserva["reserva_id"], observacion=observacion
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # AN - la reserva pasa a ATENDIDA
    assert body["estado"] == "ATENDIDA"
    assert _estado(db_session, reserva["reserva_id"]) == "ATENDIDA"
    # AU - observacion registrada sin perder la original
    assert observacion in body["observacion"]

    # AO - stock_actual exactamente igual
    assert _stock(db_session, a)[0] == stock_a_antes[0]
    assert _stock(db_session, b)[0] == stock_b_antes[0]
    # AP - stock_reservado disminuye exactamente por detalle_reserva
    assert _stock(db_session, a)[1] == stock_a_antes[1] - 2
    assert _stock(db_session, b)[1] == stock_b_antes[1] - 1

    # AQ/AR/AS/AT - LIBERACION_RESERVA por cada detalle con trazabilidad
    for inventario_id, cantidad in ((a, 2), (b, 1)):
        liberaciones = _movimientos(db_session, inventario_id, "LIBERACION_RESERVA")
        assert len(liberaciones) == 1, liberaciones
        movimiento = liberaciones[0]
        assert movimiento.cantidad == cantidad
        assert movimiento.usuario_id == usuario.id
        assert movimiento.referencia_tipo == "RESERVA"
        assert movimiento.referencia_id == reserva["reserva_id"]
        assert observacion in (movimiento.observaciones or "")


# ---------------------------------------------------------------------------
# AV - Doble finalizacion (la segunda debe fallar con 409)
# ---------------------------------------------------------------------------


def test_av_doble_finalizacion_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero1")
    reserva = _reserva(client, admin_headers, db_session, mia, etiqueta="a")
    inventario_id = reserva["inventarios"][0]
    stock_antes = _stock(db_session, inventario_id)

    primera = _finalizar_sin_compra(client, cajero, reserva["reserva_id"])
    assert primera.status_code == 200, primera.text
    stock_tras_primera = _stock(db_session, inventario_id)
    liberaciones = len(_movimientos(db_session, inventario_id, "LIBERACION_RESERVA"))

    segunda = _finalizar_sin_compra(client, cajero, reserva["reserva_id"])
    assert segunda.status_code == 409, segunda.text

    # La segunda llamada no libera ni descuenta nada mas
    assert _estado(db_session, reserva["reserva_id"]) == "ATENDIDA"
    assert _stock(db_session, inventario_id) == stock_tras_primera
    assert _stock(db_session, inventario_id)[1] == stock_antes[1] - 2
    assert (
        len(_movimientos(db_session, inventario_id, "LIBERACION_RESERVA"))
        == liberaciones
    )


# ---------------------------------------------------------------------------
# AW/AX - Finalizar estados no atendibles
# ---------------------------------------------------------------------------


def test_aw_ax_finalizar_estados_invalidos(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    stock_actual = 10

    pendiente = _reserva(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        stock_actual=stock_actual,
        confirmar=False,
    )
    liberaciones_pendiente = _contar_liberaciones(
        db_session, pendiente["inventarios"][0]
    )
    assert (
        _finalizar_sin_compra(client, encargado, pendiente["reserva_id"]).status_code
        == 409
    )
    assert _estado(db_session, pendiente["reserva_id"]) == "PENDIENTE"
    # El intento fallido no debe aportar ninguna LIBERACION_RESERVA nueva.
    assert (
        _contar_liberaciones(db_session, pendiente["inventarios"][0])
        == liberaciones_pendiente
    )

    cancelada = _reserva(
        client, admin_headers, db_session, mia, etiqueta="b", stock_actual=stock_actual
    )
    cancelacion = client.patch(
        f"/reservas-sucursal/{cancelada['reserva_id']}/cancelar",
        json={},
        headers=admin_headers,
    )
    assert cancelacion.status_code == 200, cancelacion.text
    assert _estado(db_session, cancelada["reserva_id"]) == "CANCELADA"
    # La cancelacion (CU17) ya genero sus LIBERACION_RESERVA: CU18 no anade mas.
    liberaciones_cancelada = _contar_liberaciones(
        db_session, cancelada["inventarios"][0]
    )
    assert liberaciones_cancelada >= 1
    assert (
        _finalizar_sin_compra(client, encargado, cancelada["reserva_id"]).status_code
        == 409
    )
    assert (
        _contar_liberaciones(db_session, cancelada["inventarios"][0])
        == liberaciones_cancelada
    )

    # AY - ATENDIDA (via una primera finalizacion correcta) -> 409
    atendible = _reserva(
        client, admin_headers, db_session, mia, etiqueta="c", stock_actual=stock_actual
    )
    assert (
        _finalizar_sin_compra(client, encargado, atendible["reserva_id"]).status_code
        == 200
    )
    liberaciones_atendible = _contar_liberaciones(
        db_session, atendible["inventarios"][0]
    )
    assert liberaciones_atendible == 1
    assert (
        _finalizar_sin_compra(client, encargado, atendible["reserva_id"]).status_code
        == 409
    )
    assert (
        _contar_liberaciones(db_session, atendible["inventarios"][0])
        == liberaciones_atendible
    )


# ---------------------------------------------------------------------------
# AZ/BA - Scope y existencia en finalizar-sin-compra
# ---------------------------------------------------------------------------


def test_az_ba_finalizar_scope_y_existencia(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    ajena = _reserva(client, admin_headers, db_session, otra, etiqueta="a")
    stock_ajena_antes = _stock(db_session, ajena["inventarios"][0])

    assert _finalizar_sin_compra(
        client, encargado, ajena["reserva_id"]
    ).status_code == 403
    assert _estado(db_session, ajena["reserva_id"]) == "CONFIRMADA"
    assert _stock(db_session, ajena["inventarios"][0]) == stock_ajena_antes

    assert _finalizar_sin_compra(client, encargado, 999_999_999).status_code == 404


# ---------------------------------------------------------------------------
# BB - Inconsistencia de stock_reservado: rollback total, sin liberacion parcial
# ---------------------------------------------------------------------------


def test_bb_inconsistencia_rollback_total(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, etiqueta="a", cantidades=(2, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]
    assert a < b, "el SP procesa por inventario_id ascendente"
    stock_a_antes = _stock(db_session, a)

    # Se rompe la consistencia de la SEGUNDA linea: la primera ya se habria
    # liberado dentro del procedimiento antes de fallar.
    db_session.get(Inventario, b).stock_reservado = 0
    db_session.flush()

    resp = _finalizar_sin_compra(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 409, resp.text

    # Rollback completo: nada liberado (ni la primera linea), reserva intacta
    assert _movimientos(db_session, a, "LIBERACION_RESERVA") == []
    assert _movimientos(db_session, b, "LIBERACION_RESERVA") == []
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _stock(db_session, a) == stock_a_antes
    assert _stock(db_session, b)[1] == 1
