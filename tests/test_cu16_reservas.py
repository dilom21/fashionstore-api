"""Pruebas de CU16 - Gestionar reserva de prendas (CLIENTE).

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session: no quedan reservas, stock_reservado ni movimientos temporales en la
base de datos real.

CU16 NO es una venta: reservar nunca cambia inventario.stock_actual, no crea
Venta/Pago y no genera movimientos SALIDA_VENTA.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Cliente, Rol, Usuario
from app.modules.carrito.models.models import Carrito
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import DetalleReserva, Reserva

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU16CAT_{_suf()}", "descripcion": "cat CU16"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU16PROD_{_suf()}",
            "descripcion": "producto CU16",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU16T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU16C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": sku or f"ZZCU16SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU16TEMP_{_suf()}",
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


def _crear_cliente(db_session, etiqueta="a"):
    """Crea un CLIENTE (usuario + perfil) y devuelve (cliente, headers)."""
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None, "No existe el rol CLIENTE"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu16.{etiqueta}.{_suf()}@fashionstore.test",
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
        estado=True,
    )
    db_session.add(cliente)
    db_session.flush()
    token = create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": rol.nombre,
            "contexto": "cliente",
        }
    )
    return cliente, {"Authorization": f"Bearer {token}"}


def _contexto(client, admin_headers, db_session, *, stock_actual=10, stock_reservado=0):
    """Catalogo + inventario en la primera sucursal activa."""
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    temporada = _crear_temporada(client, admin_headers)
    sucursal_id = _sucursales_activas(db_session)[0]
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
        "inventario": inventario,
        "producto": producto,
        "variante": variante,
        "temporada": temporada,
        "categoria": categoria,
        "talla": talla,
        "color": color,
    }


def _agregar(client, headers, sucursal_id, inventario_id, cantidad=1):
    """POST /carritos/items (CU15): fuente de verdad del carrito."""
    return client.post(
        "/carritos/items",
        json={
            "sucursal_id": sucursal_id,
            "inventario_id": inventario_id,
            "cantidad": cantidad,
        },
        headers=headers,
    )


def _fecha_futura(dias=2) -> str:
    return (datetime.now(_UTC) + timedelta(days=dias)).isoformat()


def _reservar(client, headers, carrito_id, *, fecha_atencion=None, observacion=None):
    return client.post(
        "/reservas",
        json={
            "carrito_id": carrito_id,
            "fecha_atencion": fecha_atencion or _fecha_futura(),
            "observacion": observacion,
        },
        headers=headers,
    )


def _cancelar(client, headers, reserva_id, observacion=None):
    return client.patch(
        f"/reservas/{reserva_id}/cancelar",
        json={"observacion": observacion},
        headers=headers,
    )


def _reservas(db_session, cliente_id) -> list[Reserva]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Reserva)
            .where(Reserva.cliente_id == cliente_id)
            .order_by(Reserva.id)
        ).all()
    )


def _detalles(db_session, reserva_id) -> list[DetalleReserva]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(DetalleReserva)
            .where(DetalleReserva.reserva_id == reserva_id)
            .order_by(DetalleReserva.inventario_id)
        ).all()
    )


def _movimientos(db_session, inventario_id, tipo=None):
    db_session.expire_all()
    condiciones = [MovimientoInventario.inventario_id == inventario_id]
    if tipo is not None:
        condiciones.append(MovimientoInventario.tipo == tipo)
    return list(
        db_session.scalars(
            select(MovimientoInventario)
            .where(*condiciones)
            .order_by(MovimientoInventario.id)
        ).all()
    )


def _stock(db_session, inventario_id) -> tuple[int, int]:
    """(stock_actual, stock_reservado) leidos frescos desde la BD."""
    db_session.expire_all()
    inventario = db_session.get(Inventario, inventario_id)
    return inventario.stock_actual, inventario.stock_reservado


def _preparar_carrito(client, admin_headers, db_session, etiqueta="a", cantidad=2, **kwargs):
    """Cliente + carrito ACTIVO con una linea. Devuelve cliente, headers, ctx y carrito."""
    cliente, headers = _crear_cliente(db_session, etiqueta)
    ctx = _contexto(client, admin_headers, db_session, **kwargs)
    resp = _agregar(
        client, headers, ctx["sucursal_id"], ctx["inventario_id"], cantidad
    )
    assert resp.status_code == 200, resp.text
    return cliente, headers, ctx, resp.json()["carrito_id"]


# ---------------------------------------------------------------------------
# A - Crear reserva desde carrito propio ACTIVO
# ---------------------------------------------------------------------------


def test_a_crear_reserva_desde_carrito(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=3
    )

    resp = _reservar(
        client, headers, carrito_id, observacion="quiero probarmelas"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["estado"] == "PENDIENTE"
    assert body["carrito_id"] == carrito_id
    assert body["cliente_id"] == cliente.id
    assert body["sucursal_id"] == ctx["sucursal_id"]
    assert body["observacion"] == "quiero probarmelas"
    assert body["cantidad_total_unidades"] == 3
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["inventario_id"] == ctx["inventario_id"]
    assert item["cantidad"] == 3
    assert item["producto_id"] == ctx["producto"]["id"]
    assert item["producto_nombre"] == ctx["producto"]["nombre"]
    assert item["sku"] == ctx["variante"]["sku"]
    assert item["talla_nombre"] == ctx["talla"]["nombre"]
    assert item["color_nombre"] == ctx["color"]["nombre"]
    assert item["temporada_nombre"] == ctx["temporada"]["nombre"]

    reservas = _reservas(db_session, cliente.id)
    assert len(reservas) == 1
    assert reservas[0].id == body["reserva_id"]
    assert reservas[0].estado == "PENDIENTE"
    assert reservas[0].carrito_id == carrito_id

    detalles = _detalles(db_session, body["reserva_id"])
    assert [(d.inventario_id, d.cantidad) for d in detalles] == [
        (ctx["inventario_id"], 3)
    ]


# ---------------------------------------------------------------------------
# B - Inventario: stock_actual intacto, stock_reservado aumenta
# ---------------------------------------------------------------------------


def test_b_stock_actual_no_cambia_y_reservado_aumenta(
    client, admin_headers, db_session
):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client,
        admin_headers,
        db_session,
        cantidad=4,
        stock_actual=10,
        stock_reservado=2,
    )
    assert _stock(db_session, ctx["inventario_id"]) == (10, 2)

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 201, resp.text

    actual, reservado = _stock(db_session, ctx["inventario_id"])
    assert actual == 10  # reservar NUNCA baja stock_actual
    assert reservado == 6  # 2 + 4
    assert actual - reservado == 4  # disponible disminuye


# ---------------------------------------------------------------------------
# C - Movimiento de inventario tipo RESERVA
# ---------------------------------------------------------------------------


def test_c_movimiento_reserva_por_cada_inventario(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 201, resp.text
    reserva_id = resp.json()["reserva_id"]

    movimientos = _movimientos(db_session, ctx["inventario_id"], "RESERVA")
    assert len(movimientos) == 1
    movimiento = movimientos[0]
    assert movimiento.tipo == "RESERVA"
    assert movimiento.cantidad == 2
    assert movimiento.referencia_tipo == "RESERVA"
    assert movimiento.referencia_id == reserva_id
    # CU16 nunca registra salida por venta
    assert _movimientos(db_session, ctx["inventario_id"], "SALIDA_VENTA") == []


# ---------------------------------------------------------------------------
# D - Carrito convertido y fuera del listado de carritos activos
# ---------------------------------------------------------------------------


def test_d_carrito_convertido_y_fuera_del_listado(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    carrito = db_session.get(Carrito, carrito_id)
    assert carrito.estado == "CONVERTIDO"

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    assert listado.json() == {"items": [], "total_carritos_activos": 0}

    # Un carrito convertido ya no acepta cambios de prendas (regla de CU15)
    detalle = client.get(f"/carritos/{carrito_id}", headers=headers)
    assert detalle.status_code == 409


# ---------------------------------------------------------------------------
# E - Sin JWT
# ---------------------------------------------------------------------------


def test_e_sin_jwt_responde_401(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )

    sin_token = client.post(
        "/reservas",
        json={"carrito_id": carrito_id, "fecha_atencion": _fecha_futura()},
    )
    assert sin_token.status_code == 401

    listado = client.get("/reservas")
    assert listado.status_code == 401

    cancelar = client.patch("/reservas/1/cancelar", json={})
    assert cancelar.status_code == 401


# ---------------------------------------------------------------------------
# F - Sesion de personal (no cliente)
# ---------------------------------------------------------------------------


def test_f_personal_responde_403(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )

    resp = _reservar(client, admin_headers, carrito_id)
    assert resp.status_code == 403, resp.text

    listado = client.get("/reservas", headers=admin_headers)
    assert listado.status_code == 403

    assert _reservas(db_session, cliente.id) == []


# ---------------------------------------------------------------------------
# G - Carrito de otro cliente
# ---------------------------------------------------------------------------


def test_g_carrito_de_otro_cliente_responde_403(client, admin_headers, db_session):
    cliente_b, headers_b, ctx_b, carrito_b = _preparar_carrito(
        client, admin_headers, db_session, etiqueta="b", cantidad=1
    )
    cliente_a, headers_a = _crear_cliente(db_session, "a")

    resp = _reservar(client, headers_a, carrito_b)
    assert resp.status_code == 403, resp.text

    assert _reservas(db_session, cliente_a.id) == []
    assert _reservas(db_session, cliente_b.id) == []

    db_session.expire_all()
    assert db_session.get(Carrito, carrito_b).estado == "ACTIVO"


# ---------------------------------------------------------------------------
# H - Carrito inexistente
# ---------------------------------------------------------------------------


def test_h_carrito_inexistente_responde_404(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session, "a")

    resp = _reservar(client, headers, 99999999)
    assert resp.status_code == 404, resp.text

    assert _reservas(db_session, cliente.id) == []


# ---------------------------------------------------------------------------
# I - Carrito EXPIRADO (regla de 2 horas de CU15)
# ---------------------------------------------------------------------------


def test_i_carrito_expirado_no_genera_reserva(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )

    carrito = db_session.get(Carrito, carrito_id)
    carrito.fecha_actualizacion = datetime.now(_UTC) - timedelta(hours=3)
    db_session.flush()

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 409, resp.text

    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "EXPIRADO"
    assert _reservas(db_session, cliente.id) == []
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)
    assert _movimientos(db_session, ctx["inventario_id"], "RESERVA") == []


# ---------------------------------------------------------------------------
# J - Carrito ELIMINADO
# ---------------------------------------------------------------------------


def test_j_carrito_eliminado_no_genera_reserva(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )

    carrito = db_session.get(Carrito, carrito_id)
    carrito.estado = "ELIMINADO"
    db_session.flush()

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 409, resp.text
    assert _reservas(db_session, cliente.id) == []
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)


# ---------------------------------------------------------------------------
# K y N - Un carrito origina una sola reserva (UNIQUE reserva.carrito_id)
# ---------------------------------------------------------------------------


def test_k_carrito_convertido_no_genera_segunda_reserva(
    client, admin_headers, db_session
):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )

    primera = _reservar(client, headers, carrito_id)
    assert primera.status_code == 201, primera.text
    reserva_id = primera.json()["reserva_id"]

    segunda = _reservar(client, headers, carrito_id)
    assert segunda.status_code == 409, segunda.text

    reservas = _reservas(db_session, cliente.id)
    assert len(reservas) == 1
    assert reservas[0].id == reserva_id
    assert _stock(db_session, ctx["inventario_id"]) == (10, 2)
    assert len(_movimientos(db_session, ctx["inventario_id"], "RESERVA")) == 1


# ---------------------------------------------------------------------------
# L - Fecha de atencion invalida / payload invalido
# ---------------------------------------------------------------------------


def test_l_fecha_pasada_o_actual_responde_422(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )

    pasada = _reservar(
        client,
        headers,
        carrito_id,
        fecha_atencion=(datetime.now(_UTC) - timedelta(hours=1)).isoformat(),
    )
    assert pasada.status_code == 422, pasada.text

    actual = _reservar(
        client, headers, carrito_id, fecha_atencion=datetime.now(_UTC).isoformat()
    )
    assert actual.status_code == 422, actual.text

    carrito_invalido = _reservar(client, headers, 0)
    assert carrito_invalido.status_code == 422, carrito_invalido.text

    assert _reservas(db_session, cliente.id) == []
    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "ACTIVO"


def _segundo_inventario(
    client, admin_headers, db_session, ctx, *, stock_actual, stock_reservado=0
):
    """Segunda variante/inventario ACTIVO en la misma sucursal del contexto."""
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    return _crear_inventario(
        db_session,
        sucursal_id=ctx["sucursal_id"],
        variante_id=variante["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=stock_actual,
        stock_reservado=stock_reservado,
    )


# ---------------------------------------------------------------------------
# M - Stock insuficiente: sin reserva parcial
# ---------------------------------------------------------------------------


def test_m_stock_insuficiente_no_genera_reserva_parcial(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session, "a")
    ctx = _contexto(client, admin_headers, db_session)
    inventario2 = _segundo_inventario(
        client, admin_headers, db_session, ctx, stock_actual=2
    )

    assert (
        _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2).status_code
        == 200
    )
    assert (
        _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 2).status_code
        == 200
    )
    carrito_id = client.get("/carritos", headers=headers).json()["items"][0][
        "carrito_id"
    ]

    # Otra operacion consumio la disponibilidad de la segunda prenda.
    inventario2.stock_actual = 0
    db_session.flush()

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 409, resp.text
    assert "Stock insuficiente" in resp.json()["detail"]

    assert _reservas(db_session, cliente.id) == []
    assert _stock(db_session, ctx["inventario_id"])[1] == 0
    assert _stock(db_session, inventario2.id)[1] == 0
    assert _movimientos(db_session, ctx["inventario_id"], "RESERVA") == []
    assert _movimientos(db_session, inventario2.id, "RESERVA") == []
    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "ACTIVO"


def test_m2_guardia_de_stock_del_sp_es_atomica(
    client, admin_headers, db_session, monkeypatch
):
    """El SP falla en la segunda prenda: no queda nada a medias."""
    from app.modules.reservas.services import service as reserva_service

    cliente, headers = _crear_cliente(db_session, "a")
    ctx = _contexto(client, admin_headers, db_session)
    inventario2 = _segundo_inventario(
        client, admin_headers, db_session, ctx, stock_actual=2
    )

    assert (
        _agregar(
            client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2
        ).status_code
        == 200
    )
    assert (
        _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 2).status_code
        == 200
    )
    carrito_id = client.get("/carritos", headers=headers).json()["items"][0][
        "carrito_id"
    ]

    # Otra operacion consumio la disponibilidad de la segunda prenda. Se
    # desactiva el fast-fail del service para forzar la guardia del SP.
    inventario2.stock_actual = 0
    db_session.flush()
    monkeypatch.setattr(
        reserva_service, "_validar_stock_carrito", lambda carrito: None
    )

    resp = _reservar(client, headers, carrito_id)
    assert resp.status_code == 409, resp.text
    assert "Stock insuficiente" in resp.json()["detail"]

    # Atomicidad: la primera prenda no quedo parcialmente reservada.
    assert _reservas(db_session, cliente.id) == []
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)
    assert _stock(db_session, inventario2.id)[1] == 0
    assert _movimientos(db_session, ctx["inventario_id"], "RESERVA") == []
    assert _movimientos(db_session, inventario2.id, "RESERVA") == []
    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "ACTIVO"


# ---------------------------------------------------------------------------
# N - Doble conversion del mismo carrito: una sola reserva
# ---------------------------------------------------------------------------


def test_n_doble_conversion_solo_una_reserva(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )

    primera = _reservar(client, headers, carrito_id)
    assert primera.status_code == 201, primera.text
    reserva_id = primera.json()["reserva_id"]

    for _ in range(2):
        repetida = _reservar(client, headers, carrito_id)
        assert repetida.status_code == 409, repetida.text

    del_carrito = [
        reserva
        for reserva in _reservas(db_session, cliente.id)
        if reserva.carrito_id == carrito_id
    ]
    assert len(del_carrito) == 1
    assert del_carrito[0].id == reserva_id
    assert _stock(db_session, ctx["inventario_id"]) == (10, 2)
    assert len(_movimientos(db_session, ctx["inventario_id"], "RESERVA")) == 1


# ---------------------------------------------------------------------------
# O - Listado: solo reservas del cliente autenticado
# ---------------------------------------------------------------------------


def test_o_listado_solo_reservas_propias(client, admin_headers, db_session):
    cliente_a, headers_a, ctx_a, carrito_a = _preparar_carrito(
        client, admin_headers, db_session, etiqueta="a", cantidad=1
    )
    cliente_b, headers_b, ctx_b, carrito_b = _preparar_carrito(
        client, admin_headers, db_session, etiqueta="b", cantidad=1
    )
    assert _reservar(client, headers_a, carrito_a).status_code == 201
    assert _reservar(client, headers_b, carrito_b).status_code == 201

    listado_a = client.get("/reservas", headers=headers_a)
    assert listado_a.status_code == 200
    body_a = listado_a.json()
    assert body_a["total_reservas"] == 1
    assert body_a["items"][0]["cliente_id"] == cliente_a.id
    assert body_a["items"][0]["estado"] == "PENDIENTE"
    assert body_a["items"][0]["cantidad_lineas"] == 1
    assert body_a["items"][0]["cantidad_unidades"] == 1

    listado_b = client.get("/reservas", headers=headers_b)
    assert listado_b.json()["items"][0]["cliente_id"] == cliente_b.id

    pendientes = client.get("/reservas?estado=PENDIENTE", headers=headers_a)
    assert pendientes.status_code == 200
    assert pendientes.json()["total_reservas"] == 1

    canceladas = client.get("/reservas?estado=CANCELADA", headers=headers_a)
    assert canceladas.status_code == 200
    assert canceladas.json() == {"items": [], "total_reservas": 0}

    invalido = client.get("/reservas?estado=PREPARANDO", headers=headers_a)
    assert invalido.status_code == 422


# ---------------------------------------------------------------------------
# P y Q - Detalle propio y ajeno
# ---------------------------------------------------------------------------


def test_p_obtener_reserva_propia(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )
    reserva_id = _reservar(client, headers, carrito_id).json()["reserva_id"]

    resp = client.get(f"/reservas/{reserva_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reserva_id"] == reserva_id
    assert body["carrito_id"] == carrito_id
    assert body["estado"] == "PENDIENTE"
    assert body["items"][0]["inventario_id"] == ctx["inventario_id"]
    assert body["cantidad_total_unidades"] == 1

    inexistente = client.get("/reservas/99999999", headers=headers)
    assert inexistente.status_code == 404


def test_q_obtener_reserva_ajena_responde_403(client, admin_headers, db_session):
    cliente_b, headers_b, ctx_b, carrito_b = _preparar_carrito(
        client, admin_headers, db_session, etiqueta="b", cantidad=1
    )
    reserva_b = _reservar(client, headers_b, carrito_b).json()["reserva_id"]

    cliente_a, headers_a = _crear_cliente(db_session, "a")
    resp = client.get(f"/reservas/{reserva_b}", headers=headers_a)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# R - Cancelar reserva PENDIENTE (libera solo stock_reservado)
# ---------------------------------------------------------------------------


def test_r_cancelar_reserva_pendiente(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client,
        admin_headers,
        db_session,
        cantidad=3,
        stock_actual=10,
        stock_reservado=1,
    )
    reserva_id = _reservar(client, headers, carrito_id).json()["reserva_id"]
    assert _stock(db_session, ctx["inventario_id"]) == (10, 4)

    resp = _cancelar(client, headers, reserva_id, "ya no la necesito")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "CANCELADA"
    assert "ya no la necesito" in (body["observacion"] or "")

    actual, reservado = _stock(db_session, ctx["inventario_id"])
    assert actual == 10  # stock_actual intacto
    assert reservado == 1  # solo se libera stock_reservado

    liberaciones = _movimientos(
        db_session, ctx["inventario_id"], "LIBERACION_RESERVA"
    )
    assert len(liberaciones) == 1
    assert liberaciones[0].cantidad == 3
    assert liberaciones[0].referencia_tipo == "RESERVA"
    assert liberaciones[0].referencia_id == reserva_id


# ---------------------------------------------------------------------------
# S - Cancelar dos veces
# ---------------------------------------------------------------------------


def test_s_cancelar_dos_veces(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=2
    )
    reserva_id = _reservar(client, headers, carrito_id).json()["reserva_id"]
    assert _cancelar(client, headers, reserva_id).status_code == 200

    segunda = _cancelar(client, headers, reserva_id)
    assert segunda.status_code == 409, segunda.text

    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)
    assert (
        len(_movimientos(db_session, ctx["inventario_id"], "LIBERACION_RESERVA"))
        == 1
    )


# ---------------------------------------------------------------------------
# T - Cancelar reserva ajena
# ---------------------------------------------------------------------------


def test_t_cancelar_reserva_ajena_responde_403(client, admin_headers, db_session):
    cliente_b, headers_b, ctx_b, carrito_b = _preparar_carrito(
        client, admin_headers, db_session, etiqueta="b", cantidad=1
    )
    reserva_b = _reservar(client, headers_b, carrito_b).json()["reserva_id"]

    cliente_a, headers_a = _crear_cliente(db_session, "a")
    resp = _cancelar(client, headers_a, reserva_b)
    assert resp.status_code == 403, resp.text

    assert _stock(db_session, ctx_b["inventario_id"]) == (10, 1)
    assert _reservas(db_session, cliente_b.id)[0].estado == "PENDIENTE"
    assert _reservas(db_session, cliente_a.id) == []


# ---------------------------------------------------------------------------
# U - Cancelar NO reactiva el carrito (queda CONVERTIDO)
# ---------------------------------------------------------------------------


def test_u_carrito_sigue_convertido_tras_cancelar(client, admin_headers, db_session):
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )
    reserva_id = _reservar(client, headers, carrito_id).json()["reserva_id"]
    assert _cancelar(client, headers, reserva_id).status_code == 200

    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "CONVERTIDO"

    # Tampoco se puede volver a reservar con el mismo carrito
    assert _reservar(client, headers, carrito_id).status_code == 409
    assert client.get("/carritos", headers=headers).json()["items"] == []


# ---------------------------------------------------------------------------
# V - Regresion CU13 / CU14 / CU15
# ---------------------------------------------------------------------------


def test_v_regresion_cu13_cu14_cu15(client, admin_headers, db_session):
    """CU16 no rompe CU13/CU14 (personal) ni CU15 (cliente)."""
    cliente, headers, ctx, carrito_id = _preparar_carrito(
        client, admin_headers, db_session, cantidad=1
    )

    inventario = client.get("/inventario", headers=admin_headers)
    assert inventario.status_code == 200, inventario.text
    assert "items" in inventario.json() and "total" in inventario.json()

    movimientos = client.get("/movimientos-inventario", headers=admin_headers)
    assert movimientos.status_code == 200, movimientos.text
    assert "items" in movimientos.json() and "total" in movimientos.json()

    carritos = client.get("/carritos", headers=headers)
    assert carritos.status_code == 200, carritos.text
    assert carritos.json()["total_carritos_activos"] == 1

    detalle_carrito = client.get(f"/carritos/{carrito_id}", headers=headers)
    assert detalle_carrito.status_code == 200, detalle_carrito.text

    reserva = _reservar(client, headers, carrito_id)
    assert reserva.status_code == 201, reserva.text

    # CU14 (Kardex) ya refleja el movimiento RESERVA generado por CU16.
    kardex_reserva = client.get(
        "/movimientos-inventario?tipo=RESERVA", headers=admin_headers
    )
    assert kardex_reserva.status_code == 200, kardex_reserva.text
    assert "RESERVA" in kardex_reserva.text
    assert "SALIDA_VENTA" not in kardex_reserva.text
