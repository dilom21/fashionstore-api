import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.temporadas_colecciones.models.models import ProductoColeccion


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    nombre = nombre or f"ZZCU08CAT_{_suf()}"
    resp = client.post(
        "/categorias",
        json={"nombre": nombre, "descripcion": "categoria temporal CU08"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="15.00"):
    nombre = nombre or f"ZZCU08PROD_{_suf()}"
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre,
            "descripcion": "producto temporal CU08",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_temporada(client, headers, nombre=None, inicio="2026-01-01", fin="2026-06-30"):
    nombre = nombre or f"ZZCU08TEMP_{_suf()}"
    resp = client.post(
        "/temporadas",
        json={"nombre": nombre, "fecha_inicio": inicio, "fecha_fin": fin},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_coleccion(client, headers, temporada_id, nombre=None):
    nombre = nombre or f"ZZCU08COL_{_suf()}"
    resp = client.post(
        "/colecciones",
        json={
            "temporada_id": temporada_id,
            "nombre": nombre,
            "descripcion": "coleccion temporal CU08",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------


def test_seguridad_401_sin_token(client):
    assert client.get("/temporadas").status_code == 401
    assert client.get("/colecciones").status_code == 401
    assert (
        client.post(
            "/temporadas",
            json={
                "nombre": "x",
                "fecha_inicio": "2026-01-01",
                "fecha_fin": "2026-02-01",
            },
        ).status_code
        == 401
    )


def test_seguridad_403_no_admin(client, cajero_headers):
    assert client.get("/temporadas", headers=cajero_headers).status_code == 403
    assert client.get("/colecciones", headers=cajero_headers).status_code == 403
    assert (
        client.post(
            "/temporadas",
            json={
                "nombre": f"ZZCU08TEMP_{_suf()}",
                "fecha_inicio": "2026-01-01",
                "fecha_fin": "2026-02-01",
            },
            headers=cajero_headers,
        ).status_code
        == 403
    )


def test_admin_accede(client, admin_headers):
    assert client.get("/temporadas", headers=admin_headers).status_code == 200
    assert client.get("/colecciones", headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


def test_temporada_crear_duplicado_y_fechas(client, admin_headers):
    nombre = f"ZZCU08TEMP_{_suf()}"
    creada = _crear_temporada(client, admin_headers, nombre=nombre)
    assert creada["estado"] is True
    assert creada["nombre"] == nombre

    duplicada = client.post(
        "/temporadas",
        json={
            "nombre": nombre,
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-03-01",
        },
        headers=admin_headers,
    )
    assert duplicada.status_code == 409

    rango_invalido = client.post(
        "/temporadas",
        json={
            "nombre": f"ZZCU08TEMP_{_suf()}",
            "fecha_inicio": "2026-05-01",
            "fecha_fin": "2026-01-01",
        },
        headers=admin_headers,
    )
    assert rango_invalido.status_code == 400


def test_temporada_editar_listar_filtrar(client, admin_headers):
    nombre = f"ZZCU08TEMP_{_suf()}"
    creada = _crear_temporada(client, admin_headers, nombre=nombre)

    nuevo_nombre = f"ZZCU08TEMP_EDIT_{_suf()}"
    editada = client.patch(
        f"/temporadas/{creada['id']}",
        json={
            "nombre": nuevo_nombre,
            "fecha_inicio": "2026-02-01",
            "fecha_fin": "2026-07-01",
        },
        headers=admin_headers,
    )
    assert editada.status_code == 200, editada.text
    assert editada.json()["nombre"] == nuevo_nombre
    assert editada.json()["fecha_inicio"] == "2026-02-01"

    rango_invalido = client.patch(
        f"/temporadas/{creada['id']}",
        json={"fecha_inicio": "2026-09-01"},
        headers=admin_headers,
    )
    assert rango_invalido.status_code == 400

    detalle = client.get(f"/temporadas/{creada['id']}", headers=admin_headers)
    assert detalle.status_code == 200
    assert detalle.json()["id"] == creada["id"]

    por_busqueda = client.get(
        "/temporadas",
        params={"buscar": nuevo_nombre},
        headers=admin_headers,
    )
    assert por_busqueda.status_code == 200
    assert any(t["id"] == creada["id"] for t in por_busqueda.json())

    inexistente = client.get("/temporadas/999999999", headers=admin_headers)
    assert inexistente.status_code == 404


def test_temporada_estado_con_colecciones_activas(client, admin_headers):
    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])

    bloqueado = client.patch(
        f"/temporadas/{temporada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert bloqueado.status_code == 409

    desactivar_coleccion = client.patch(
        f"/colecciones/{coleccion['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar_coleccion.status_code == 200

    libre = client.patch(
        f"/temporadas/{temporada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert libre.status_code == 200
    assert libre.json()["estado"] is False

    reactivar = client.patch(
        f"/temporadas/{temporada['id']}/estado",
        json={"estado": True},
        headers=admin_headers,
    )
    assert reactivar.status_code == 200


def test_temporada_estado_con_inventario(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    resp_talla = client.post(
        "/tallas", json={"nombre": f"ZZCU08T_{_suf()}"}, headers=admin_headers
    )
    assert resp_talla.status_code == 201, resp_talla.text
    talla = resp_talla.json()
    resp_color = client.post(
        "/colores", json={"nombre": f"ZZCU08C_{_suf()}"}, headers=admin_headers
    )
    assert resp_color.status_code == 201, resp_color.text
    color = resp_color.json()
    resp_variante = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla["id"],
            "color_id": color["id"],
            "sku": f"ZZCU08SKU_{_suf()}",
        },
        headers=admin_headers,
    )
    assert resp_variante.status_code == 201, resp_variante.text
    variante = resp_variante.json()

    sucursal = db_session.scalar(select(Sucursal).order_by(Sucursal.id))
    assert sucursal is not None

    temporada = _crear_temporada(client, admin_headers)
    db_session.add(
        Inventario(
            sucursal_id=sucursal.id,
            variante_producto_id=variante["id"],
            temporada_id=temporada["id"],
            stock_actual=5,
            stock_reservado=0,
            fecha_actualizacion=datetime.now(timezone.utc),
        )
    )
    db_session.flush()

    bloqueado = client.patch(
        f"/temporadas/{temporada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert bloqueado.status_code == 409


# ---------------------------------------------------------------------------
# Colecciones
# ---------------------------------------------------------------------------


def test_coleccion_crear_duplicado_y_temporada(client, admin_headers):
    temporada = _crear_temporada(client, admin_headers)
    nombre = f"ZZCU08COL_{_suf()}"
    creada = _crear_coleccion(
        client, admin_headers, temporada["id"], nombre=nombre
    )
    assert creada["temporada_id"] == temporada["id"]
    assert creada["estado"] is True

    duplicada = client.post(
        "/colecciones",
        json={"temporada_id": temporada["id"], "nombre": nombre},
        headers=admin_headers,
    )
    assert duplicada.status_code == 409

    otra_temporada = _crear_temporada(client, admin_headers)
    misma_nombre_otra = client.post(
        "/colecciones",
        json={"temporada_id": otra_temporada["id"], "nombre": nombre},
        headers=admin_headers,
    )
    assert misma_nombre_otra.status_code == 201

    inexistente = client.post(
        "/colecciones",
        json={"temporada_id": 999999999, "nombre": f"ZZCU08COL_{_suf()}"},
        headers=admin_headers,
    )
    assert inexistente.status_code == 404

    temporada_inactiva = _crear_temporada(client, admin_headers)
    desactivar = client.patch(
        f"/temporadas/{temporada_inactiva['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200, desactivar.text
    inactiva = client.post(
        "/colecciones",
        json={
            "temporada_id": temporada_inactiva["id"],
            "nombre": f"ZZCU08COL_{_suf()}",
        },
        headers=admin_headers,
    )
    assert inactiva.status_code == 409

def test_coleccion_editar_mover_y_estado(client, admin_headers):
    temporada = _crear_temporada(client, admin_headers)
    otra = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])

    movida = client.patch(
        f"/colecciones/{coleccion['id']}",
        json={"temporada_id": otra["id"], "nombre": f"ZZCU08COL_EDIT_{_suf()}"},
        headers=admin_headers,
    )
    assert movida.status_code == 200, movida.text
    assert movida.json()["temporada_id"] == otra["id"]

    inactiva = _crear_temporada(client, admin_headers)
    client.patch(
        f"/temporadas/{inactiva['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    mover_inactiva = client.patch(
        f"/colecciones/{coleccion['id']}",
        json={"temporada_id": inactiva["id"]},
        headers=admin_headers,
    )
    assert mover_inactiva.status_code == 409

    detalle = client.get(f"/colecciones/{coleccion['id']}", headers=admin_headers)
    assert detalle.status_code == 200
    assert detalle.json()["temporada"]["id"] == otra["id"]
    assert detalle.json()["total_productos"] == 0

    desactivar = client.patch(
        f"/colecciones/{coleccion['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200
    assert desactivar.json()["estado"] is False

    filtro = client.get(
        "/colecciones",
        params={"temporada_id": otra["id"], "estado": False},
        headers=admin_headers,
    )
    assert filtro.status_code == 200
    assert any(c["id"] == coleccion["id"] for c in filtro.json())

    inexistente = client.get("/colecciones/999999999", headers=admin_headers)
    assert inexistente.status_code == 404


# ---------------------------------------------------------------------------
# Asignacion producto_coleccion
# ---------------------------------------------------------------------------


def test_asignacion_productos_put_atomico_idempotente(
    client, admin_headers, db_session
):
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

    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])
    cid = coleccion["id"]

    vacio = client.get(f"/colecciones/{cid}/productos", headers=admin_headers)
    assert vacio.status_code == 200
    assert vacio.json()["total"] == 0

    asignar = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [p1["id"], p2["id"]]},
        headers=admin_headers,
    )
    assert asignar.status_code == 200, asignar.text
    assert {p["id"] for p in asignar.json()["productos"]} == {p1["id"], p2["id"]}
    assert asignar.json()["total"] == 2

    # Idempotencia: mismo conjunto en otro orden no altera el resultado.
    repetido = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [p2["id"], p1["id"]]},
        headers=admin_headers,
    )
    assert repetido.status_code == 200
    assert repetido.json()["total"] == 2

    # Duplicados en el payload se deduplican.
    con_duplicados = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [p1["id"], p1["id"], p2["id"], p3["id"]]},
        headers=admin_headers,
    )
    assert con_duplicados.status_code == 200
    assert con_duplicados.json()["total"] == 3

    total_db = db_session.scalar(
        select(func.count())
        .select_from(ProductoColeccion)
        .where(ProductoColeccion.coleccion_id == cid)
    )
    assert total_db == 3

    # Reemplazo completo: quitar un producto.
    quitar = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [p1["id"]]},
        headers=admin_headers,
    )
    assert quitar.status_code == 200
    assert {p["id"] for p in quitar.json()["productos"]} == {p1["id"]}

    # Reemplazo a vacio.
    limpiar = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": []},
        headers=admin_headers,
    )
    assert limpiar.status_code == 200
    assert limpiar.json()["total"] == 0

    inexistente = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [999999999]},
        headers=admin_headers,
    )
    assert inexistente.status_code == 404

    producto_inactivo = client.put(
        f"/colecciones/{cid}/productos",
        json={"producto_ids": [inactivo["id"]]},
        headers=admin_headers,
    )
    assert producto_inactivo.status_code == 409

    coleccion_inexistente = client.put(
        "/colecciones/999999999/productos",
        json={"producto_ids": [p1["id"]]},
        headers=admin_headers,
    )
    assert coleccion_inexistente.status_code == 404

    listado_inexistente = client.get(
        "/colecciones/999999999/productos", headers=admin_headers
    )
    assert listado_inexistente.status_code == 404


def test_coleccion_deshabilitada_conserva_productos(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])

    client.put(
        f"/colecciones/{coleccion['id']}/productos",
        json={"producto_ids": [producto["id"]]},
        headers=admin_headers,
    )
    client.patch(
        f"/colecciones/{coleccion['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )

    productos = client.get(
        f"/colecciones/{coleccion['id']}/productos", headers=admin_headers
    )
    assert productos.status_code == 200
    assert productos.json()["total"] == 1


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_cu08(client, admin_headers, db_session, admin_usuario):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    temporada = _crear_temporada(client, admin_headers)
    coleccion = _crear_coleccion(client, admin_headers, temporada["id"])
    client.put(
        f"/colecciones/{coleccion['id']}/productos",
        json={"producto_ids": [producto["id"]]},
        headers=admin_headers,
    )

    entidades = {"temporada", "coleccion", "producto_coleccion"}
    eventos = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada.in_(entidades),
            Bitacora.usuario_id == admin_usuario.id,
        )
    ).all()
    entidades_registradas = {evento.entidad_afectada for evento in eventos}
    assert "temporada" in entidades_registradas
    assert "coleccion" in entidades_registradas
    assert "producto_coleccion" in entidades_registradas
