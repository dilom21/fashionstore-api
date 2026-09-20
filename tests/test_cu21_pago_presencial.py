"""Pruebas de CU21 - Registrar pago presencial (CAJERO/POS).

Cubre:

- pago presencial directo (APROBADO -> sp_confirmar_venta -> COMPLETADA);
- venta proveniente de CU18/CU20 (reserva CONFIRMADA -> ATENDIDA, sobrantes
  liberados por el procedimiento);
- fallos de dominio (venta digital, estado invalido, fuera de sucursal,
  sin permiso, sin token, metodo invalido);
- atomicidad: si sp_confirmar_venta falla (incluido stock insuficiente) el pago
  APROBADO no queda persistido;
- idempotencia ante doble submit.

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session: no quedan pagos, ventas, reservas ni movimientos en la BD real.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Rol,
    Usuario,
)
from app.modules.compras.models.models import MovimientoInventario
from app.modules.inventario.models.models import Inventario
from app.modules.pagos.models.models import Pago
from app.modules.reservas.models.models import Reserva
from app.modules.ventas.models.models import Venta

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Catalogo / inventario
# ---------------------------------------------------------------------------


def _crear_categoria(client, headers):
    resp = client.post(
        "/categorias",
        json={"nombre": f"ZZCU21CAT_{_suf()}", "descripcion": "cat CU21"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": f"ZZCU21PROD_{_suf()}",
            "descripcion": "producto CU21",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers):
    resp = client.post(
        "/tallas", json={"nombre": f"ZZCU21T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers):
    resp = client.post(
        "/colores", json={"nombre": f"ZZCU21C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU21SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": f"ZZCU21TEMP_{_suf()}",
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
        "variante_id": variante["id"],
        "producto": producto,
    }


# ---------------------------------------------------------------------------
# Usuarios / autenticacion
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
        correo=f"zzcu21.{etiqueta}.{_suf()}@fashionstore.test",
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
    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu21.{etiqueta}.{_suf()}@fashionstore.test",
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
# Reserva (CU16/CU17)
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

    respuesta = client.post(
        "/reservas",
        json={
            "carrito_id": carrito_id,
            "fecha_atencion": (
                datetime.now(_UTC) + timedelta(days=2)
            ).isoformat(),
            "observacion": "reserva CU21",
        },
        headers=headers,
    )
    assert respuesta.status_code == 201, respuesta.text
    reserva_id = respuesta.json()["reserva_id"]

    if confirmar:
        confirmacion = client.patch(
            f"/reservas-sucursal/{reserva_id}/confirmar",
            json={},
            headers=admin_headers,
        )
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
# Endpoints / verificacion
# ---------------------------------------------------------------------------


def _venta_presencial(client, headers, payload):
    return client.post("/ventas/presencial", json=payload, headers=headers)


def _pago(client, headers, venta_id, metodo="EFECTIVO"):
    return client.post(
        "/pagos/presencial",
        json={"venta_id": venta_id, "metodo": metodo},
        headers=headers,
    )


def _venta_directa(
    db_session,
    *,
    sucursal_id,
    canal="PRESENCIAL",
    estado="PENDIENTE",
    total="50.00",
    reserva_id=None,
    cliente_id=None,
    empleado_id=None,
) -> Venta:
    venta = Venta(
        cliente_id=cliente_id,
        empleado_id=empleado_id,
        sucursal_id=sucursal_id,
        reserva_id=reserva_id,
        fecha_hora=datetime.now(_UTC),
        canal=canal,
        estado=estado,
        total=Decimal(total),
        carrito_id=None,
    )
    db_session.add(venta)
    db_session.commit()
    return venta


def _stock(db_session, inventario_id) -> tuple[int, int]:
    db_session.expire_all()
    inventario = db_session.get(Inventario, inventario_id)
    return inventario.stock_actual, inventario.stock_reservado


def _estado_reserva(db_session, reserva_id) -> str:
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
            select(func.count())
            .select_from(Pago)
            .where(Pago.venta_id == venta_id)
        )
        or 0
    )


def _pagos(db_session, venta_id) -> list[Pago]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Pago).where(Pago.venta_id == venta_id).order_by(Pago.id)
        ).all()
    )


def _estado_venta(db_session, venta_id) -> str:
    db_session.expire_all()
    return db_session.get(Venta, venta_id).estado


# ===========================================================================
# Pago presencial directo
# ===========================================================================


def test_a_pago_presencial_valido_confirma_venta(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    usuario, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)

    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]
    assert Decimal(str(venta.json()["total"])) == Decimal("100.00")
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"
    assert _contar_pagos(db_session, venta_id) == 0

    resp = _pago(client, headers, venta_id, metodo="EFECTIVO")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["venta_id"] == venta_id
    assert body["estado_pago"] == "APROBADO"
    assert body["metodo"] == "EFECTIVO"
    assert Decimal(str(body["monto"])) == Decimal("100.00")
    assert body["estado_venta"] == "COMPLETADA"
    assert body["reserva_id"] is None
    assert body["estado_reserva"] is None

    pagos = _pagos(db_session, venta_id)
    assert len(pagos) == 1
    assert pagos[0].estado == "APROBADO"
    assert Decimal(pagos[0].monto) == Decimal("100.00")
    assert pagos[0].venta_id == venta_id

    # Inventario: stock_actual baja por las unidades vendidas; stock_reservado
    # no cambia (venta directa, sin reserva).
    assert _stock(db_session, ctx["inventario_id"]) == (8, 0)
    salidas = _movimientos(db_session, ctx["inventario_id"], "SALIDA_VENTA")
    assert len(salidas) == 1 and salidas[0].cantidad == 2
    assert salidas[0].referencia_tipo == "VENTA"
    assert salidas[0].referencia_id == venta_id


def test_b_monto_y_estado_no_los_decide_el_frontend(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]

    # Campos extra no autorizados son ignorados por Pydantic.
    resp = client.post(
        "/pagos/presencial",
        json={
            "venta_id": venta_id,
            "metodo": "EFECTIVO",
            "monto": "0.01",
            "estado": "RECHAZADO",
            "empleado_id": 999999999,
            "sucursal_id": 999999999,
            "canal": "WEB",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(str(body["monto"])) == Decimal("50.00")
    assert body["estado_pago"] == "APROBADO"
    assert body["estado_venta"] == "COMPLETADA"


def test_c_metodos_permitidos(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")

    for metodo in ("EFECTIVO", "TARJETA", "TRANSFERENCIA", "QR", "OTRO"):
        ctx = _contexto(client, admin_headers, db_session, sucursal)
        venta = _venta_presencial(
            client,
            headers,
            {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
        )
        assert venta.status_code == 201, venta.text
        resp = _pago(client, headers, venta.json()["venta_id"], metodo=metodo)
        assert resp.status_code == 201, resp.text
        assert resp.json()["metodo"] == metodo


def test_d_metodo_invalido_422(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert venta.status_code == 201, venta.text

    resp = _pago(client, headers, venta.json()["venta_id"], metodo="STRIPE")
    assert resp.status_code == 422, resp.text
    assert _contar_pagos(db_session, venta.json()["venta_id"]) == 0
    assert _estado_venta(db_session, venta.json()["venta_id"]) == "PENDIENTE"


def test_e_doble_submit_no_duplica_pago(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    venta_id = venta.json()["venta_id"]

    primero = _pago(client, headers, venta_id)
    assert primero.status_code == 201, primero.text

    segundo = _pago(client, headers, venta_id)
    assert segundo.status_code == 409, segundo.text

    assert _contar_pagos(db_session, venta_id) == 1
    assert _estado_venta(db_session, venta_id) == "COMPLETADA"
    assert _stock(db_session, ctx["inventario_id"]) == (8, 0)
    assert len(_movimientos(db_session, ctx["inventario_id"], "SALIDA_VENTA")) == 1


def test_f_venta_ya_completada_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    venta_id = venta.json()["venta_id"]
    assert _pago(client, headers, venta_id).status_code == 201

    resp = _pago(client, headers, venta_id)
    assert resp.status_code == 409, resp.text


def test_g_venta_cancelada_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    venta = _venta_directa(db_session, sucursal_id=sucursal, estado="CANCELADA")

    resp = _pago(client, headers, venta.id)
    assert resp.status_code == 409, resp.text


def test_h_venta_inexistente_404(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    resp = _pago(client, headers, 999_999_999)
    assert resp.status_code == 404, resp.text


def test_i_venta_digital_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    cliente, cliente_headers = _crear_cliente(db_session, "digital")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    agregado = _agregar(
        client, cliente_headers, sucursal, ctx["inventario_id"], 1
    )
    assert agregado.status_code == 200, agregado.text
    carrito_id = agregado.json()["carrito_id"]
    digital = client.post(
        "/ventas/digital",
        json={"carrito_id": carrito_id, "canal": "WEB"},
        headers=cliente_headers,
    )
    assert digital.status_code == 201, digital.text
    venta_id = digital.json()["venta_id"]

    resp = _pago(client, headers, venta_id)
    assert resp.status_code == 409, resp.text
    assert _contar_pagos(db_session, venta_id) == 0
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"


def test_j_venta_otra_sucursal_403(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    ajena = _venta_directa(db_session, sucursal_id=otra)

    resp = _pago(client, headers, ajena.id)
    assert resp.status_code == 403, resp.text
    assert _contar_pagos(db_session, ajena.id) == 0


def test_k_admin_alcance_global(client, admin_headers, db_session):
    _, otra = _sucursales_activas(db_session)[:2]
    admin, headers = _crear_personal(db_session, "ADMINISTRADOR", None, "admin")
    ctx = _contexto(client, admin_headers, db_session, otra)
    venta = _venta_presencial(
        client,
        admin_headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert venta.status_code == 201, venta.text

    resp = _pago(client, headers, venta.json()["venta_id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["estado_venta"] == "COMPLETADA"


def test_l_cliente_sin_permiso_403(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    _, cliente_headers = _crear_cliente(db_session, "sinpermiso")
    ctx = _contexto(client, admin_headers, db_session, sucursal)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    venta_id = venta.json()["venta_id"]

    resp = _pago(client, cliente_headers, venta_id)
    assert resp.status_code == 403, resp.text
    assert _contar_pagos(db_session, venta_id) == 0


def test_m_sin_token_401(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    venta_id = venta.json()["venta_id"]

    resp = client.post(
        "/pagos/presencial",
        json={"venta_id": venta_id, "metodo": "EFECTIVO"},
    )
    assert resp.status_code == 401, resp.text


def test_n_venta_sin_detalles_no_persiste_pago(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    vacia = _venta_directa(db_session, sucursal_id=sucursal, total="0.00")

    resp = _pago(client, headers, vacia.id)
    assert resp.status_code == 409, resp.text
    assert _contar_pagos(db_session, vacia.id) == 0
    assert _estado_venta(db_session, vacia.id) == "PENDIENTE"


# ===========================================================================
# Atomicidad
# ===========================================================================


def test_o_fallo_sp_confirmar_venta_hace_rollback(
    client, admin_headers, db_session, monkeypatch
):
    from app.modules.pagos.repositories import repository as repo_module

    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    venta_id = venta.json()["venta_id"]

    def confirmar_falla(db, venta_id, usuario_id):
        raise DBAPIError("CALL sp_confirmar_venta ...", {}, Exception("boom"))

    monkeypatch.setattr(
        repo_module.PagoRepository, "confirmar_venta", confirmar_falla
    )

    resp = _pago(client, headers, venta_id)
    assert resp.status_code == 409, resp.text
    assert "boom" not in resp.text

    assert _contar_pagos(db_session, venta_id) == 0
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)
    assert _movimientos(db_session, ctx["inventario_id"], "SALIDA_VENTA") == []


def test_p_stock_insuficiente_al_confirmar_rollback_pago(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)
    venta = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 2}]},
    )
    venta_id = venta.json()["venta_id"]

    # El inventario cambia entre CU20 y CU21 (CU20 no reserva stock directo).
    inventario = db_session.get(Inventario, ctx["inventario_id"])
    inventario.stock_actual = 1
    db_session.commit()

    resp = _pago(client, headers, venta_id)
    assert resp.status_code == 409, resp.text
    assert _contar_pagos(db_session, venta_id) == 0
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"
    assert _stock(db_session, ctx["inventario_id"]) == (1, 0)
    assert _movimientos(db_session, ctx["inventario_id"], "SALIDA_VENTA") == []


# ===========================================================================
# Venta proveniente de reserva (CU18/CU20)
# ===========================================================================


def test_q_desde_reserva_parcial_consume_y_libera(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(3,)
    )
    inventario_id = reserva["inventarios"][0]
    assert _estado_reserva(db_session, reserva["reserva_id"]) == "CONFIRMADA"
    assert _stock(db_session, inventario_id) == (10, 3)

    venta = _venta_presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 2}],
        },
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]
    assert Decimal(str(venta.json()["total"])) == Decimal("100.00")

    resp = _pago(client, headers, venta_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["estado_venta"] == "COMPLETADA"
    assert body["reserva_id"] == reserva["reserva_id"]
    assert body["estado_reserva"] == "ATENDIDA"

    # reserva CONFIRMADA -> ATENDIDA
    assert _estado_reserva(db_session, reserva["reserva_id"]) == "ATENDIDA"

    # stock_actual -2 (vendidas); stock_reservado -3 (2 consumidas + 1 liberada)
    assert _stock(db_session, inventario_id) == (8, 0)

    salidas = _movimientos(db_session, inventario_id, "SALIDA_VENTA")
    assert len(salidas) == 1 and salidas[0].cantidad == 2

    liberaciones = _movimientos(db_session, inventario_id, "LIBERACION_RESERVA")
    assert len(liberaciones) == 1 and liberaciones[0].cantidad == 1


def test_r_desde_reserva_completa_sin_sobrantes(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "ENCARGADO_SUCURSAL", mia, "e1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(2,)
    )
    inventario_id = reserva["inventarios"][0]

    venta = _venta_presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": inventario_id, "cantidad": 2}],
        },
    )
    assert venta.status_code == 201, venta.text

    resp = _pago(client, headers, venta.json()["venta_id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["estado_reserva"] == "ATENDIDA"

    assert _estado_reserva(db_session, reserva["reserva_id"]) == "ATENDIDA"
    assert _stock(db_session, inventario_id) == (8, 0)
    assert len(_movimientos(db_session, inventario_id, "SALIDA_VENTA")) == 1
    assert _movimientos(db_session, inventario_id, "LIBERACION_RESERVA") == []


def test_s_desde_reserva_dos_lineas_parcial(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(
        client, admin_headers, db_session, mia, cantidades=(2, 1)
    )
    a, b = reserva["inventarios"][0], reserva["inventarios"][1]

    venta = _venta_presencial(
        client,
        headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [{"inventario_id": a, "cantidad": 2}],
        },
    )
    assert venta.status_code == 201, venta.text

    resp = _pago(client, headers, venta.json()["venta_id"])
    assert resp.status_code == 201, resp.text

    assert _estado_reserva(db_session, reserva["reserva_id"]) == "ATENDIDA"
    # Linea a: comprada completa -> stock_actual 8, stock_reservado 0.
    assert _stock(db_session, a) == (8, 0)
    # Linea b: no comprada -> stock_actual intacto, stock_reservado liberado.
    assert _stock(db_session, b) == (10, 0)
    assert len(_movimientos(db_session, a, "SALIDA_VENTA")) == 1
    assert _movimientos(db_session, a, "LIBERACION_RESERVA") == []
    assert len(_movimientos(db_session, b, "LIBERACION_RESERVA")) == 1


# ===========================================================================
# Regresion
# ===========================================================================


def test_t_cu20_sigue_creando_venta_pendiente_sin_pago(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx = _contexto(client, admin_headers, db_session, sucursal, stock_actual=10)

    resp = _venta_presencial(
        client,
        headers,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert resp.status_code == 201, resp.text
    venta_id = resp.json()["venta_id"]
    assert resp.json()["estado"] == "PENDIENTE"
    assert _contar_pagos(db_session, venta_id) == 0
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)
    assert _movimientos(db_session, ctx["inventario_id"]) == []


def test_u_cu19_digital_sigue_operativo(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    cliente, headers = _crear_cliente(db_session, "digital2")
    ctx = _contexto(client, admin_headers, db_session, sucursal)

    agregado = _agregar(client, headers, sucursal, ctx["inventario_id"], 2)
    assert agregado.status_code == 200, agregado.text
    resp = client.post(
        "/ventas/digital",
        json={"carrito_id": agregado.json()["carrito_id"], "canal": "WEB"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["canal"] == "WEB"
    assert resp.json()["estado"] == "PENDIENTE"
    assert _contar_pagos(db_session, resp.json()["venta_id"]) == 0
    assert _stock(db_session, ctx["inventario_id"]) == (10, 0)


def test_v_cu18_preparar_venta_sigue_operativo(
    client, admin_headers, db_session
):
    mia = _sucursales_activas(db_session)[0]
    _, headers = _crear_personal(db_session, "CAJERO", mia, "c1")
    reserva = _reserva(client, admin_headers, db_session, mia, cantidades=(2,))
    inventario_id = reserva["inventarios"][0]

    preparado = client.post(
        f"/atencion-reservas/{reserva['reserva_id']}/preparar-venta",
        json={"items": [{"inventario_id": inventario_id, "cantidad_compra": 2}]},
        headers=headers,
    )
    assert preparado.status_code == 200, preparado.text
    assert preparado.json()["estado"] == "CONFIRMADA"
    assert _stock(db_session, inventario_id) == (10, 2)
