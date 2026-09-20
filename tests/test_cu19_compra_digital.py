"""Pruebas de CU19 - Realizar compra digital (CLIENTE).

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session, por lo que no quedan ventas, carritos ni inventario temporal en la
BD real.

CU19 NO procesa pago, NO descuenta stock_actual y NO llama sp_confirmar_venta:
solo prepara la venta PENDIENTE y marca el carrito CONVERTIDO para CU22.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Cliente, Rol, Usuario
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.ventas.models.models import DetalleVenta, Venta

_UTC = timezone.utc
PAGO_TABLA = "pago"


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU19CAT_{_suf()}", "descripcion": "cat CU19"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU19PROD_{_suf()}",
            "descripcion": "producto CU19",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU19T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU19C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": sku or f"ZZCU19SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre or f"ZZCU19TEMP_{_suf()}",
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
        correo=f"zzcu19.{etiqueta}.{_suf()}@fashionstore.test",
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


def _checkout(client, headers, carrito_id, canal="WEB"):
    return client.post(
        "/ventas/digital",
        json={"carrito_id": carrito_id, "canal": canal},
        headers=headers,
    )


def _carrito_de_una_linea(client, headers, ctx, cantidad=2):
    resp = _agregar(
        client, headers, ctx["sucursal_id"], ctx["inventario_id"], cantidad
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ventas_de_carrito(db_session, carrito_id) -> list[Venta]:
    return list(
        db_session.scalars(
            select(Venta).where(Venta.carrito_id == carrito_id)
        ).all()
    )


def _contar_pagos(db_session, venta_id) -> int:
    from sqlalchemy import text

    return int(
        db_session.scalar(
            text("SELECT COUNT(*) FROM pago WHERE venta_id = :venta_id"),
            {"venta_id": venta_id},
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


def test_a_checkout_web_crea_venta_pendiente_y_convierte_carrito(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=2)

    resp = _checkout(client, headers, carrito["carrito_id"], canal="WEB")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["canal"] == "WEB"
    assert body["estado"] == "PENDIENTE"
    assert body["carrito_id"] == carrito["carrito_id"]
    assert body["cliente_id"] == cliente.id
    assert body["sucursal_id"] == ctx["sucursal_id"]
    assert float(body["total"]) == 100.0
    assert body["cantidad_total_unidades"] == 2
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["inventario_id"] == ctx["inventario_id"]
    assert item["cantidad"] == 2
    assert float(item["precio_unitario"]) == 50.0
    assert float(item["subtotal_linea"]) == 100.0

    db_session.expire_all()
    venta = db_session.get(Venta, body["venta_id"])
    assert venta is not None
    assert venta.cliente_id == cliente.id
    assert venta.carrito_id == carrito["carrito_id"]
    assert venta.sucursal_id == ctx["sucursal_id"]
    assert venta.canal == "WEB"
    assert venta.estado == "PENDIENTE"
    assert Decimal(venta.total) == Decimal("100.00")

    detalles = db_session.scalars(
        select(DetalleVenta).where(DetalleVenta.venta_id == venta.id)
    ).all()
    assert len(detalles) == 1
    assert detalles[0].inventario_id == ctx["inventario_id"]
    assert detalles[0].cantidad == 2
    assert Decimal(detalles[0].precio_unitario) == Decimal("50.00")

    carrito_db = db_session.get(Carrito, carrito["carrito_id"])
    assert carrito_db.estado == "CONVERTIDO"

    # El historial del carrito se conserva (no se borran los detalle_carrito).
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DetalleCarrito)
            .where(DetalleCarrito.carrito_id == carrito["carrito_id"])
        )
        == 1
    )


def test_b_checkout_movil_valido(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    resp = _checkout(client, headers, carrito["carrito_id"], canal="MOVIL")
    assert resp.status_code == 201, resp.text
    assert resp.json()["canal"] == "MOVIL"
    assert resp.json()["estado"] == "PENDIENTE"

    db_session.expire_all()
    venta = db_session.get(Venta, resp.json()["venta_id"])
    assert venta.canal == "MOVIL"


def test_c_precio_se_toma_de_la_bd_no_del_cliente(
    client, admin_headers, db_session
):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=2)

    # El precio real cambia en la BD despues de armar el carrito.
    producto = db_session.get(Producto, ctx["producto"]["id"])
    producto.precio = Decimal("77.50")
    db_session.commit()

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert float(body["items"][0]["precio_unitario"]) == 77.50
    assert float(body["total"]) == 155.00


def test_d_total_recalculado_por_la_base(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=3)

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 201, resp.text
    venta_id = resp.json()["venta_id"]

    total_detalles = db_session.scalar(
        select(func.sum(DetalleVenta.cantidad * DetalleVenta.precio_unitario)).where(
            DetalleVenta.venta_id == venta_id
        )
    )
    db_session.expire_all()
    assert Decimal(db_session.get(Venta, venta_id).total) == Decimal(total_detalles)


# ---------------------------------------------------------------------------
# Sin pago / sin salida de inventario (frontera con CU22)
# ---------------------------------------------------------------------------


def test_e_no_crea_pago(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 201, resp.text
    assert _contar_pagos(db_session, resp.json()["venta_id"]) == 0


def test_f_inventario_sin_descuento_definitivo(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(
        client, admin_headers, db_session, stock_actual=10, stock_reservado=2
    )
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=3)

    antes_movimientos = db_session.scalar(
        select(func.count()).select_from(MovimientoInventario)
    )

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    inventario = db_session.get(Inventario, ctx["inventario_id"])
    assert inventario.stock_actual == 10
    assert inventario.stock_reservado == 2
    despues_movimientos = db_session.scalar(
        select(func.count()).select_from(MovimientoInventario)
    )
    assert despues_movimientos == antes_movimientos


# ---------------------------------------------------------------------------
# Errores de dominio
# ---------------------------------------------------------------------------


def test_g_carrito_ajeno_403(client, admin_headers, db_session):
    cliente_a, headers_a = _crear_cliente(db_session, "a")
    cliente_b, headers_b = _crear_cliente(db_session, "b")
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers_a, ctx, cantidad=1)

    resp = _checkout(client, headers_b, carrito["carrito_id"])
    assert resp.status_code == 403, resp.text
    assert _ventas_de_carrito(db_session, carrito["carrito_id"]) == []


def test_h_carrito_inexistente_404(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    resp = _checkout(client, headers, 999999999)
    assert resp.status_code == 404, resp.text


def test_i_carrito_no_activo_409(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    carrito_db = db_session.get(Carrito, carrito["carrito_id"])
    carrito_db.estado = "ELIMINADO"
    db_session.commit()

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 409, resp.text
    assert _ventas_de_carrito(db_session, carrito["carrito_id"]) == []


def test_j_carrito_vacio_409(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    sucursal_id = _sucursales_activas(db_session)[0]
    ahora = datetime.now(_UTC)
    carrito = Carrito(
        cliente_id=cliente.id,
        sucursal_id=sucursal_id,
        fecha_creacion=ahora,
        fecha_actualizacion=ahora,
        estado="ACTIVO",
    )
    db_session.add(carrito)
    db_session.commit()

    resp = _checkout(client, headers, carrito.id)
    assert resp.status_code == 409, resp.text


def test_k_stock_insuficiente_409(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, stock_actual=10)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=3)

    # El stock fisico cae despues de armar el carrito: el checkout revalida.
    inventario = db_session.get(Inventario, ctx["inventario_id"])
    inventario.stock_actual = 1
    db_session.commit()

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 409, resp.text
    assert _ventas_de_carrito(db_session, carrito["carrito_id"]) == []
    db_session.expire_all()
    assert db_session.get(Carrito, carrito["carrito_id"]).estado == "ACTIVO"


def test_l_producto_no_disponible_409(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    variante = db_session.get(VarianteProducto, ctx["variante"]["id"])
    variante.estado = False
    db_session.commit()

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 409, resp.text
    assert _ventas_de_carrito(db_session, carrito["carrito_id"]) == []


def test_m_canal_invalido_422(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    resp = _checkout(client, headers, carrito["carrito_id"], canal="PRESENCIAL")
    assert resp.status_code == 422, resp.text
    assert _ventas_de_carrito(db_session, carrito["carrito_id"]) == []


def test_n_sin_autenticacion_401(client, admin_headers, db_session):
    ctx = _contexto(client, admin_headers, db_session)
    resp = client.post(
        "/ventas/digital",
        json={"carrito_id": 1, "canal": "WEB"},
    )
    assert resp.status_code == 401, resp.text


def test_o_sesion_personal_no_puede_comprar(
    client, admin_headers, cajero_headers, db_session
):
    ctx = _contexto(client, admin_headers, db_session)
    assert (
        _checkout(client, admin_headers, 1).status_code == 403
    )
    assert (
        _checkout(client, cajero_headers, 1).status_code == 403
    )


# ---------------------------------------------------------------------------
# Idempotencia, concurrencia y rollback
# ---------------------------------------------------------------------------


def test_p_segundo_intento_no_duplica_venta(client, admin_headers, db_session):
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    primero = _checkout(client, headers, carrito["carrito_id"])
    assert primero.status_code == 201, primero.text
    venta_id = primero.json()["venta_id"]

    segundo = _checkout(client, headers, carrito["carrito_id"])
    assert segundo.status_code == 409, segundo.text

    ventas = _ventas_de_carrito(db_session, carrito["carrito_id"])
    assert len(ventas) == 1
    assert ventas[0].id == venta_id


def test_q_unique_carrito_protege_concurrencia(
    client, admin_headers, db_session, monkeypatch
):
    """Si la venta ya existe pero el pre-chequeo no la ve (carrera), el UNIQUE
    uq_venta_carrito evita la duplicacion y se traduce a 409 limpio."""
    from app.modules.ventas.repositories import repository as repo_module

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    # "Otra peticion" ya creo la venta.
    existente = Venta(
        cliente_id=cliente.id,
        empleado_id=None,
        sucursal_id=ctx["sucursal_id"],
        reserva_id=None,
        fecha_hora=datetime.now(_UTC),
        canal="WEB",
        estado="PENDIENTE",
        total=Decimal("50.00"),
        carrito_id=carrito["carrito_id"],
    )
    db_session.add(existente)
    db_session.commit()

    monkeypatch.setattr(
        repo_module.VentaRepository,
        "obtener_por_carrito",
        lambda db, carrito_id: None,
    )

    resp = _checkout(client, headers, carrito["carrito_id"])
    assert resp.status_code == 409, resp.text
    assert "uq_venta_carrito" not in resp.text

    ventas = _ventas_de_carrito(db_session, carrito["carrito_id"])
    assert len(ventas) == 1
    assert ventas[0].id == existente.id


def test_r_rollback_si_falla_un_detalle(
    client, admin_headers, db_session, monkeypatch
):
    """Si falla la creacion de un detalle, no queda venta ni carrito convertido."""
    from app.modules.ventas.repositories import repository as repo_module

    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    # Segunda linea en la misma sucursal.
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
    assert primero.status_code == 200, primero.text
    carrito_id = primero.json()["carrito_id"]
    segundo = _agregar(client, headers, ctx["sucursal_id"], inventario2.id, 1)
    assert segundo.status_code == 200, segundo.text

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

    resp = _checkout(client, headers, carrito_id)
    assert resp.status_code == 409, resp.text
    assert "boom" not in resp.text

    assert _ventas_de_carrito(db_session, carrito_id) == []
    db_session.expire_all()
    assert db_session.get(Carrito, carrito_id).estado == "ACTIVO"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DetalleVenta)
            .join(Venta, Venta.id == DetalleVenta.venta_id)
            .where(Venta.carrito_id == carrito_id)
        )
        == 0
    )


def test_s_cu15_sigue_operativo_tras_conversion(client, admin_headers, db_session):
    """Tras convertir el carrito, CU15 lo trata como no activo."""
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    carrito = _carrito_de_una_linea(client, headers, ctx, cantidad=1)

    assert _checkout(client, headers, carrito["carrito_id"]).status_code == 201

    listado = client.get("/carritos", headers=headers)
    assert listado.status_code == 200
    assert listado.json()["total_carritos_activos"] == 0
    assert client.get(
        f"/carritos/{carrito['carrito_id']}", headers=headers
    ).status_code == 409
