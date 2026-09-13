"""Pruebas de CU13 - Consultar inventario por sucursal.

Las escrituras (categoria, producto, talla, color, variante, temporada e
inventario) ocurren dentro de la transaccion revertida del fixture db_session,
por lo que no quedan datos temporales en la base de datos real. CU13 es solo
consulta: la API no modifica inventario.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Rol, Usuario
from app.modules.inventario.models.models import Inventario

_UTC = timezone.utc

CAMPOS_ITEM = {
    "inventario_id",
    "sucursal_id",
    "sucursal_nombre",
    "producto_id",
    "producto_nombre",
    "precio",
    "categoria_id",
    "categoria_nombre",
    "variante_producto_id",
    "sku",
    "talla_id",
    "talla_nombre",
    "color_id",
    "color_nombre",
    "temporada_id",
    "temporada_nombre",
    "stock_actual",
    "stock_reservado",
    "stock_disponible",
    "fecha_actualizacion",
}


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    nombre = nombre or f"ZZCU13CAT_{_suf()}"
    resp = client.post(
        "/categorias",
        json={"nombre": nombre, "descripcion": "categoria temporal CU13"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None):
    nombre = nombre or f"ZZCU13PROD_{_suf()}"
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre,
            "descripcion": "producto temporal CU13",
            "precio": "19.90",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    nombre = nombre or f"ZZCU13T_{_suf()}"
    resp = client.post("/tallas", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    nombre = nombre or f"ZZCU13C_{_suf()}"
    resp = client.post("/colores", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    sku = sku or f"ZZCU13SKU_{_suf()}"
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={"talla_id": talla_id, "color_id": color_id, "sku": sku},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None):
    nombre = nombre or f"ZZCU13TEMP_{_suf()}"
    resp = client.post(
        "/temporadas",
        json={
            "nombre": nombre,
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-06-30",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_base(client, headers):
    """Crea categoria/producto/talla/color/variante/temporada temporales."""
    categoria = _crear_categoria(client, headers)
    producto = _crear_producto(client, headers, categoria["id"])
    talla = _crear_talla(client, headers)
    color = _crear_color(client, headers)
    variante = _crear_variante(
        client, headers, producto["id"], talla["id"], color["id"]
    )
    temporada = _crear_temporada(client, headers)
    return {
        "categoria": categoria,
        "producto": producto,
        "talla": talla,
        "color": color,
        "variante": variante,
        "temporada": temporada,
    }


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
        fecha_actualizacion=datetime.now(_UTC),
    )
    db_session.add(inventario)
    db_session.flush()
    return inventario


def _sucursales_activas(db_session) -> list[int]:
    from app.modules.sucursales.models.models import Sucursal

    return list(
        db_session.scalars(
            select(Sucursal.id)
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.id)
        ).all()
    )


def _segunda_sucursal(db_session) -> int:
    ids = _sucursales_activas(db_session)
    assert len(ids) >= 2, "CU13 requiere al menos dos sucursales activas"
    return ids[1]


def _usuario_headers(db_session, rol_nombre: str, *, con_empleado: bool = False):
    from datetime import date

    from app.modules.autenticacion_seguridad.models.models import Empleado

    rol = db_session.scalar(select(Rol).where(Rol.nombre == rol_nombre))
    assert rol is not None, f"No existe el rol {rol_nombre}"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu13.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()

    if con_empleado:
        empleado = Empleado(
            usuario_id=usuario.id,
            sucursal_id=_sucursales_activas(db_session)[0],
            nombres="ZZCU13",
            apellidos="Empleado",
            ci=f"ZZCU13{_suf()}",
            fecha_contratacion=date.today(),
            estado=True,
        )
        db_session.add(empleado)
        db_session.flush()

    token = create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "rol": rol.nombre,
            "contexto": "personal",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _consultar(client, headers, **params):
    resp = client.get("/inventario", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    return body


# ---------------------------------------------------------------------------
# Caso A: Administrador consulta todas las sucursales
# ---------------------------------------------------------------------------


def test_admin_consulta_todas_las_sucursales(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    sucursales = _sucursales_activas(db_session)[:2]
    assert len(sucursales) == 2
    for sucursal_id in sucursales:
        _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
            stock_actual=10,
            stock_reservado=1,
        )

    body = _consultar(
        client, admin_headers, producto=base["producto"]["nombre"], limit=100
    )
    assert body["total"] == 2
    assert {item["sucursal_id"] for item in body["items"]} == set(sucursales)
    for item in body["items"]:
        assert CAMPOS_ITEM.issubset(item.keys())
        assert item["stock_disponible"] == (
            item["stock_actual"] - item["stock_reservado"]
        )
        assert item["categoria_nombre"] == base["categoria"]["nombre"]
        assert item["temporada_nombre"] == base["temporada"]["nombre"]


# ---------------------------------------------------------------------------
# Caso B: Administrador filtra por una sucursal concreta
# ---------------------------------------------------------------------------


def test_admin_filtra_por_sucursal(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    sucursal_uno = _sucursales_activas(db_session)[0]
    sucursal_dos = _segunda_sucursal(db_session)
    for sucursal_id in (sucursal_uno, sucursal_dos):
        _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
            stock_actual=8,
            stock_reservado=0,
        )

    body = _consultar(
        client,
        admin_headers,
        producto=base["producto"]["nombre"],
        sucursal_id=sucursal_uno,
    )
    assert body["total"] == 1
    assert [item["sucursal_id"] for item in body["items"]] == [sucursal_uno]


# ---------------------------------------------------------------------------
# Caso C: Encargado consulta unicamente su propia sucursal
# ---------------------------------------------------------------------------


def test_encargado_solo_su_sucursal(
    client, admin_headers, encargado_headers, encargado_usuario, db_session
):
    base = _crear_base(client, admin_headers)
    sucursal_encargado = encargado_usuario.empleado.sucursal_id
    sucursal_ajena = _segunda_sucursal(db_session)
    assert sucursal_ajena != sucursal_encargado
    for sucursal_id in (sucursal_encargado, sucursal_ajena):
        _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=base["variante"]["id"],
            temporada_id=base["temporada"]["id"],
            stock_actual=6,
            stock_reservado=1,
        )

    body = _consultar(
        client,
        encargado_headers,
        producto=base["producto"]["nombre"],
        limit=100,
    )
    assert body["total"] == 1
    assert {item["sucursal_id"] for item in body["items"]} == {
        sucursal_encargado
    }


# ---------------------------------------------------------------------------
# Caso D: Encargado intenta consultar otra sucursal -> 403
# ---------------------------------------------------------------------------


def test_encargado_sucursal_ajena_403(
    client, encargado_headers, encargado_usuario, db_session
):
    sucursal_ajena = _segunda_sucursal(db_session)
    assert sucursal_ajena != encargado_usuario.empleado.sucursal_id
    resp = client.get(
        "/inventario", headers=encargado_headers, params={"sucursal_id": sucursal_ajena}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Caso E: Usuario sin permiso de inventario -> 403
# ---------------------------------------------------------------------------


def test_usuario_sin_permiso_403(client, db_session):
    headers = _usuario_headers(db_session, "CLIENTE")
    resp = client.get("/inventario", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Caso F: Sucursal inexistente -> 404
# ---------------------------------------------------------------------------


def test_sucursal_inexistente_404(client, admin_headers):
    resp = client.get(
        "/inventario", headers=admin_headers, params={"sucursal_id": 999999999}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Caso G: Filtros sin coincidencias -> 200 con lista vacia
# ---------------------------------------------------------------------------


def test_filtros_sin_resultados(client, admin_headers):
    body = _consultar(
        client, admin_headers, producto=f"ZZCU13_NOEXISTE_{_suf()}"
    )
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Caso H: stock_disponible = stock_actual - stock_reservado
# ---------------------------------------------------------------------------


def test_stock_disponible_calculado(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=_sucursales_activas(db_session)[0],
        variante_id=base["variante"]["id"],
        temporada_id=base["temporada"]["id"],
        stock_actual=15,
        stock_reservado=4,
    )

    body = _consultar(
        client, admin_headers, producto=base["producto"]["nombre"]
    )
    assert body["total"] == 1
    item = body["items"][0]
    assert item["stock_actual"] == 15
    assert item["stock_reservado"] == 4
    assert item["stock_disponible"] == 11


# ---------------------------------------------------------------------------
# Caso I: inconsistencia stock_reservado > stock_actual (no se oculta)
# ---------------------------------------------------------------------------


def test_stock_disponible_negativo_no_se_oculta(
    client, admin_headers, db_session
):
    """Si la BD tiene reservado > actual, CU13 devuelve el valor real.

    No se "clampa" a 0 para no ocultar la inconsistencia; el filtro
    disponibilidad=SIN_STOCK permite localizarla.
    """
    base = _crear_base(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=_sucursales_activas(db_session)[0],
        variante_id=base["variante"]["id"],
        temporada_id=base["temporada"]["id"],
        stock_actual=2,
        stock_reservado=5,
    )

    body = _consultar(
        client, admin_headers, producto=base["producto"]["nombre"]
    )
    assert body["items"][0]["stock_disponible"] == -3


# ---------------------------------------------------------------------------
# Filtros por producto / categoria / talla / color / temporada
# ---------------------------------------------------------------------------


def test_filtros_por_producto_categoria_talla_color_temporada(
    client, admin_headers, db_session
):
    base = _crear_base(client, admin_headers)
    _add_inventario(
        db_session,
        sucursal_id=_sucursales_activas(db_session)[0],
        variante_id=base["variante"]["id"],
        temporada_id=base["temporada"]["id"],
        stock_actual=5,
        stock_reservado=0,
    )
    variante_id = base["variante"]["id"]
    nombre = base["producto"]["nombre"]

    filtros = (
        {"producto": nombre.upper()[:12]},  # parcial, case-insensitive
        {"producto": nombre.lower()},
        {"producto_id": base["producto"]["id"]},
        {"categoria_id": base["categoria"]["id"]},
        {"talla_id": base["talla"]["id"]},
        {"color_id": base["color"]["id"]},
        {"temporada_id": base["temporada"]["id"]},
    )
    for params in filtros:
        body = _consultar(client, admin_headers, **params)
        assert body["total"] >= 1, params
        assert variante_id in {
            item["variante_producto_id"] for item in body["items"]
        }, params


# ---------------------------------------------------------------------------
# Filtro disponibilidad (CON_STOCK / SIN_STOCK / STOCK_BAJO / TODOS)
# ---------------------------------------------------------------------------


def test_filtros_disponibilidad(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    producto_id = base["producto"]["id"]
    temporada_id = base["temporada"]["id"]
    sucursal_id = _sucursales_activas(db_session)[0]

    def _nueva_variante_con_stock(stock_actual: int, stock_reservado: int):
        talla = _crear_talla(client, admin_headers)
        color = _crear_color(client, admin_headers)
        variante = _crear_variante(
            client, admin_headers, producto_id, talla["id"], color["id"]
        )
        _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=variante["id"],
            temporada_id=temporada_id,
            stock_actual=stock_actual,
            stock_reservado=stock_reservado,
        )
        return variante["id"]

    variante_con = _nueva_variante_con_stock(10, 0)
    variante_sin = _nueva_variante_con_stock(0, 0)
    variante_bajo = _nueva_variante_con_stock(3, 0)

    nombre = base["producto"]["nombre"]

    def _ids(disponibilidad):
        body = _consultar(
            client,
            admin_headers,
            producto=nombre,
            disponibilidad=disponibilidad,
            limit=100,
        )
        return {item["variante_producto_id"] for item in body["items"]}

    assert _ids("CON_STOCK") == {variante_con, variante_bajo}
    assert _ids("SIN_STOCK") == {variante_sin}
    assert _ids("STOCK_BAJO") == {variante_bajo}
    assert _ids("TODOS") == {variante_con, variante_sin, variante_bajo}

    body = _consultar(
        client, admin_headers, producto=nombre, disponibilidad="SIN_STOCK"
    )
    assert body["items"][0]["stock_disponible"] <= 0


# ---------------------------------------------------------------------------
# Paginacion (patron items/total/limit/offset reutilizado de CU05)
# ---------------------------------------------------------------------------


def test_paginacion_limit_offset(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    producto_id = base["producto"]["id"]
    temporada_id = base["temporada"]["id"]
    sucursal_id = _sucursales_activas(db_session)[0]

    for _ in range(2):
        talla = _crear_talla(client, admin_headers)
        color = _crear_color(client, admin_headers)
        variante = _crear_variante(
            client, admin_headers, producto_id, talla["id"], color["id"]
        )
        _add_inventario(
            db_session,
            sucursal_id=sucursal_id,
            variante_id=variante["id"],
            temporada_id=temporada_id,
            stock_actual=5,
            stock_reservado=0,
        )

    nombre = base["producto"]["nombre"]
    pagina_uno = _consultar(
        client, admin_headers, producto=nombre, limit=1, offset=0
    )
    pagina_dos = _consultar(
        client, admin_headers, producto=nombre, limit=1, offset=1
    )
    assert pagina_uno["limit"] == 1
    assert pagina_uno["total"] == 2
    assert len(pagina_uno["items"]) == 1
    assert len(pagina_dos["items"]) == 1
    assert (
        pagina_uno["items"][0]["inventario_id"]
        != pagina_dos["items"][0]["inventario_id"]
    )


# ---------------------------------------------------------------------------
# Seguridad adicional y solo-lectura
# ---------------------------------------------------------------------------


def test_sin_token_401(client):
    assert client.get("/inventario").status_code == 401


def test_encargado_sin_empleado_403(client, db_session):
    headers = _usuario_headers(db_session, "ENCARGADO_SUCURSAL")
    resp = client.get("/inventario", headers=headers)
    assert resp.status_code == 403


def test_cu13_no_modifica_inventario(client, admin_headers, db_session):
    base = _crear_base(client, admin_headers)
    inventario = _add_inventario(
        db_session,
        sucursal_id=_sucursales_activas(db_session)[0],
        variante_id=base["variante"]["id"],
        temporada_id=base["temporada"]["id"],
        stock_actual=7,
        stock_reservado=2,
    )
    inventario_id = inventario.id
    antes = (inventario.stock_actual, inventario.stock_reservado)

    _consultar(client, admin_headers, producto=base["producto"]["nombre"])

    db_session.expire_all()
    actual = db_session.get(Inventario, inventario_id)
    assert actual is not None
    assert (actual.stock_actual, actual.stock_reservado) == antes


# ---------------------------------------------------------------------------
# Autorizacion CU13: solo Administrador y Encargado; CAJERO y CLIENTE -> 403
# ---------------------------------------------------------------------------


def test_cajero_sin_acceso_403(client, cajero_headers):
    resp = client.get("/inventario", headers=cajero_headers)
    assert resp.status_code == 403


def test_cajero_sin_acceso_403_en_rutas_relacionadas(client, cajero_headers, db_session):
    sucursal_id = _sucursales_activas(db_session)[0]
    assert (
        client.get(
            f"/inventario/sucursal/{sucursal_id}", headers=cajero_headers
        ).status_code
        == 403
    )
    assert (
        client.get("/inventario/producto/1", headers=cajero_headers).status_code
        == 403
    )


def test_cajero_conserva_su_sesion_y_catalogo(client, cajero_headers):
    """Regresion: tras quitarle el permiso de CU13, el Cajero sigue operando."""
    assert client.get("/auth/me", headers=cajero_headers).status_code == 200
    assert client.get("/productos").status_code == 200

