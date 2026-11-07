"""Pruebas HTTP del parche de identidad (FastAPI real contra el clúster 5CA)."""
from __future__ import annotations

import os

from lab_common import jwt_hs256, claims_tenant


def _preparar_entorno(ctx, password: str) -> None:
    os.environ["DATABASE_URL"] = ctx.dsn["api"]
    os.environ["SECRET_KEY"] = ctx.secreto
    os.environ["SAAS_AUTH_ENABLED"] = "true"
    os.environ["SAAS_RESERVAS_ENABLED"] = "false"
    os.environ["FASHIONSTORE_BOOTSTRAP_DSN"] = ctx.dsn["bootstrap"]
    os.environ["FASHIONSTORE_PLATFORM_DSN"] = ctx.dsn["platform"]
    os.environ["FASHIONSTORE_LAB_PASSWORD"] = password


def pruebas_fastapi(ctx, ev, password: str) -> None:
    _preparar_entorno(ctx, password)
    from fastapi.testclient import TestClient

    from fastapi_lab.lab_app import app

    cliente = TestClient(app)
    estado: dict[str, str] = {}

    def login(correo: str, empresa: int, contexto: str) -> object:
        return cliente.post("/lab/login", json={
            "correo": correo, "password": os.environ["FASHIONSTORE_LAB_PASSWORD"],
            "empresa_id": empresa, "contexto": contexto})

    def f01():
        respuesta = login("a1.admin@lab.test", 1, "personal")
        exigir(respuesta.status_code == 200, respuesta.text)
        token = respuesta.json()["access_token"]
        estado["a1"] = token
        datos = cliente.get("/lab/me", headers={"Authorization": f"Bearer {token}"})
        exigir(datos.status_code == 200, datos.text)
        cuerpo = datos.json()
        exigir(cuerpo["empresa_id"] == 1 and cuerpo["usuario_id"] == 101,
               f"cuerpo={cuerpo}")
        exigir(cuerpo["rol"] == "ADMINISTRADOR", f"rol={cuerpo['rol']}")
        return f"login OK, /lab/me={cuerpo}"
    ev.espera("FAPI-01", "Login real (bootstrap+Argon2) y /lab/me con JWT", f01)

    def f02():
        respuesta = cliente.post("/lab/login", json={
            "correo": "a1.admin@lab.test", "password": "incorrecta",
            "empresa_id": 1, "contexto": "personal"})
        exigir(respuesta.status_code == 401, respuesta.text)
        return "401 con clave incorrecta"
    ev.espera("FAPI-02", "Login con contraseña incorrecta devuelve 401", f02)

    def f03():
        respuesta = login("a2.cliente@lab.test", 2, "cliente")
        exigir(respuesta.status_code == 403, respuesta.text)
        return "403 al pedir una empresa sin membresía"
    ev.espera("FAPI-03", "Login pidiendo empresa ajena devuelve 403", f03)

    def f04():
        respuesta = login("a2.cliente@lab.test", 1, "cliente")
        exigir(respuesta.status_code == 200, respuesta.text)
        estado["a2"] = respuesta.json()["access_token"]
        productos = cliente.get("/lab/productos",
                                headers={"Authorization": f"Bearer {estado['a2']}"})
        exigir(productos.status_code == 200, productos.text)
        exigir(productos.json()["total"] == 1, productos.text)
        return "el cliente A2 ve 1 producto (RLS por empresa)"
    ev.espera("FAPI-04", "La API HTTP respeta RLS sobre el pool compartido", f04)

    def f05():
        respuesta = cliente.get("/lab/productos")
        exigir(respuesta.status_code in (401, 403), respuesta.text)
        return f"{respuesta.status_code} sin token"
    ev.espera("FAPI-05", "Sin token, el endpoint empresarial no responde", f05)

    def f06():
        falso = jwt_hs256("clave-ajena", claims_tenant(101, 1, "personal"))
        respuesta = cliente.get("/lab/productos",
                                headers={"Authorization": f"Bearer {falso}"})
        exigir(respuesta.status_code == 401, respuesta.text)
        return "401 con token firmado por otra clave"
    ev.espera("FAPI-06", "Token con firma de otra clave devuelve 401", f06)

    def f07():
        admin = cliente.get("/lab/admin/clientes",
                            headers={"Authorization": f"Bearer {estado['a1']}"})
        exigir(admin.status_code == 200, admin.text)
        exigir(admin.json()["total"] == 2, admin.text)
        cliente_ = cliente.get("/lab/admin/clientes",
                               headers={"Authorization": f"Bearer {estado['a2']}"})
        exigir(cliente_.status_code == 403, cliente_.text)
        return "admin ve 2 clientes; el cliente recibe 403"
    ev.espera("FAPI-07", "Autorización por rol de empresa en HTTP", f07)

    def f08():
        respuesta = cliente.post("/lab/reservas?carrito_id=1",
                                 headers={"Authorization": f"Bearer {estado['a2']}"})
        exigir(respuesta.status_code == 200, respuesta.text)
        exigir(respuesta.json()["reserva_id"] > 0, respuesta.text)
        return f"reserva creada por HTTP: {respuesta.json()}"
    ev.espera("FAPI-08", "El SP tenant se ejecuta por HTTP con identidad firmada", f08)

    def f09():
        respuesta = login("a1.admin@lab.test", 2, "personal")
        exigir(respuesta.status_code == 403, respuesta.text)
        respuesta = login("nadie@lab.test", 1, "cliente")
        exigir(respuesta.status_code == 401, respuesta.text)
        return "empresa ajena 403 y correo inexistente 401"
    ev.espera("FAPI-09", "El bootstrap no filtra existencia de correos ni empresas", f09)

    from lab_tests import _reset_reservas
    _reset_reservas(ctx)


def exigir(condicion: bool, mensaje: str):
    if not condicion:
        raise AssertionError(mensaje)
    return mensaje
