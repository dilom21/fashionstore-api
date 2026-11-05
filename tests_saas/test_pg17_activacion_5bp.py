"""5BP / ETAPA G - PostgreSQL 17 real: pipeline, RLS/ACL y B9 en HTTP.

Requisitos
----------
Laboratorio NUEVO en PostgreSQL **17** (nunca 18, nunca :55443/:55444/:55445)::

    python database/saas/90_laboratorio_pg17_5bp.py --init --confirm-desechable

Si el manifiesto no existe, la bateria se OMITE. Nunca se sustituye por SQLite ni
por PostgreSQL 18: eso falsearia la evidencia de motor que esta etapa debe dar.

Que prueba
----------
* Motor: ``SHOW server_version`` es 17.x y el pipeline se aplico DESDE CERO con
  las 15 fases del manifiesto controlado, con checksums y registro de versiones.
* Separacion de principales en PG17: el runtime tenant no inserta en
  ``public.usuario``; el principal global no tiene privilegios de tabla y no
  ejecuta rutinas tenant; los duenios NOLOGIN no pueden iniciar sesion.
* HTTP A/B real sobre ``fs_saas_runtime``: alta de empresa, login, aislamiento
  entre tenants y ejecucion de la cadena FastAPI completa.
* B9: aceptacion legitima de invitacion, token invalido, reuso, saturacion del
  limite por token (429 + ``Retry-After``) y ausencia de secretos en la tabla de
  contadores.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BP_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bp_pg17" / "lab_5bp_manifest.json"),
    )
)
CLAVE_SUPERADMIN = "5bp-clave-superadmin-laboratorio-0123456789abcdef"
PASSWORD = "Lab5bp#Clave2026"
PASSWORD_NUEVA = "Lab5bp#Nueva2026"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _dsn_sqlalchemy(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _sql(lab: dict, consulta: str, args=None):
    import psycopg

    with psycopg.connect(lab["admin_dsn"]) as conn:
        cursor = conn.execute(consulta, args)
        if cursor.description is None:
            return []
        return cursor.fetchall()


def _login(api, correo, password, slug, contexto="personal"):
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _crear_empresa(api, admin_cab, lab, etiqueta: str, plan: str) -> dict:
    sufijo = secrets.token_hex(4)
    planes = {p["codigo"]: p for p in api.get(
        "/saas/superadmin/planes", headers=admin_cab).json()}
    ciudad_id = _sql(lab, "SELECT id FROM public.ciudad WHERE estado IS TRUE "
                          "ORDER BY id LIMIT 1")[0][0]
    hoy = date.today()
    slug = f"lab5bp-{etiqueta}-{sufijo}"
    alta = api.post("/saas/superadmin/empresas", headers=admin_cab, json={
        "razon_social": f"Empresa {etiqueta.upper()} 5BP {sufijo}",
        "nombre_comercial": f"Tienda {etiqueta.upper()} {sufijo}",
        "slug": slug,
        "nit": f"NIT-{etiqueta}-{sufijo}",
        "correo_contacto": f"contacto.{etiqueta}.{sufijo}@lab.invalid",
        "telefono": None,
        "plan_id": planes[plan]["id"],
        "fecha_inicio": hoy.isoformat(),
        "fecha_vencimiento": hoy.replace(year=hoy.year + 1).isoformat(),
    })
    assert alta.status_code == 201, alta.text
    empresa_id = alta.json()["empresa"]["id"]
    correo = f"admin.{etiqueta}.{sufijo}@lab.invalid"
    primer = api.post(
        f"/saas/superadmin/empresas/{empresa_id}/administrador-inicial",
        headers=admin_cab,
        json={
            "correo": correo, "password": PASSWORD,
            "nombres": "Admin", "apellidos": etiqueta.upper(),
            "ci": f"CI-{etiqueta.upper()}-{sufijo}",
            "ciudad_id": ciudad_id,
            "nombre_sucursal": f"Casa {etiqueta.upper()}",
            "direccion_sucursal": f"Calle {etiqueta.upper()} 123",
            "telefono": None,
        },
    )
    assert primer.status_code == 201, primer.text
    entrada = _login(api, correo, PASSWORD, slug)
    assert entrada.status_code == 200, entrada.text
    datos = entrada.json()
    sucursal_id = int(_sql(
        lab, "SELECT id FROM public.sucursal WHERE empresa_id=%s ORDER BY id LIMIT 1",
        (empresa_id,))[0][0])
    return {
        "empresa_id": empresa_id, "slug": slug, "correo": correo,
        "token": datos["access_token"],
        "sucursal_id": sucursal_id,
        "rol_admin": int(_sql(lab, "SELECT id FROM public.rol WHERE nombre=%s",
                              ("ADMINISTRADOR",))[0][0]),
        "rol_cajero": int(_sql(lab, "SELECT id FROM public.rol WHERE nombre=%s",
                               ("CAJERO",))[0][0]),
    }


def _alta_personal(api, empresa: dict, correo: str, rol_id: int) -> httpx.Response:
    return api.post(
        "/tenant/empresa/usuarios",
        headers=_cabecera(empresa["token"]),
        json={
            "correo": correo, "nombres": "Personal", "apellidos": "5BP",
            "ci": f"CI-{secrets.token_hex(4)}", "telefono": None,
            "sucursal_id": empresa["sucursal_id"], "rol_id": rol_id,
            "expira_horas": 24,
        },
    )


def _aceptar(api, token: str, password: str = PASSWORD_NUEVA) -> httpx.Response:
    return api.post("/auth/tenant/invitaciones/aceptar", json={
        "token": token, "password": password, "password_confirmacion": password,
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio PostgreSQL 17 (5BP) no aprovisionado; ejecute "
            "database/saas/90_laboratorio_pg17_5bp.py --init --confirm-desechable")
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    cluster = datos["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"} or cluster["puerto"] != 55446:
        pytest.skip("El manifiesto no corresponde al laboratorio desechable 5BP")
    if "lab" not in cluster["base"].lower():
        pytest.skip("El manifiesto no apunta a una base descartable")
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    env = {
        **os.environ,
        "DATABASE_URL": _dsn_sqlalchemy(lab["runtime_dsn"]),
        "SAAS_ADMIN_DATABASE_URL": _dsn_sqlalchemy(lab["admin_runtime_dsn"]),
        "SECRET_KEY": secrets.token_urlsafe(32),
        "SAAS_AUTH_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "true",
        "SAAS_SUPERADMIN_KEY_SHA256": hashlib.sha256(
            CLAVE_SUPERADMIN.encode()).hexdigest(),
        "SAAS_RESERVAS_ENABLED": "true",
        "SAAS_VENTAS_ENABLED": "true",
        "SAAS_COMPRAS_ENABLED": "true",
        "SAAS_DEVOLUCIONES_ENABLED": "true",
        "SAAS_TENANT_ASSERTION_KEY": lab["assertion_key"],
        # B9: limite por token bajo para poder saturarlo sin bloquear la IP.
        "SAAS_INVITATION_RATE_LIMIT_ENABLED": "true",
        "SAAS_INVITATION_RATE_LIMIT_IP": "100",
        "SAAS_INVITATION_RATE_LIMIT_TOKEN": "3",
        "SAAS_INVITATION_RATE_LIMIT_VENTANA_SEGUNDOS": "120",
        "APP_ENV": "lab-5bp",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bp_{puerto}.log"
    registro.parent.mkdir(parents=True, exist_ok=True)
    salida = open(registro, "w", encoding="utf-8")
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=salida, stderr=salida,
    )
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
        contenido = registro.read_text(encoding="utf-8", errors="replace")[-4000:]
        pytest.fail(f"uvicorn no arranco sobre PostgreSQL 17: {contenido}")
    yield {"base": base, "proceso": proceso}
    proceso.terminate()
    try:
        proceso.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proceso.kill()
    salida.close()


@pytest.fixture(scope="session")
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=90.0) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def admin_cab():
    return {"X-SaaS-Admin-Key": CLAVE_SUPERADMIN}


@pytest.fixture(scope="session")
def empresas(api, admin_cab, lab):
    a = _crear_empresa(api, admin_cab, lab, "a", "PROFESIONAL")
    b = _crear_empresa(api, admin_cab, lab, "b", "BASICO")
    return {"a": a, "b": b}


# ---------------------------------------------------------------------------
# T1 - Motor y pipeline
# ---------------------------------------------------------------------------


def test_pg17_version_real(lab):
    version = _sql(lab, "SHOW server_version")[0][0]
    assert version.startswith("17."), f"Se esperaba PostgreSQL 17 y hay {version}"
    assert lab["cluster"]["version"].startswith("PostgreSQL 17")
    assert "17" in lab["cluster"]["binarios"]["pg_dump"]


def test_pipeline_completo_y_registrado(lab):
    fases = _sql(lab, "SELECT fase, orden FROM saas_migracion.version_aplicada "
                      "ORDER BY orden")
    assert len(fases) == 15, f"Fases aplicadas: {len(fases)}"
    ids = [f[0] for f in fases]
    assert ids[0].startswith("F00")
    assert ids[-1].startswith("F14")
    assert ids.index("F13-invitaciones-01000") > ids.index("F12-superadmin-00900")


def test_registro_conserva_checksums(lab):
    filas = _sql(lab, "SELECT checksum, archivos FROM saas_migracion.version_aplicada")
    assert filas
    for checksum, archivos in filas:
        assert len(checksum) == 64, "Cada fase debe registrar un SHA-256 compuesto"
        assert json.loads(archivos), "Cada fase debe registrar sus archivos"


def test_esquema_saas_presente(lab):
    conteos = _sql(lab, """
        SELECT
          (SELECT count(*) FROM information_schema.columns
            WHERE column_name='empresa_id' AND table_schema='public'),
          (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='r' AND n.nspname='public' AND c.relforcerowsecurity),
          (SELECT count(*) FROM pg_policies WHERE schemaname='saas')
    """)[0]
    assert conteos[0] >= 31
    assert conteos[1] >= 31
    assert conteos[2] >= 2


# ---------------------------------------------------------------------------
# T2 - Separacion de principales en PostgreSQL 17
# ---------------------------------------------------------------------------


def test_runtime_no_inserta_usuario(lab):
    import psycopg

    with pytest.raises(psycopg.Error) as excinfo:
        with psycopg.connect(lab["runtime_dsn"], autocommit=True) as conn:
            conn.execute(
                "INSERT INTO public.usuario (rol_id, correo, password_hash, "
                "fecha_creacion, estado) VALUES (1, 'prohibido.5bp@lab.invalid', "
                "'x', NOW(), TRUE)")
    assert getattr(excinfo.value, "sqlstate", None) == "42501"


def test_admin_global_sin_privilegios_de_tabla(lab):
    grants = _sql(
        lab, "SELECT count(*) FROM information_schema.role_table_grants "
             "WHERE grantee = %s", (lab["roles"]["admin_runtime"],))[0][0]
    assert grants == 0
    ejecuta_sp = _sql(
        lab,
        "SELECT has_function_privilege(%s, "
        "'public.sp_alta_personal_tenant(bigint,bigint,text,text,text,text,text,"
        "bigint,bigint,text,text,timestamp with time zone)', 'EXECUTE')",
        (lab["roles"]["admin_runtime"],))[0][0]
    assert ejecuta_sp is False
    ejecuta_b9 = _sql(
        lab,
        "SELECT has_function_privilege(%s, "
        "'saas_admin.estado_invitacion(text,text,integer,integer,integer)', "
        "'EXECUTE')",
        (lab["roles"]["admin_runtime"],))[0][0]
    assert ejecuta_b9 is True


def test_definer_y_search_path(lab):
    filas = dict(_sql(lab, """
        SELECT p.proname, p.prosecdef
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname='public' AND p.proname='sp_alta_personal_tenant'
    """))
    assert filas["sp_alta_personal_tenant"] is True
    duenio, configuracion = _sql(lab, """
        SELECT pg_get_userbyid(p.proowner),
               COALESCE(array_to_string(p.proconfig, ','), '<sin>')
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname='public' AND p.proname='sp_alta_personal_tenant'
    """)[0]
    assert duenio == "saas_personal_owner"
    assert "search_path" in configuracion
    nologin = _sql(lab, """
        SELECT rolname, rolcanlogin FROM pg_roles
         WHERE rolname IN ('saas_admin_owner','saas_personal_owner',
                           'saas_rls_owner','saas_actor_sql_owner')
         ORDER BY rolname
    """)
    assert len(nologin) == 4
    assert all(fila[1] is False for fila in nologin)


# ---------------------------------------------------------------------------
# T3 - HTTP A/B sobre el motor 17
# ---------------------------------------------------------------------------


def test_login_ab_y_aislamiento(api, empresas):
    a, b = empresas["a"], empresas["b"]
    entrada_a = _login(api, a["correo"], PASSWORD, a["slug"])
    entrada_b = _login(api, b["correo"], PASSWORD, b["slug"])
    assert entrada_a.status_code == 200, entrada_a.text
    assert entrada_b.status_code == 200, entrada_b.text
    assert entrada_a.json()["empresa_id"] == a["empresa_id"]
    assert entrada_b.json()["empresa_id"] == b["empresa_id"]
    # Un administrador de A no puede autenticarse contra el tenant B.
    cruzado = _login(api, a["correo"], PASSWORD, b["slug"])
    assert cruzado.status_code == 401, cruzado.text

    lista_a = api.get("/tenant/empresa/usuarios", headers=_cabecera(a["token"]))
    lista_b = api.get("/tenant/empresa/usuarios", headers=_cabecera(b["token"]))
    assert lista_a.status_code == 200 and lista_b.status_code == 200
    ids_a = {fila["usuario_id"] for fila in lista_a.json()}
    ids_b = {fila["usuario_id"] for fila in lista_b.json()}
    assert ids_a.isdisjoint(ids_b), "Los tenants comparten usuarios: RLS rota"


def test_catalogo_tenant_responde(api, empresas):
    respuesta = api.get("/tenant/catalogo/productos",
                        headers=_cabecera(empresas["a"]["token"]))
    assert respuesta.status_code in (200, 404)
    sin_token = api.get("/tenant/catalogo/productos")
    assert sin_token.status_code in (401, 403)


# ---------------------------------------------------------------------------
# T4 - B9: rate limit de invitaciones
# ---------------------------------------------------------------------------


def _limpiar_contadores(lab):
    _sql(lab, "DELETE FROM saas_admin.intento_invitacion")


def test_aceptacion_legitima_funciona(api, lab, empresas):
    a = empresas["a"]
    correo = f"invitado.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo, a["rol_cajero"])
    assert alta.status_code == 201, alta.text
    token = alta.json()["invitacion"]["token"]
    assert len(token) >= 43

    _limpiar_contadores(lab)
    aceptada = _aceptar(api, token)
    assert aceptada.status_code == 200, aceptada.text
    assert aceptada.json()["aceptada"] is True
    assert _login(api, correo, PASSWORD_NUEVA, a["slug"]).status_code == 200

    contadores = _sql(lab, "SELECT resultado, count(*) FROM saas_admin.intento_invitacion "
                           "GROUP BY 1")
    assert dict(contadores) == {"OK": 1}


def test_token_invalido_no_revela_y_registra(api, lab, empresas):
    _limpiar_contadores(lab)
    a = empresas["a"]
    correo = f"invitado.{secrets.token_hex(4)}@lab.invalid"
    assert _alta_personal(api, a, correo, a["rol_cajero"]).status_code == 201

    inventado = secrets.token_urlsafe(40)
    respuesta = _aceptar(api, inventado)
    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Invitación inválida o expirada"
    assert inventado not in respuesta.text

    huella = hashlib.sha256(inventado.encode()).hexdigest()[:16]
    filas = _sql(lab, "SELECT ip_hash, token_huella, resultado "
                      "FROM saas_admin.intento_invitacion WHERE token_huella=%s",
                 (huella,))
    assert len(filas) == 1
    assert filas[0][2] == "FALLA"
    assert len(filas[0][0]) == 32 and len(filas[0][1]) == 16


def test_saturacion_por_token_devuelve_429(api, lab, empresas):
    _limpiar_contadores(lab)
    a = empresas["a"]
    correo = f"invitado.{secrets.token_hex(4)}@lab.invalid"
    assert _alta_personal(api, a, correo, a["rol_cajero"]).status_code == 201

    token_atacado = secrets.token_urlsafe(40)  # nunca existio
    codigos = [_aceptar(api, token_atacado).status_code for _ in range(3)]
    assert codigos == [400, 400, 400], codigos

    bloqueada = _aceptar(api, token_atacado)
    assert bloqueada.status_code == 429
    assert int(bloqueada.headers["Retry-After"]) >= 1
    assert token_atacado not in bloqueada.text

    # Un invitado distinto (otro token) sigue pudiendo aceptar: el limite por
    # token no bloquea a terceros y la IP tiene cupo amplio en este laboratorio.
    correo_nuevo = f"invitado.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo_nuevo, a["rol_cajero"])
    assert alta.status_code == 201, alta.text
    valido = alta.json()["invitacion"]["token"]
    assert _aceptar(api, valido).status_code == 200

    fallas = _sql(lab, "SELECT count(*) FROM saas_admin.intento_invitacion "
                       "WHERE resultado='FALLA'")[0][0]
    assert fallas == 3, "Los intentos bloqueados no deben seguir sumando fallas"


def test_reuso_del_token_falla(api, lab, empresas):
    _limpiar_contadores(lab)
    a = empresas["a"]
    correo = f"invitado.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo, a["rol_cajero"])
    token = alta.json()["invitacion"]["token"]
    assert _aceptar(api, token).status_code == 200
    segundo = _aceptar(api, token, "OtraClave#2026")
    assert segundo.status_code == 400
    assert segundo.json()["detail"] == "Invitación inválida o expirada"


def test_tabla_de_contadores_sin_secretos(lab):
    columnas = {fila[0] for fila in _sql(lab, """
        SELECT column_name FROM information_schema.columns
         WHERE table_schema='saas_admin' AND table_name='intento_invitacion'
    """)}
    assert "token" not in columnas
    assert "password" not in columnas
    permisos = _sql(
        lab, "SELECT has_table_privilege(%s, 'saas_admin.intento_invitacion', "
             "'INSERT'), has_table_privilege(%s, "
             "'saas_admin.intento_invitacion', 'SELECT')",
        (lab["roles"]["runtime"], lab["roles"]["runtime"]))[0]
    assert permisos == (False, False)
