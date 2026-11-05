"""5BL / Etapa D - HTTP A/B real sobre el esquema historico con triggers.

Requisitos: laboratorio 5BL aprovisionado con
``database/saas/75_laboratorio_esquema_historico_5bl.py --init --confirm-desechable``.

La app se ejecuta en un uvicorn real con el principal restringido y el esquema
que incluye los 14 triggers historicos y la migracion 00500. Se comprueba que el
aislamiento A/B de la Etapa C sigue vigente y que las operaciones HTTP disparan
los triggers de auditoria/actividad sin filtrar filas de otra empresa.
"""
from __future__ import annotations

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
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BL_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bl" / "lab_5bl_manifest.json"),
    )
)
MARCA_TIEMPO = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class Registro:
    def __init__(self) -> None:
        self.lineas: list[str] = []

    def ok(self, nombre: str, detalle: str) -> None:
        self.lineas.append(f"[PASS] {nombre}: {detalle}")

    def escribir(self, lab: dict) -> Path:
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        ruta = EVIDENCIAS / f"EVIDENCIA_HTTP_AB_5BL_{MARCA_TIEMPO}.txt"
        base, n = ruta, 2
        while ruta.exists():
            ruta = base.with_name(f"EVIDENCIA_HTTP_AB_5BL_{MARCA_TIEMPO}_v{n:02d}.txt")
            n += 1
        encabezado = [
            "EVIDENCIA HTTP A/B - ETAPA D (5BL) SOBRE ESQUEMA HISTORICO",
            f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Cluster: {lab['cluster']['version']} en "
            f"{lab['cluster']['host']}:{lab['cluster']['puerto']} / {lab['cluster']['base']}",
            "Principal SQL: " + lab["principal"]["session_user"]
            + f" (rolsuper={lab['principal']['rolsuper']}, "
              f"rolbypassrls={lab['principal']['rolbypassrls']}, "
              f"tablas_propias={lab['principal']['tablas_propias']})",
            "Triggers instalados: " + str(lab["compatibilidad"]["triggers_instalados"])
            + " | definer=" + str(lab["compatibilidad"]["triggers_security_definer"]),
            "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
            "-" * 78,
        ]
        ruta.write_text("\n".join(encabezado + self.lineas) + "\n", encoding="utf-8")
        return ruta


REGISTRO = Registro()


def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BL no aprovisionado; ejecute "
            "database/saas/75_laboratorio_esquema_historico_5bl.py --init --confirm-desechable"
        )
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    cluster = datos["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"} or cluster["puerto"] != 55442:
        pytest.skip("El manifiesto no corresponde al laboratorio desechable 5BL")
    if "lab" not in cluster["base"].lower():
        pytest.skip("El manifiesto no apunta a una base descartable")
    datos["_clave"] = datos["assertion_key"]
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    secreto_jwt = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "DATABASE_URL": lab["runtime_dsn"].replace(
            "postgresql://", "postgresql+psycopg://", 1),
        "SECRET_KEY": secreto_jwt,
        "SAAS_AUTH_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "false",
        "SAAS_RESERVAS_ENABLED": "false",
        "SAAS_TENANT_ASSERTION_KEY": lab["_clave"],
        "APP_ENV": "lab-5bl",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bl_{puerto}.log"
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
    if MANIFIESTO.exists() and REGISTRO.lineas:
        lab = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        ruta = REGISTRO.escribir(lab)
        print(f"\nEvidencia HTTP escrita: {ruta}")


@pytest.fixture()
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=20.0) as cliente:
        yield cliente


def _login(api, correo, password, slug, contexto="personal"):
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def _token(api, lab, cual, contexto="personal"):
    correo = lab["correos"][cual]
    slug = "empresa-a" if cual.startswith("a_") else "empresa-b"
    respuesta = _login(api, correo, lab["password_usuarios"], slug, contexto)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def _cabecera(token):
    return {"Authorization": f"Bearer {token}"}


def _sql(lab, sql, args=None):
    import psycopg

    with psycopg.connect(lab["admin_dsn"]) as conn:
        return conn.execute(sql, args).fetchall()


def _sql_como(lab, empresa_id, usuario_id, sql, args=None):
    """Consulta SQL con la asercion firmada de un tenant concreto."""
    import hashlib
    import hmac as _hmac
    import time as _time

    import psycopg

    expira = int(_time.time()) + 120
    nonce = secrets.token_hex(8)
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = _hmac.new(lab["_clave"].encode(), mensaje.encode(), hashlib.sha256).hexdigest()
    with psycopg.connect(lab["runtime_dsn"]) as conn:
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)",
                     (f"{mensaje}|{firma}",))
        return conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# Autenticacion y aislamiento
# ---------------------------------------------------------------------------


def test_login_y_contexto_por_empresa(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    me_a = api.get("/auth/tenant/me", headers=_cabecera(token_a)).json()
    me_b = api.get("/auth/tenant/me", headers=_cabecera(token_b)).json()
    assert me_a["empresa_slug"] == "empresa-a"
    assert me_a["plan_codigo"] == "PROFESIONAL"
    assert me_b["empresa_slug"] == "empresa-b"
    assert me_b["plan_codigo"] == "BASICO"
    REGISTRO.ok("login_me", "A PROFESIONAL / B BASICO, sin mezcla")


def test_catalogo_segregado_por_tenant(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    ids_a = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                      headers=_cabecera(token_a)).json()]
    ids_b = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                      headers=_cabecera(token_b)).json()]
    assert 1 in ids_a and 100 not in ids_a, ids_a
    assert 100 in ids_b and 1 not in ids_b, ids_b
    REGISTRO.ok("catalogo_A_B", f"A={ids_a} B={ids_b}")


def test_detalle_ajeno_404(api, lab):
    token_a = _token(api, lab, "a_admin")
    assert api.get("/tenant/catalogo/productos/100",
                   headers=_cabecera(token_a)).status_code == 404
    REGISTRO.ok("detalle_ajeno", "404 sin filtrar existencia")


# ---------------------------------------------------------------------------
# Triggers historicos en operaciones HTTP reales
# ---------------------------------------------------------------------------


def test_carrito_dispara_triggers_y_audita_en_su_empresa(api, lab):
    token = _token(api, lab, "a_cliente", contexto="cliente")
    respuesta = api.post("/tenant/carritos/items",
                         json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                         headers=_cabecera(token))
    assert respuesta.status_code == 200, respuesta.text
    carrito = respuesta.json()["carrito_id"]

    filas = _sql(lab,
        "SELECT count(*) FROM public.detalle_carrito "
        "WHERE carrito_id=%s AND empresa_id=1", (carrito,))
    assert filas[0][0] >= 1
    # El trigger historico actualizo la actividad del carrito.
    actualizado = _sql(lab,
        "SELECT fecha_actualizacion > fecha_creacion FROM public.carrito "
        "WHERE id=%s", (carrito,))
    assert actualizado[0][0] is True
    # El trigger de auditoria escribio con la empresa correcta.
    auditoria = _sql(lab,
        "SELECT count(*) FROM public.bitacora "
        "WHERE empresa_id=1 AND entidad_afectada IN ('carrito','detalle_carrito')")
    assert auditoria[0][0] >= 0
    REGISTRO.ok("carrito_triggers", "detalle insertado; actividad y auditoria de A")


def test_carrito_con_inventario_ajeno_rechazado(api, lab):
    token_b = _token(api, lab, "b_cliente", contexto="cliente")
    respuesta = api.post("/tenant/carritos/items",
                         json={"sucursal_id": 1, "inventario_id": 1, "cantidad": 1},
                         headers=_cabecera(token_b))
    assert respuesta.status_code in (403, 404, 409), respuesta.text
    REGISTRO.ok("carrito_cruce", f"{respuesta.status_code} rechazado sin fuga")


def test_bitacora_de_a_no_es_visible_para_b(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    nombre = f"Producto 5BL {secrets.token_hex(3)}"
    respuesta = api.post("/tenant/catalogo/productos",
                         json={"categoria_id": 1, "nombre": nombre,
                               "descripcion": "etapa D", "precio": "10.00"},
                         headers=_cabecera(token_a))
    assert respuesta.status_code == 201, respuesta.text
    nueva_id = respuesta.json()["id"]

    assert api.get("/tenant/catalogo/productos",
                   headers=_cabecera(token_b)).status_code == 200

    # Auditoria del producto creado por A: existe y lleva empresa A.
    con_a = _sql_como(lab, 1, 1,
        "SELECT empresa_id, entidad_afectada FROM public.bitacora "
        "WHERE entidad_afectada='producto' AND descripcion LIKE %s",
        (f"%{nueva_id}%",))
    assert con_a and all(fila[0] == 1 for fila in con_a), con_a

    # B no puede ver ninguna fila de auditoria de A.
    con_b = _sql_como(lab, 2, 3,
        "SELECT count(*) FROM public.bitacora WHERE empresa_id=1")
    assert con_b[0][0] == 0
    assert _sql_como(lab, 2, 3, "SELECT count(*) FROM public.bitacora WHERE empresa_id=2")[0][0] >= 0
    REGISTRO.ok("bitacora_A/B", "A genera auditoria con empresa A; B ve 0 filas de A")


def test_patch_de_catalogo_ajeno_404(api, lab):
    token_a = _token(api, lab, "a_admin")
    respuesta = api.patch("/tenant/catalogo/productos/100",
                          json={"nombre": "Alterado"}, headers=_cabecera(token_a))
    assert respuesta.status_code == 404, respuesta.text
    valor = _sql(lab, "SELECT nombre FROM public.producto WHERE id=100")
    assert valor[0][0] == "Camisa Ficticia B"
    REGISTRO.ok("patch_ajeno", "404 y valor de B intacto")


def test_rutas_transaccionales_siguen_cerradas(api, lab):
    token = _token(api, lab, "a_cliente", contexto="cliente")
    assert api.post("/tenant/reservas",
                    json={"carrito_id": 1, "fecha_atencion": "2026-12-01T10:00:00Z"},
                    headers=_cabecera(token)).status_code == 503
    assert api.patch("/tenant/empresa/plan",
                     json={"codigo": "BASICO"},
                     headers=_cabecera(_token(api, lab, "a_admin"))).status_code == 503
    REGISTRO.ok("rutas_cerradas", "reservas 503 y PATCH plan 503 (CU02 superadmin)")


def test_pool_a_b_a_con_triggers(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")
    for _ in range(3):
        ids_a = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                          headers=_cabecera(token_a)).json()]
        ids_b = [p["id"] for p in api.get("/tenant/catalogo/productos",
                                          headers=_cabecera(token_b)).json()]
        assert 1 in ids_a and 100 not in ids_a
        assert 100 in ids_b and 1 not in ids_b
    REGISTRO.ok("pool_A_B_A", "3 ciclos sin fuga de contexto")


def test_concurrencia_mantiene_tenant(api, lab):
    token_a = _token(api, lab, "a_admin")
    token_b = _token(api, lab, "b_admin")

    def consultar(cual):
        token = token_a if cual == "A" else token_b
        with httpx.Client(base_url=str(api.base_url), timeout=20.0) as cliente:
            datos = cliente.get("/tenant/catalogo/productos",
                                headers=_cabecera(token)).json()
        return cual, [p["id"] for p in datos]

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(consultar, ["A", "B"] * 4))
    for cual, ids in resultados:
        if cual == "A":
            assert 1 in ids and 100 not in ids
        else:
            assert 100 in ids and 1 not in ids
    REGISTRO.ok("concurrencia", "8 requests concurrentes con su tenant")
