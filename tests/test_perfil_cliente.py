"""Smoke de perfil; el fixture revierte toda escritura al terminar."""

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.autenticacion_seguridad.models.models import Cliente, Rol, Usuario


def _crear_cliente(db_session):
    rol = db_session.scalar(select(Rol).where(Rol.nombre == "CLIENTE"))
    assert rol is not None
    sufijo = uuid.uuid4().hex
    usuario = Usuario(
        rol_id=rol.id,
        correo=f"perfil.{sufijo}@example.test",
        password_hash="hash-sin-usar",
        fecha_creacion=datetime.now(timezone.utc),
        estado=True,
    )
    db_session.add(usuario)
    db_session.flush()
    cliente = Cliente(
        usuario_id=usuario.id,
        nombre="Juan",
        apellido="Perez",
        telefono="70000000",
        sexo="MASCULINO",
        ci=f"CI{sufijo[:20]}",
        fecha_nacimiento=date(2002, 5, 10),
        estado=True,
    )
    db_session.add(cliente)
    db_session.flush()
    token = create_access_token({"sub": str(usuario.id), "contexto": "cliente"})
    return usuario, cliente, {"Authorization": f"Bearer {token}"}


def test_perfil_propio_patch_parcial_y_aislamiento(client, db_session, cajero_headers):
    usuario_a, cliente_a, headers_a = _crear_cliente(db_session)
    usuario_b, cliente_b, headers_b = _crear_cliente(db_session)
    ruta = "/auth/clientes/me/perfil"

    assert client.get(ruta).status_code == 401
    assert client.get(ruta, headers=cajero_headers).status_code == 403
    original = client.get(ruta, headers=headers_a)
    assert original.status_code == 200
    assert original.json()["cliente_id"] == cliente_a.id
    assert original.json()["usuario_id"] == usuario_a.id
    assert "password_hash" not in original.json()

    actualizado = client.patch(ruta, json={"telefono": " 71111111 "}, headers=headers_a)
    assert actualizado.status_code == 200, actualizado.text
    assert actualizado.json()["telefono"] == "71111111"
    for campo in ("cliente_id", "usuario_id", "correo", "ci", "rol", "estado"):
        assert actualizado.json()[campo] == original.json()[campo]
    assert db_session.get(Cliente, cliente_a.id).telefono == "71111111"
    assert db_session.get(Usuario, usuario_a.id).password_hash == "hash-sin-usar"

    otro = client.get(ruta, headers=headers_b)
    assert otro.status_code == 200
    assert otro.json()["cliente_id"] == cliente_b.id
    assert otro.json()["usuario_id"] == usuario_b.id
    assert otro.json()["telefono"] == "70000000"


def test_perfil_rechaza_campos_y_valores_invalidos(client, db_session, cajero_headers):
    _, _, headers = _crear_cliente(db_session)
    ruta = "/auth/clientes/me/perfil"
    casos = (
        {"correo": "otro@example.test"},
        {"ci": "999999"},
        {"password": "Otra#123"},
        {"cliente_id": 9},
        {"nombre": "   "},
        {"apellido": "x"},
        {"telefono": "  "},
        {"sexo": "INVALIDO"},
        {"fecha_nacimiento": (date.today() + timedelta(days=1)).isoformat()},
        {"telefono": None},
        {},
    )
    for datos in casos:
        respuesta = client.patch(ruta, json=datos, headers=headers)
        assert respuesta.status_code == 422, (datos, respuesta.text)
    assert client.patch(ruta, json={"telefono": "123"}, headers=cajero_headers).status_code == 403
