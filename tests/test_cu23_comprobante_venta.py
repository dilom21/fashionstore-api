"""Pruebas de CU23 - Emitir comprobante de venta (GET, solo lectura).

Cubre los tres origenes reales:

- WEB (CU19 + pago APROBADO + sp_confirmar_venta),
- PRESENCIAL directa (CU20 + CU21),
- PRESENCIAL desde reserva (CU18/CU20 + CU21, reserva ATENDIDA).

Y las reglas de autorizacion (CLIENTE dueno, ADMINISTRADOR global,
ENCARGADO_SUCURSAL/CAJERO por sucursal), los errores (404/403/409/422), el
calculo de subtotales/ unidades y que la consulta NO modifica la base.

Todas las escrituras ocurren dentro de la transaccion revertida del fixture
db_session: no quedan ventas, pagos, reservas ni movimientos reales.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

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
from app.modules.pagos.repositories.repository import PagoRepository
from app.modules.reservas.models.models import Reserva
from app.modules.ventas.models.models import DetalleVenta, Venta

_UTC = timezone.utc


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Catalogo / inventario
# ---------------------------------------------------------------------------


def _crear_categoria(client, headers):
    resp = client.post(
        "/categorias",
        json={"nombre": f"ZZCU23CAT_{_suf()}", "descripcion": "cat CU23"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, precio="50.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": f"ZZCU23PROD_{_suf()}",
            "descripcion": "producto CU23",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers):
    resp = client.post(
        "/tallas", json={"nombre": f"ZZCU23T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers):
    resp = client.post(
        "/colores", json={"nombre": f"ZZCU23C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": f"ZZCU23SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers):
    resp = client.post(
        "/temporadas",
        json={
            "nombre": f"ZZCU23TEMP_{_suf()}",
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
        "variante": variante,
        "producto": producto,
        "talla": talla,
        "color": color,
        "precio": Decimal(precio),
    }


# ---------------------------------------------------------------------------
# Usuarios / autenticacion (patron existente de los CU11-CU22)
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
        correo=f"zzcu23.{etiqueta}.{_suf()}@fashionstore.test",
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
        ci=f"CI{uuid.uuid4().hex[:9]}",
        estado=True,
    )
    db_session.add(cliente)
    db_session.flush()
    return (
        cliente,
        {"Authorization": f"Bearer {_token(usuario, rol.nombre, 'cliente')}"},
    )


def _crear_personal(db_session, rol_nombre, sucursal_id=None, etiqueta="p"):
    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu23.{etiqueta}.{_suf()}@fashionstore.test",
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

    return (
        usuario,
        {"Authorization": f"Bearer {_token(usuario, rol.nombre, 'personal')}"},
    )


def _usuario_por_rol(db_session, rol_nombre) -> Usuario:
    usuario = db_session.scalar(
        select(Usuario)
        .join(Rol, Rol.id == Usuario.rol_id)
        .where(Rol.nombre == rol_nombre, Usuario.estado.is_(True))
        .order_by(Usuario.id)
    )
    assert usuario is not None, f"No existe usuario activo con rol {rol_nombre}"
    return usuario


# ---------------------------------------------------------------------------
# Flujos reales CU16-CU21
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
    """CU16 (crear reserva) + CU17 (confirmar)."""
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
            "observacion": "reserva CU23",
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
        "headers": headers,
        "sucursal_id": sucursal_id,
    }


def _venta_presencial(client, headers, payload):
    return client.post("/ventas/presencial", json=payload, headers=headers)


def _pago_presencial(client, headers, venta_id, metodo="EFECTIVO"):
    return client.post(
        "/pagos/presencial",
        json={"venta_id": venta_id, "metodo": metodo},
        headers=headers,
    )


def _venta_digital(client, headers, carrito_id, canal="WEB"):
    return client.post(
        "/ventas/digital",
        json={"carrito_id": carrito_id, "canal": canal},
        headers=headers,
    )


def _comprobante(client, headers, venta_id):
    return client.get(f"/ventas/{venta_id}/comprobante", headers=headers)


# ---------------------------------------------------------------------------
# Helpers de datos (ORM) y verificacion
# ---------------------------------------------------------------------------


def _venta_orm(
    db_session,
    *,
    sucursal_id,
    canal="PRESENCIAL",
    estado="PENDIENTE",
    total="0.00",
    reserva_id=None,
    cliente_id=None,
    empleado_id=None,
    carrito_id=None,
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
        carrito_id=carrito_id,
    )
    db_session.add(venta)
    db_session.flush()
    return venta


def _pago_orm(
    db_session,
    *,
    venta_id,
    monto="0.00",
    metodo="TARJETA",
    estado="APROBADO",
    referencia=None,
    pasarela="STRIPE",
) -> Pago:
    pago = Pago(
        venta_id=venta_id,
        fecha_hora=datetime.now(_UTC),
        monto=Decimal(monto),
        metodo=metodo,
        estado=estado,
        referencia_transaccion=referencia,
        pasarela=pasarela,
    )
    db_session.add(pago)
    db_session.flush()
    return pago


def _confirmar_venta_sp(db_session, venta_id, usuario_id) -> None:
    """sp_confirmar_venta (autoridad de CU21/CU22); CU23 no lo usa."""
    PagoRepository.confirmar_venta(db_session, venta_id, usuario_id)


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
        db_session.scalars(
            select(MovimientoInventario).where(*condiciones)
        ).all()
    )


def _estado_venta(db_session, venta_id) -> str:
    db_session.expire_all()
    return db_session.get(Venta, venta_id).estado


def _estado_reserva(db_session, reserva_id) -> str:
    db_session.expire_all()
    return db_session.get(Reserva, reserva_id).estado


def _pagos(db_session, venta_id) -> list[Pago]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Pago).where(Pago.venta_id == venta_id).order_by(Pago.id)
        ).all()
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


def _contar_detalles(db_session, venta_id) -> int:
    return int(
        db_session.scalar(
            select(func.count())
            .select_from(DetalleVenta)
            .where(DetalleVenta.venta_id == venta_id)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Constructores de los tres origenes reales
# ---------------------------------------------------------------------------


def _presencial_directa(
    client,
    cajero_headers,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    cantidad=2,
    cliente_id=None,
    precio="50.00",
):
    """CU20 + CU21: venta PRESENCIAL directa COMPLETADA."""
    ctx = _contexto(
        client, admin_headers, db_session, sucursal_id, precio=precio
    )
    payload = {
        "items": [{"inventario_id": ctx["inventario_id"], "cantidad": cantidad}]
    }
    if cliente_id is not None:
        payload["cliente_id"] = cliente_id

    venta = _venta_presencial(client, cajero_headers, payload)
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]

    pago = _pago_presencial(client, cajero_headers, venta_id)
    assert pago.status_code == 201, pago.text
    assert pago.json()["estado_venta"] == "COMPLETADA"
    assert _estado_venta(db_session, venta_id) == "COMPLETADA"

    return {
        "venta_id": venta_id,
        "ctx": ctx,
        "cantidad": cantidad,
        "cliente_id": cliente_id,
        "total": Decimal(str(venta.json()["total"])),
    }


def _presencial_desde_reserva(
    client,
    admin_headers,
    cajero_headers,
    db_session,
    sucursal_id,
    *,
    cantidad_compra=2,
):
    """CU16 + CU17 + CU20 + CU21 (reserva ATENDIDA, venta COMPLETADA)."""
    reserva = _reserva(
        client, admin_headers, db_session, sucursal_id, cantidades=(2,)
    )
    inventario_id = reserva["inventarios"][0]

    venta = _venta_presencial(
        client,
        cajero_headers,
        {
            "reserva_id": reserva["reserva_id"],
            "items": [
                {"inventario_id": inventario_id, "cantidad": cantidad_compra}
            ],
        },
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]

    pago = _pago_presencial(client, cajero_headers, venta_id)
    assert pago.status_code == 201, pago.text
    assert pago.json()["estado_venta"] == "COMPLETADA"
    assert _estado_reserva(db_session, reserva["reserva_id"]) == "ATENDIDA"

    return {
        "venta_id": venta_id,
        "reserva": reserva,
        "inventario_id": inventario_id,
        "cantidad": cantidad_compra,
        "total": Decimal(str(venta.json()["total"])),
    }


def _digital_web(
    client,
    admin_headers,
    db_session,
    sucursal_id,
    *,
    cantidad=2,
    canal="WEB",
    precio="50.00",
):
    """CU19 + pago APROBADO + sp_confirmar_venta: venta WEB COMPLETADA."""
    cliente, headers = _crear_cliente(db_session)
    ctx = _contexto(
        client, admin_headers, db_session, sucursal_id, precio=precio
    )
    agregado = _agregar(
        client, headers, sucursal_id, ctx["inventario_id"], cantidad
    )
    assert agregado.status_code == 200, agregado.text
    carrito_id = agregado.json()["carrito_id"]

    venta = _venta_digital(client, headers, carrito_id, canal=canal)
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"

    admin_usuario = _usuario_por_rol(db_session, "ADMINISTRADOR")
    _pago_orm(
        db_session,
        venta_id=venta_id,
        monto=str(Decimal(str(venta.json()["total"]))),
        metodo="TARJETA",
        estado="APROBADO",
        referencia=f"pi_test_{_suf()}",
        pasarela="STRIPE",
    )
    _confirmar_venta_sp(db_session, venta_id, int(admin_usuario.id))
    db_session.commit()
    assert _estado_venta(db_session, venta_id) == "COMPLETADA"

    return {
        "venta_id": venta_id,
        "ctx": ctx,
        "carrito_id": carrito_id,
        "cliente": cliente,
        "headers": headers,
        "cantidad": cantidad,
        "total": Decimal(str(venta.json()["total"])),
    }


def _validar_base(body, *, venta_id, sucursal_id, canal):
    """Campos comunes a cualquier origen (WEB/MOVIL/PRESENCIAL)."""
    assert body["venta_id"] == venta_id
    assert body["estado_venta"] == "COMPLETADA"
    assert body["canal"] == canal
    assert body["fecha_hora"]
    assert Decimal(str(body["total"])) > Decimal("0")
    assert body["sucursal"]["id"] == sucursal_id
    assert body["sucursal"]["nombre"]
    assert body["sucursal"]["direccion"]
    assert body["pago"]["estado"] == "APROBADO"
    assert body["pago"]["pago_id"] > 0
    assert Decimal(str(body["pago"]["monto"])) == Decimal(str(body["total"]))
    assert body["pago"]["metodo"]
    assert body["items"]
    # No se inventan campos inexistentes ni se exponen datos de tarjeta.
    assert "numero_factura" not in body
    assert "impuestos" not in body
    assert "numero_tarjeta" not in body
    assert "client_secret" not in body["pago"]


# ===========================================================================
# A/B/C - Personal autorizado (ADMIN global, CAJERO y ENCARGADO por sucursal)
# ===========================================================================


def test_a_administrador_consulta_venta_completada(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    venta = _presencial_directa(
        client, cajero, admin_headers, db_session, sucursal, cantidad=2
    )

    resp = _comprobante(client, admin_headers, venta["venta_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    _validar_base(
        body,
        venta_id=venta["venta_id"],
        sucursal_id=sucursal,
        canal="PRESENCIAL",
    )
    assert body["reserva_id"] is None
    assert body["carrito_id"] is None
    assert body["cliente"] is None  # venta anonima
    assert body["empleado"] is not None
    assert body["empleado"]["nombres"] == "Empleado"
    assert body["pago"]["pasarela"] is None
    assert body["pago"]["metodo"] == "EFECTIVO"

    item = body["items"][0]
    assert item["inventario_id"] == venta["ctx"]["inventario_id"]
    assert item["producto_id"] == venta["ctx"]["producto"]["id"]
    assert item["producto_nombre"] == venta["ctx"]["producto"]["nombre"]
    assert item["sku"] == venta["ctx"]["variante"]["sku"]
    assert item["talla_nombre"] == venta["ctx"]["talla"]["nombre"]
    assert item["color_nombre"] == venta["ctx"]["color"]["nombre"]
    assert item["cantidad"] == 2
    assert Decimal(str(item["precio_unitario"])) == Decimal("50.00")
    assert Decimal(str(item["subtotal_linea"])) == Decimal("100.00")
    assert body["cantidad_total_unidades"] == 2
    assert Decimal(str(body["pago"]["monto"])) == venta["total"]


def test_b_cajero_consulta_venta_de_su_sucursal(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    venta = _presencial_directa(
        client, cajero, admin_headers, db_session, sucursal, cantidad=1
    )

    resp = _comprobante(client, cajero, venta["venta_id"])
    assert resp.status_code == 200, resp.text
    _validar_base(
        resp.json(),
        venta_id=venta["venta_id"],
        sucursal_id=sucursal,
        canal="PRESENCIAL",
    )


def test_c_encargado_consulta_venta_de_su_sucursal(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    _, encargado = _crear_personal(
        db_session, "ENCARGADO_SUCURSAL", sucursal, "e1"
    )
    venta = _presencial_directa(
        client, cajero, admin_headers, db_session, sucursal, cantidad=3
    )

    resp = _comprobante(client, encargado, venta["venta_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validar_base(
        body,
        venta_id=venta["venta_id"],
        sucursal_id=sucursal,
        canal="PRESENCIAL",
    )
    assert body["cantidad_total_unidades"] == 3


# ===========================================================================
# D - Personal de otra sucursal: 403
# ===========================================================================


def test_d_personal_de_otra_sucursal_403(client, admin_headers, db_session):
    mia, otra = _sucursales_activas(db_session)[:2]
    _, cajero = _crear_personal(db_session, "CAJERO", mia, "c1")
    _, cajero_ajeno = _crear_personal(db_session, "CAJERO", otra, "c2")
    _, encargado_ajeno = _crear_personal(
        db_session, "ENCARGADO_SUCURSAL", otra, "e2"
    )
    venta = _presencial_directa(
        client, cajero, admin_headers, db_session, mia, cantidad=1
    )

    assert _comprobante(client, cajero_ajeno, venta["venta_id"]).status_code == 403
    assert (
        _comprobante(client, encargado_ajeno, venta["venta_id"]).status_code == 403
    )
    # El ADMINISTRADOR si puede (alcance global)
    assert (
        _comprobante(client, admin_headers, venta["venta_id"]).status_code == 200
    )


# ===========================================================================
# E - Cliente consulta su propia venta WEB/MOVIL
# ===========================================================================


def test_e_cliente_consulta_su_venta_web(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    venta = _digital_web(
        client, admin_headers, db_session, sucursal, cantidad=2, canal="WEB"
    )

    resp = _comprobante(client, venta["headers"], venta["venta_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    _validar_base(
        body, venta_id=venta["venta_id"], sucursal_id=sucursal, canal="WEB"
    )
    assert body["carrito_id"] == venta["carrito_id"]
    assert body["reserva_id"] is None
    assert body["empleado"] is None
    assert body["cliente"]["id"] == venta["cliente"].id
    assert body["cliente"]["nombre"] == "Josias"
    assert body["cliente"]["apellido"] == "Prueba"
    assert body["pago"]["metodo"] == "TARJETA"
    assert body["pago"]["pasarela"] == "STRIPE"
    assert body["pago"]["referencia_transaccion"]
    assert body["cantidad_total_unidades"] == 2


# ===========================================================================
# F - Cliente consultando una venta ajena: 403
# ===========================================================================


def test_f_cliente_venta_ajena_403(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    venta = _digital_web(client, admin_headers, db_session, sucursal)
    _, headers_ajeno = _crear_cliente(db_session, "otro")

    resp = _comprobante(client, headers_ajeno, venta["venta_id"])
    assert resp.status_code == 403, resp.text

    # Venta presencial anonima (cliente_id null): ningun cliente puede verla
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    anonima = _presencial_directa(
        client, cajero, admin_headers, db_session, sucursal, cantidad=1
    )
    assert (
        _comprobante(client, venta["headers"], anonima["venta_id"]).status_code
        == 403
    )


# ===========================================================================
# G - Venta inexistente / venta_id invalido
# ===========================================================================


def test_g_venta_inexistente_404(client, admin_headers, db_session):
    assert _comprobante(client, admin_headers, 999_999_999).status_code == 404
    # venta_id no numerico: validacion de FastAPI
    assert (
        client.get("/ventas/abc/comprobante", headers=admin_headers).status_code
        == 422
    )
    # 0 no es una venta valida
    assert _comprobante(client, admin_headers, 0).status_code == 404
    # sin token
    assert client.get("/ventas/1/comprobante").status_code == 401


# ===========================================================================
# H - Venta todavia no COMPLETADA: 409
# ===========================================================================


def test_h_venta_pendiente_409(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")

    ctx = _contexto(client, admin_headers, db_session, sucursal)
    venta = _venta_presencial(
        client,
        cajero,
        {"items": [{"inventario_id": ctx["inventario_id"], "cantidad": 1}]},
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]
    assert _estado_venta(db_session, venta_id) == "PENDIENTE"

    resp = _comprobante(client, admin_headers, venta_id)
    assert resp.status_code == 409, resp.text
    assert "COMPLETADA" in resp.json()["detail"]


# ===========================================================================
# I - Venta COMPLETADA sin pago APROBADO: 409
# ===========================================================================


def test_i_venta_completada_sin_pago_aprobado_409(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]

    # COMPLETADA con un pago RECHAZADO
    con_rechazo = _venta_orm(
        db_session, sucursal_id=sucursal, estado="COMPLETADA", total="50.00"
    )
    _pago_orm(
        db_session,
        venta_id=con_rechazo.id,
        monto="50.00",
        estado="RECHAZADO",
        pasarela=None,
    )
    db_session.flush()

    resp = _comprobante(client, admin_headers, int(con_rechazo.id))
    assert resp.status_code == 409, resp.text
    assert "APROBADO" in resp.json()["detail"]

    # COMPLETADA sin ningun pago
    sin_pago = _venta_orm(
        db_session, sucursal_id=sucursal, estado="COMPLETADA", total="20.00"
    )
    db_session.flush()
    assert _comprobante(client, admin_headers, int(sin_pago.id)).status_code == 409


# ===========================================================================
# J - Venta presencial directa: cliente opcional (null permitido)
# ===========================================================================


def test_j_presencial_directa_cliente_opcional(
    client, admin_headers, db_session
):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")

    # 1) Sin cliente: venta anonima -> cliente = null, empleado != null
    anonima = _presencial_directa(
        client, cajero, admin_headers, db_session, sucursal, cantidad=2
    )
    resp = _comprobante(client, cajero, anonima["venta_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validar_base(
        body,
        venta_id=anonima["venta_id"],
        sucursal_id=sucursal,
        canal="PRESENCIAL",
    )
    assert body["cliente"] is None
    assert body["empleado"] is not None
    assert body["reserva_id"] is None
    assert body["carrito_id"] is None

    # 2) Con cliente registrado -> cliente real, no inventado
    cliente, _ = _crear_cliente(db_session, "cli")
    con_cliente = _presencial_directa(
        client,
        cajero,
        admin_headers,
        db_session,
        sucursal,
        cantidad=1,
        cliente_id=cliente.id,
    )
    resp2 = _comprobante(client, cajero, con_cliente["venta_id"])
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["cliente"]["id"] == cliente.id
    assert body2["cliente"]["nombre"] == "Josias"
    assert body2["cliente"]["ci"] == cliente.ci


# ===========================================================================
# K - Venta proveniente de CU18 (reserva_id != null)
# ===========================================================================


def test_k_venta_desde_reserva(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    venta = _presencial_desde_reserva(
        client, admin_headers, cajero, db_session, sucursal, cantidad_compra=2
    )

    resp = _comprobante(client, cajero, venta["venta_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    _validar_base(
        body,
        venta_id=venta["venta_id"],
        sucursal_id=sucursal,
        canal="PRESENCIAL",
    )
    assert body["reserva_id"] == venta["reserva"]["reserva_id"]
    assert body["carrito_id"] is None  # CU20 desde reserva no usa carrito
    assert body["empleado"] is not None
    assert body["cliente"]["id"] == venta["reserva"]["cliente"].id
    assert body["items"][0]["inventario_id"] == venta["inventario_id"]
    assert body["items"][0]["cantidad"] == 2
    assert body["cantidad_total_unidades"] == 2
    assert _estado_reserva(db_session, venta["reserva"]["reserva_id"]) == "ATENDIDA"

    # El ADMINISTRADOR tambien lo ve (alcance global) y el cliente dueno tambien
    assert (
        _comprobante(client, admin_headers, venta["venta_id"]).status_code == 200
    )
    assert (
        _comprobante(
            client, venta["reserva"]["headers"], venta["venta_id"]
        ).status_code
        == 200
    )


# ===========================================================================
# L - La consulta NO modifica venta, pago, reserva ni inventario
# ===========================================================================


def test_l_consulta_no_modifica_nada(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    venta = _presencial_desde_reserva(
        client, admin_headers, cajero, db_session, sucursal
    )
    venta_id = venta["venta_id"]
    reserva_id = venta["reserva"]["reserva_id"]
    inventario_id = venta["inventario_id"]

    db_session.expire_all()
    fila_venta = db_session.get(Venta, venta_id)
    antes_venta = (
        fila_venta.estado,
        Decimal(fila_venta.total),
        fila_venta.fecha_hora,
        fila_venta.cliente_id,
        fila_venta.empleado_id,
        fila_venta.sucursal_id,
        fila_venta.reserva_id,
        fila_venta.carrito_id,
        fila_venta.canal,
    )
    pagos = _pagos(db_session, venta_id)
    assert len(pagos) == 1
    antes_pago = (
        int(pagos[0].id),
        pagos[0].estado,
        Decimal(pagos[0].monto),
        pagos[0].metodo,
        pagos[0].referencia_transaccion,
        pagos[0].pasarela,
    )
    fila_reserva = db_session.get(Reserva, reserva_id)
    antes_reserva = (
        fila_reserva.estado,
        fila_reserva.observacion,
        fila_reserva.fecha_atencion,
    )
    antes_stock = _stock(db_session, inventario_id)
    antes_movimientos = len(_movimientos(db_session, inventario_id))
    antes_pagos_n = _contar_pagos(db_session, venta_id)
    antes_detalles_n = _contar_detalles(db_session, venta_id)

    _, encargado = _crear_personal(
        db_session, "ENCARGADO_SUCURSAL", sucursal, "e1"
    )
    for headers in (cajero, encargado, admin_headers):
        assert _comprobante(client, headers, venta_id).status_code == 200

    db_session.expire_all()
    fila_venta = db_session.get(Venta, venta_id)
    despues_venta = (
        fila_venta.estado,
        Decimal(fila_venta.total),
        fila_venta.fecha_hora,
        fila_venta.cliente_id,
        fila_venta.empleado_id,
        fila_venta.sucursal_id,
        fila_venta.reserva_id,
        fila_venta.carrito_id,
        fila_venta.canal,
    )
    pagos = _pagos(db_session, venta_id)
    despues_pago = (
        int(pagos[0].id),
        pagos[0].estado,
        Decimal(pagos[0].monto),
        pagos[0].metodo,
        pagos[0].referencia_transaccion,
        pagos[0].pasarela,
    )
    fila_reserva = db_session.get(Reserva, reserva_id)
    despues_reserva = (
        fila_reserva.estado,
        fila_reserva.observacion,
        fila_reserva.fecha_atencion,
    )

    assert despues_venta == antes_venta
    assert despues_pago == antes_pago
    assert despues_reserva == antes_reserva
    assert _stock(db_session, inventario_id) == antes_stock
    assert len(_movimientos(db_session, inventario_id)) == antes_movimientos
    assert _contar_pagos(db_session, venta_id) == antes_pagos_n
    assert _contar_detalles(db_session, venta_id) == antes_detalles_n


# ===========================================================================
# M - Calculo de subtotal_linea y cantidad_total_unidades
# ===========================================================================


def test_m_subtotal_lineas_y_unidades(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    _, cajero = _crear_personal(db_session, "CAJERO", sucursal, "c1")
    ctx1 = _contexto(
        client, admin_headers, db_session, sucursal, precio="50.00"
    )
    ctx2 = _contexto(
        client, admin_headers, db_session, sucursal, precio="20.00"
    )

    venta = _venta_presencial(
        client,
        cajero,
        {
            "items": [
                {"inventario_id": ctx1["inventario_id"], "cantidad": 2},
                {"inventario_id": ctx2["inventario_id"], "cantidad": 3},
            ]
        },
    )
    assert venta.status_code == 201, venta.text
    venta_id = venta.json()["venta_id"]
    pago = _pago_presencial(client, cajero, venta_id, metodo="TARJETA")
    assert pago.status_code == 201, pago.text

    resp = _comprobante(client, cajero, venta_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["items"]) == 2
    por_inventario = {item["inventario_id"]: item for item in body["items"]}
    linea1 = por_inventario[ctx1["inventario_id"]]
    linea2 = por_inventario[ctx2["inventario_id"]]
    assert linea1["cantidad"] == 2
    assert Decimal(str(linea1["precio_unitario"])) == Decimal("50.00")
    assert Decimal(str(linea1["subtotal_linea"])) == Decimal("100.00")
    assert linea2["cantidad"] == 3
    assert Decimal(str(linea2["precio_unitario"])) == Decimal("20.00")
    assert Decimal(str(linea2["subtotal_linea"])) == Decimal("60.00")

    assert body["cantidad_total_unidades"] == 5
    suma = sum(
        Decimal(str(item["subtotal_linea"])) for item in body["items"]
    )
    assert suma == Decimal(str(body["total"])) == Decimal("160.00")


# ===========================================================================
# N - Idempotencia: dos consultas devuelven lo mismo y no crean filas
# ===========================================================================


def test_n_dos_consultas_idempotentes(client, admin_headers, db_session):
    sucursal = _sucursales_activas(db_session)[0]
    venta = _digital_web(client, admin_headers, db_session, sucursal)
    venta_id = venta["venta_id"]
    inventario_id = venta["ctx"]["inventario_id"]

    antes = (
        _contar_pagos(db_session, venta_id),
        _contar_detalles(db_session, venta_id),
        int(db_session.scalar(select(func.count()).select_from(Venta)) or 0),
        int(db_session.scalar(select(func.count()).select_from(Pago)) or 0),
        len(_movimientos(db_session, inventario_id)),
        _stock(db_session, inventario_id),
        _estado_venta(db_session, venta_id),
    )

    primera = _comprobante(client, venta["headers"], venta_id)
    segunda = _comprobante(client, venta["headers"], venta_id)
    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 200, segunda.text
    assert primera.json() == segunda.json()

    despues = (
        _contar_pagos(db_session, venta_id),
        _contar_detalles(db_session, venta_id),
        int(db_session.scalar(select(func.count()).select_from(Venta)) or 0),
        int(db_session.scalar(select(func.count()).select_from(Pago)) or 0),
        len(_movimientos(db_session, inventario_id)),
        _stock(db_session, inventario_id),
        _estado_venta(db_session, venta_id),
    )
    assert despues == antes
