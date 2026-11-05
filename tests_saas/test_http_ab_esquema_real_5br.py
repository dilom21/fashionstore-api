"""5BR / Etapa I - HTTP A/B REAL sobre el ESQUEMA HISTORICO ORIGINAL restaurado.

Diferencia con 5BK/5BL
----------------------
No se ensaya sobre la reconstruccion sintetica (40/14/12) sino sobre las 41
tablas, 27 triggers y 24 rutinas del `pg_dump` REAL, restauradas con
``96_laboratorio_esquema_real_5br.py`` y migradas con el manifiesto ``98``.

Requisitos
----------
    python database/saas/96_laboratorio_esquema_real_5br.py --init --confirm-desechable

La fixture de sesion reconstruye el laboratorio completo (recrear -> restaurar ->
pipeline) salvo que ``FASHIONSTORE_LAB_5BR_RESET=0``. Si el cluster no existe, la
bateria se OMITE: nunca se simula con SQLite.

La app FastAPI se ejecuta en un proceso **uvicorn real** con
``DATABASE_URL`` apuntando al principal restringido ``fs_saas_runtime``
(sin SUPERUSER, sin BYPASSRLS, sin propiedad de tablas).
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
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
RAIZ_LAB = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
            / "Temp" / "opencode" / "fs_lab_5br_pg17")
MANIFIESTO = RAIZ_LAB / "lab_5br_manifest.json"
ENSATO = RAIZ_LAB / "lab_5br_ensayo.json"
SECRETOS = RAIZ_LAB / "lab_5br_secrets.json"
HOST_LAB, PUERTO_LAB = "127.0.0.1", 55448
BASE_LAB = "fashionstore_lab_5br_esquema_real"
SELLO = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class Registro:
    def __init__(self) -> None:
        self.http: list[str] = []
        self.sql: list[str] = []

    def ok_http(self, nombre: str, detalle: str) -> None:
        self.http.append(f"[PASS] {nombre}: {detalle}")

    def ok_sql(self, nombre: str, detalle: str) -> None:
        self.sql.append(f"[PASS] {nombre}: {detalle}")

    def _escribir(self, prefijo: str, titulo: str, filas: list[str]) -> Path:
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        ruta = EVIDENCIAS / f"{prefijo}_{SELLO}.txt"
        encabezado = [
            titulo,
            f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Cluster: PostgreSQL 17 en {HOST_LAB}:{PUERTO_LAB} / {BASE_LAB}",
            "Esquema: ORIGINAL restaurado (41 tablas / 27 triggers / 24 rutinas / 1 vista)",
            "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
            "-" * 78,
        ]
        ruta.write_text("\n".join(encabezado + filas) + "\n", encoding="utf-8")
        return ruta

    def escribir(self) -> tuple[Path, Path]:
        return (self._escribir("EVIDENCIA_HTTP_AB_5BR",
                               "EVIDENCIA HTTP A/B - ETAPA I 5BR (esquema real)",
                               self.http),
                self._escribir("EVIDENCIA_RLS_5BR",
                               "EVIDENCIA SQL / RLS - ETAPA I 5BR (esquema real)",
                               self.sql))


REGISTRO = Registro()


def _dsn(rol: str, password: str) -> str:
    from urllib.parse import quote

    return (f"postgresql://{rol}:{quote(password, safe='')}"
            f"@{HOST_LAB}:{PUERTO_LAB}/{BASE_LAB}")


def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _asercion(clave: str, empresa_id: int, usuario_id: int, *, ttl: int = 120) -> str:
    expira = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = hmac.new(clave.encode("utf-8"), mensaje.encode("utf-8"),
                     hashlib.sha256).hexdigest()
    return f"{mensaje}|{firma}"


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BR no aprovisionado; ejecute "
            "database/saas/96_laboratorio_esquema_real_5br.py --init --confirm-desechable")
    if os.environ.get("FASHIONSTORE_LAB_5BR_RESET", "1") != "0":
        comandos = [
            ["database/saas/96_laboratorio_esquema_real_5br.py", "--recrear",
             "--confirm-desechable"],
            ["database/saas/96_laboratorio_esquema_real_5br.py", "--restaurar",
             "--confirm-desechable"],
            ["database/saas/99_ensayo_esquema_real_5br.py", "--todo"],
        ]
        for comando in comandos:
            resultado = subprocess.run(
                [sys.executable, *comando], cwd=str(ROOT),
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=900)
            if resultado.returncode != 0:
                pytest.fail(
                    f"No se pudo reconstruir el laboratorio 5BR ({comando[0]}): "
                    f"{resultado.stderr[-900:]}{resultado.stdout[-900:]}")
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    secretos = json.loads(SECRETOS.read_text(encoding="utf-8"))
    ensayo = (json.loads(ENSATO.read_text(encoding="utf-8"))
              if ENSATO.exists() else {})
    if not ensayo.get("password_usuarios"):
        pytest.skip("Falta lab_5br_ensayo.json: ejecute "
                    "99_ensayo_esquema_real_5br.py --todo")
    cluster = datos["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"} or cluster["puerto"] != PUERTO_LAB:
        pytest.skip("El manifiesto no corresponde al laboratorio desechable 5BR")
    if "lab_5br" not in cluster["base"].lower():
        pytest.skip("El manifiesto no apunta a la base descartable 5BR")
    datos["runtime_dsn"] = _dsn("fs_saas_runtime", secretos["password_runtime"])
    datos["admin_dsn"] = _dsn("postgres", secretos["password_admin"])
    datos["password_usuarios"] = ensayo["password_usuarios"]
    datos["_clave"] = secretos["assertion_key"]
    datos["empresas"] = {"empresa_a": 1, "empresa_b": 2,
                         "empresa_c": 3, "empresa_d": 4}
    datos["usuarios"] = {"a_admin": 1, "a_cliente": 2, "b_admin": 3,
                         "b_cliente": 4, "b_cajero": 5}
    datos["correos"] = {
        "a_admin": "admin@staging.invalid",
        "a_cliente": "cliente@staging.invalid",
        "b_admin": "admin.b@staging.invalid",
        "b_cliente": "cliente.b@staging.invalid",
        "b_cajero": "cajero.b@staging.invalid",
    }
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    env = {
        **os.environ,
        "DATABASE_URL": lab["runtime_dsn"].replace("postgresql://",
                                                   "postgresql+psycopg://", 1),
        "SECRET_KEY": secrets.token_urlsafe(32),
        "SAAS_AUTH_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "false",
        "SAAS_RESERVAS_ENABLED": "false",
        "SAAS_TENANT_ASSERTION_KEY": lab["_clave"],
        "APP_ENV": "lab-5br",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5br_{puerto}.log"
    registro.parent.mkdir(parents=True, exist_ok=True)
    salida = open(registro, "w", encoding="utf-8")
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=salida, stderr=salida)
    base = f"http://127.0.0.1:{puerto}"
    limite = time.time() + 90
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
        contenido = registro.read_text(encoding="utf-8", errors="replace")[-2500:]
        pytest.fail(f"uvicorn no arranco sobre el esquema real: {contenido}")
    yield {"base": base, "proceso": proceso}
    proceso.terminate()
    try:
        proceso.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proceso.kill()
    salida.close()


@pytest.fixture(scope="session", autouse=True)
def escribir_evidencia():
    yield
    if REGISTRO.http or REGISTRO.sql:
        rutas = REGISTRO.escribir()
        print("\nEvidencias escritas: " + " | ".join(str(r) for r in rutas))


@pytest.fixture()
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=25.0) as cliente:
        yield cliente


def _login(api: httpx.Client, correo: str, password: str, slug: str,
           contexto: str = "personal") -> httpx.Response:
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto})


def _token(api: httpx.Client, lab: dict, cual: str, contexto: str = "personal") -> str:
    slug = "empresa-a" if cual.startswith("a_") else "empresa-b"
    respuesta = _login(api, lab["correos"][cual], lab["password_usuarios"], slug, contexto)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Autenticacion sobre el esquema ORIGINAL (incluye el fix de diccionarios RLS)
# ---------------------------------------------------------------------------


def test_login_resuelve_tenant_con_diccionarios_bajo_rls(api, lab):
    """Regresion 5BR: rol/rol_funcion tienen RLS del origen; sin la politica de
    catalogo, saas.tenant_actual() devolvia NULL y el login fallaba."""
    respuesta = _login(api, lab["correos"]["a_admin"], lab["password_usuarios"],
                       "empresa-a")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["empresa_id"] == 1 and cuerpo["rol"] == "ADMINISTRADOR"
    REGISTRO.ok_http("login_A_sobre_esquema_original",
                     "200, empresa 1, rol ADMINISTRADOR (diccionarios legibles bajo RLS)")


def test_login_b_y_me(api, lab):
    respuesta = _login(api, lab["correos"]["b_admin"], lab["password_usuarios"],
                       "empresa-b")
    assert respuesta.status_code == 200, respuesta.text
    me = api.get("/auth/tenant/me", headers=_cabecera(respuesta.json()["access_token"]))
    assert me.status_code == 200 and me.json()["empresa_slug"] == "empresa-b"
    REGISTRO.ok_http("login_B_me", "200 / 200 empresa-b")


def test_login_cruzado_rechazado(api, lab):
    respuesta = _login(api, lab["correos"]["a_admin"], lab["password_usuarios"],
                       "empresa-b")
    assert respuesta.status_code == 401, respuesta.text
    REGISTRO.ok_http("login_cruzado", "401")


def test_catalogo_aislado_en_ambas_empresas(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    cat_a = [c["id"] for c in api.get("/tenant/catalogo/categorias",
                                      headers=_cabecera(token_a)).json()]
    cat_b = [c["id"] for c in api.get("/tenant/catalogo/categorias",
                                      headers=_cabecera(token_b)).json()]
    prod_a = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                        headers=_cabecera(token_a)).json()]
    prod_b = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                        headers=_cabecera(token_b)).json()]
    assert 1 in cat_a and 100 not in cat_a, cat_a
    assert 100 in cat_b and 1 not in cat_b, cat_b
    assert 1 in prod_a and 100 not in prod_a, prod_a
    assert 100 in prod_b and 1 not in prod_b, prod_b
    REGISTRO.ok_http("catalogo_A_B", f"A={cat_a}/{prod_a} B={cat_b}/{prod_b}")


def test_recurso_ajeno_404(api, lab):
    token_a = _token(api, lab, "a_admin")
    for ruta in ("/tenant/catalogo/productos/100",
                 "/tenant/catalogo/categorias/100"):
        respuesta = api.get(ruta, headers=_cabecera(token_a))
        assert respuesta.status_code == 404, (ruta, respuesta.status_code)
    REGISTRO.ok_http("detalle_cruzado_404", "producto/categoria de B -> 404")


def test_venta_historica_aislada(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    ventas_a = api.get("/tenant/comercial/ventas", headers=_cabecera(token_a))
    ventas_b = api.get("/tenant/comercial/ventas", headers=_cabecera(token_b))
    assert ventas_a.status_code == 200 and len(ventas_a.json()) == 1
    assert ventas_b.status_code == 200 and ventas_b.json() == []
    ajeno = api.get("/tenant/comercial/ventas/1", headers=_cabecera(token_b))
    assert ajeno.status_code == 404
    REGISTRO.ok_http("ventas_A_B", "A=1 venta historica, B=0, detalle ajeno 404")


def test_carrito_write_y_cruce(api, lab):
    token_a = _token(api, lab, "a_cliente", contexto="cliente")
    token_b = _token(api, lab, "b_cliente", contexto="cliente")
    item_a = api.post("/tenant/carritos/items",
                      json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                      headers=_cabecera(token_a))
    assert item_a.status_code == 200, item_a.text
    item_b = api.post("/tenant/carritos/items",
                      json={"sucursal_id": 100, "inventario_id": 100, "cantidad": 1},
                      headers=_cabecera(token_b))
    assert item_b.status_code == 200, item_b.text
    cruce = api.delete(f"/tenant/carritos/{item_b.json()['carrito_id']}",
                       headers=_cabecera(token_a))
    assert cruce.status_code == 404, cruce.text
    REGISTRO.ok_http("carrito_escritura_y_cruce", "200 A/B; DELETE ajeno 404")


def test_rol_cliente_no_administra(api, lab):
    token = _token(api, lab, "a_cliente", contexto="cliente")
    assert api.get("/tenant/empresa/usuarios",
                   headers=_cabecera(token)).status_code == 403
    assert api.get("/tenant/empresa/sucursales",
                   headers=_cabecera(token)).status_code == 200
    REGISTRO.ok_http("cliente_sin_admin", "403 usuarios / 200 sucursales propias")


def test_revocacion_de_membresia_corta_el_acceso(api, lab):
    import psycopg

    token = _token(api, lab, "b_cajero")
    assert api.get("/auth/tenant/me", headers=_cabecera(token)).status_code == 200
    with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
        conn.execute("UPDATE public.usuario_empresa SET estado = FALSE "
                     "WHERE usuario_id = 5 AND empresa_id = 2")
    try:
        assert api.get("/auth/tenant/me",
                       headers=_cabecera(token)).status_code == 403
    finally:
        with psycopg.connect(lab["admin_dsn"], autocommit=True) as conn:
            conn.execute("UPDATE public.usuario_empresa SET estado = TRUE "
                         "WHERE usuario_id = 5 AND empresa_id = 2")
    assert api.get("/auth/tenant/me", headers=_cabecera(token)).status_code == 200
    REGISTRO.ok_http("revocacion_membresia", "200 -> 403 -> 200")


# ---------------------------------------------------------------------------
# SQL real con el principal restringido
# ---------------------------------------------------------------------------


def test_principal_restringido_y_diccionarios(lab):
    import psycopg

    asercion = _asercion(lab["_clave"], 2, 3)
    with psycopg.connect(lab["runtime_dsn"]) as conn:
        fila = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolinherit FROM pg_roles "
            "WHERE rolname = current_user").fetchone()
        assert fila == (False, False, False)
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (asercion,))
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] == 2
        roles = conn.execute("SELECT count(*) FROM public.rol").fetchone()[0]
        funciones = conn.execute("SELECT count(*) FROM public.funcion").fetchone()[0]
        assert roles >= 3 and funciones >= 1
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'public.usuario', 'SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege(current_user, "
            "'saas.tenant_actual()', 'EXECUTE')").fetchone()[0] is True
    REGISTRO.ok_sql("principal_y_diccionarios",
                    f"super/bypass/inherit=False; tenant=2; rol={roles} funcion={funciones}")


def test_triggers_originales_presentes_y_sin_execute_publico(lab):
    import psycopg

    nombres = (
        "trg_bitacora_devolucion", "trg_bitacora_empleado",
        "trg_bitacora_orden_compra", "trg_bitacora_pago", "trg_bitacora_rol",
        "trg_bitacora_variante_producto", "trg_bitacora_venta",
        "trg_normalizar_sku", "trg_proteger_eliminacion_venta",
        "trg_recalcular_total_venta", "trg_validar_detalle_devolucion",
        "trg_validar_detalle_venta_sucursal", "trg_validar_producto_proveedor_orden",
    )
    rutinas = (
        "fn_normalizar_sku", "fn_proteger_eliminacion_venta",
        "fn_recalcular_total_venta_trigger", "fn_validar_detalle_devolucion",
        "fn_validar_detalle_venta_sucursal", "fn_validar_producto_proveedor_orden",
        "sp_ajustar_inventario", "sp_confirmar_venta", "sp_crear_reserva",
        "sp_recalcular_total_venta", "sp_recibir_orden_compra",
        "sp_registrar_devolucion",
    )
    with psycopg.connect(lab["admin_dsn"]) as conn:
        triggers = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgname = ANY(%s)", (list(nombres),)).fetchone()[0]
        rutinas_vivas = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
            (list(rutinas),)).fetchone()[0]
        legado_publico = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY(%s) "
            "AND has_function_privilege('public', p.oid, 'EXECUTE')",
            (list(rutinas),)).fetchone()[0]
        token = conn.execute(
            "SELECT to_regclass('public.token_recuperacion') IS NOT NULL").fetchone()[0]
    assert triggers == 13, triggers
    assert rutinas_vivas == 12, rutinas_vivas
    assert legado_publico == 0, legado_publico
    assert token is True
    REGISTRO.ok_sql("objetos_originales_5bl_ausentes",
                    "13 triggers + 12 rutinas + token_recuperacion presentes; "
                    "EXECUTE PUBLIC=0")


def test_sin_privilegios_de_plataforma(lab):
    import psycopg

    with psycopg.connect(lab["admin_dsn"]) as conn:
        concesiones = conn.execute(
            """
            SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              CROSS JOIN (VALUES ('anon'), ('authenticated'), ('service_role')) v(r)
              JOIN pg_roles r ON r.rolname = v.r
              CROSS JOIN LATERAL aclexplode(c.relacl) a
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S')
               AND a.grantee = r.oid""").fetchone()[0]
        defaults = conn.execute(
            """
            SELECT count(*) FROM pg_default_acl d
              JOIN pg_namespace n ON n.oid = d.defaclnamespace
              CROSS JOIN LATERAL aclexplode(d.defaclacl) a
              JOIN pg_roles r ON r.oid = a.grantee
             WHERE n.nspname = 'public'
               AND r.rolname IN ('anon', 'authenticated', 'service_role')
            """).fetchone()[0]
        force = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "AND c.relkind = 'r' AND c.relforcerowsecurity").fetchone()[0]
    assert concesiones == 0, concesiones
    assert defaults == 0, defaults
    assert force >= 41, force
    REGISTRO.ok_sql("acl_plataforma_revocada",
                    f"grants={concesiones} default_acl={defaults} force_rls={force}")


def test_sin_asercion_no_ve_nada(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM public.producto").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM public.venta").fetchone()[0] == 0
    REGISTRO.ok_sql("deny_all_sin_asercion", "producto=0 venta=0 tenant=NULL")
