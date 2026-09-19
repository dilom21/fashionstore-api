"""Pruebas de CU17 - Gestionar reservas de sucursal (ADMINISTRADOR/ENCARGADO).

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session: no quedan reservas, stock_reservado ni movimientos en la BD real.

CU17 no vende ni cobra: confirmar no toca inventario y cancelar libera
unicamente stock_reservado a traves de sp_cancelar_reserva.
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
from app.modules.carrito.models.models import Carrito
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU17CAT_{_suf()}", "descripcion": "cat CU17"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU17PROD_{_suf()}",
            "descripcion": "producto CU17",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU17T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU17C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": sku or f"ZZCU17SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU17TEMP_{_suf()}",
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
    """CLIENTE (usuario + perfil) con token de contexto 'cliente'."""
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None, "No existe el rol CLIENTE"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu17.{etiqueta}.{_suf()}@fashionstore.test",
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
    """Usuario de personal (contexto 'personal'); con Empleado si se pide sucursal."""
    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu17.{etiqueta}.{_suf()}@fashionstore.test",
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
            nombres="Encargado",
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
            "observacion": "reserva CU17",
        },
        headers=headers,
    )


def _reserva_en_sucursal(
    client,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    etiqueta="a",
    cantidad=2,
    stock_actual=10,
    fecha_atencion=None,
    **kwargs,
):
    """Cliente + carrito + reserva PENDIENTE real (CU16) en la sucursal dada."""
    cliente, headers = _crear_cliente(db_session, etiqueta)
    ctx = _contexto(
        client,
        admin_headers,
        db_session,
        sucursal_id,
        stock_actual=stock_actual,
        **kwargs,
    )
    carrito = _agregar(client, headers, sucursal_id, ctx["inventario_id"], cantidad)
    assert carrito.status_code == 200, carrito.text
    carrito_id = carrito.json()["carrito_id"]

    respuesta = _reservar(
        client, headers, carrito_id, fecha_atencion=fecha_atencion
    )
    assert respuesta.status_code == 201, respuesta.text
    return {
        "reserva_id": respuesta.json()["reserva_id"],
        "carrito_id": carrito_id,
        "inventario_id": ctx["inventario_id"],
        "cliente": cliente,
        "cliente_headers": headers,
        "sucursal_id": sucursal_id,
        "cantidad": cantidad,
    }


def _listar(client, headers, **params):
    return client.get("/reservas-sucursal", params=params, headers=headers)


def _detalle(client, headers, reserva_id):
    return client.get(f"/reservas-sucursal/{reserva_id}", headers=headers)


def _confirmar(client, headers, reserva_id):
    return client.patch(
        f"/reservas-sucursal/{reserva_id}/confirmar", json={}, headers=headers
    )


def _cancelar(client, headers, reserva_id, observacion=None):
    return client.patch(
        f"/reservas-sucursal/{reserva_id}/cancelar",
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


def _estado(db_session, reserva_id) -> str:
    db_session.expire_all()
    return db_session.get(Reserva, reserva_id).estado


def _carrito_estado(db_session, carrito_id) -> str:
    db_session.expire_all()
    return db_session.get(Carrito, carrito_id).estado


def _ids(respuesta) -> set[int]:
    return {item["reserva_id"] for item in respuesta.json()["items"]}


# ---------------------------------------------------------------------------
# A - ENCARGADO lista unicamente su sucursal
# ---------------------------------------------------------------------------


def test_a_encargado_lista_solo_su_sucursal(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    propia = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")
    ajena = _reserva_en_sucursal(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _listar(client, encargado)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert propia["reserva_id"] in _ids(resp)
    assert ajena["reserva_id"] not in _ids(resp)
    assert all(item["sucursal_id"] == mia for item in body["items"])
    assert body["limit"] == 20 and body["offset"] == 0
    assert body["total"] == len(body["items"])


# ---------------------------------------------------------------------------
# B - ADMINISTRADOR lista todas las sucursales
# ---------------------------------------------------------------------------


def test_b_administrador_lista_todas_las_sucursales(
    client, admin_headers, db_session
):
    primera, segunda = _sucursales_activas(db_session)[:2]
    reserva_1 = _reserva_en_sucursal(
        client, admin_headers, db_session, primera, etiqueta="a"
    )
    reserva_2 = _reserva_en_sucursal(
        client, admin_headers, db_session, segunda, etiqueta="b"
    )

    resp = _listar(client, admin_headers)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert {reserva_1["reserva_id"], reserva_2["reserva_id"]} <= ids


# ---------------------------------------------------------------------------
# C - ADMINISTRADOR filtra por sucursal
# ---------------------------------------------------------------------------


def test_c_administrador_filtra_por_sucursal(client, admin_headers, db_session):
    primera, segunda = _sucursales_activas(db_session)[:2]
    reserva_1 = _reserva_en_sucursal(
        client, admin_headers, db_session, primera, etiqueta="a"
    )
    reserva_2 = _reserva_en_sucursal(
        client, admin_headers, db_session, segunda, etiqueta="b"
    )

    resp = _listar(client, admin_headers, sucursal_id=segunda)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert reserva_2["reserva_id"] in ids
    assert reserva_1["reserva_id"] not in ids
    assert all(item["sucursal_id"] == segunda for item in resp.json()["items"])


# ---------------------------------------------------------------------------
# D - ENCARGADO puede enviar su propia sucursal
# ---------------------------------------------------------------------------


def test_d_encargado_puede_enviar_su_propia_sucursal(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    propia = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")

    resp = _listar(client, encargado, sucursal_id=mia)
    assert resp.status_code == 200, resp.text
    assert propia["reserva_id"] in _ids(resp)


# ---------------------------------------------------------------------------
# E - ENCARGADO no puede filtrar otra sucursal (403, no se ignora)
# ---------------------------------------------------------------------------


def test_e_encargado_no_puede_filtrar_otra_sucursal(
    client, admin_headers, db_session
):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    _reserva_en_sucursal(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _listar(client, encargado, sucursal_id=otra)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# F - Filtro por estado real
# ---------------------------------------------------------------------------


def test_f_filtro_por_estado(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    pendiente = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")
    confirmada = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="b")
    cancelada = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="c")

    assert _confirmar(client, encargado, confirmada["reserva_id"]).status_code == 200
    assert _cancelar(client, encargado, cancelada["reserva_id"]).status_code == 200

    pendientes = _listar(client, encargado, estado="PENDIENTE")
    assert pendientes.status_code == 200, pendientes.text
    assert pendiente["reserva_id"] in _ids(pendientes)
    assert confirmada["reserva_id"] not in _ids(pendientes)
    assert cancelada["reserva_id"] not in _ids(pendientes)

    confirmadas = _listar(client, encargado, estado="CONFIRMADA")
    assert confirmada["reserva_id"] in _ids(confirmadas)
    assert pendiente["reserva_id"] not in _ids(confirmadas)

    canceladas = _listar(client, encargado, estado="CANCELADA")
    assert cancelada["reserva_id"] in _ids(canceladas)

    invalido = _listar(client, encargado, estado="PREPARANDO")
    assert invalido.status_code == 422


# ---------------------------------------------------------------------------
# G - Filtro por fechas de atencion
# ---------------------------------------------------------------------------


def test_g_filtro_por_fechas(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    cercana = _reserva_en_sucursal(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=2)).isoformat(),
    )
    lejana = _reserva_en_sucursal(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="b",
        fecha_atencion=(datetime.now(_UTC) + timedelta(days=40)).isoformat(),
    )

    desde = (datetime.now(_UTC) + timedelta(days=1)).date().isoformat()
    hasta = (datetime.now(_UTC) + timedelta(days=5)).date().isoformat()

    resp = _listar(client, encargado, fecha_desde=desde, fecha_hasta=hasta)
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert cercana["reserva_id"] in ids
    assert lejana["reserva_id"] not in ids

    solo_desde = _listar(client, encargado, fecha_desde=hasta)
    assert solo_desde.status_code == 200, solo_desde.text
    assert lejana["reserva_id"] in _ids(solo_desde)
    assert cercana["reserva_id"] not in _ids(solo_desde)


# ---------------------------------------------------------------------------
# H - Rango de fechas invalido
# ---------------------------------------------------------------------------


def test_h_rango_de_fechas_invalido_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    resp = _listar(
        client, encargado, fecha_desde="2026-10-01", fecha_hasta="2026-09-01"
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# I - Busqueda por id de reserva, nombre y apellido del cliente
# ---------------------------------------------------------------------------


def test_i_busqueda_por_id_nombre_y_apellido(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    objetivo = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")
    otro = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="b")

    por_id = _listar(client, encargado, buscar=str(objetivo["reserva_id"]))
    assert por_id.status_code == 200, por_id.text
    assert objetivo["reserva_id"] in _ids(por_id)
    assert otro["reserva_id"] not in _ids(por_id)

    por_nombre = _listar(client, encargado, buscar="Harold")
    assert por_nombre.status_code == 200, por_nombre.text
    assert objetivo["reserva_id"] in _ids(por_nombre)

    por_apellido = _listar(client, encargado, buscar="Prueb")
    assert por_apellido.status_code == 200, por_apellido.text
    assert objetivo["reserva_id"] in _ids(por_apellido)


# ---------------------------------------------------------------------------
# J - ENCARGADO consulta el detalle de su sucursal
# ---------------------------------------------------------------------------


def test_j_encargado_consulta_detalle_propio(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=3
    )

    resp = _detalle(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["reserva_id"] == reserva["reserva_id"]
    assert body["carrito_id"] == reserva["carrito_id"]
    assert body["estado"] == "PENDIENTE"
    assert body["sucursal_id"] == mia
    assert body["sucursal_nombre"]
    assert body["cliente_id"] == reserva["cliente"].id
    assert body["cliente_nombre"] == "Harold"
    assert body["cliente_apellido"] == "Prueba"
    assert body["cantidad_total_unidades"] == 3
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["inventario_id"] == reserva["inventario_id"]
    assert item["cantidad"] == 3
    assert item["producto_id"]
    assert item["producto_nombre"]
    assert item["sku"]
    assert item["talla_nombre"]
    assert item["color_nombre"]
    assert item["temporada_nombre"]
    # CU17 no expone precios ni datos sensibles del cliente
    assert "precio" not in item
    assert "password" not in resp.text


# ---------------------------------------------------------------------------
# K - ENCARGADO no puede ver el detalle de otra sucursal
# ---------------------------------------------------------------------------


def test_k_encargado_no_ve_detalle_de_otra_sucursal(
    client, admin_headers, db_session
):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    ajena = _reserva_en_sucursal(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _detalle(client, encargado, ajena["reserva_id"])
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# L - ADMINISTRADOR consulta el detalle de cualquier sucursal
# ---------------------------------------------------------------------------


def test_l_administrador_consulta_cualquier_sucursal(
    client, admin_headers, db_session
):
    _, otra = _sucursales_activas(db_session)[:2]
    ajena = _reserva_en_sucursal(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _detalle(client, admin_headers, ajena["reserva_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["sucursal_id"] == otra
    assert resp.json()["estado"] == "PENDIENTE"


# ---------------------------------------------------------------------------
# M - Reserva inexistente
# ---------------------------------------------------------------------------


def test_m_reserva_inexistente_404(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    assert _detalle(client, encargado, 99999999).status_code == 404
    assert _confirmar(client, encargado, 99999999).status_code == 404
    assert _cancelar(client, encargado, 99999999).status_code == 404
    assert _detalle(client, admin_headers, 99999999).status_code == 404


# ---------------------------------------------------------------------------
# N - ENCARGADO confirma PENDIENTE sin tocar inventario
# ---------------------------------------------------------------------------


def test_n_encargado_confirma_pendiente_sin_tocar_inventario(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        cantidad=2,
        stock_actual=10,
        stock_reservado=1,
    )

    assert _stock(db_session, reserva["inventario_id"]) == (10, 3)
    movimientos_antes = len(_movimientos(db_session, reserva["inventario_id"]))

    resp = _confirmar(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CONFIRMADA"
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"

    # Confirmar NO toca inventario ni genera movimientos
    assert _stock(db_session, reserva["inventario_id"]) == (10, 3)
    assert len(_movimientos(db_session, reserva["inventario_id"])) == movimientos_antes
    assert (
        _movimientos(db_session, reserva["inventario_id"], "LIBERACION_RESERVA") == []
    )
    assert _movimientos(db_session, reserva["inventario_id"], "SALIDA_VENTA") == []
    assert _carrito_estado(db_session, reserva["carrito_id"]) == "CONVERTIDO"


# ---------------------------------------------------------------------------
# O - ADMINISTRADOR confirma PENDIENTE de cualquier sucursal
# ---------------------------------------------------------------------------


def test_o_administrador_confirma_cualquier_sucursal(
    client, admin_headers, db_session
):
    _, otra = _sucursales_activas(db_session)[:2]
    reserva = _reserva_en_sucursal(client, admin_headers, db_session, otra, etiqueta="b")

    resp = _confirmar(client, admin_headers, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CONFIRMADA"
    assert resp.json()["sucursal_id"] == otra
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


# ---------------------------------------------------------------------------
# P - Confirmar una reserva ya CONFIRMADA
# ---------------------------------------------------------------------------


def test_p_confirmar_confirmada_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")

    assert _confirmar(client, encargado, reserva["reserva_id"]).status_code == 200

    repetida = _confirmar(client, encargado, reserva["reserva_id"])
    assert repetida.status_code == 409, repetida.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


# ---------------------------------------------------------------------------
# Q - Confirmar una reserva CANCELADA
# ---------------------------------------------------------------------------


def test_q_confirmar_cancelada_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(client, admin_headers, db_session, mia, etiqueta="a")
    assert _cancelar(client, encargado, reserva["reserva_id"]).status_code == 200

    resp = _confirmar(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 409, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "CANCELADA"


# ---------------------------------------------------------------------------
# R - ENCARGADO cancela PENDIENTE propia (libera solo stock_reservado)
# ---------------------------------------------------------------------------


def test_r_encargado_cancela_pendiente_propia(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client,
        admin_headers,
        db_session,
        mia,
        etiqueta="a",
        cantidad=3,
        stock_actual=10,
        stock_reservado=1,
    )
    assert _stock(db_session, reserva["inventario_id"]) == (10, 4)

    resp = _cancelar(client, encargado, reserva["reserva_id"], "no se presento")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "CANCELADA"
    assert "no se presento" in (body["observacion"] or "")

    actual, reservado = _stock(db_session, reserva["inventario_id"])
    assert actual == 10  # stock_actual intacto
    assert reservado == 1  # solo se libera stock_reservado

    liberaciones = _movimientos(
        db_session, reserva["inventario_id"], "LIBERACION_RESERVA"
    )
    assert len(liberaciones) == 1
    assert liberaciones[0].cantidad == 3
    assert liberaciones[0].referencia_tipo == "RESERVA"
    assert liberaciones[0].referencia_id == reserva["reserva_id"]
    assert _movimientos(db_session, reserva["inventario_id"], "SALIDA_VENTA") == []


# ---------------------------------------------------------------------------
# S - ENCARGADO cancela CONFIRMADA propia
# ---------------------------------------------------------------------------


def test_s_encargado_cancela_confirmada_propia(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )
    assert _confirmar(client, encargado, reserva["reserva_id"]).status_code == 200

    resp = _cancelar(client, encargado, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CANCELADA"

    assert _stock(db_session, reserva["inventario_id"]) == (10, 0)
    assert (
        len(
            _movimientos(
                db_session, reserva["inventario_id"], "LIBERACION_RESERVA"
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# T - ENCARGADO no puede cancelar una reserva de otra sucursal
# ---------------------------------------------------------------------------


def test_t_encargado_no_cancela_otra_sucursal(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    ajena = _reserva_en_sucursal(
        client, admin_headers, db_session, otra, etiqueta="b", cantidad=2
    )

    resp = _cancelar(client, encargado, ajena["reserva_id"])
    assert resp.status_code == 403, resp.text

    # Nada cambio: estado, stock y movimientos intactos
    assert _estado(db_session, ajena["reserva_id"]) == "PENDIENTE"
    assert _stock(db_session, ajena["inventario_id"]) == (10, 2)
    assert _movimientos(db_session, ajena["inventario_id"], "LIBERACION_RESERVA") == []
    assert _carrito_estado(db_session, ajena["carrito_id"]) == "CONVERTIDO"


# ---------------------------------------------------------------------------
# U - ADMINISTRADOR cancela reservas de cualquier sucursal
# ---------------------------------------------------------------------------


def test_u_administrador_cancela_cualquier_sucursal(
    client, admin_headers, db_session
):
    _, otra = _sucursales_activas(db_session)[:2]
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, otra, etiqueta="b", cantidad=2
    )

    resp = _cancelar(client, admin_headers, reserva["reserva_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "CANCELADA"

    assert _stock(db_session, reserva["inventario_id"]) == (10, 0)
    assert (
        len(
            _movimientos(
                db_session, reserva["inventario_id"], "LIBERACION_RESERVA"
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# V - Cancelar una reserva ya CANCELADA (sin doble liberacion)
# ---------------------------------------------------------------------------


def test_v_cancelar_cancelada_409_sin_doble_liberacion(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )
    assert _cancelar(client, encargado, reserva["reserva_id"]).status_code == 200

    repetida = _cancelar(client, encargado, reserva["reserva_id"])
    assert repetida.status_code == 409, repetida.text

    assert _estado(db_session, reserva["reserva_id"]) == "CANCELADA"
    assert _stock(db_session, reserva["inventario_id"]) == (10, 0)
    assert (
        len(
            _movimientos(
                db_session, reserva["inventario_id"], "LIBERACION_RESERVA"
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# W - El carrito originario sigue CONVERTIDO tras la cancelacion
# ---------------------------------------------------------------------------


def test_w_carrito_sigue_convertido_tras_cancelar(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=1
    )

    assert _cancelar(client, encargado, reserva["reserva_id"]).status_code == 200
    assert _carrito_estado(db_session, reserva["carrito_id"]) == "CONVERTIDO"

    # El cliente no puede reutilizar ese carrito para reservar de nuevo
    repetir = _reservar(
        client, reserva["cliente_headers"], reserva["carrito_id"]
    )
    assert repetir.status_code == 409, repetir.text
    assert client.get("/carritos", headers=reserva["cliente_headers"]).json() == {
        "items": [],
        "total_carritos_activos": 0,
    }


# ---------------------------------------------------------------------------
# X - CAJERO fuera de CU17 aunque tenga RBAC GESTIONAR_RESERVAS
# ---------------------------------------------------------------------------


def test_x_cajero_no_puede_usar_cu17(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "cajero")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )

    assert _listar(client, cajero).status_code == 403
    assert _detalle(client, cajero, reserva["reserva_id"]).status_code == 403
    assert _confirmar(client, cajero, reserva["reserva_id"]).status_code == 403
    assert _cancelar(client, cajero, reserva["reserva_id"]).status_code == 403

    # Nada cambio con los intentos del cajero
    assert _estado(db_session, reserva["reserva_id"]) == "PENDIENTE"
    assert _stock(db_session, reserva["inventario_id"]) == (10, 2)
    assert _movimientos(db_session, reserva["inventario_id"], "LIBERACION_RESERVA") == []


# ---------------------------------------------------------------------------
# Y - CLIENTE fuera de CU17; sus endpoints de CU16 siguen funcionando
# ---------------------------------------------------------------------------


def test_y_cliente_no_puede_usar_cu17_y_cu16_sigue(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )
    headers = reserva["cliente_headers"]

    assert _listar(client, headers).status_code == 403
    assert _detalle(client, headers, reserva["reserva_id"]).status_code == 403
    assert _confirmar(client, headers, reserva["reserva_id"]).status_code == 403
    assert _cancelar(client, headers, reserva["reserva_id"]).status_code == 403

    propias = client.get("/reservas", headers=headers)
    assert propias.status_code == 200, propias.text
    assert propias.json()["total_reservas"] == 1
    detalle_cliente = client.get(
        f"/reservas/{reserva['reserva_id']}", headers=headers
    )
    assert detalle_cliente.status_code == 200, detalle_cliente.text
    assert detalle_cliente.json()["estado"] == "PENDIENTE"


# ---------------------------------------------------------------------------
# Z - Sin token
# ---------------------------------------------------------------------------


def test_z_sin_token_401(client, db_session):
    assert client.get("/reservas-sucursal").status_code == 401
    assert client.get("/reservas-sucursal/1").status_code == 401
    assert client.patch("/reservas-sucursal/1/confirmar", json={}).status_code == 401
    assert client.patch("/reservas-sucursal/1/cancelar", json={}).status_code == 401


# ---------------------------------------------------------------------------
# AA - Concurrencia: bloqueo FOR UPDATE y una sola transicion por reserva
# ---------------------------------------------------------------------------


def test_aa_confirmar_usa_for_update(client, admin_headers, db_session):
    """La confirmacion bloquea la fila (SELECT ... FOR UPDATE)."""
    from sqlalchemy import event

    from app.core.database import engine

    sentencias: list[str] = []

    def _capturar(conn, cursor, statement, parameters, context, executemany):
        sentencias.append(statement.upper())

    event.listen(engine, "before_cursor_execute", _capturar)
    try:
        mia = _sucursales_activas(db_session)[0]
        _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
        reserva = _reserva_en_sucursal(
            client, admin_headers, db_session, mia, etiqueta="a", cantidad=1
        )
        sentencias.clear()
        resp = _confirmar(client, encargado, reserva["reserva_id"])
    finally:
        event.remove(engine, "before_cursor_execute", _capturar)

    assert resp.status_code == 200, resp.text
    assert any("FOR UPDATE" in sql and "RESERVA" in sql for sql in sentencias)
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


def test_aa_doble_confirmacion_y_carrera_con_cancelar(
    client, admin_headers, db_session
):
    """Dos transiciones sobre la misma reserva: una aplica y la otra recibe 409."""
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")

    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )
    primera = _confirmar(client, encargado, reserva["reserva_id"])
    segunda = _confirmar(client, encargado, reserva["reserva_id"])
    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 409, segunda.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"

    otra = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="b", cantidad=2
    )
    cancelacion = _cancelar(client, encargado, otra["reserva_id"])
    confirmacion = _confirmar(client, encargado, otra["reserva_id"])
    assert cancelacion.status_code == 200, cancelacion.text
    assert confirmacion.status_code == 409, confirmacion.text

    assert _estado(db_session, otra["reserva_id"]) == "CANCELADA"
    assert _stock(db_session, otra["inventario_id"]) == (10, 0)
    assert (
        len(
            _movimientos(
                db_session, otra["inventario_id"], "LIBERACION_RESERVA"
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# AB - Regresion CU16 (endpoints del cliente)
# ---------------------------------------------------------------------------


def test_ab_regresion_cu16_flujo_cliente(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )
    headers = reserva["cliente_headers"]

    listado = client.get("/reservas", headers=headers)
    assert listado.status_code == 200, listado.text
    assert listado.json()["items"][0]["reserva_id"] == reserva["reserva_id"]

    detalle = client.get(f"/reservas/{reserva['reserva_id']}", headers=headers)
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["estado"] == "PENDIENTE"

    ctx = _contexto(client, admin_headers, db_session, mia)
    _, headers2 = _crear_cliente(db_session, "z")
    agregado = _agregar(client, headers2, mia, ctx["inventario_id"], 1)
    assert agregado.status_code == 200, agregado.text
    creada = _reservar(client, headers2, agregado.json()["carrito_id"])
    assert creada.status_code == 201, creada.text
    assert creada.json()["estado"] == "PENDIENTE"

    cancelada = client.patch(
        f"/reservas/{reserva['reserva_id']}/cancelar",
        json={"observacion": "cancelada por el cliente"},
        headers=headers,
    )
    assert cancelada.status_code == 200, cancelada.text
    assert cancelada.json()["estado"] == "CANCELADA"


# ---------------------------------------------------------------------------
# AC - Regresion CU14: la cancelacion de CU17 aparece en el Kardex
# ---------------------------------------------------------------------------


def test_ac_regresion_cu14_liberacion_visible_en_kardex(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, encargado = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "enc1")
    reserva = _reserva_en_sucursal(
        client, admin_headers, db_session, mia, etiqueta="a", cantidad=2
    )

    assert _cancelar(client, encargado, reserva["reserva_id"]).status_code == 200

    movimientos = _movimientos(
        db_session, reserva["inventario_id"], "LIBERACION_RESERVA"
    )
    assert len(movimientos) == 1
    assert movimientos[0].referencia_id == reserva["reserva_id"]
    assert movimientos[0].cantidad == 2

    kardex = client.get(
        "/movimientos-inventario",
        params={"tipo": "LIBERACION_RESERVA"},
        headers=admin_headers,
    )
    assert kardex.status_code == 200, kardex.text
    body = kardex.json()
    assert "items" in body and "total" in body
    assert "LIBERACION_RESERVA" in kardex.text
