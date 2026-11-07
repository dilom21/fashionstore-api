"""Pruebas HTTP del laboratorio 5CB: FastAPI real contra el clúster desechable.

Se usa la aplicación mínima de ``fastapi_lab/`` (mismos repositorios y pools
separados) para comprobar de punta a punta lo que las pruebas SQL verifican por
dentro: login con Argon2 sobre el pool bootstrap, JWT emitido por la app,
RLS por empresa sobre un pool compartido, autorización por rol y auditoría
escrita por el trigger (el repositorio real no duplica el evento).
"""
from __future__ import annotations

import os

from lab_pruebas import (
    _eventos, _limpiar_categoria, _total_eventos_categoria, entorno_app, exigir,
)


def _preparar(ctx) -> None:
    """Fija el entorno ANTES de importar app/fastapi_lab (leen env al importar)."""
    entorno_app(ctx)
    os.environ["SAAS_AUTH_ENABLED"] = "true"
    os.environ["FASHIONSTORE_BOOTSTRAP_DSN"] = ctx.dsn["bootstrap"]
    os.environ["FASHIONSTORE_PLATFORM_DSN"] = ctx.dsn["platform"]


def pruebas_http(ctx, ev, password: str) -> None:
    _preparar(ctx)
    from fastapi.testclient import TestClient

    from fastapi_lab.lab_app import app

    http = TestClient(app)
    estado: dict[str, str] = {}
    base_eventos = _total_eventos_categoria(ctx)

    def cabecera(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def login(correo: str, empresa: int, contexto: str,
              clave: str | None = None):
        return http.post("/lab/login", json={
            "correo": correo, "password": clave or password,
            "empresa_id": empresa, "contexto": contexto})

    def f01():
        respuesta = login("a1.admin@lab.test", 1, "personal")
        exigir(respuesta.status_code == 200, respuesta.text)
        estado["a1"] = respuesta.json()["access_token"]
        datos = http.get("/lab/me", headers=cabecera(estado["a1"]))
        exigir(datos.status_code == 200, datos.text)
        cuerpo = datos.json()
        exigir(cuerpo["empresa_id"] == 1 and cuerpo["usuario_id"] == 101,
               f"cuerpo={cuerpo}")
        exigir(cuerpo["rol"] == "ADMINISTRADOR", f"rol={cuerpo['rol']}")
        return f"login bootstrap+Argon2 OK, /lab/me={cuerpo}"
    ev.espera("FAPI-01", "Login real (bootstrap + Argon2) y /lab/me con JWT", f01)

    def f02():
        respuesta = login("a1.admin@lab.test", 1, "personal", "incorrecta")
        exigir(respuesta.status_code == 401, respuesta.text)
        return "401 con contraseña incorrecta"
    ev.espera("FAPI-02", "Login con contraseña incorrecta devuelve 401", f02)

    def f03():
        ajena = login("a2.cliente@lab.test", 2, "cliente")
        exigir(ajena.status_code == 403, ajena.text)
        inexistente = login("nadie@lab.test", 1, "cliente")
        exigir(inexistente.status_code == 401, inexistente.text)
        return "empresa ajena 403 y correo inexistente 401"
    ev.espera("FAPI-03", "El bootstrap no filtra existencia de correos ni empresas", f03)

    def f04():
        respuesta = login("a2.cliente@lab.test", 1, "cliente")
        exigir(respuesta.status_code == 200, respuesta.text)
        estado["a2"] = respuesta.json()["access_token"]
        productos = http.get("/lab/productos", headers=cabecera(estado["a2"]))
        exigir(productos.status_code == 200, productos.text)
        exigir(productos.json()["total"] == 1, productos.text)
        return "el cliente A2 ve 1 producto (RLS por empresa sobre el pool api)"
    ev.espera("FAPI-04", "La API HTTP respeta RLS sobre el pool compartido", f04)

    def f05():
        sin_token = http.get("/lab/productos")
        exigir(sin_token.status_code in (401, 403), sin_token.text)
        return f"{sin_token.status_code} sin cabecera Authorization"
    ev.espera("FAPI-05", "Sin token, el endpoint empresarial no responde", f05)

    def f06():
        from lab_comun import claims_tenant, jwt_hs256

        falso = jwt_hs256("clave-ajena", claims_tenant(101, 1, "personal"))
        respuesta = http.get("/lab/productos", headers=cabecera(falso))
        exigir(respuesta.status_code == 401, respuesta.text)
        return "401 con token firmado por otra clave"
    ev.espera("FAPI-06", "Token con firma de otra clave devuelve 401", f06)

    def f07():
        admin = http.get("/lab/admin/clientes", headers=cabecera(estado["a1"]))
        exigir(admin.status_code == 200, admin.text)
        exigir(admin.json()["total"] == 2, admin.text)
        cliente_ = http.get("/lab/admin/clientes", headers=cabecera(estado["a2"]))
        exigir(cliente_.status_code == 403, cliente_.text)
        return "el administrador ve 2 clientes; el cliente recibe 403"
    ev.espera("FAPI-07", "Autorización por rol de empresa en HTTP", f07)

    def f08():
        respuesta = http.post("/lab/categorias",
                              headers=cabecera(estado["a1"]),
                              json={"nombre": "Cat HTTP 5CB"})
        exigir(respuesta.status_code == 200, respuesta.text)
        cid = int(respuesta.json()["categoria_id"])
        estado["cid"] = str(cid)
        eventos = _eventos(ctx, cid)
        exigir(eventos == [(1, 101, "CREAR")],
               f"la escritura por HTTP dejó {eventos}")
        return f"categoría {cid} creada por HTTP con un único evento CREAR"
    ev.espera("FAPI-08",
              "Crear por HTTP deja EXACTAMENTE un evento (el trigger, no el "
              "repositorio)", f08)

    def f09():
        cid = int(estado["cid"])
        respuesta = http.patch(f"/lab/categorias/{cid}",
                              headers=cabecera(estado["a1"]),
                              json={"nombre": "Cat HTTP 5CB v2"})
        exigir(respuesta.status_code == 200, respuesta.text)
        eventos = _eventos(ctx, cid)
        exigir(eventos == [(1, 101, "CREAR"), (1, 101, "MODIFICAR")],
               f"eventos={eventos}")
        return "CREAR + MODIFICAR por HTTP: un evento por escritura"
    ev.espera("FAPI-09", "Actualizar por HTTP añade un MODIFICAR sin duplicar", f09)

    def f10():
        admin = http.get("/lab/bitacora", headers=cabecera(estado["a1"]))
        exigir(admin.status_code == 200, admin.text)
        exigir(admin.json()["total"] >= 3, admin.text)
        cliente_ = http.get("/lab/bitacora", headers=cabecera(estado["a2"]))
        exigir(cliente_.status_code == 403, cliente_.text)
        return f"admin ve {admin.json()['total']} eventos; el cliente recibe 403"
    ev.espera("FAPI-10", "La bitácora es sólo para el administrador del tenant", f10)

    def f11():
        respuesta = login("b1.cliente@lab.test", 2, "cliente")
        exigir(respuesta.status_code == 200, respuesta.text)
        estado["b1"] = respuesta.json()["access_token"]
        productos = http.get("/lab/productos", headers=cabecera(estado["b1"]))
        exigir(productos.status_code == 200, productos.text)
        exigir(productos.json()["total"] == 1, productos.text)
        catalogo = http.get("/lab/categorias", headers=cabecera(estado["b1"]))
        exigir(catalogo.status_code == 200, catalogo.text)
        exigir(catalogo.json()["empresa_id"] == 2, catalogo.text)
        exigir(all(c["id"] > 0 for c in catalogo.json()["categorias"]),
               catalogo.text)
        exigir(catalogo.json()["total"] == 1, catalogo.text)
        return "B1 ve su catálogo (1 categoría) y nunca el de la empresa A"
    ev.espera("FAPI-11", "Aislamiento entre empresas sobre HTTP", f11)

    def f12():
        # Ninguna petición debe heredar la identidad de la anterior: el token se
        # publica como GUC local y la conexión vuelve al pool limpia.
        anonima = http.get("/lab/productos")
        exigir(anonima.status_code in (401, 403), anonima.text)
        otra = http.get("/lab/productos", headers=cabecera(estado["a2"]))
        exigir(otra.status_code == 200, otra.text)
        exigir(otra.json()["total"] == 1, otra.text)
        b = http.get("/lab/productos", headers=cabecera(estado["b1"]))
        exigir(b.status_code == 200 and b.json()["total"] == 1, b.text)
        return "A2 y B1 no comparten identidad ni filas entre peticiones"
    ev.espera("FAPI-12", "El pool HTTP no filtra la identidad entre peticiones", f12)

    def f13():
        _limpiar_categoria(ctx, int(estado["cid"]))
        final = _total_eventos_categoria(ctx)
        exigir(final == base_eventos,
               f"la bitácora quedó con {final} eventos y la línea base era "
               f"{base_eventos}")
        return f"bitácora restaurada a {final} eventos de categoría"
    ev.espera("FAPI-13", "Las pruebas HTTP no dejan residuo en la base", f13)