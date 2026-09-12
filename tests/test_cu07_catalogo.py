import uuid

from sqlalchemy import select

from app.modules.autenticacion_seguridad.models.models import Bitacora, Rol
from app.modules.catalogo.models.models import (
    Categoria,
    Producto,
    RecursoProducto,
    VarianteProducto,
)
from app.modules.roles.services.service import RolService


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _crear_categoria(client, headers, nombre=None):
    nombre = nombre or f"ZZCU07CAT_{_suf()}"
    resp = client.post(
        "/categorias",
        json={"nombre": nombre, "descripcion": "categoria temporal CU07"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="19.90"):
    nombre = nombre or f"ZZCU07PROD_{_suf()}"
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre,
            "descripcion": "producto temporal CU07",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    nombre = nombre or f"ZZCU07T_{_suf()}"
    resp = client.post("/tallas", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    nombre = nombre or f"ZZCU07C_{_suf()}"
    resp = client.post("/colores", json={"nombre": nombre}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    sku = sku or f"ZZCU07SKU_{_suf()}"
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={"talla_id": talla_id, "color_id": color_id, "sku": sku},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Regresion del catalogo publico
# ---------------------------------------------------------------------------


def test_regresion_catalogo_publico(client, db_session):
    resp = client.get("/productos")
    assert resp.status_code == 200
    productos = resp.json()
    assert isinstance(productos, list)
    assert len(productos) >= 1
    assert all(p["estado"] is True for p in productos)

    producto = db_session.scalar(
        select(Producto).where(Producto.estado.is_(True)).order_by(Producto.id)
    )
    assert producto is not None

    detalle = client.get(f"/productos/{producto.id}")
    assert detalle.status_code == 200
    body = detalle.json()
    assert body["id"] == producto.id
    assert "categoria" in body
    assert "categoria_id" in body

    disponibilidad = client.get(f"/productos/{producto.id}/disponibilidad")
    assert disponibilidad.status_code == 200
    assert disponibilidad.json()["producto_id"] == producto.id

    categorias = client.get("/categorias")
    assert categorias.status_code == 200
    assert isinstance(categorias.json(), list)

    categoria = db_session.scalar(
        select(Categoria).where(Categoria.estado.is_(True)).order_by(Categoria.id)
    )
    assert categoria is not None
    cat_detalle = client.get(f"/categorias/{categoria.id}")
    assert cat_detalle.status_code == 200
    assert cat_detalle.json()["id"] == categoria.id


# ---------------------------------------------------------------------------
# Seguridad granular
# ---------------------------------------------------------------------------


def test_seguridad_401_sin_token(client):
    assert client.get("/productos/admin").status_code == 401
    assert client.get("/tallas").status_code == 401
    assert client.post("/categorias", json={"nombre": "x"}).status_code == 401


def test_seguridad_403_sin_permiso(client, cajero_headers):
    assert client.get("/productos/admin", headers=cajero_headers).status_code == 403
    assert client.get("/tallas", headers=cajero_headers).status_code == 403
    assert (
        client.post(
            "/categorias", json={"nombre": f"ZZCU07CAT_{_suf()}"}, headers=cajero_headers
        ).status_code
        == 403
    )


def test_permisos_granulares_por_accion(client, admin_headers, db_session):
    rol_encargado = db_session.scalar(
        select(Rol).where(Rol.nombre == "ENCARGADO_SUCURSAL")
    )
    assert rol_encargado is not None
    assert RolService.tiene_permiso(
        db_session, rol_encargado.id, "GESTIONAR_PRODUCTOS", "EDITAR"
    )
    assert not RolService.tiene_permiso(
        db_session, rol_encargado.id, "GESTIONAR_PRODUCTOS", "ELIMINAR"
    )
    assert client.get("/productos/admin", headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------


def test_producto_crear_editar_estado(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    assert producto["categoria_id"] == categoria["id"]
    assert producto["estado"] is True

    nuevo_nombre = f"ZZCU07PROD_EDIT_{_suf()}"
    editar = client.patch(
        f"/productos/{producto['id']}",
        json={"nombre": nuevo_nombre, "precio": "25.50"},
        headers=admin_headers,
    )
    assert editar.status_code == 200, editar.text
    assert editar.json()["nombre"] == nuevo_nombre

    desactivar = client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200
    assert desactivar.json()["estado"] is False

    admin_detalle = client.get(
        f"/productos/admin/{producto['id']}", headers=admin_headers
    )
    assert admin_detalle.status_code == 200
    assert admin_detalle.json()["estado"] is False

    reactivar = client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": True},
        headers=admin_headers,
    )
    assert reactivar.status_code == 200
    assert reactivar.json()["estado"] is True


def test_producto_precio_invalido(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    for precio in ("0", "-5"):
        resp = client.post(
            "/productos",
            json={
                "categoria_id": categoria["id"],
                "nombre": f"ZZCU07PROD_{_suf()}",
                "precio": precio,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text


def test_producto_categoria_inexistente_e_inactiva(client, admin_headers):
    inexistente = client.post(
        "/productos",
        json={
            "categoria_id": 999999999,
            "nombre": f"ZZCU07PROD_{_suf()}",
            "precio": "10.00",
        },
        headers=admin_headers,
    )
    assert inexistente.status_code == 404

    categoria = _crear_categoria(client, admin_headers)
    desactivar = client.patch(
        f"/categorias/{categoria['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200

    inactiva = client.post(
        "/productos",
        json={
            "categoria_id": categoria["id"],
            "nombre": f"ZZCU07PROD_{_suf()}",
            "precio": "10.00",
        },
        headers=admin_headers,
    )
    assert inactiva.status_code == 409


def test_producto_filtros_admin(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(
        client, admin_headers, categoria["id"], nombre=f"ZZCU07FILTRO_{_suf()}"
    )

    por_busqueda = client.get(
        "/productos/admin",
        params={"buscar": "ZZCU07FILTRO"},
        headers=admin_headers,
    )
    assert por_busqueda.status_code == 200
    assert any(p["id"] == producto["id"] for p in por_busqueda.json())

    por_categoria = client.get(
        "/productos/admin",
        params={"categoria_id": categoria["id"]},
        headers=admin_headers,
    )
    assert por_categoria.status_code == 200
    assert all(p["categoria_id"] == categoria["id"] for p in por_categoria.json())

    client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    inactivos = client.get(
        "/productos/admin", params={"estado": False}, headers=admin_headers
    )
    assert inactivos.status_code == 200
    assert any(p["id"] == producto["id"] for p in inactivos.json())


# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------


def test_categoria_crud_duplicado_y_dependencia(client, admin_headers):
    nombre = f"ZZCU07CATCRUD_{_suf()}"
    creada = _crear_categoria(client, admin_headers, nombre=nombre)

    duplicada = client.post(
        "/categorias", json={"nombre": nombre}, headers=admin_headers
    )
    assert duplicada.status_code == 409

    actualizada = client.patch(
        f"/categorias/{creada['id']}",
        json={"descripcion": "actualizada CU07"},
        headers=admin_headers,
    )
    assert actualizada.status_code == 200
    assert actualizada.json()["descripcion"] == "actualizada CU07"

    producto = _crear_producto(client, admin_headers, creada["id"])
    en_uso = client.patch(
        f"/categorias/{creada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert en_uso.status_code == 409

    client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    libre = client.patch(
        f"/categorias/{creada['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert libre.status_code == 200
    assert libre.json()["estado"] is False


# ---------------------------------------------------------------------------
# Tallas y colores
# ---------------------------------------------------------------------------


def test_talla_crud_duplicado_y_dependencia(client, admin_headers):
    nombre = f"ZZCU07TCRUD_{_suf()}"
    talla = _crear_talla(client, admin_headers, nombre=nombre)

    duplicada = client.post(
        "/tallas", json={"nombre": nombre}, headers=admin_headers
    )
    assert duplicada.status_code == 409

    actualizada = client.patch(
        f"/tallas/{talla['id']}",
        json={"nombre": f"ZZCU07TEDIT_{_suf()}"},
        headers=admin_headers,
    )
    assert actualizada.status_code == 200

    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    color = _crear_color(client, admin_headers)
    _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )

    en_uso = client.patch(
        f"/tallas/{talla['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert en_uso.status_code == 409

    libre = _crear_talla(client, admin_headers)
    desactivar = client.patch(
        f"/tallas/{libre['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200


def test_color_crud_duplicado_y_dependencia(client, admin_headers):
    nombre = f"ZZCU07CCRUD_{_suf()}"
    color = _crear_color(client, admin_headers, nombre=nombre)

    duplicado = client.post(
        "/colores", json={"nombre": nombre}, headers=admin_headers
    )
    assert duplicado.status_code == 409

    actualizado = client.patch(
        f"/colores/{color['id']}",
        json={"nombre": f"ZZCU07CEDIT_{_suf()}"},
        headers=admin_headers,
    )
    assert actualizado.status_code == 200

    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )

    en_uso = client.patch(
        f"/colores/{color['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert en_uso.status_code == 409

    libre = _crear_color(client, admin_headers)
    desactivar = client.patch(
        f"/colores/{libre['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200


# ---------------------------------------------------------------------------
# Variantes
# ---------------------------------------------------------------------------


def test_variante_crud_y_reglas(client, admin_headers, db_session):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)

    sku = f"zzcu07_{_suf()}"
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"], sku=sku
    )
    assert variante["sku"] == sku.upper()

    combinacion = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla["id"],
            "color_id": color["id"],
            "sku": f"ZZCU07SKU_{_suf()}",
        },
        headers=admin_headers,
    )
    assert combinacion.status_code == 409

    sku_dup = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla["id"],
            "color_id": _crear_color(client, admin_headers)["id"],
            "sku": sku,
        },
        headers=admin_headers,
    )
    assert sku_dup.status_code == 409

    editar = client.patch(
        f"/variantes/{variante['id']}",
        json={"sku": f"ZZCU07SKUEDIT_{_suf()}"},
        headers=admin_headers,
    )
    assert editar.status_code == 200

    desactivar = client.patch(
        f"/variantes/{variante['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200
    reactivar = client.patch(
        f"/variantes/{variante['id']}/estado",
        json={"estado": True},
        headers=admin_headers,
    )
    assert reactivar.status_code == 200

    listado = client.get(
        f"/productos/{producto['id']}/variantes", headers=admin_headers
    )
    assert listado.status_code == 200
    assert any(v["id"] == variante["id"] for v in listado.json())


def test_variante_referencias_inactivas_y_producto_inactivo(
    client, admin_headers
):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    color = _crear_color(client, admin_headers)

    talla_inactiva = _crear_talla(client, admin_headers)
    client.patch(
        f"/tallas/{talla_inactiva['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    resp_talla = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla_inactiva["id"],
            "color_id": color["id"],
            "sku": f"ZZCU07SKU_{_suf()}",
        },
        headers=admin_headers,
    )
    assert resp_talla.status_code == 409

    color_inactivo = _crear_color(client, admin_headers)
    client.patch(
        f"/colores/{color_inactivo['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    talla = _crear_talla(client, admin_headers)
    resp_color = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla["id"],
            "color_id": color_inactivo["id"],
            "sku": f"ZZCU07SKU_{_suf()}",
        },
        headers=admin_headers,
    )
    assert resp_color.status_code == 409

    client.patch(
        f"/productos/{producto['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    resp_producto = client.post(
        f"/productos/{producto['id']}/variantes",
        json={
            "talla_id": talla["id"],
            "color_id": color["id"],
            "sku": f"ZZCU07SKU_{_suf()}",
        },
        headers=admin_headers,
    )
    assert resp_producto.status_code == 409


# ---------------------------------------------------------------------------
# Recursos de producto
# ---------------------------------------------------------------------------


def test_recurso_crud_y_principal(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])

    url_invalida = client.post(
        f"/productos/{producto['id']}/recursos",
        json={"tipo": "imagen", "url": "no-es-url"},
        headers=admin_headers,
    )
    assert url_invalida.status_code == 422

    primero = client.post(
        f"/productos/{producto['id']}/recursos",
        json={
            "tipo": "imagen",
            "url": "https://cdn.fashionstore.test/1.jpg",
            "es_principal": True,
        },
        headers=admin_headers,
    )
    assert primero.status_code == 201, primero.text
    primero = primero.json()
    assert primero["es_principal"] is True

    segundo = client.post(
        f"/productos/{producto['id']}/recursos",
        json={
            "tipo": "imagen",
            "url": "https://cdn.fashionstore.test/2.jpg",
            "es_principal": True,
        },
        headers=admin_headers,
    )
    assert segundo.status_code == 201, segundo.text
    segundo = segundo.json()

    listado = client.get(
        f"/productos/{producto['id']}/recursos", headers=admin_headers
    )
    assert listado.status_code == 200
    recursos = {r["id"]: r for r in listado.json()}
    assert recursos[segundo["id"]]["es_principal"] is True
    assert recursos[primero["id"]]["es_principal"] is False

    editar = client.patch(
        f"/recursos-producto/{primero['id']}",
        json={"tipo": "galeria", "url": "https://cdn.fashionstore.test/1b.jpg"},
        headers=admin_headers,
    )
    assert editar.status_code == 200
    assert editar.json()["tipo"] == "galeria"

    principal = client.patch(
        f"/recursos-producto/{primero['id']}/principal", headers=admin_headers
    )
    assert principal.status_code == 200
    assert principal.json()["es_principal"] is True

    desactivar = client.patch(
        f"/recursos-producto/{primero['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert desactivar.status_code == 200
    reactivar = client.patch(
        f"/recursos-producto/{primero['id']}/estado",
        json={"estado": True},
        headers=admin_headers,
    )
    assert reactivar.status_code == 200


def test_recurso_principal_por_color(client, admin_headers):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    color = _crear_color(client, admin_headers)

    primero = client.post(
        f"/productos/{producto['id']}/recursos",
        json={
            "tipo": "imagen",
            "url": "https://cdn.fashionstore.test/color1.jpg",
            "color_id": color["id"],
            "es_principal": True,
        },
        headers=admin_headers,
    )
    assert primero.status_code == 201, primero.text

    segundo = client.post(
        f"/productos/{producto['id']}/recursos",
        json={
            "tipo": "imagen",
            "url": "https://cdn.fashionstore.test/color2.jpg",
            "color_id": color["id"],
            "es_principal": True,
        },
        headers=admin_headers,
    )
    assert segundo.status_code == 201, segundo.text

    listado = client.get(
        f"/productos/{producto['id']}/recursos",
        params={"estado": True},
        headers=admin_headers,
    )
    principales = [
        r for r in listado.json() if r["color_id"] == color["id"] and r["es_principal"]
    ]
    assert len(principales) == 1


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_catalogo(client, admin_headers, db_session, admin_usuario):
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)

    eventos_categoria = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada == "categoria",
            Bitacora.usuario_id == admin_usuario.id,
            Bitacora.descripcion.ilike(f"%{categoria['nombre']}%"),
        )
    ).all()
    assert eventos_categoria, "No se registro auditoria de la categoria"

    eventos_producto = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada == "producto",
            Bitacora.usuario_id == admin_usuario.id,
            Bitacora.descripcion.ilike(f"%{producto['id']}%"),
        )
    ).all()
    assert eventos_producto, "El trigger de auditoria de producto no registro"

    eventos_talla = db_session.scalars(
        select(Bitacora).where(
            Bitacora.entidad_afectada == "talla",
            Bitacora.usuario_id == admin_usuario.id,
            Bitacora.descripcion.ilike(f"%{talla['nombre']}%"),
        )
    ).all()
    assert eventos_talla, "No se registro auditoria de la talla"
