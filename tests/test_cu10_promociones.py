import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.promociones.models.models import PromocionProducto

INICIO = "2026-01-01T00:00:00+00:00"
FIN = "2026-06-30T23:59:59+00:00"


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    nombre = nombre or f"ZZCU10CAT_{_suf()}"
    resp = client.post(
        "/categorias",
        json={"nombre": nombre, "descripcion": "categoria temporal CU10"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="15.00"):
    nombre = nombre or f"ZZCU10PROD_{_suf()}"
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre,
            "descripcion": "producto temporal CU10",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_promocion(
    client,
    headers,
    *,
    nombre=None,
    tipo="PORCENTAJE",
    valor="10",
    inicio=INICIO,
    fin=FIN,
    descripcion="promocion temporal CU10",
):
    nombre = nombre or f"ZZCU10PROMO_{_suf()}"
    resp = client.post(
        "/promociones",
        json={
            "nombre": nombre,
            "descripcion": descripcion,
            "tipo_descuento": tipo,
            "valor_descuento": valor,
            "fecha_inicio": inicio,
            "fecha_fin": fin,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------


def test_seguridad_401_sin_token(client):
    assert client.get("/promociones").status_code == 401
    assert client.get("/promociones/1").status_code == 401
    assert client.get("/promociones/1/productos").status_code == 401
    assert (
        client.post(
            "/promociones",
            json={
                "nombre": "x",
                "tipo_descuento": "MONTO",
                "valor_descuento": "5",
                "fecha_inicio": INICIO,
                "fecha_fin": FIN,
            },
        ).status_code
        == 401
    )


def test_seguridad_403_no_admin(client, cajero_headers):
    assert client.get("/promociones", headers=cajero_headers).status_code == 403
    assert (
        client.post(
            "/promociones",
            json={
                "nombre": f"ZZCU10PROMO_{_suf()}",
                "tipo_descuento": "MONTO",
                "valor_descuento": "5",
                "fecha_inicio": INICIO,
                "fecha_fin": FIN,
            },
            headers=cajero_headers,
        ).status_code
        == 403
    )


def test_admin_accede(client, admin_headers):
    assert client.get("/promociones", headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# Promociones - creacion y validaciones
# ---------------------------------------------------------------------------


def test_crear_porcentaje_y_monto(client, admin_headers):
    porcentaje = _crear_promocion(
        client, admin_headers, tipo="PORCENTAJE", valor="25.5"
    )
    assert porcentaje["tipo_descuento"] == "PORCENTAJE"
    assert _decimal(porcentaje["valor_descuento"]) == Decimal("25.5")
    assert porcentaje["estado"] is True

    monto = _crear_promocion(client, admin_headers, tipo="MONTO", valor="150")
    assert monto["tipo_descuento"] == "MONTO"
    assert _decimal(monto["valor_descuento"]) == Decimal("150")

    # La BD no tiene UNIQUE sobre nombre: se permite repetir.
    nombre = f"ZZCU10PROMO_{_suf()}"
    _crear_promocion(client, admin_headers, nombre=nombre)
    repetida = _crear_promocion(client, admin_headers, nombre=nombre)
    assert repetida["nombre"] == nombre

    # Trim del nombre.
    con_espacios = _crear_promocion(
        client, admin_headers, nombre=f"  ZZCU10PROMO_{_suf()}  "
    )
    assert con_espacios["nombre"].startswith("ZZCU10PROMO_")


def test_tipo_normalizado_mayusculas(client, admin_headers):
    resp = client.post(
        "/promociones",
        json={
            "nombre": f"ZZCU10PROMO_{_suf()}",
            "tipo_descuento": "porcentaje",
            "valor_descuento": "10",
            "fecha_inicio": INICIO,
            "fecha_fin": FIN,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo_descuento"] == "PORCENTAJE"


def test_porcentaje_mayor_100(client, admin_headers):
    resp = client.post(
        "/promociones",
        json={
            "nombre": f"ZZCU10PROMO_{_suf()}",
            "tipo_descuento": "PORCENTAJE",
            "valor_descuento": "100.01",
            "fecha_inicio": INICIO,
            "fecha_fin": FIN,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_valor_menor_igual_cero(client, admin_headers):
    for valor in ("0", "-5"):
        resp = client.post(
            "/promociones",
            json={
                "nombre": f"ZZCU10PROMO_{_suf()}",
                "tipo_descuento": "MONTO",
                "valor_descuento": valor,
                "fecha_inicio": INICIO,
                "fecha_fin": FIN,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400


def test_tipo_invalido(client, admin_headers):
    resp = client.post(
        "/promociones",
        json={
            "nombre": f"ZZCU10PROMO_{_suf()}",
            "tipo_descuento": "DOSXUNO",
            "valor_descuento": "10",
            "fecha_inicio": INICIO,
            "fecha_fin": FIN,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_nombre_vacio(client, admin_headers):
    resp = client.post(
        "/promociones",
        json={
            "nombre": "   ",
            "tipo_descuento": "MONTO",
            "valor_descuento": "10",
            "fecha_inicio": INICIO,
            "fecha_fin": FIN,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_fechas_invalidas(client, admin_headers):
    resp = client.post(
        "/promociones",
        json={
            "nombre": f"ZZCU10PROMO_{_suf()}",
            "tipo_descuento": "MONTO",
            "valor_descuento": "10",
            "fecha_inicio": FIN,
            "fecha_fin": INICIO,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_promociones_futuras_vencidas_solapadas_permitidas(client, admin_headers):
    # No se prohiben promociones futuras ni vencidas ni solapadas.
    futura = _crear_promocion(
        client,
        admin_headers,
        inicio="2030-01-01T00:00:00+00:00",
        fin="2030-12-31T00:00:00+00:00",
    )
    vencida = _crear_promocion(
        client,
        admin_headers,
        inicio="2000-01-01T00:00:00+00:00",
        fin="2000-12-31T00:00:00+00:00",
    )
    solapada_a = _crear_promocion(client, admin_headers)
    solapada_b = _crear_promocion(client, admin_headers)
    assert {futura["id"], vencida["id"], solapada_a["id"], solapada_b["id"]}


# ---------------------------------------------------------------------------
# Promociones - listado, busqueda, filtros, detalle, edicion
# ---------------------------------------------------------------------------


def test_listar_buscar_filtrar(client, admin_headers):
    sufijo = _suf()
    nombre = f"ZZCU10BUSCAR_{sufijo}"
    creada = _crear_promocion(
        client, admin_headers, nombre=nombre, tipo="PORCENTAJE", valor="5"
    )
    _crear_promocion(client, admin_headers, tipo="MONTO", valor="20")

    por_busqueda = client.get(
        "/promociones", params={"buscar": sufijo}, headers=admin_headers
    )
    assert por_busqueda.status_code == 200
    assert [p["id"] for p in por_busqueda.json()] == [creada["id"]]

    por_tipo = client.get(
        "/promociones",
        params={"tipo_descuento": "PORCENTAJE", "buscar": sufijo},
        headers=admin_headers,
    )
    assert por_tipo.status_code == 200
    assert any(p["id"] == creada["id"] for p in por_tipo.json())

    por_tipo_inexistente = client.get(
        "/promociones",
        params={"tipo_descuento": "MONTO", "buscar": sufijo},
        headers=admin_headers,
    )
    assert por_tipo_inexistente.status_code == 200
    assert por_tipo_inexistente.json() == []

    desactivada = client.patch(
        f"/promociones/{creada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivada.status_code == 200

    por_estado = client.get(
        "/promociones",
        params={"estado": False, "buscar": sufijo},
        headers=admin_headers,
    )
    assert por_estado.status_code == 200
    assert [p["id"] for p in por_estado.json()] == [creada["id"]]


def test_detalle_y_404(client, admin_headers):
    creada = _crear_promocion(client, admin_headers)
    detalle = client.get(
        f"/promociones/{creada['id']}", headers=admin_headers
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["id"] == creada["id"]
    assert detalle.json()["total_productos"] == 0

    inexistente = client.get("/promociones/999999999", headers=admin_headers)
    assert inexistente.status_code == 404


def test_editar(client, admin_headers):
    creada = _crear_promocion(client, admin_headers, tipo="PORCENTAJE", valor="10")

    editada = client.patch(
        f"/promociones/{creada['id']}",
        json={
            "nombre": f"ZZCU10PROMO_EDIT_{_suf()}",
            "tipo_descuento": "MONTO",
            "valor_descuento": "75.5",
            "fecha_inicio": "2026-02-01T00:00:00+00:00",
            "fecha_fin": "2026-07-01T00:00:00+00:00",
            "descripcion": "actualizada",
        },
        headers=admin_headers,
    )
    assert editada.status_code == 200, editada.text
    body = editada.json()
    assert body["tipo_descuento"] == "MONTO"
    assert _decimal(body["valor_descuento"]) == Decimal("75.5")
    assert body["descripcion"] == "actualizada"
    assert body["fecha_inicio"].startswith("2026-02-01")

    # Edicion invalida: porcentaje > 100.
    invalida = client.patch(
        f"/promociones/{creada['id']}",
        json={"tipo_descuento": "PORCENTAJE", "valor_descuento": "150"},
        headers=admin_headers,
    )
    assert invalida.status_code == 400

    # Edicion invalida: fechas.
    fechas = client.patch(
        f"/promociones/{creada['id']}",
        json={"fecha_inicio": "2026-09-01T00:00:00+00:00"},
        headers=admin_headers,
    )
    assert fechas.status_code == 400

    # Limpiar descripcion.
    limpiar = client.patch(
        f"/promociones/{creada['id']}",
        json={"descripcion": None},
        headers=admin_headers,
    )
    assert limpiar.status_code == 200, limpiar.text
    assert limpiar.json()["descripcion"] is None

    inexistente = client.patch(
        "/promociones/999999999",
        json={"valor_descuento": "5"},
        headers=admin_headers,
    )
    assert inexistente.status_code == 404


def test_estado_idempotente(client, admin_headers, db_session):
    creada = _crear_promocion(client, admin_headers)

    primera = client.patch(
        f"/promociones/{creada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert primera.status_code == 200
    assert primera.json()["estado"] is False

    segunda = client.patch(
        f"/promociones/{creada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert segunda.status_code == 200
    assert segunda.json()["estado"] is False

    # Crear (1) + primera transicion (1) = 2 eventos; la segunda no audita.
    total = db_session.scalar(
        select(func.count())
        .select_from(Bitacora)
        .where(
            Bitacora.entidad_afectada == "promocion",
            Bitacora.descripcion.ilike(f"%id {creada['id']}%"),
        )
    )
    assert total == 2

    inexistente = client.patch(
        "/promociones/999999999/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert inexistente.status_code == 404


# ---------------------------------------------------------------------------
# Productos asociados
# ---------------------------------------------------------------------------


def test_productos_put_atomico_idempotente(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    p1 = _crear_producto(client, admin_headers, categoria["id"])
    p2 = _crear_producto(client, admin_headers, categoria["id"])
    p3 = _crear_producto(client, admin_headers, categoria["id"])
    inactivo = _crear_producto(client, admin_headers, categoria["id"])
    client.patch(
        f"/productos/{inactivo['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )

    promocion = _crear_promocion(client, admin_headers)
    pid = promocion["id"]

    vacio = client.get(f"/promociones/{pid}/productos", headers=admin_headers)
    assert vacio.status_code == 200
    assert vacio.json()["total"] == 0

    asignar = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [p1["id"], p2["id"]]},
        headers=admin_headers,
    )
    assert asignar.status_code == 200, asignar.text
    assert {p["id"] for p in asignar.json()["productos"]} == {p1["id"], p2["id"]}
    assert asignar.json()["total"] == 2

    # Idempotencia: mismo conjunto en otro orden.
    repetido = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [p2["id"], p1["id"]]},
        headers=admin_headers,
    )
    assert repetido.status_code == 200
    assert repetido.json()["total"] == 2

    # Deduplicacion.
    con_duplicados = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [p1["id"], p1["id"], p2["id"], p3["id"]]},
        headers=admin_headers,
    )
    assert con_duplicados.status_code == 200
    assert con_duplicados.json()["total"] == 3

    total_db = db_session.scalar(
        select(func.count())
        .select_from(PromocionProducto)
        .where(PromocionProducto.promocion_id == pid)
    )
    assert total_db == 3

    # Reemplazo completo.
    quitar = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [p1["id"]]},
        headers=admin_headers,
    )
    assert quitar.status_code == 200
    assert {p["id"] for p in quitar.json()["productos"]} == {p1["id"]}

    # Reemplazo a vacio.
    limpiar = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": []},
        headers=admin_headers,
    )
    assert limpiar.status_code == 200
    assert limpiar.json()["total"] == 0

    # Producto inexistente.
    inexistente = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [999999999]},
        headers=admin_headers,
    )
    assert inexistente.status_code == 404

    # Producto inactivo.
    producto_inactivo = client.put(
        f"/promociones/{pid}/productos",
        json={"producto_ids": [inactivo["id"]]},
        headers=admin_headers,
    )
    assert producto_inactivo.status_code == 409

    # Promocion inexistente.
    promo_inexistente = client.put(
        "/promociones/999999999/productos",
        json={"producto_ids": [p1["id"]]},
        headers=admin_headers,
    )
    assert promo_inexistente.status_code == 404

    listado_inexistente = client.get(
        "/promociones/999999999/productos", headers=admin_headers
    )
    assert listado_inexistente.status_code == 404


def test_put_productos_no_modifica_producto(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"], precio="99.00")
    promocion = _crear_promocion(client, admin_headers)

    client.put(
        f"/promociones/{promocion['id']}/productos",
        json={"producto_ids": [producto["id"]]},
        headers=admin_headers,
    )

    sin_cambios = client.get(
        f"/productos/admin/{producto['id']}", headers=admin_headers
    )
    assert sin_cambios.status_code == 200
    assert sin_cambios.json()["precio"] == producto["precio"]
    assert sin_cambios.json()["estado"] == producto["estado"]


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_cu10(client, admin_headers, db_session, admin_usuario):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    promocion = _crear_promocion(client, admin_headers)
    client.put(
        f"/promociones/{promocion['id']}/productos",
        json={"producto_ids": [producto["id"]]},
        headers=admin_headers,
    )

    entidades = {"promocion", "promocion_producto"}
    eventos = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada.in_(entidades),
            Bitacora.usuario_id == admin_usuario.id,
        )
    ).all()
    entidades_registradas = {evento.entidad_afectada for evento in eventos}
    assert "promocion" in entidades_registradas
    assert "promocion_producto" in entidades_registradas
