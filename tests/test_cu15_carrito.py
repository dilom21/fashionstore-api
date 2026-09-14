"""Pruebas de CU15 - Gestionar carrito de compras (CLIENTE).

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session, por lo que no quedan carritos ni inventario temporal en la BD real.
CU15 no modifica inventario ni genera movimiento_inventario (solo intencion de
compra).
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Cliente, Rol, Usuario
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU15CAT_{_suf()}", "descripcion": "cat CU15"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU15PROD_{_suf()}",
            "descripcion": "producto CU15",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU15T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU15C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": sku or f"ZZCU15SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU15TEMP_{_suf()}",
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
        correo=f"zzcu15.{etiqueta}.{_suf()}@fashionstore.test",
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
    return client.post(
        "/carritos/items",
        json={
            "sucursal_id": sucursal_id,
            "inventario_id": inventario_id,
            "cantidad": cantidad,
        },
        headers=headers,
    )


def _linea(carrito_id, detalle_id):
    return f"/carritos/{carrito_id}/items/{detalle_id}"


def _carritos(db_session, cliente_id) -> list[Carrito]:
    return list(
        db_session.scalars(
            select(Carrito).where(Carrito.cliente_id == cliente_id)
        ).all()
    )


def test_a_agrega_primera_prenda_crea_carrito(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "ACTIVO"
    assert len(body["items"]) == 1
    assert body["cantidad_total_unidades"] == 2
    assert float(body["subtotal_carrito"]) == 100.0
    linea = body["items"][0]
    assert linea["cantidad"] == 2
    assert linea["producto_id"] == ctx["producto"]["id"]
    assert linea["stock_disponible"] == 10

    carritos = _carritos(db_session, cliente.id)
    assert len(carritos) == 1
    assert carritos[0].estado == "ACTIVO"


def test_b_reutiliza_carrito_misma_sucursal(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    # Segunda variante del mismo producto (otra talla/color) en la MISMA sucursal
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario2 = _crear_inventario(
        db_session,
        sucursal_id=ctx["sucursal_id"],
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=5,
    )

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"])
    assert primero.status_code == 200, primero.text
    segundo = _agregar(client, headers, ctx["sucursal_id"], inventario2.id)
    assert segundo.status_code == 200, segundo.text

    assert primero.json()["carrito_id"] == segundo.json()["carrito_id"]
    assert len(segundo.json()["items"]) == 2
    assert len(_carritos(db_session, cliente.id)) == 1


def test_c_mismo_inventario_incrementa_cantidad(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
    assert primero.status_code == 200, primero.text
    segundo = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert segundo.status_code == 200, segundo.text

    body = segundo.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["cantidad"] == 3
    assert body["cantidad_total_unidades"] == 3

    detalles = db_session.scalars(
        select(DetalleCarrito).where(
            DetalleCarrito.carrito_id == body["carrito_id"]
        )
    ).all()
    assert len(detalles) == 1


def test_d_misma_prenda_otra_talla_color_linea_distinta(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario2 = _crear_inventario(
        db_session,
        sucursal_id=ctx["sucursal_id"],
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=7,
    )

    _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    resp = _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    variantes = {item["variante_producto_id"] for item in body["items"]}
    assert variantes == {ctx["variante"]["id"], variante2["id"]}


def test_e_carrito_en_otra_sucursal_permitido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sucursal_dos = _sucursales_activas(db_session)[1]
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario2 = _crear_inventario(
        db_session,
        sucursal_id=sucursal_dos,
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=4,
    )

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"])
    assert primero.status_code == 200, primero.text
    segundo = _agregar(client, headers, sucursal_dos, inventario2.id)
    assert segundo.status_code == 200, segundo.text
    assert primero.json()["carrito_id"] != segundo.json()["carrito_id"]

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200, listado.text
    assert listado.json()["total_carritos_activos"] == 2


def test_f_no_permite_dos_activos_misma_sucursal(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)

    activos = [
        c
        for c in _carritos(db_session, cliente.id)
        if c.estado == "ACTIVO" and c.sucursal_id == ctx["sucursal_id"]
    ]
    assert len(activos) == 1


def test_g_inventario_de_otra_sucursal_rechazado(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sucursal_dos = _sucursales_activas(db_session)[1]
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario2 = _crear_inventario(
        db_session,
        sucursal_id=sucursal_dos,
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=5,
    )

    # El inventario es de sucursal_dos pero se declara la sucursal 1.
    resp = _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 1)
    assert resp.status_code == 409, resp.text
    assert _carritos(db_session, cliente.id) == []


def test_h_stock_insuficiente_rechazado(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(
        client, admin_headers, db_session, stock_actual=3, stock_reservado=0
    )

    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 4)
    assert resp.status_code == 409, resp.text

    assert (
        _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
        .status_code
        == 200
    )
    # 2 en carrito + 2 mas = 4 > stock disponible 3 -> rechazado
    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
    assert resp.status_code == 409, resp.text


def test_i_agregar_no_modifica_stock_actual(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, stock_actual=10)
    inventario_id = ctx["inventario_id"]
    antes = ctx["inventario"].stock_actual

    assert (
        _agregar(client, headers, ctx["sucursal_id"], inventario_id, 3).status_code
        == 200
    )

    db_session.expire_all()
    assert db_session.get(Inventario, inventario_id).stock_actual == antes


def test_j_agregar_no_modifica_stock_reservado(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(
        client, admin_headers, db_session, stock_actual=10, stock_reservado=1
    )
    inventario_id = ctx["inventario_id"]

    assert (
        _agregar(client, headers, ctx["sucursal_id"], inventario_id, 2).status_code
        == 200
    )

    db_session.expire_all()
    assert db_session.get(Inventario, inventario_id).stock_reservado == 1


def test_k_agregar_no_genera_movimiento_inventario(
    client, admin_headers, db_session
):
    from sqlalchemy import func

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    antes = db_session.scalar(
        select(func.count()).select_from(MovimientoInventario)
    )

    assert (
        _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2).status_code
        == 200
    )

    db_session.expire_all()
    despues = db_session.scalar(
        select(func.count()).select_from(MovimientoInventario)
    )
    assert despues == antes


def test_l_actualizar_cantidad_recalcula_monto(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, stock_actual=10)

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert creado.status_code == 200, creado.text
    carrito = creado.json()
    detalle_id = carrito["items"][0]["detalle_id"]

    resp = client.patch(
        _linea(carrito["carrito_id"], detalle_id),
        json={"cantidad": 3},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["cantidad"] == 3
    assert float(body["subtotal_carrito"]) == 150.0
    assert body["cantidad_total_unidades"] == 3


def test_m_cantidad_invalida_422(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    assert (
        _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 0).status_code
        == 422
    )
    assert (
        _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], -1).status_code
        == 422
    )

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1).json()
    detalle_id = creado["items"][0]["detalle_id"]
    for cantidad in (0, -5):
        resp = client.patch(
            _linea(creado["carrito_id"], detalle_id),
            json={"cantidad": cantidad},
            headers=headers,
        )
        assert resp.status_code == 422


def test_n_eliminar_detalle(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario2 = _crear_inventario(
        db_session,
        sucursal_id=ctx["sucursal_id"],
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=5,
    )

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    segundo = _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 1)
    assert segundo.status_code == 200, segundo.text
    body = segundo.json()
    assert len(body["items"]) == 2
    detalle_id = body["items"][0]["detalle_id"]

    resp = client.delete(
        _linea(body["carrito_id"], detalle_id), headers=headers
    )
    assert resp.status_code == 200, resp.text
    restante = resp.json()
    assert len(restante["items"]) == 1
    assert restante["estado"] == "ACTIVO"
    assert (
        len(
            db_session.scalars(
                select(DetalleCarrito).where(
                    DetalleCarrito.carrito_id == body["carrito_id"]
                )
            ).all()
        )
        == 1
    )


def test_o_eliminar_ultimo_detalle_marca_carrito_eliminado(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert creado.status_code == 200, creado.text
    carrito_id = creado.json()["carrito_id"]
    detalle_id = creado.json()["items"][0]["detalle_id"]

    resp = client.delete(_linea(carrito_id, detalle_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "ELIMINADO"
    assert resp.json()["items"] == []

    carrito = db_session.get(Carrito, carrito_id)
    db_session.refresh(carrito)
    assert carrito.estado == "ELIMINADO"

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    assert listado.json() == {"items": [], "total_carritos_activos": 0}


def test_p_eliminar_carrito_es_logico(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    carrito_id = creado.json()["carrito_id"]

    resp = client.delete(f"/carritos/{carrito_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "ELIMINADO"

    # Sin DELETE fisico: la fila sigue existiendo.
    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id) is not None
    assert db_session.get(Carrito, carrito_id).estado == "ELIMINADO"

    listado = client.get("/carritos", headers=headers)
    assert listado.json()["total_carritos_activos"] == 0


def test_q_carrito_vencido_pasa_a_expirado(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    carrito_id = creado.json()["carrito_id"]

    carrito = db_session.get(Carrito, carrito_id)
    carrito.fecha_actualizacion = datetime.now(_UTC) - timedelta(hours=3)
    db_session.commit()

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    assert listado.json()["total_carritos_activos"] == 0

    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "EXPIRADO"

    # Un carrito expirado no puede consultarse como activo.
    assert client.get(f"/carritos/{carrito_id}", headers=headers).status_code == 409


def test_r_actividad_refresca_fecha_actualizacion(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    carrito_id = creado.json()["carrito_id"]

    carrito = db_session.get(Carrito, carrito_id)
    antes = datetime.now(_UTC) - timedelta(minutes=90)
    carrito.fecha_actualizacion = antes
    db_session.commit()

    # Nueva actividad (insert de detalle) -> el trigger refresca la fecha.
    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    despues = db_session.get(Carrito, carrito_id).fecha_actualizacion
    assert despues > antes
    assert resp.json()["fecha_actualizacion"] > antes.isoformat()


def test_s_no_accede_a_carrito_ajeno(client, admin_headers, db_session):
    cliente_a, headers_a = _crear_cliente(db_session, "a")
    cliente_b, headers_b = _crear_cliente(db_session, "b")
    ctx = _contexto(client, admin_headers, db_session)

    creado = _agregar(client, headers_a, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert creado.status_code == 200, creado.text
    carrito_id = creado.json()["carrito_id"]
    detalle_id = creado.json()["items"][0]["detalle_id"]

    assert client.get(f"/carritos/{carrito_id}", headers=headers_b).status_code == 403
    assert (
        client.patch(
            _linea(carrito_id, detalle_id),
            json={"cantidad": 2},
            headers=headers_b,
        ).status_code
        == 403
    )
    assert (
        client.delete(_linea(carrito_id, detalle_id), headers=headers_b).status_code
        == 403
    )
    assert client.delete(f"/carritos/{carrito_id}", headers=headers_b).status_code == 403

    # El propietario conserva su carrito intacto.
    assert client.get(f"/carritos/{carrito_id}", headers=headers_a).status_code == 200


def test_t_listar_solo_carritos_propios(client, admin_headers, db_session):
    cliente_a, headers_a = _crear_cliente(db_session, "a")
    cliente_b, headers_b = _crear_cliente(db_session, "b")
    ctx = _contexto(client, admin_headers, db_session)

    _agregar(client, headers_a, ctx["sucursal_id"], ctx["inventario_id"], 1)

    listado_b = client.get("/carritos", headers=headers_b)
    assert listado_b.status_code == 200
    assert listado_b.json() == {"items": [], "total_carritos_activos": 0}

    listado_a = client.get("/carritos", headers=headers_a)
    assert listado_a.json()["total_carritos_activos"] == 1


def test_u_contador_cuenta_carritos_no_productos(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sucursal_dos = _sucursales_activas(db_session)[1]
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante2 = _crear_variante(
        client, admin_headers, ctx["producto"]["id"], talla["id"], color["id"]
    )
    inventario_local = _crear_inventario(
        db_session,
        sucursal_id=ctx["sucursal_id"],
        variante_id=variante2["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=6,
    )
    inventario_suc2 = _crear_inventario(
        db_session,
        sucursal_id=sucursal_dos,
        variante_id=ctx["variante"]["id"],
        temporada_id=ctx["temporada"]["id"],
        stock_actual=9,
    )

    # Carrito 1 (sucursal 1) con 2 lineas distintas (2 unidades + 1).
    _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
    _agregar(client, headers, ctx["sucursal_id"], inventario_local.id, 1)
    # Carrito 2 (sucursal 2).
    segundo_carrito = _agregar(client, headers, sucursal_dos, inventario_suc2.id, 1)
    assert segundo_carrito.status_code == 200, segundo_carrito.text

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    body = listado.json()
    # 2 carritos activos (no 3 productos/lineas)
    assert body["total_carritos_activos"] == 2
    assert len(body["items"]) == 2

    por_sucursal = {item["sucursal_id"]: item for item in body["items"]}
    assert por_sucursal[ctx["sucursal_id"]]["cantidad_lineas"] == 2
    assert por_sucursal[ctx["sucursal_id"]]["cantidad_unidades"] == 3
    assert por_sucursal[sucursal_dos]["cantidad_lineas"] == 1


def test_w_sin_token_401(client, admin_headers, db_session):
    ctx = _contexto(client, admin_headers, db_session)
    assert client.get("/carritos").status_code == 401
    assert (
        client.post(
            "/carritos/items",
            json={
                "sucursal_id": ctx["sucursal_id"],
                "inventario_id": ctx["inventario_id"],
                "cantidad": 1,
            },
        ).status_code
        == 401
    )


def test_x_usuario_personal_no_accede(
    client, admin_headers, cajero_headers, db_session
):
    assert client.get("/carritos", headers=admin_headers).status_code == 403
    assert client.get("/carritos", headers=cajero_headers).status_code == 403


def test_y_cu13_cu14_siguen_funcionando(client, admin_headers):
    inventario = client.get("/inventario", headers=admin_headers, params={"limit": 1})
    assert inventario.status_code == 200
    assert set(inventario.json().keys()) == {"items", "total", "limit", "offset"}

    movimientos = client.get(
        "/movimientos-inventario", headers=admin_headers, params={"limit": 1}
    )
    assert movimientos.status_code == 200
    assert set(movimientos.json().keys()) == {"items", "total", "limit", "offset"}


# ---------------------------------------------------------------------------
# Auditoria de expiracion (2h) en acceso directo por carrito_id
# ---------------------------------------------------------------------------


def _crear_carrito_vencido(client, admin_headers, db_session, headers, *, horas=3):
    """Crea un carrito ACTIVO con 1 linea y envejece fecha_actualizacion >2h."""
    ctx = _contexto(client, admin_headers, db_session)
    creado = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2)
    assert creado.status_code == 200, creado.text
    carrito_id = creado.json()["carrito_id"]
    detalle_id = creado.json()["items"][0]["detalle_id"]

    carrito = db_session.get(Carrito, carrito_id)
    vencido = datetime.now(_UTC) - timedelta(hours=horas)
    carrito.fecha_actualizacion = vencido
    db_session.commit()
    return ctx, carrito_id, detalle_id, vencido


def test_exp_patch_directo_carrito_vencido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    _, carrito_id, detalle_id, vencido = _crear_carrito_vencido(
        client, admin_headers, db_session, headers
    )
    detalle_antes = db_session.get(DetalleCarrito, detalle_id).cantidad

    # PATCH directo SIN haber llamado antes a GET /carritos.
    resp = client.patch(
        _linea(carrito_id, detalle_id), json={"cantidad": 5}, headers=headers
    )
    assert resp.status_code == 409, resp.text

    db_session.expire_all()
    carrito = db_session.get(Carrito, carrito_id)
    assert carrito.estado == "EXPIRADO"
    assert carrito.fecha_actualizacion == vencido  # no se reactiva
    assert db_session.get(DetalleCarrito, detalle_id).cantidad == detalle_antes


def test_exp_delete_detalle_carrito_vencido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    _, carrito_id, detalle_id, vencido = _crear_carrito_vencido(
        client, admin_headers, db_session, headers
    )

    resp = client.delete(_linea(carrito_id, detalle_id), headers=headers)
    assert resp.status_code == 409, resp.text

    db_session.expire_all()
    carrito = db_session.get(Carrito, carrito_id)
    assert carrito.estado == "EXPIRADO"
    assert carrito.fecha_actualizacion == vencido
    assert db_session.get(DetalleCarrito, detalle_id) is not None


def test_exp_get_directo_carrito_vencido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    _, carrito_id, _, vencido = _crear_carrito_vencido(
        client, admin_headers, db_session, headers
    )

    resp = client.get(f"/carritos/{carrito_id}", headers=headers)
    assert resp.status_code == 409, resp.text

    db_session.expire_all()
    carrito = db_session.get(Carrito, carrito_id)
    assert carrito.estado == "EXPIRADO"
    assert carrito.fecha_actualizacion == vencido


def test_exp_delete_carrito_vencido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    _, carrito_id, _, vencido = _crear_carrito_vencido(
        client, admin_headers, db_session, headers
    )

    resp = client.delete(f"/carritos/{carrito_id}", headers=headers)
    assert resp.status_code == 409, resp.text

    db_session.expire_all()
    carrito = db_session.get(Carrito, carrito_id)
    assert carrito.estado == "EXPIRADO"  # ni activo ni eliminado
    assert carrito.fecha_actualizacion == vencido


def test_exp_listado_excluye_vencidos(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    _, carrito_id, _, _ = _crear_carrito_vencido(
        client, admin_headers, db_session, headers
    )

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    assert listado.json() == {"items": [], "total_carritos_activos": 0}

    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "EXPIRADO"


# ---------------------------------------------------------------------------
# Condicion de carrera al crear el primer carrito (recuperacion)
# ---------------------------------------------------------------------------


def test_race_primer_carrito_recupera_existente(
    client, admin_headers, db_session, monkeypatch
):
    from app.modules.carrito.repositories import repository as repo_module

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    # El carrito ya fue persistido por "otra peticion".
    existente = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert existente.status_code == 200, existente.text
    carrito_id = existente.json()["carrito_id"]

    original = repo_module.CarritoRepository.obtener_activo
    llamadas = {"n": 0}

    def obtener_activo_parcheado(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return None  # la 1ra lectura no ve el carrito (simula la carrera)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        repo_module.CarritoRepository, "obtener_activo", obtener_activo_parcheado
    )

    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert resp.status_code == 200, resp.text
    assert resp.json()["carrito_id"] == carrito_id
    assert resp.json()["items"][0]["cantidad"] == 2

    activos = [
        c for c in _carritos(db_session, cliente.id) if c.estado == "ACTIVO"
    ]
    assert len(activos) == 1


def test_race_detalle_duplicado_recupera_existente(
    client, admin_headers, db_session, monkeypatch
):
    """Violacion real de uq_detalle_carrito_inventario -> retry y recuperacion."""
    from app.modules.carrito.repositories import repository as repo_module

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert primero.status_code == 200, primero.text
    carrito_id = primero.json()["carrito_id"]

    original = repo_module.CarritoRepository.obtener_detalle_por_inventario
    llamadas = {"n": 0}

    def obtener_detalle_parcheado(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return None  # la 1ra lectura no ve la linea (simula la carrera)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        repo_module.CarritoRepository,
        "obtener_detalle_por_inventario",
        obtener_detalle_parcheado,
    )

    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["carrito_id"] == carrito_id
    assert len(body["items"]) == 1
    assert body["items"][0]["cantidad"] == 2
    assert llamadas["n"] == 2  # la constraint esperada entro al retry


def _integrity_error(nombre_constraint):
    """IntegrityError de laboratorio con el metadato diag.constraint_name."""
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError

    error = IntegrityError("INSERT INTO ...", {}, Exception("detalle interno"))
    error.orig = SimpleNamespace(
        diag=SimpleNamespace(constraint_name=nombre_constraint)
    )
    return error


def test_deteccion_constraint_de_carrera():
    """Solo las dos constraints conocidas se consideran condicion de carrera."""
    from app.modules.carrito.services.service import (
        CONSTRAINTS_CARRERA,
        _constraint_violada,
        _es_conflicto_de_carrera,
    )

    esperadas = (
        "uq_carrito_activo_cliente_sucursal",
        "uq_detalle_carrito_inventario",
    )
    assert CONSTRAINTS_CARRERA == frozenset(esperadas)

    for nombre in esperadas:
        error = _integrity_error(nombre)
        assert _constraint_violada(error) == nombre
        assert _es_conflicto_de_carrera(error) is True

    assert _es_conflicto_de_carrera(_integrity_error("uq_otra_tabla")) is False
    assert _constraint_violada(_integrity_error(None)) is None
    assert _es_conflicto_de_carrera(_integrity_error(None)) is False


def test_integrity_error_inesperado_no_reintenta(
    client, admin_headers, db_session, monkeypatch
):
    """Un IntegrityError de otra constraint NO entra al retry de carrera."""
    from app.modules.carrito.repositories import repository as repo_module

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    primero = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert primero.status_code == 200, primero.text

    activo_original = repo_module.CarritoRepository.obtener_activo
    lecturas = {"carrito": 0}

    def obtener_activo_parcheado(*args, **kwargs):
        lecturas["carrito"] += 1
        return activo_original(*args, **kwargs)

    intentos = {"crear": 0}

    def crear_detalle_parcheado(*args, **kwargs):
        intentos["crear"] += 1
        # Constraint ajena a las carreras esperadas (p. ej. un CHECK).
        raise _integrity_error("ck_detalle_carrito_cantidad_positiva")

    monkeypatch.setattr(
        repo_module.CarritoRepository, "obtener_activo", obtener_activo_parcheado
    )
    monkeypatch.setattr(
        repo_module.CarritoRepository, "crear_detalle", crear_detalle_parcheado
    )
    monkeypatch.setattr(
        repo_module.CarritoRepository,
        "obtener_detalle_por_inventario",
        lambda *args, **kwargs: None,
    )

    resp = _agregar(client, headers, ctx["sucursal_id"], ctx["inventario_id"], 1)
    assert resp.status_code == 409, resp.text
    assert (
        resp.json()["detail"]
        == "No fue posible guardar el carrito: datos inconsistentes"
    )
    assert "INSERT" not in resp.text and "ck_detalle" not in resp.text
    assert intentos["crear"] == 1  # sin reintento
    assert lecturas["carrito"] == 1  # sin releer como si fuera carrera

