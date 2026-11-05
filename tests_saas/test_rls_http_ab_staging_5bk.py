"""5BK / Etapa C - Integracion HTTP A/B REAL contra PostgreSQL desechable.

Requisitos
----------
El laboratorio se aprovisiona con::

    python database/saas/74_laboratorio_rls_principal_5bk.py --init --confirm-desechable

y deja un manifiesto en ``%LOCALAPPDATA%\\Temp\\opencode\\fs_lab_5bk\\``. Si el
manifiesto no existe, la bateria se OMITE (no se simula nada con SQLite).

Lo que prueba este archivo
--------------------------
* La app FastAPI se ejecuta en un proceso uvicorn real, con
  ``SAAS_AUTH_ENABLED=true`` y ``DATABASE_URL`` apuntando al rol SQL restringido
  ``fs_saas_runtime`` (no superusuario, no BYPASSRLS, no propietario).
* Dos empresas ficticias A y B tienen usuarios, sucursales, catalogo e
  inventario propios. Todo el trafico es HTTP por TCP, sin mocks ni overrides
  de dependencias.
* Ademas de la respuesta HTTP se comprueba el efecto SQL real: RLS con asercion
  firmada, rechazo de ``set_config`` malicioso, limite de pool y revalidacion de
  membresia en base.

Evidencia: al terminar se escriben dos archivos nuevos en
``database/saas/evidencias/`` con nombre unico y sin secretos.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
RUTA_MANIFIESTO_POR_DEFECTO = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    / "Temp" / "opencode" / "fs_lab_5bk" / "lab_5bk_manifest.json"
)
MANIFIESTO = Path(
    os.environ.get("FASHIONSTORE_LAB_5BK_MANIFEST", str(RUTA_MANIFIESTO_POR_DEFECTO))
)

MARCA_TIEMPO = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class Registro:
    """Bitacora sanitizada: sin DSN, sin JWT, sin contrasenas."""

    def __init__(self) -> None:
        self.http: list[str] = []
        self.sql: list[str] = []

    def ok_http(self, nombre: str, detalle: str) -> None:
        self.http.append(f"[PASS] {nombre}: {detalle}")

    def ok_sql(self, nombre: str, detalle: str) -> None:
        self.sql.append(f"[PASS] {nombre}: {detalle}")

    def _escribir(self, sufijo: str, titulo: str, filas: list[str], lab: dict) -> Path:
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        ruta = EVIDENCIAS / f"{sufijo}_{MARCA_TIEMPO}.txt"
        base = ruta
        n = 2
        while ruta.exists():
            ruta = base.with_name(f"{sufijo}_{MARCA_TIEMPO}_v{n:02d}.txt")
            n += 1
        encabezado = [
            titulo,
            f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Cluster: {lab['cluster']['version']} en "
            f"{lab['cluster']['host']}:{lab['cluster']['puerto']} / {lab['cluster']['base']}",
            "Principal SQL: " + lab["principal"]["session_user"]
            + f" (rolsuper={lab['principal']['rolsuper']}, "
              f"rolbypassrls={lab['principal']['rolbypassrls']}, "
              f"tablas_propias={lab['principal']['tablas_propias']})",
            "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
            "-" * 78,
        ]
        ruta.write_text("\n".join(encabezado + filas) + "\n", encoding="utf-8")
        return ruta

    def escribir(self, lab: dict) -> tuple[Path, Path]:
        http = self._escribir("EVIDENCIA_HTTP_AB_5BK", "EVIDENCIA HTTP A/B - ETAPA C 5BK",
                              self.http, lab)
        sql = self._escribir("EVIDENCIA_RLS_5BK", "EVIDENCIA RLS / PRINCIPAL SQL - ETAPA C 5BK",
                             self.sql, lab)
        return http, sql


REGISTRO = Registro()


def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _dsn_sqlalchemy(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _asercion(clave: str, empresa_id: int, usuario_id: int, *, ttl: int = 120,
              firma_forzada: str | None = None) -> str:
    expira = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = firma_forzada or hmac.new(
        clave.encode("utf-8"), mensaje.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{mensaje}|{firma}"


def _sql_escalar(lab: dict, sql: str, asercion: str | None = None) -> object:
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        if asercion is not None:
            conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion,))
        return conn.execute(sql).fetchone()[0]


def _sql_filas(lab: dict, sql: str, asercion: str | None = None) -> list[tuple]:
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        if asercion is not None:
            conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion,))
        return conn.execute(sql).fetchall()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BK no aprovisionado; ejecute "
            "database/saas/74_laboratorio_rls_principal_5bk.py --init --confirm-desechable"
        )
    # Repetibilidad: el laboratorio es desechable, asi que se recrea la base
    # (esquema + semilla + roles/politicas + secreto nuevo) antes de probar.
    # FASHIONSTORE_LAB_5BK_RESET=0 permite reutilizar el estado existente.
    if os.environ.get("FASHIONSTORE_LAB_5BK_RESET", "1") != "0":
        script = ROOT / "database" / "saas" / "74_laboratorio_rls_principal_5bk.py"
        resultado = subprocess.run(
            [sys.executable, str(script), "--recrear-base", "--confirm-desechable"],
            cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
        if resultado.returncode != 0:
            pytest.fail(
                "No se pudo recrear el laboratorio 5BK (rc="
                f"{resultado.returncode}): {resultado.stderr[-800:]}"
            )
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    cluster = datos["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"} or cluster["puerto"] != 55441:
        pytest.skip("El manifiesto no corresponde al laboratorio desechable 5BK")
    if "staging" not in cluster["base"].lower():
        pytest.skip("El manifiesto no apunta a una base descartable")
    datos["_clave"] = datos["assertion_key"]
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    secreto_jwt = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "DATABASE_URL": _dsn_sqlalchemy(lab["runtime_dsn"]),
        "SECRET_KEY": secreto_jwt,
        "SAAS_AUTH_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "false",
        "SAAS_RESERVAS_ENABLED": "false",
        "SAAS_TENANT_ASSERTION_KEY": lab["_clave"],
        "APP_ENV": "lab-5bk",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bk_{puerto}.log"
    registro.parent.mkdir(parents=True, exist_ok=True)
    salida = open(registro, "w", encoding="utf-8")
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=salida, stderr=salida,
    )
    base = f"http://127.0.0.1:{puerto}"
    limite = time.time() + 60
    listo = False
    while time.time() < limite:
        if proceso.poll() is not None:
            break
        try:
            if httpx.get(f"{base}/health", timeout=2.0).status_code == 200:
                listo = True
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    if not listo:
        proceso.terminate()
        salida.close()
        contenido = registro.read_text(encoding="utf-8", errors="replace")[-2000:]
        pytest.fail(f"uvicorn no arranco con el principal restringido: {contenido}")
    yield {"base": base, "secreto_jwt": secreto_jwt, "proceso": proceso}
    proceso.terminate()
    try:
        proceso.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proceso.kill()
    salida.close()


@pytest.fixture(scope="session", autouse=True)
def escribir_evidencia(request):
    yield
    if MANIFIESTO.exists() and (REGISTRO.http or REGISTRO.sql):
        lab = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        rutas = REGISTRO.escribir(lab)
        print("\nEvidencias escritas: " + " | ".join(str(r) for r in rutas))


@pytest.fixture()
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=20.0) as cliente:
        yield cliente


def _login(api: httpx.Client, correo: str, password: str, slug: str,
           contexto: str = "personal") -> httpx.Response:
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def _token(api: httpx.Client, lab: dict, cual: str, contexto: str = "personal") -> str:
    correo = lab["correos"][cual]
    slug = "empresa-a" if cual.startswith(("a_",)) else "empresa-b"
    respuesta = _login(api, correo, lab["password_usuarios"], slug, contexto)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Autenticacion y contexto
# ---------------------------------------------------------------------------


def test_login_admin_a_y_me(api, lab):
    respuesta = _login(api, lab["correos"]["a_admin"], lab["password_usuarios"], "empresa-a")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["empresa_id"] == lab["empresas"]["empresa_a"]
    assert cuerpo["rol"] == "ADMINISTRADOR"
    me = api.get("/auth/tenant/me", headers=_cabecera(cuerpo["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["empresa_slug"] == "empresa-a"
    assert me.json()["plan_codigo"] == "PROFESIONAL"
    REGISTRO.ok_http("login_admin_A_me", "200 / 200, empresa A, rol ADMINISTRADOR")


def test_login_admin_b_y_me_otra_empresa(api, lab):
    respuesta = _login(api, lab["correos"]["b_admin"], lab["password_usuarios"], "empresa-b")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["empresa_id"] == lab["empresas"]["empresa_b"]
    me = api.get("/auth/tenant/me", headers=_cabecera(cuerpo["access_token"]))
    assert me.status_code == 200
    assert me.json()["plan_codigo"] == "BASICO"
    REGISTRO.ok_http("login_admin_B_me", "200 / 200, empresa B, plan BASICO")


def test_login_cliente_tiene_contexto_cliente(api, lab):
    respuesta = _login(api, lab["correos"]["a_cliente"], lab["password_usuarios"],
                       "empresa-a", "cliente")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["contexto"] == "cliente"
    REGISTRO.ok_http("login_cliente_A", "200, contexto cliente")


@pytest.mark.parametrize("clave,detalle", [
    ("a_deshabilitado", "usuario deshabilitado"),
    ("a_revocado", "membresia revocada"),
    ("c_suspendida", "suscripcion suspendida"),
    ("d_inactiva", "empresa inactiva"),
])
def test_identidades_no_vigentes_no_reciben_token(api, lab, clave, detalle):
    slug = {
        "a_deshabilitado": "empresa-a",
        "a_revocado": "empresa-a",
        "c_suspendida": "empresa-c",
        "d_inactiva": "empresa-d",
    }[clave]
    respuesta = _login(api, lab["correos"][clave], lab["password_usuarios"], slug)
    assert respuesta.status_code == 401, respuesta.text
    REGISTRO.ok_http(f"login_rechazado_{clave}", f"401 ({detalle})")


def test_credenciales_invalidas_401(api, lab):
    respuesta = _login(api, lab["correos"]["a_admin"], "Password-Incorrecta-123", "empresa-a")
    assert respuesta.status_code == 401
    REGISTRO.ok_http("login_password_incorrecta", "401")


def test_jwt_legacy_no_es_token_tenant(api, lab, servidor):
    import jwt as pyjwt

    legacy = pyjwt.encode(
        {"sub": "1", "correo": lab["correos"]["a_admin"], "rol": "ADMINISTRADOR",
         "contexto": "personal", "exp": int(time.time()) + 3600},
        servidor["secreto_jwt"], algorithm="HS256",
    )
    respuesta = api.get("/auth/tenant/me", headers=_cabecera(legacy))
    assert respuesta.status_code == 401, respuesta.text
    REGISTRO.ok_http("jwt_legacy_en_ruta_tenant", "401 (sin claim scope=tenant)")


def test_token_alterado_401(api, lab):
    token = _token(api, lab, "a_admin")
    alterado = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    respuesta = api.get("/auth/tenant/me", headers=_cabecera(alterado))
    assert respuesta.status_code == 401
    REGISTRO.ok_http("jwt_firma_alterada", "401")


def test_sin_token_401(api):
    assert api.get("/auth/tenant/me").status_code == 401


def test_login_no_cambia_de_empresa_con_el_slug_de_otra(api, lab):
    """Un admin de A no obtiene token de B cambiando solo el slug."""
    respuesta = _login(api, lab["correos"]["a_admin"], lab["password_usuarios"], "empresa-b")
    assert respuesta.status_code == 401, respuesta.text
    REGISTRO.ok_http("login_cruzado", "admin A + slug empresa-b -> 401")


def test_funcion_deshabilitada_responde_503(api, lab):
    token = _token(api, lab, "a_cliente", contexto="cliente")
    respuesta = api.post("/tenant/reservas",
                         json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                         headers=_cabecera(token))
    assert respuesta.status_code == 503, respuesta.text
    REGISTRO.ok_http("reserva_write_flag_off", "503 (SAAS_RESERVAS_ENABLED=false)")


# ---------------------------------------------------------------------------
# Aislamiento de lectura
# ---------------------------------------------------------------------------


def test_catalogo_a_solo_ve_su_empresa(api, lab):
    token = _token(api, lab, "a_admin")
    categorias = api.get("/tenant/catalogo/categorias", headers=_cabecera(token))
    productos = api.get("/tenant/catalogo/productos", headers=_cabecera(token))
    assert categorias.status_code == productos.status_code == 200
    ids_cat = [c["id"] for c in categorias.json()]
    ids_prod = [p["id"] for p in productos.json()]
    assert 1 in ids_cat and 100 not in ids_cat, ids_cat
    assert 1 in ids_prod and 100 not in ids_prod, ids_prod
    assert "B" not in str(productos.json())
    REGISTRO.ok_http("catalogo_A", f"categorias={ids_cat} productos={ids_prod} sin datos de B")


def test_catalogo_b_solo_ve_su_empresa(api, lab):
    token = _token(api, lab, "b_admin")
    categorias = api.get("/tenant/catalogo/categorias", headers=_cabecera(token))
    productos = api.get("/tenant/catalogo/productos", headers=_cabecera(token))
    ids_cat = [c["id"] for c in categorias.json()]
    ids_prod = [p["id"] for p in productos.json()]
    assert 100 in ids_cat and 1 not in ids_cat, ids_cat
    assert 100 in ids_prod and 1 not in ids_prod, ids_prod
    REGISTRO.ok_http("catalogo_B", "categorias=[100] productos=[100] sin datos de A")


def test_detalle_cruzado_devuelve_404_sin_filtrar(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    casos = [
        ("GET", "/tenant/catalogo/productos/100", token_a),
        ("GET", "/tenant/catalogo/categorias/100", token_a),
        ("GET", "/tenant/catalogo/productos/100/variantes", token_a),
        ("GET", "/tenant/catalogo/productos/1", token_b),
    ]
    for metodo, ruta, token in casos:
        respuesta = api.request(metodo, ruta, headers=_cabecera(token))
        assert respuesta.status_code == 404, (ruta, respuesta.status_code, respuesta.text)
    REGISTRO.ok_http("detalle_cruzado_404", "4 recursos ajenos -> 404 identico")


def test_sucursales_y_usuarios_aislados(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    suc_a = api.get("/tenant/empresa/sucursales", headers=_cabecera(token_a)).json()
    suc_b = api.get("/tenant/empresa/sucursales", headers=_cabecera(token_b)).json()
    assert [s["id"] for s in suc_a] == [1]
    assert [s["id"] for s in suc_b] == [100]
    users_a = api.get("/tenant/empresa/usuarios", headers=_cabecera(token_a))
    users_b = api.get("/tenant/empresa/usuarios", headers=_cabecera(token_b))
    assert users_a.status_code == users_b.status_code == 200
    correos_a = {u["correo"] for u in users_a.json()}
    correos_b = {u["correo"] for u in users_b.json()}
    assert correos_a == {
        "admin@staging.invalid", "cliente@staging.invalid",
        "deshabilitado@staging.invalid", "revocado@staging.invalid",
    }, correos_a
    assert correos_b == {"admin.b@staging.invalid", "cliente.b@staging.invalid",
                         "cajero.b@staging.invalid"}
    assert not (correos_a & correos_b)
    REGISTRO.ok_http("sucursales_usuarios", "A=[1] B=[100]; correos disjuntos")


def test_empresa_plan_y_comercial_propios(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    me_a = api.get("/tenant/empresa/me", headers=_cabecera(token_a))
    assert me_a.status_code == 200 and me_a.json()["slug"] == "empresa-a"
    plan_b = api.get("/tenant/empresa/plan", headers=_cabecera(token_b))
    assert plan_b.status_code == 200 and plan_b.json()["codigo"] == "BASICO"
    ventas_a = api.get("/tenant/comercial/ventas", headers=_cabecera(token_a))
    ventas_b = api.get("/tenant/comercial/ventas", headers=_cabecera(token_b))
    assert ventas_a.status_code == 200 and len(ventas_a.json()) == 1
    assert ventas_b.status_code == 200 and ventas_b.json() == []
    detalle_ajeno = api.get("/tenant/comercial/ventas/1", headers=_cabecera(token_b))
    assert detalle_ajeno.status_code == 404
    REGISTRO.ok_http("comercial_A_B", "A=1 venta, B=0 ventas, detalle ajeno 404")


# ---------------------------------------------------------------------------
# Permisos de rol
# ---------------------------------------------------------------------------


def test_cliente_no_accede_a_administracion(api, lab):
    token = _token(api, lab, "a_cliente", contexto="cliente")
    usuarios = api.get("/tenant/empresa/usuarios", headers=_cabecera(token))
    # GET /tenant/empresa/sucursales es de solo lectura para cualquier miembro
    # del tenant (asi esta definido en el router); el listado de usuarios, no.
    sucursales = api.get("/tenant/empresa/sucursales", headers=_cabecera(token))
    assert usuarios.status_code == 403
    assert sucursales.status_code == 200
    assert [s["id"] for s in sucursales.json()] == [1]
    REGISTRO.ok_http("cliente_sin_admin",
                     "403 en usuarios; 200 sucursales propias (lectura permitida)")


def test_cajero_no_accede_a_administracion(api, lab):
    token = _token(api, lab, "b_cajero")
    respuesta = api.get("/tenant/empresa/usuarios", headers=_cabecera(token))
    assert respuesta.status_code == 403
    REGISTRO.ok_http("cajero_sin_admin", "403")


def test_admin_tenant_no_accede_superadmin(api, lab):
    token = _token(api, lab, "a_admin")
    respuesta = api.get("/saas/superadmin/empresas", headers=_cabecera(token))
    assert respuesta.status_code in (403, 503), respuesta.text
    REGISTRO.ok_http("admin_tenant_superadmin", f"{respuesta.status_code} (flag superadmin OFF)")


# ---------------------------------------------------------------------------
# Escrituras HTTP tenant
# ---------------------------------------------------------------------------


def test_post_y_patch_catalogo_propio(api, lab):
    token = _token(api, lab, "a_admin")
    creada = api.post("/tenant/catalogo/categorias",
                      json={"nombre": "Categoria HTTP 5BK", "descripcion": "etapa C"},
                      headers=_cabecera(token))
    assert creada.status_code == 201, creada.text
    nueva_id = creada.json()["id"]
    producto = api.post("/tenant/catalogo/productos",
                        json={"categoria_id": nueva_id, "nombre": "Producto HTTP 5BK",
                              "descripcion": "etapa C", "precio": "25.50"},
                        headers=_cabecera(token))
    assert producto.status_code == 201, producto.text
    producto_id = producto.json()["id"]
    editado = api.patch(f"/tenant/catalogo/productos/{producto_id}",
                        json={"nombre": "Producto HTTP 5BK editado"},
                        headers=_cabecera(token))
    assert editado.status_code == 200, editado.text
    assert editado.json()["nombre"] == "Producto HTTP 5BK editado"
    assert nueva_id in [c["id"] for c in
                        api.get("/tenant/catalogo/categorias", headers=_cabecera(token)).json()]
    REGISTRO.ok_http("post_patch_catalogo_A", "201 cat, 201 prod, 200 patch, visible solo en A")


def test_patch_de_recurso_ajeno_404(api, lab):
    token_a = _token(api, lab, "a_admin")
    respuesta = api.patch("/tenant/catalogo/productos/100",
                          json={"nombre": "intento cruzado"}, headers=_cabecera(token_a))
    assert respuesta.status_code == 404, respuesta.text
    token_b = _token(api, lab, "b_admin")
    assert api.get("/tenant/catalogo/productos/100",
                   headers=_cabecera(token_b)).json()["nombre"] == "Camisa Ficticia B"
    REGISTRO.ok_http("patch_cruzado", "A no modifica producto 100 de B (404, valor intacto)")


def test_post_sucursal_y_patch_membresia_ajena(api, lab):
    token_a = _token(api, lab, "a_admin")
    nueva = api.post("/tenant/empresa/sucursales",
                     json={"ciudad_id": 1, "nombre": "Sucursal HTTP A",
                           "direccion": "Calle 5BK", "telefono": None},
                     headers=_cabecera(token_a))
    assert nueva.status_code == 201, nueva.text
    assert {s["id"] for s in api.get("/tenant/empresa/sucursales",
                                     headers=_cabecera(token_a)).json()} >= {1, nueva.json()["id"]}
    ajeno = api.patch(f"/tenant/empresa/usuarios/{lab['usuarios']['b_cajero']}/estado",
                      json={"estado": False}, headers=_cabecera(token_a))
    assert ajeno.status_code == 404, ajeno.text
    REGISTRO.ok_http("post_sucursal_A_patch_B", "201 sucursal A; PATCH membresia B -> 404")


def test_carrito_post_delete_y_cruce_de_tenant(api, lab):
    token_a = _token(api, lab, "a_cliente", contexto="cliente")
    token_b = _token(api, lab, "b_cliente", contexto="cliente")
    item_a = api.post("/tenant/carritos/items",
                      json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                      headers=_cabecera(token_a))
    assert item_a.status_code == 200, item_a.text
    carrito_a = item_a.json()["carrito_id"]
    detalle_a = item_a.json()["detalle_id"]

    item_b = api.post("/tenant/carritos/items",
                      json={"sucursal_id": 100, "inventario_id": 100, "cantidad": 1},
                      headers=_cabecera(token_b))
    assert item_b.status_code == 200, item_b.text
    carrito_b = item_b.json()["carrito_id"]

    cruce = api.delete(f"/tenant/carritos/{carrito_b}", headers=_cabecera(token_a))
    assert cruce.status_code == 404, cruce.text
    cruce_item = api.delete(f"/tenant/carritos/{carrito_b}/items/{item_b.json()['detalle_id']}",
                            headers=_cabecera(token_a))
    assert cruce_item.status_code == 404, cruce_item.text

    borrado = api.delete(f"/tenant/carritos/{carrito_a}/items/{detalle_a}",
                         headers=_cabecera(token_a))
    assert borrado.status_code == 200, borrado.text
    assert borrado.json()["cantidad_lineas"] == 0
    REGISTRO.ok_http("carrito_post_delete", "200 post A/B; DELETE ajeno 404; DELETE propio 200/0 lineas")


def test_post_con_inventario_ajeno_rechazado(api, lab):
    token_b = _token(api, lab, "b_cliente", contexto="cliente")
    respuesta = api.post("/tenant/carritos/items",
                         json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                         headers=_cabecera(token_b))
    # El carrito se crea antes de validar inventario: la FK compuesta/R tutela
    # rechaza la referencia cruzada con 409 sin exponer datos de A.
    assert respuesta.status_code in (403, 404, 409), respuesta.text
    REGISTRO.ok_http("carrito_inventario_ajeno", f"{respuesta.status_code} (rechazado, sin fuga)")


def test_revocacion_de_membresia_corta_el_acceso(api, lab):
    token = _token(api, lab, "b_cajero")
    assert api.get("/auth/tenant/me", headers=_cabecera(token)).status_code == 200
    import psycopg

    with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
        conn.execute("UPDATE public.usuario_empresa SET estado = FALSE "
                     "WHERE usuario_id = %s AND empresa_id = %s",
                     (lab["usuarios"]["b_cajero"], lab["empresas"]["empresa_b"]))
    try:
        respuesta = api.get("/auth/tenant/me", headers=_cabecera(token))
        assert respuesta.status_code == 403, respuesta.text
    finally:
        with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
            conn.execute("UPDATE public.usuario_empresa SET estado = TRUE "
                         "WHERE usuario_id = %s AND empresa_id = %s",
                         (lab["usuarios"]["b_cajero"], lab["empresas"]["empresa_b"]))
    assert api.get("/auth/tenant/me", headers=_cabecera(token)).status_code == 200
    REGISTRO.ok_http("revocacion_membresia", "200 -> 403 -> (restaurada) 200")


# ---------------------------------------------------------------------------
# Pool y concurrencia
# ---------------------------------------------------------------------------


def test_pool_a_b_a_sin_contexto_filtrado(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    for _ in range(6):
        a = api.get("/tenant/catalogo/productos", headers=_cabecera(token_a))
        b = api.get("/tenant/catalogo/productos", headers=_cabecera(token_b))
        ids_a = [p["id"] for p in a.json()]
        ids_b = [p["id"] for p in b.json()]
        assert 1 in ids_a and 100 not in ids_a, ids_a
        assert 100 in ids_b and 1 not in ids_b, ids_b
    REGISTRO.ok_http("pool_A_B_A", "6 ciclos A->B->A sin mezcla de filas")


def test_requests_concurrentes_mantienen_su_tenant(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")

    def consultar(cual: str) -> tuple[str, list[int]]:
        token = token_a if cual == "A" else token_b
        with httpx.Client(base_url=str(api.base_url), timeout=20.0) as cliente:
            productos = cliente.get("/tenant/catalogo/productos",
                                    headers=_cabecera(token)).json()
        return cual, [p["id"] for p in productos]

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(consultar, ["A", "B"] * 6))
    for cual, ids in resultados:
        if cual == "A":
            assert 1 in ids and 100 not in ids, (cual, ids)
        else:
            assert 100 in ids and 1 not in ids, (cual, ids)
    REGISTRO.ok_http("concurrencia_12", "12 requests concurrentes, cada uno con su tenant")


def test_conexion_reutilizada_sin_asercion_no_ve_nada(lab):
    import psycopg

    asercion_a = _asercion(lab["_clave"], lab["empresas"]["empresa_a"],
                           lab["usuarios"]["a_admin"])
    with psycopg.connect(lab["runtime_dsn"]) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion_a,))
            visible = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        # Misma conexion fisica, transaccion nueva: la asercion era LOCAL.
        posterior = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        actual = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
    assert visible >= 1
    assert posterior == 0
    assert actual is None
    REGISTRO.ok_sql("conexion_reutilizada",
                    f"con asercion A={visible} filas; nueva transaccion={posterior}; tenant_actual=NULL")


# ---------------------------------------------------------------------------
# Adversarial SQL con el rol REAL de conexion
# ---------------------------------------------------------------------------


def test_sql_sin_asercion_deniega_todo(lab):
    assert _sql_escalar(lab, "SELECT count(*) FROM public.producto") == 0
    assert _sql_escalar(lab, "SELECT count(*) FROM public.cliente") == 0
    assert _sql_escalar(lab, "SELECT count(*) FROM public.usuario_empresa") == 0
    REGISTRO.ok_sql("sin_asercion", "producto=0 cliente=0 usuario_empresa=0")


def test_set_config_malicioso_no_suplanta_tenant(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        conn.execute("SELECT set_config('app.empresa_id', %s, true)",
                     (str(lab["empresas"]["empresa_b"]),))
        conn.execute("SELECT set_config('app.usuario_id', '3', true)")
        filas = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        actual = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
    assert filas == 0 and actual is None
    REGISTRO.ok_sql("set_config_GUC_falso", "app.empresa_id=B + app.usuario_id=3 -> 0 filas")


def test_asercion_de_A_no_lee_ni_escribe_B(lab):
    clave = lab["_clave"]
    a_admin = lab["usuarios"]["a_admin"]
    asercion_a = _asercion(clave, lab["empresas"]["empresa_a"], a_admin)

    visibles = _sql_filas(lab, "SELECT id FROM public.producto ORDER BY id", asercion_a)
    ids = [fila[0] for fila in visibles]
    assert 1 in ids and 100 not in ids, ids
    assert _sql_escalar(lab, "SELECT count(*) FROM public.producto WHERE id = 100",
                        asercion_a) == 0
    assert _sql_escalar(lab, "SELECT count(*) FROM public.usuario WHERE id = 3",
                        asercion_a) == 0
    assert _sql_escalar(lab, "SELECT count(*) FROM public.sucursal WHERE id = 100",
                        asercion_a) == 0

    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion_a,))
        try:
            conn.execute(
                "INSERT INTO public.categoria (empresa_id, nombre, estado) "
                "VALUES (%s, 'Intento cruzado 5BK', TRUE)",
                (lab["empresas"]["empresa_b"],),
            )
            insertado = True
        except psycopg.errors.Error:
            conn.rollback()
            insertado = False
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion_a,))
        borradas = conn.execute(
            "DELETE FROM public.producto WHERE id = 100").rowcount
        conn.rollback()
    assert insertado is False
    assert borradas == 0
    REGISTRO.ok_sql("A_contra_B", "SELECT B=0; INSERT empresa_id=B rechazado; DELETE fila B=0")


def test_asercion_falsificada_y_vencida_deniegan(lab):
    clave = lab["_clave"]
    empresa_b = lab["empresas"]["empresa_b"]
    b_admin = lab["usuarios"]["b_admin"]
    # Firma de A sobre un mensaje que declara la empresa B.
    falsa = _asercion(clave, empresa_b, b_admin, firma_forzada="0" * 64)
    # Clave equivocada pero formato y firma bien construidos.
    otra_clave = _asercion(secrets.token_urlsafe(48), empresa_b, b_admin)
    # Expirada.
    vencida = _asercion(clave, empresa_b, b_admin, ttl=-3600)
    for nombre, valor in (("firma_invalida", falsa), ("clave_ajena", otra_clave),
                          ("expirada", vencida)):
        assert _sql_escalar(lab, "SELECT count(*) FROM public.producto", valor) == 0, nombre
    REGISTRO.ok_sql("asercion_falsificada", "firma invalida=0, clave ajena=0, expirada=0")


def test_membresia_revocada_invalida_la_asercion_en_base(lab):
    clave = lab["_clave"]
    empresa_b = lab["empresas"]["empresa_b"]
    b_admin = lab["usuarios"]["b_admin"]
    asercion = _asercion(clave, empresa_b, b_admin)
    assert _sql_escalar(lab, "SELECT count(*) FROM public.producto", asercion) == 1
    import psycopg

    with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
        conn.execute("UPDATE public.usuario_empresa SET estado = FALSE "
                     "WHERE usuario_id = %s AND empresa_id = %s", (b_admin, empresa_b))
    try:
        tras_revocar = _sql_escalar(lab, "SELECT count(*) FROM public.producto", asercion)
    finally:
        with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
            conn.execute("UPDATE public.usuario_empresa SET estado = TRUE "
                         "WHERE usuario_id = %s AND empresa_id = %s", (b_admin, empresa_b))
    assert tras_revocar == 0
    REGISTRO.ok_sql("revocacion_en_base", "asercion valida tras revocar membresia -> 0 filas")


def test_privilegios_del_principal(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        fila = conn.execute(
            "SELECT r.rolsuper, r.rolbypassrls, r.rolinherit, "
            "(SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid) "
            "FROM pg_roles r WHERE r.rolname = current_user"
        ).fetchone()
        forzadas = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity"
        ).fetchone()[0]
        propias = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND pg_has_role(current_user, c.relowner, 'USAGE')"
        ).fetchone()[0]
        politicas = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'").fetchone()[0]
        assert fila[0] is False and fila[1] is False
        assert fila[2] is False and fila[3] == 0
        assert propias == 0
        assert forzadas >= 37
        assert politicas >= 40
        # Sin SELECT directo sobre usuario ni sobre su hash.
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'public.usuario', 'SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_column_privilege(current_user, 'public.usuario', 'password_hash', 'SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'saas.rls_secret', 'SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege(current_user, 'saas.tenant_actual()', 'EXECUTE')"
        ).fetchone()[0] is True
    REGISTRO.ok_sql("privilegios_principal",
                    f"super={fila[0]} bypass={fila[1]} hereda={fila[2]} roles={fila[3]} "
                    f"propias={propias} forzadas={forzadas} politicas={politicas} "
                    "sin SELECT a usuario/rls_secret")


def test_intento_de_elevacion_de_rol(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        fallos = 0
        for sentencia in ("SET ROLE postgres", "SET ROLE saas_rls_owner",
                          "SET SESSION AUTHORIZATION postgres"):
            try:
                conn.execute(sentencia)
                fallos += 1
                conn.rollback()
            except psycopg.errors.Error:
                conn.rollback()
        assert fallos == 0
    REGISTRO.ok_sql("elevacion_rol", "SET ROLE postgres / owner y SESSION AUTHORIZATION rechazados")


def test_superficie_de_funciones_y_vistas_expuestas(lab):
    """El runtime no debe poder ejecutar nada mas alla del bootstrap y RLS."""
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        funciones = conn.execute(
            """
            SELECT n.nspname || '.' || p.proname
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('public', 'saas')
               AND p.prokind = 'f'
               AND has_function_privilege(current_user, p.oid, 'EXECUTE')
             ORDER BY 1
            """
        ).fetchall()
        vistas = conn.execute(
            """
            SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname IN ('public', 'saas') AND c.relkind IN ('v', 'm')
               AND has_table_privilege(current_user, c.oid, 'SELECT')
            """
        ).fetchone()[0]
    nombres = {fila[0] for fila in funciones}
    assert nombres == {
        "saas.login_credenciales", "saas.login_membresia", "saas.tenant_actual",
    }, nombres
    assert vistas == 0
    REGISTRO.ok_sql("superficie_funciones", f"EXECUTE solo en {sorted(nombres)}; vistas accesibles=0")


def test_rollback_tras_conflicto_no_deja_estado_parcial(api, lab):
    """Un UNIQUE por empresa rechaza el duplicado y revierte la transaccion."""
    token = _token(api, lab, "a_admin")
    nombre = "Categoria Rollback 5BK"
    primera = api.post("/tenant/catalogo/categorias", json={"nombre": nombre},
                       headers=_cabecera(token))
    assert primera.status_code == 201, primera.text
    repetida = api.post("/tenant/catalogo/categorias", json={"nombre": nombre},
                        headers=_cabecera(token))
    assert repetida.status_code == 409, repetida.text
    coincidencias = [c for c in
                     api.get("/tenant/catalogo/categorias", headers=_cabecera(token)).json()
                     if c["nombre"] == nombre]
    assert len(coincidencias) == 1, coincidencias
    REGISTRO.ok_http("rollback_conflicto", "201 -> 409 -> una sola fila persistida")
