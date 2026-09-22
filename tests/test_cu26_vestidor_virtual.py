"""Pruebas de CU26 - Vestidor Virtual (AR) - parte backend de Josias.

No dependen de camara ni MediaPipe: el motor AR es local en el telefono.
Todas las escrituras ocurren dentro de la transaccion revertida del fixture
``db_session``, por lo que no quedan filas temporales en Supabase.

Las tablas AR YA EXISTEN y estan vacias; aqui se insertan configuraciones de
prueba directamente con SQLAlchemy (no hay endpoint administrativo de AR).
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Rol,
    Usuario,
)
from app.modules.catalogo.models.models import RecursoProducto
from app.modules.vestidor_virtual.models.models import (
    ConfiguracionVestidorAR,
    PruebaVestidorAR,
    SesionVestidorAR,
)

_UTC = timezone.utc

CONFIG_URL = "/vestidor-virtual/productos/{producto_id}/configuraciones"
SESIONES_URL = "/vestidor-virtual/sesiones"
PRUEBAS_URL = "/vestidor-virtual/sesiones/{sesion_id}/pruebas"
PRUEBA_FINALIZAR_URL = "/vestidor-virtual/pruebas/{prueba_id}/finalizar"
SESION_FINALIZAR_URL = "/vestidor-virtual/sesiones/{sesion_id}/finalizar"


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Helpers de datos
# ---------------------------------------------------------------------------


def _crear_categoria(client, headers, nombre=None):
    resp = client.post(
        "/categorias",
        json={"nombre": nombre or f"ZZCU26CAT_{_suf()}", "descripcion": "cat CU26"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_producto(client, headers, categoria_id, nombre=None, precio="180.00"):
    resp = client.post(
        "/productos",
        json={
            "categoria_id": categoria_id,
            "nombre": nombre or f"ZZCU26PROD_{_suf()}",
            "descripcion": "producto CU26",
            "precio": precio,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_talla(client, headers, nombre=None):
    resp = client.post(
        "/tallas", json={"nombre": nombre or f"ZZCU26T_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_color(client, headers, nombre=None):
    resp = client.post(
        "/colores", json={"nombre": nombre or f"ZZCU26C_{_suf()}"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_variante(client, headers, producto_id, talla_id, color_id, sku=None):
    resp = client.post(
        f"/productos/{producto_id}/variantes",
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku": sku or f"ZZCU26SKU_{_suf()}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_recurso(
    client, headers, producto_id, *, url, tipo="AR_PNG", color_id=None
):
    payload = {"tipo": tipo, "url": url, "es_principal": False}
    if color_id is not None:
        payload["color_id"] = color_id
    resp = client.post(
        f"/productos/{producto_id}/recursos", json=payload, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _crear_configuracion(
    db_session,
    *,
    recurso_id,
    estado=True,
    tipo_asset="PNG_2D",
    zona_cuerpo="TORSO",
    orden_capa=1,
) -> ConfiguracionVestidorAR:
    configuracion = ConfiguracionVestidorAR(
        recurso_producto_id=recurso_id,
        zona_cuerpo=zona_cuerpo,
        tipo_asset=tipo_asset,
        factor_ancho=Decimal("1.45"),
        factor_alto=Decimal("1.60"),
        offset_x=Decimal("0.00"),
        offset_y=Decimal("0.05"),
        rotacion_offset=Decimal("0.00"),
        orden_capa=orden_capa,
        opacidad=Decimal("1.00"),
        estado=estado,
    )
    db_session.add(configuracion)
    db_session.flush()
    return configuracion


def _crear_cliente(db_session, etiqueta="a"):
    """Crea un CLIENTE (usuario + perfil) y devuelve (cliente, headers)."""
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None, "No existe el rol CLIENTE"
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"zzcu26.{etiqueta}.{_suf()}@fashionstore.test",
        password_hash="temporal",
        fecha_creacion=datetime.now(_UTC),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    cliente = Cliente(
        usuario_id=usuario.id,
        nombre="Cliente",
        apellido="Vestidor",
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


def _contexto(client, admin_headers, db_session, *, con_config=True, con_recurso=True):
    """Producto + variante + (recurso AR) + (configuracion AR)."""
    categoria = _crear_categoria(client, admin_headers)
    producto = _crear_producto(client, admin_headers, categoria["id"])
    talla = _crear_talla(client, admin_headers)
    color = _crear_color(client, admin_headers)
    variante = _crear_variante(
        client, admin_headers, producto["id"], talla["id"], color["id"]
    )

    recurso = None
    configuracion = None
    if con_recurso:
        recurso = _crear_recurso(
            client,
            admin_headers,
            producto["id"],
            url=f"https://cdn.test/vestidor/{_suf()}.png",
            color_id=color["id"],
        )
    if con_config and recurso is not None:
        configuracion = _crear_configuracion(
            db_session, recurso_id=recurso["id"]
        )

    return {
        "categoria": categoria,
        "producto": producto,
        "talla": talla,
        "color": color,
        "variante": variante,
        "recurso": recurso,
        "configuracion": configuracion,
    }


def _crear_sesion(client, headers):
    return client.post(SESIONES_URL, headers=headers)


# ---------------------------------------------------------------------------
# 1. Autenticacion / contrato de entrada
# ---------------------------------------------------------------------------


def test_auth_requiere_cliente(client, admin_headers, cajero_headers, db_session):
    ctx = _contexto(client, admin_headers, db_session)

    # Sin token -> 401.
    assert client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"])
    ).status_code == 401
    assert client.post(SESIONES_URL).status_code == 401

    # Personal (contexto != cliente) -> 403.
    assert (
        client.get(
            CONFIG_URL.format(producto_id=ctx["producto"]["id"]),
            headers=admin_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(SESIONES_URL, headers=cajero_headers).status_code == 403
    )


def test_no_acepta_cliente_id_ni_estado_del_cliente(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    # POST /sesiones rechaza cualquier campo no contratado.
    resp = client.post(SESIONES_URL, json={"cliente_id": 1}, headers=headers)
    assert resp.status_code == 422, resp.text

    # Iniciar prueba tambien rechaza cliente_id / estado / fechas.
    sesion = _crear_sesion(client, headers).json()
    for campo in ("cliente_id", "estado", "fecha_inicio"):
        resp = client.post(
            PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
            json={
                "configuracion_id": ctx["configuracion"].id,
                campo: 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422, (campo, resp.text)


# ---------------------------------------------------------------------------
# 2. Configuraciones: producto inexistente / sin config
# ---------------------------------------------------------------------------


def test_producto_inexistente_404(client, db_session):
    _, headers = _crear_cliente(db_session)
    resp = client.get(
        CONFIG_URL.format(producto_id=999999999), headers=headers
    )
    assert resp.status_code == 404, resp.text


def test_producto_sin_configuracion_compatible_false(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, con_config=False)

    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]), headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["producto_id"] == ctx["producto"]["id"]
    assert body["compatible"] is False
    assert body["configuraciones"] == []


# ---------------------------------------------------------------------------
# 3. Configuracion activa PNG_2D / filtros
# ---------------------------------------------------------------------------


def test_configuracion_activa_se_devuelve(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    config = ctx["configuracion"]

    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]), headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["compatible"] is True
    assert len(body["configuraciones"]) == 1
    item = body["configuraciones"][0]
    assert item["configuracion_id"] == config.id
    assert item["recurso_producto_id"] == ctx["recurso"]["id"]
    assert item["asset_url"] == ctx["recurso"]["url"]
    assert item["zona_cuerpo"] == "TORSO"
    assert item["tipo_asset"] == "PNG_2D"
    assert item["orden_capa"] == 1
    assert float(item["factor_ancho"]) == 1.45
    assert float(item["factor_alto"]) == 1.60
    assert float(item["opacidad"]) == 1.00
    assert item["color_id"] == ctx["color"]["id"]
    assert item["color"] == ctx["color"]["nombre"]


def test_filtro_por_variante_y_color(client, admin_headers, db_session):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)

    # Variante compatible: devuelve la configuracion.
    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]),
        params={"variante_id": ctx["variante"]["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is True

    # Color sin configuracion -> compatible false.
    otro_color = _crear_color(client, admin_headers)
    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]),
        params={"color_id": otro_color["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is False

    # Variante de otro producto -> compatible false (sin error).
    otro = _contexto(client, admin_headers, db_session)
    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]),
        params={"variante_id": otro["variante"]["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is False


# ---------------------------------------------------------------------------
# 4. Configuracion inactiva / recurso inactivo no se devuelven
# ---------------------------------------------------------------------------


def test_configuracion_inactiva_no_se_devuelve(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, con_config=False)
    _crear_configuracion(
        db_session, recurso_id=ctx["recurso"]["id"], estado=False
    )

    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]), headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is False
    assert resp.json()["configuraciones"] == []


def test_recurso_inactivo_no_se_devuelve(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    # La configuracion sigue activa, pero su recurso se deshabilita.
    resp = client.patch(
        f"/recursos-producto/{ctx['recurso']['id']}/estado",
        json={"estado": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]), headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is False
    assert resp.json()["configuraciones"] == []


def test_tipo_asset_distinto_de_png_2d_no_se_devuelve(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, con_config=False)
    # GLB_3D no aplica a este parcial (solo PNG_2D / TORSO).
    _crear_configuracion(
        db_session, recurso_id=ctx["recurso"]["id"], tipo_asset="GLB_3D"
    )
    resp = client.get(
        CONFIG_URL.format(producto_id=ctx["producto"]["id"]), headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compatible"] is False


# ---------------------------------------------------------------------------
# 5. Sesiones
# ---------------------------------------------------------------------------


def test_crear_sesion_toma_cliente_del_jwt(client, db_session):
    cliente, headers = _crear_cliente(db_session)
    resp = _crear_sesion(client, headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cliente_id"] == cliente.id
    assert body["estado"] == "ACTIVA"
    assert body["fecha_inicio"] is not None
    assert body["fecha_fin"] is None

    sesion = db_session.get(SesionVestidorAR, body["sesion_id"])
    assert sesion is not None
    assert sesion.cliente_id == cliente.id


def test_crear_sesion_es_idempotente(client, db_session):
    _, headers = _crear_cliente(db_session)
    primera = _crear_sesion(client, headers)
    segunda = _crear_sesion(client, headers)
    assert primera.status_code == 201, primera.text
    assert segunda.status_code == 201, segunda.text
    assert primera.json()["sesion_id"] == segunda.json()["sesion_id"]

    activas = db_session.scalars(
        select(SesionVestidorAR).where(
            SesionVestidorAR.cliente_id == primera.json()["cliente_id"],
            SesionVestidorAR.estado == "ACTIVA",
        )
    ).all()
    assert len(activas) == 1


def test_sesion_ajena_prohibida(client, admin_headers, db_session):
    cliente_a, headers_a = _crear_cliente(db_session, "a")
    _, headers_b = _crear_cliente(db_session, "b")
    ctx = _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers_a).json()

    # B intenta iniciar prueba y finalizar la sesion de A.
    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": ctx["configuracion"].id},
        headers=headers_b,
    )
    assert resp.status_code == 403, resp.text

    resp = client.patch(
        SESION_FINALIZAR_URL.format(sesion_id=sesion["sesion_id"]),
        json={"estado": "FINALIZADA"},
        headers=headers_b,
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 6. Pruebas
# ---------------------------------------------------------------------------


def test_iniciar_prueba_valida_configuracion(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers).json()

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={
            "configuracion_id": ctx["configuracion"].id,
            "variante_producto_id": ctx["variante"]["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["estado"] == "INICIADA"
    assert body["sesion_vestidor_ar_id"] == sesion["sesion_id"]
    assert body["configuracion_id"] == ctx["configuracion"].id
    assert body["variante_producto_id"] == ctx["variante"]["id"]
    assert body["fecha_fin"] is None


def test_iniciar_prueba_sin_variante_permitido(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers).json()

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": ctx["configuracion"].id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["variante_producto_id"] is None


def test_iniciar_prueba_configuracion_inexistente_404(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers).json()

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": 999999999},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text


def test_iniciar_prueba_configuracion_inactiva_409(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session, con_config=False)
    config = _crear_configuracion(
        db_session, recurso_id=ctx["recurso"]["id"], estado=False
    )
    sesion = _crear_sesion(client, headers).json()

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": config.id},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text


def test_iniciar_prueba_variante_incompatible_409(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    otro = _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers).json()

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={
            "configuracion_id": ctx["configuracion"].id,
            "variante_producto_id": otro["variante"]["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 409, resp.text


def test_iniciar_prueba_sesion_finalizada_409(
    client, admin_headers, db_session
):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    sesion = _crear_sesion(client, headers).json()
    client.patch(
        SESION_FINALIZAR_URL.format(sesion_id=sesion["sesion_id"]),
        json={"estado": "FINALIZADA"},
        headers=headers,
    )

    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": ctx["configuracion"].id},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# 7. Finalizar prueba / sesion e idempotencia
# ---------------------------------------------------------------------------


def _iniciar_prueba(client, headers, ctx):
    sesion = _crear_sesion(client, headers).json()
    resp = client.post(
        PRUEBAS_URL.format(sesion_id=sesion["sesion_id"]),
        json={"configuracion_id": ctx["configuracion"].id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return sesion, resp.json()


def test_finalizar_prueba_y_idempotencia(client, admin_headers, db_session):
    _, headers = _crear_cliente(db_session)
    ctx = _contexto(client, admin_headers, db_session)
    _, prueba = _iniciar_prueba(client, headers, ctx)
    url = PRUEBA_FINALIZAR_URL.format(prueba_id=prueba["prueba_id"])

    resp = client.patch(url, json={"estado": "COMPLETADA"}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "COMPLETADA"
    assert body["fecha_fin"] is not None

    # Mismo estado final -> idempotente.
    repetido = client.patch(url, json={"estado": "COMPLETADA"}, headers=headers)
    assert repetido.status_code == 200, repetido.text
    assert repetido.json()["estado"] == "COMPLETADA"

    # Estado final distinto -> conflicto.
    conflicto = client.patch(url, json={"estado": "CANCELADA"}, headers=headers)
    assert conflicto.status_code == 409, conflicto.text


def test_prueba_ajena_prohibida(client, admin_headers, db_session):
    _, headers_a = _crear_cliente(db_session, "a")
    _, headers_b = _crear_cliente(db_session, "b")
    ctx = _contexto(client, admin_headers, db_session)
    _, prueba = _iniciar_prueba(client, headers_a, ctx)

    resp = client.patch(
        PRUEBA_FINALIZAR_URL.format(prueba_id=prueba["prueba_id"]),
        json={"estado": "COMPLETADA"},
        headers=headers_b,
    )
    assert resp.status_code == 403, resp.text


def test_prueba_inexistente_404(client, db_session):
    _, headers = _crear_cliente(db_session)
    resp = client.patch(
        PRUEBA_FINALIZAR_URL.format(prueba_id=999999999),
        json={"estado": "COMPLETADA"},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text


def test_finalizar_sesion_y_idempotencia(client, db_session):
    _, headers = _crear_cliente(db_session)
    sesion = _crear_sesion(client, headers).json()
    url = SESION_FINALIZAR_URL.format(sesion_id=sesion["sesion_id"])

    resp = client.patch(url, json={"estado": "FINALIZADA"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "FINALIZADA"
    assert resp.json()["fecha_fin"] is not None

    repetido = client.patch(url, json={"estado": "FINALIZADA"}, headers=headers)
    assert repetido.status_code == 200, repetido.text

    conflicto = client.patch(url, json={"estado": "CANCELADA"}, headers=headers)
    assert conflicto.status_code == 409, conflicto.text


def test_estados_invalidos_422(client, db_session):
    _, headers = _crear_cliente(db_session)
    sesion = _crear_sesion(client, headers).json()

    # Estado no final en finalizar sesion.
    resp = client.patch(
        SESION_FINALIZAR_URL.format(sesion_id=sesion["sesion_id"]),
        json={"estado": "ACTIVA"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # Estado no permitido en finalizar prueba.
    resp = client.patch(
        PRUEBA_FINALIZAR_URL.format(prueba_id=1),
        json={"estado": "INICIADA"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 8. Regresion CU09 / Asistencia Inteligente
# ---------------------------------------------------------------------------


def test_regresion_cu09_y_asistencia(client, db_session):
    # CU09 sigue publico y operativo.
    assert client.get("/productos").status_code == 200
    assert client.get("/catalogo/filtros").status_code == 200

    # Asistencia Inteligente sigue requiriendo CLIENTE (no se rompio).
    assert (
        client.post(
            "/asistencia-inteligente/recomendaciones",
            json={"consulta": "look casual"},
        ).status_code
        == 401
    )
