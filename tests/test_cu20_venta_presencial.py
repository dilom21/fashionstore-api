"""Pruebas de CU20 - Registrar venta presencial (PERSONAL de sucursal).

Cubre los dos origenes:

- venta presencial directa (personal + items);
- venta presencial proveniente de CU18 (reserva CONFIRMADA).

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session: no quedan ventas, detalles, reservas ni inventario temporal en la BD
real.

CU20 SOLO crea venta + detalle_venta en PENDIENTE: no registra pago, no
descuenta stock_actual, no consume/libera stock_reservado y no marca la reserva
ATENDIDA. Eso corresponde a CU21.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Rol,
    Usuario,
)
from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva
from app.modules.ventas.models.models import DetalleVenta, Venta

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Helpers de catalogo / inventario
# ---------------------------------------------------------------------------


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU20CAT_{_suf()}", "descripcion": "cat CU20"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU20PROD_{_suf()}",
            "descripcion": "producto CU20",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU20T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU20C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU20SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU20TEMP_{_suf()}",
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


def _contexto(
    client,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    stock_actual=10,
    stock_reservado=0,
    precio="50.00",
):
    """Catalogo + inventario nuevo en la sucursal indicada."""
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(
        client, admin_headers, categoria["id"], precio=precio
    )
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
        "temporada": temporada,
    }


# ---------------------------------------------------------------------------
# Helpers de usuarios / autenticacion
# ---------------------------------------------------------------------------


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
        correo=f"zzcu20.{etiqueta}.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    cliente = Cliente(
        usuario_id=usuario.id,
        nombre="Josias",
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
        correo=f"zzcu20.{etiqueta}.{_suf()}@fashionstore.test",
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


# ---------------------------------------------------------------------------
# Helpers de reserva (CU16/CU17)
# ---------------------------------------------------------------------------


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


def _reservar(client, headers, carrito_id, *, fecha_atencion=None):
    return client.post(
        "/reservas",
        json={
            "carrito_id": carrito_id,
            "fecha_atencion": (
                fecha_atencion
                or (datetime.now(_UTC) + timedelta(days=2)).isoformat()
            ),
            "observacion": "reserva CU20",
        },
        headers=headers,
    )


def _confirmar_cu17(client, admin_headers, reserva_id):
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
    confirmar=True,
):
    """Reserva real (CU16) con 1..N lineas; CONFIRMADA por CU17 salvo indicacion."""
    cliente, headers = _crear_cliente(db_session, etiqueta)
    inventarios: list[int] = []
    carrito_id = None

    for cantidad in cantidades:
        ctx = _contexto(
            client,
            admin_headers,
            db_session,
            sucursal_id,
            stock_actual=stock_actual,
        )
        agregado = _agregar(
            client, headers, sucursal_id, ctx["inventario_id"], cantidad
        )
        assert agregado.status_code == 200, agregado.text
        carrito_id = agregado.json()["carrito_id"]
        inventarios.append(ctx["inventario_id"])

    respuesta = _reservar(client, headers, carrito_id)
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
        "sucursal_id": sucursal_id,
    }


# ---------------------------------------------------------------------------
# Helpers de endpoint / verificacion
# ---------------------------------------------------------------------------


def _presencial(client, headers, payload):
    return client.post("/ventas/presencial", json=payload, headers=headers)


def _stock(db_session, inventario_id) -> tuple[int, int]:
    db_session.expire_all()
    inventario = db_session.get(Inventario, inventario_id)
    return inventario.stock_actual, inventario.stock_reservado


def _estado(db_session, reserva_id) -> str:
    db_session.expire_all()
    return db_session.get(Reserva, reserva_id).estado


def _movimientos(db_session, inventario_id, tipo=None):
    db_session.expire_all()
    condiciones = [MovimientoInventario.inventario_id == inventario_id]
    if tipo is not None:
        condiciones.append(MovimientoInventario.tipo == tipo)
    return list(
        db_session.scalars(select(MovimientoInventario).where(*condiciones)).all()
    )


def _contar_pagos(db_session, venta_id) -> int:
    return int(
        db_session.scalar(
            text("SELECT COUNT(*) FROM pago WHERE venta_id = :venta_id"),
            {"venta_id": venta_id},
        )
        or 0
    )


def _ventas_de_reserva(db_session, reserva_id) -> list[Venta]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Venta).where(Venta.reserva_id == reserva_id)
        ).all()
    )


def _detalles(db_session, venta_id) -> list[DetalleVenta]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(DetalleVenta).where(DetalleVenta.venta_id == venta_id)
        ).all()
    )


# ===========================================================================
# Venta presencial directa
# ===========================================================================


def test_a_venta_directa_valida(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    usuario, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)
    stock_antes = _stock(db_session, ctx["inventario_id"])

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["canal"] == "PRESENCIAL"
    assert body["estado"] == "PENDIENTE"
    assert body["reserva_id"] is None
    assert body["cliente_id"] is None  # venta anonima admitida por el esquema
    assert body["empleado_id"] == usuario.empleado.id
    assert body["sucursal_id"] == sucursal
    assert Decimal(str(body["total"])) == Decimal("100.00")
    assert body["cantidad_total_unidades"] == 2
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["inventario_id"] == ctx["inventario_id"]
    assert item["cantidad"] == 2
    assert Decimal(str(item["precio_unitario"])) == Decimal("50.00")
    assert Decimal(str(item["subtotal_linea"])) == Decimal("100.00")

    # Venta persistida
    db_session.expire_all()
    venta = db_session.get(Venta, body["venta_id"])
    assert venta is not None
    assert venta.estado == "PENDIENTE"
    assert venta.canal == "PRESENCIAL"
    assert venta.empleado_id == usuario.empleado.id
    assert venta.reserva_id is None and venta.carrito_id is None
    detalles = _detalles(db_session, venta.id)
    assert len(detalles) == 1 and detalles[0].cantidad == 2

    # Sin pago y sin tocar inventario
    assert _contar_pagos(db_session, venta.id) == 0
    assert _stock(db_session, ctx["inventario_id"]) == stock_antes
    assert _movimientos(db_session, ctx["inventario_id"]) == []


def test_b_venta_directa_con_cliente_valido(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "ENCARGADO_SUCURSAL", sucursal, "e1")
    cliente, _ = _crear_cliente(db_session, "compra")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {
            "cliente_id": cliente.id,
            "items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cliente_id"] == cliente.id
    assert Decimal(str(body["total"])) == Decimal("50.00")


def test_c_precio_y_total_se_toman_de_la_bd(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    producto = db_session.get(Producto, ctx["producto"]["id"])
    producto.precio = Decimal("77.50")
    db_session.commit()

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(str(body["items"][0]["precio_unitario"])) == Decimal("77.50")
    assert Decimal(str(body["total"])) == Decimal("155.00")


def test_d_inventario_otra_sucursal_403(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    ajeno = _contexto(client, admin_headers, db_session, otra)

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ajeno["inventario_id"], "cantidad": 1}]},
    )
    assert resp.status_code == 403, resp.text
    db_session.expire_all()
    # Ninguna venta consumio la fila de inventario ajena.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DetalleVenta)
            .where(DetalleVenta.inventario_id == ajeno["inventario_id"])
        )
        == 0
    )


def test_e_lineas_de_sucursales_distintas_409(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    ctx_a = _contexto(client, admin_headers, db_session, mia)
    ctx_b = _contexto(client, admin_headers, db_session, otra)

    resp = _presencial(
        client,
        admin_headers,
        {
            "items": [
                {"inventario_id": ctx_a["inventario_id"], "cantidad": 1},
                {"inventario_id": ctx_b["inventario_id"], "cantidad": 1},
            ]
        },
    )
    assert resp.status_code == 409, resp.text


def test_f_stock_insuficiente_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    # disponible = 3 - 1 = 2; se intenta vender 3
    ctx = _contexto(
        client, admin_headers, db_session, sucursal,
        stock_actual=3, stock_reservado=1,
    )
    stock_antes = _stock(db_session, ctx["inventario_id"])

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 3}]},
    )
    assert resp.status_code == 409, resp.text
    assert _stock(db_session, ctx["inventario_id"]) == stock_antes


def test_g_linea_duplicada_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {
            "items": [
                {"inventario_id": ctx["inventario_id"], "cantidad": 1},
                {"inventario_id": ctx["inventario_id"], "cantidad": 1},
            ]
        },
    )
    assert resp.status_code == 409, resp.text
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)


def test_h_sin_items_422(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")

    resp = _presencial(client, headers, {"items": []})
    assert resp.status_code == 422, resp.text


def test_i_cantidad_invalida_422(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    for cantidad in (0, -1):
        resp = _presencial(
            client,
            headers,
            {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": cantidad}]},
        )
        assert resp.status_code == 422, resp.text


def test_j_cliente_inexistente_404(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {
            "cliente_id": 999_999_999,
            "items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}],
        },
    )
    assert resp.status_code == 404, resp.text


def test_k_sin_token_401(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    ctx = _contexto(client, admin_headers, db_session, sucursal)
    resp = client.post(
        "/ventas/presencial",
        json={"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert resp.status_code == 401, resp.text


def test_l_cliente_no_puede_registrar_venta_403(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_cliente(db_session, "solo")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert resp.status_code == 403, resp.text


def test_m_ignora_campos_no_autorizados_del_body(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    usuario, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {
            "empleado_id": 999_999_999,
            "sucursal_id": 999_999_999,
            "canal": "WEB",
            "estado": "COMPLETADA",
            "total": "0.01",
            "precio_unitario": "0.01",
            "items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["canal"] == "PRESENCIAL"
    assert body["estado"] == "PENDIENTE"
    assert body["empleado_id"] == usuario.empleado.id
    assert body["sucursal_id"] == sucursal
    assert Decimal(str(body["total"])) == Decimal("50.00")


def test_n_admin_sin_empleado_empleado_id_nulo(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "ADMINISTRADOR", None, "admin0")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    resp = _presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["empleado_id"] is None


def test_o_rollback_si_falla_un_detalle(
    client, admin_headers, db_session, monkeypatch
):
    from app.modules.ventas.repositories import repository as repo_module

    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx_a = _contexto(client, admin_headers, db_session, sucursal)
    ctx_b = _contexto(client, admin_headers, db_session, sucursal)
    # Persiste los inventarios: el rollback del service solo debe descartar la
    # venta fallida, no el contexto creado por la prueba.
    db_session.commit()

    original = repo_module.VentaRepository.crear_detalle
    llamadas = {"n": 0}

    def crear_detalle_falla(db, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise DBAPIError("INSERT INTO detalle_venta ...", {}, Exception("boom"))
        return original(db, **kwargs)

    monkeypatch.setattr(
        repo_module.VentaRepository, "crear_detalle", crear_detalle_falla
    )

    resp = _presencial(
        client,
        headers,
        {
            "items": [
                {"inventario_id": ctx_a["inventario_id"], "cantidad": 1},
                {"inventario_id": ctx_b["inventario_id"], "cantidad": 1},
            ]
        },
    )
    assert resp.status_code == 409, resp.text
    assert "boom" not in resp.text

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DetalleVenta)
            .where(
                DetalleVenta.inventario_id.in_(
                    [ctx_a["inventario_id"], ctx_b["inventario_id"]]
                )
            )
        )
        == 0
    )
    assert _stock(db_session, ctx_a["inventario_id"]) == (10, 0)
    assert _stock(db_session, ctx_b["inventario_id"]) == (10, 0)


# ===========================================================================
# Venta presencial desde CU18 (reserva CONFIRMADA)
# ===========================================================================


def test_p_desde_reserva_completa(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    usuario, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]
    stock_antes = _stock(db_session, inventario_id)

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 3}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["reserva_id"] == reserva["reserva_id"]
    assert body["cliente_id"] == reserva["cliente"].id
    assert body["sucursal_id"] == mia
    assert body["empleado_id"] == usuario.empleado.id
    assert body["canal"] == "PRESENCIAL"
    assert body["estado"] == "PENDIENTE"
    assert len(body["items"]) == 1
    assert body["items"][0]["cantidad"] == 3
    assert Decimal(str(body["total"])) == Decimal("150.00")

    # Regla critica CU18: reserva CONFIRMADA y stock intacto
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _stock(db_session, inventario_id) == stock_antes
    assert _movimientos(db_session, inventario_id, "LIBERACION_RESERVA") == []
    assert _contar_pagos(db_session, body["venta_id"]) == 0

    # preparar-venta de CU18 sigue funcionando (no muta)
    preparado = client.post(
        f"/atencion-reservas/{reserva['reserva_id']}/preparar-venta",
        json={"items": [{"inventario_id": inventario_id, "cantidad_compra": 3}]},
        headers=headers,
    )
    assert preparado.status_code == 200, preparado.text
    assert preparado.json()["estado"] == "CONFIRMADA"


def test_q_desde_reserva_parcial_sobrantes_intactos(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "e1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]
    stock_antes = _stock(db_session, inventario_id)

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 2}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["items"][0]["cantidad"] == 2
    assert Decimal(str(body["total"])) == Decimal("100.00")

    detalles = _detalles(db_session, body["venta_id"])
    assert len(detalles) == 1 and detalles[0].cantidad == 2

    # El sobrante (1) NO se libera: stock_reservado intacto y reserva CONFIRMADA
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _stock(db_session, inventario_id) == stock_antes
    assert _movimientos(db_session, inventario_id, "LIBERACION_RESERVA") == []


def test_r_desde_reserva_dos_lineas_solo_compradas(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(2, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": a, "cantidad": 2}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["inventario_id"] == a

    detalles = _detalles(db_session, body["venta_id"])
    assert {d.inventario_id for d in detalles} == {a}
    # La linea no comprada conserva su stock_reservado
    assert _stock(db_session, b) == (10, 1)
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"


def test_s_desde_reserva_seleccion_excede_422(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 4}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _ventas_de_reserva(db_session, reserva["reserva_id"]) == []


def test_t_desde_reserva_inventario_fuera_de_reserva_422(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(2,))
    libre = _contexto(client, admin_headers, db_session, mia)["inventario_id"]

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": libre, "cantidad": 1}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert _ventas_de_reserva(db_session, reserva["reserva_id"]) == []


def test_u_desde_reserva_no_confirmada_409(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(2,), confirmar=False
    )
    assert _estado(db_session, reserva["reserva_id"]) == "PENDIENTE"
    inventario_id = reserva["inventarios"][0]

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 1}],
        },
    )
    assert resp.status_code == 409, resp.text
    assert _estado(db_session, reserva["reserva_id"]) == "PENDIENTE"


def test_v_desde_reserva_otra_sucursal_403(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    ajena = _reserva(client, admin_headers, db_session, otra, cantidades=(2,))

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": ajena["reserva_id"],
            "items": [{"inventario_id": ajena["inventarios"][0], "cantidad": 1}],
        },
    )
    assert resp.status_code == 403, resp.text
    assert _estado(db_session, ajena["reserva_id"]) == "CONFIRMADA"
    assert _ventas_de_reserva(db_session, ajena["reserva_id"]) == []


def test_w_segundo_registro_misma_reserva_409(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(2,))
    inventario_id = reserva["inventarios"][0]
    payload = {
        "reserva_id": reserva["reserva_id"],
        "items": [{"inventario_id": inventario_id, "cantidad": 2}],
    }

    primero = _presencial(client, headers, payload)
    assert primero.status_code == 201, primero.text
    venta_id = primero.json()["venta_id"]

    segundo = _presencial(client, headers, payload)
    assert segundo.status_code == 409, segundo.text

    ventas = _ventas_de_reserva(db_session, reserva["reserva_id"])
    assert len(ventas) == 1 and ventas[0].id == venta_id


def test_x_desde_reserva_inexistente_404(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    ctx = _contexto(client, admin_headers, db_session, mia)

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": 999_999_999,
            "items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}],
        },
    )
    assert resp.status_code == 404, resp.text


def test_y_desde_reserva_con_cliente_id_422(client, admin_headers, db_session):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(2,))
    cliente, _ = _crear_cliente(db_session, "otro")

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "cliente_id": cliente.id,
            "items": [
                {"inventario_id": reserva["inventarios"][0], "cantidad": 1}
            ],
        },
    )
    assert resp.status_code == 422, resp.text


def test_z_unique_reserva_protege_concurrencia(
    client, admin_headers, db_session, monkeypatch
):
    """Si el pre-chequeo no ve la venta existente (carrera), uq_venta_reserva
    evita la duplicacion y se traduce a 409 limpio."""
    from app.modules.ventas.repositories import repository as repo_module

    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(2,))
    inventario_id = reserva["inventarios"][0]

    existente = Venta(
        cliente_id=reserva["cliente"].id,
        empleado_id=None,
        sucursal_id=mia,
        reserva_id=reserva["reserva_id"],
        fecha_hora=datetime.now(_UTC),
        canal="PRESENCIAL",
        estado="PENDIENTE",
        total=Decimal("50.00"),
        carrito_id=None,
    )
    db_session.add(existente)
    db_session.commit()

    monkeypatch.setattr(
        repo_module.VentaRepository,
        "obtener_por_reserva",
        lambda db, reserva_id: None,
    )

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 1}],
        },
    )
    assert resp.status_code == 409, resp.text
    assert "uq_venta_reserva" not in resp.text

    ventas = _ventas_de_reserva(db_session, reserva["reserva_id"])
    assert len(ventas) == 1 and ventas[0].id == existente.id


def test_aa_desde_reserva_no_toca_inventario_ni_movimientos(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(2, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]
    stock_a = _stock(db_session, a)
    stock_b = _stock(db_session, b)
    mov_a = len(_movimientos(db_session, a))
    mov_b = len(_movimientos(db_session, b))

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [
                {"inventario_id": a, "cantidad": 2},
                {"inventario_id": b, "cantidad": 1},
            ],
        },
    )
    assert resp.status_code == 201, resp.text

    assert _stock(db_session, a) == stock_a
    assert _stock(db_session, b) == stock_b
    assert len(_movimientos(db_session, a)) == mov_a
    assert len(_movimientos(db_session, b)) == mov_b
    assert _estado(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _contar_pagos(db_session, resp.json()["venta_id"]) == 0


def test_ab_desde_reserva_exige_empleado_autenticado(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    usuario, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(1,))

    resp = _presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [
                {"inventario_id": reserva["inventarios"][0], "cantidad": 1}
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["empleado_id"] == usuario.empleado.id


def test_ac_cu19_digital_sigue_operativo(client, admin_headers, db_session):
    """Regresion minima: CU19 no se rompe al extender el dominio Ventas."""
    cliente, headers = _crear_cliente(db_session, "digital")
    ctx = _contexto(client, admin_headers, db_session, _sucursales_activas(db_session)[0])

    agregado = _agregar(
        client, headers, ctx["sucursal_id"], ctx["inventario_id"], 2
    )
    assert agregado.status_code == 200, agregado.text
    carrito_id = agregado.json()["carrito_id"]

    resp = client.post(
        "/ventas/digital",
        json={"carrito_id": carrito_id, "canal": "WEB"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["canal"] == "WEB"
    assert resp.json()["estado"] == "PENDIENTE"
