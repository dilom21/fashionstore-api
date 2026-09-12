import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    nombre = nombre or f"ZZCU09CAT_{_suf()}"
    resp = client.post(
        "/categorias",
        json={"nombre": nombre, "descripcion": "categoria temporal CU09"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(
    client, headers, categoria_id, nombre=None, descripcion=None, precio="19.90"
):
    nombre = nombre or f"ZZCU09PROD_{_suf()}"
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre,
            "descripcion": descripcion or "producto temporal CU09",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    nombre = nombre or f"ZZCU09T_{_suf()}"
    resp = client.post("/tallas", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    nombre = nombre or f"ZZCU09C_{_suf()}"
    resp = client.post("/colores", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    sku = sku or f"ZZCU09SKU_{_suf()}"
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={"talla_id": talla_id, "color_id": color_id, "sku": sku},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(
    client, headers, nombre=None, inicio="2026-01-01", fin="2026-06-30"
):
    nombre = nombre or f"ZZCU09TEMP_{_suf()}"
    resp = client.post(
        "/temporadas",
        json={"nombre": nombre, "fecha_inicio": inicio, "fecha_fin": fin},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_coleccion(client, headers, temporada_id, nombre=None):
    nombre = nombre or f"ZZCU09COL_{_suf()}"
    resp = client.post(
        "/colecciones",
        json={
            "temporada_id": temporada_id,
            "nombre": nombre,
            "descripcion": "coleccion temporal CU09",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _asignar_productos(client, headers, coleccion_id, producto_ids):
    resp = client.put(
        f"/colecciones/{coleccion_id}/productos",
        json={"producto_ids": producto_ids},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sucursal_activa(db_session) -> Sucursal:
    sucursal = db_session.scalar(
        select(Sucursal)
        .where(Sucursal.estado.is_(True))
        .order_by(Sucursal.id)
    )
    assert sucursal is not None, "No existe sucursal activa para las pruebas"
    return sucursal


def _add_inventario(
    db_session,
    *,
    sucursal_id,
    variante_id,
    temporada_id,
    stock_actual,
    stock_reservado,
) -> Inventario:
    inventario = Inventario(
        sucursal_id=sucursal_id,
        variante_producto_id=variante_id,
        temporada_id=temporada_id,
        stock_actual=stock_actual,
        stock_reservado=stock_reservado,
        fecha_actualizacion=datetime.now(timezone.utc),
    )
    db_session.add(inventario)
    db_session.flush()
    return inventario


def _ids(resp) -> set[int]:
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()}


# ---------------------------------------------------------------------------
# Contrato y seguridad
# ---------------------------------------------------------------------------


def test_productos_sin_filtros_conserva_contrato(client):
    resp = client.get("/productos")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    campos = {"id", "nombre", "descripcion", "precio", "estado", "categoria_id", "categoria"}
    for producto in data:
        assert campos.issubset(producto.keys())
        assert producto["estado"] is True
        assert "categoria" in producto and "nombre" in producto["categoria"]


def test_endpoints_publicos_sin_token(client):
    assert client.get("/productos").status_code == 200
    assert client.get("/catalogo/filtros").status_code == 200
    listado = client.get("/productos").json()
    assert listado
    producto_id = listado[0]["id"]
    assert client.get(f"/productos/{producto_id}").status_code == 200
    assert (
        client.get(f"/productos/{producto_id}/disponibilidad").status_code == 200
    )


def test_query_params_invalidos_422(client):
    assert client.get("/productos", params={"categoria_id": "abc"}).status_code == 422
    assert client.get("/productos", params={"talla_id": -1}).status_code == 422
    assert (
        client.get("/productos/1/disponibilidad", params={"sucursal_id": "x"}).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# Catalogo de filtros publico
# ---------------------------------------------------------------------------


def test_catalogo_filtros_estructura(client):
    resp = client.get("/catalogo/filtros")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {
        "categorias",
        "tallas",
        "colores",
        "temporadas",
        "colecciones",
        "sucursales",
    }
    for clave in ("categorias", "tallas", "colores", "temporadas"):
        assert isinstance(data[clave], list)
        for item in data[clave]:
            assert set(item.keys()) == {"id", "nombre"}
    for coleccion in data["colecciones"]:
        assert set(coleccion.keys()) == {"id", "nombre", "temporada_id"}
    for sucursal in data["sucursales"]:
        assert set(sucursal.keys()) == {"id", "nombre", "ciudad"}


def test_catalogo_filtros_solo_activos(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])

    client.patch(
        f"/categorias/{categoria['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    client.patch(
        f"/tallas/{talla['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    client.patch(
        f"/colores/{color['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    client.patch(
        f"/colecciones/{coleccion['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    client.patch(
        f"/temporadas/{temporada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )

    data = client.get("/catalogo/filtros").json()
    assert categoria["id"] not in {c["id"] for c in data["categorias"]}
    assert talla["id"] not in {t["id"] for t in data["tallas"]}
    assert color["id"] not in {c["id"] for c in data["colores"]}
    assert temporada["id"] not in {t["id"] for t in data["temporadas"]}
    assert coleccion["id"] not in {c["id"] for c in data["colecciones"]}


# ---------------------------------------------------------------------------
# Filtros del catalogo
# ---------------------------------------------------------------------------


def test_busqueda_por_nombre_y_descripcion(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    token_nombre = _suf()
    token_desc = _suf()
    producto = _crear_producto(
        client,
        admin_headers,
        categoria["id"],
        nombre=f"ZZCU09BUSCA_{token_nombre}",
        descripcion=f"descripcion unica {token_desc}",
    )

    por_nombre = _ids(client.get("/productos", params={"buscar": token_nombre}))
    assert producto["id"] in por_nombre

    por_desc = _ids(client.get("/productos", params={"buscar": token_desc}))
    assert producto["id"] in por_desc


def test_filtro_categoria(client, admin_headers):
    cat_uno = _crear_categoria(client, admin_headers)
    cat_dos = _crear_categoria(client, admin_headers)
    producto_uno = _crear_producto(client, admin_headers, cat_uno["id"])
    producto_dos = _crear_producto(client, admin_headers, cat_dos["id"])

    resp = client.get("/productos", params={"categoria_id": cat_uno["id"]})
    ids = _ids(resp)
    assert producto_uno["id"] in ids
    assert producto_dos["id"] not in ids
    assert all(
        item["categoria_id"] == cat_uno["id"] for item in resp.json()
    )


def test_filtro_talla_y_color(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    otra_talla = _crear_talla(client, admin_headers)
    otro_color = _crear_color(client, admin_headers)

    assert producto["id"] in _ids(
        client.get("/productos", params={"talla_id": talla["id"]})
    )
    assert producto["id"] in _ids(
        client.get("/productos", params={"color_id": color["id"]})
    )
    assert producto["id"] not in _ids(
        client.get("/productos", params={"talla_id": otra_talla["id"]})
    )
    assert producto["id"] not in _ids(
        client.get("/productos", params={"color_id": otro_color["id"]})
    )


def test_filtro_coleccion_y_temporada_por_coleccion(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])
    _asignar_productos(
        client, admin_headers, coleccion["id"], [producto["id"]]
    )

    assert producto["id"] in _ids(
        client.get("/productos", params={"coleccion_id": coleccion["id"]})
    )
    assert producto["id"] in _ids(
        client.get("/productos", params={"temporada_id": temporada["id"]})
    )


def test_filtro_temporada_por_inventario(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=5,
        stock_reservado=0,
    )

    assert producto["id"] in _ids(
        client.get("/productos", params={"temporada_id": temporada["id"]})
    )


def test_filtro_sucursal_y_con_stock(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)

    def _producto_con_variante(stock_actual, stock_reservado, con_inventario=True):
        producto = _crear_producto(client, admin_headers, categoria["id"])
        talla = _crear_talla(client, admin_headers)
        color = _crear_color(client, admin_headers)
        variante = _crear_variante(
            client, admin_headers, producto["id"], talla["id"], color["id"]
        )
        if con_inventario:
            _add_inventario(
                db_session,
                sucursal_id=sucursal.id,
                variante_id=variante["id"],
                temporada_id=temporada["id"],
                stock_actual=stock_actual,
                stock_reservado=stock_reservado,
            )
        return producto

    con_disponible = _producto_con_variante(5, 1)
    sin_disponible = _producto_con_variante(3, 3)
    sin_inventario = _producto_con_variante(0, 0, con_inventario=False)

    por_sucursal = _ids(
        client.get("/productos", params={"sucursal_id": sucursal.id})
    )
    assert con_disponible["id"] in por_sucursal
    assert sin_disponible["id"] in por_sucursal
    assert sin_inventario["id"] not in por_sucursal

    con_stock = _ids(client.get("/productos", params={"con_stock": "true"}))
    assert con_disponible["id"] in con_stock
    assert sin_disponible["id"] not in con_stock
    assert sin_inventario["id"] not in con_stock

    con_stock_false = _ids(
        client.get("/productos", params={"con_stock": "false"})
    )
    assert sin_inventario["id"] in con_stock_false


def test_combinacion_filtros_y_sin_resultados(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=4,
        stock_reservado=1,
    )

    combinado = _ids(
        client.get(
            "/productos",
            params={
                "categoria_id": categoria["id"],
                "talla_id": talla["id"],
                "color_id": color["id"],
                "temporada_id": temporada["id"],
                "sucursal_id": sucursal.id,
                "con_stock": "true",
            },
        )
    )
    assert producto["id"] in combinado

    sin_resultados = client.get(
        "/productos", params={"buscar": f"ZZCU09NOEXISTE_{_suf()}"}
    )
    assert sin_resultados.status_code == 200
    assert sin_resultados.json() == []


# ---------------------------------------------------------------------------
# Detalle de producto
# ---------------------------------------------------------------------------


def test_detalle_activo_e_inactivo(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])

    detalle = client.get(f"/productos/{producto['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["id"] == producto["id"]

    client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert client.get(f"/productos/{producto['id']}").status_code == 404
    assert client.get("/productos/999999999").status_code == 404


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------


def test_disponibilidad_agrupada_y_stock(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=7,
        stock_reservado=2,
    )

    resp = client.get(f"/productos/{producto['id']}/disponibilidad")
    assert resp.status_code == 200
    body = resp.json()
    assert body["producto_id"] == producto["id"]

    sucursales = {s["sucursal_id"]: s for s in body["sucursales"]}
    assert sucursal.id in sucursales
    variante_resp = next(
        v
        for v in sucursales[sucursal.id]["variantes"]
        if v["variante_id"] == variante["id"]
    )
    assert variante_resp["stock_disponible"] == 5
    assert variante_resp["talla"] == talla["nombre"]
    assert variante_resp["color"] == color["nombre"]
    assert variante_resp["temporada"] == temporada["nombre"]


def test_disponibilidad_filtros(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla_a = _crear_talla(client, admin_headers)
    color_a = _crear_color(client, admin_headers)
    variante_a = _crear_variante(
        client, admin_headers, producto["id"], talla_a["id"], color_a["id"]
    )
    talla_b = _crear_talla(client, admin_headers)
    color_b = _crear_color(client, admin_headers)
    variante_b = _crear_variante(
        client, admin_headers, producto["id"], talla_b["id"], color_b["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada_uno = _crear_temporada(client, admin_headers)
    temporada_dos = _crear_temporada(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante_a["id"],
        temporada_id=temporada_uno["id"],
        stock_actual=5,
        stock_reservado=0,
    )
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante_b["id"],
        temporada_id=temporada_dos["id"],
        stock_actual=4,
        stock_reservado=0,
    )

    def _variantes(params):
        resp = client.get(
            f"/productos/{producto['id']}/disponibilidad", params=params
        )
        assert resp.status_code == 200, resp.text
        return {
            v["variante_id"]
            for s in resp.json()["sucursales"]
            for v in s["variantes"]
        }

    assert _variantes({"talla_id": talla_a["id"]}) == {variante_a["id"]}
    assert _variantes({"color_id": color_b["id"]}) == {variante_b["id"]}
    assert _variantes({"temporada_id": temporada_uno["id"]}) == {
        variante_a["id"]
    }
    assert _variantes({"sucursal_id": sucursal.id}) == {
        variante_a["id"],
        variante_b["id"],
    }


def test_disponibilidad_sin_stock_no_aparece(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=2,
        stock_reservado=2,
    )

    resp = client.get(f"/productos/{producto['id']}/disponibilidad")
    assert resp.status_code == 200
    assert resp.json()["sucursales"] == []


def test_disponibilidad_no_modifica_inventario(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )
    sucursal = _sucursal_activa(db_session)
    temporada = _crear_temporada(client, admin_headers)
    inventario = _add_inventario(
        db_session,
        sucursal_id=sucursal.id,
        variante_id=variante["id"],
        temporada_id=temporada["id"],
        stock_actual=10,
        stock_reservado=3,
    )
    inventario_id = inventario.id

    antes = (
        inventario.stock_actual,
        inventario.stock_reservado,
    )

    assert (
        client.get(f"/productos/{producto['id']}/disponibilidad").status_code
        == 200
    )

    db_session.expire_all()
    actual = db_session.get(Inventario, inventario_id)
    assert actual is not None
    assert (actual.stock_actual, actual.stock_reservado) == antes
