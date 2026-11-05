"""5BO / ETAPA F - Nucleo SaaS: personal, RBAC nominal, superadmin y limites.

Requisitos
----------
Laboratorio 5BO aprovisionado (cluster NUEVO en 55445, distinto de E1/5BM y
E2/5BN)::

    python database/saas/78_laboratorio_operativo_5bo.py --init --confirm-desechable

Si el manifiesto no existe, la bateria se OMITE (nunca se simula con SQLite).

Que prueba
----------
* P0 (CU03/CU04): alta real de personal con invitacion de un solo uso,
  asignacion de rol y sucursal empresariales, revocacion/reactivacion,
  aislamiento A/B, rechazo de escalada global, no secuestro de cuentas y
  conservacion del ultimo administrador bajo concurrencia.
* P0 RBAC: matriz nominal por empresa (excepcion > default > denegado),
  permiso efectivo en backend y ADMINISTRADOR no editable.
* P1 (CU01/CU02): credencial global con rotacion de dos claves, rate limiting
  COMPARTIDO en PostgreSQL, auditoria global sin secretos y separacion de
  principales SQL.
* P2 (CU02/CU05): cupos BASICO 2 / PROFESIONAL 10, concurrencia, downgrade
  rechazado, suspension/renovacion y capacidades del plan.

Las pruebas HTTP usan `uvicorn` real sobre TCP y el principal SQL restringido
(`fs_saas_runtime`); las verificaciones puntuales usan el administrador del
laboratorio para leer filas persistidas y privilegios por catalogo.
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
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BO_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bo" / "lab_5bo_manifest.json"),
    )
)
CLAVE_NUEVA = "5bo-clave-nueva-de-laboratorio-0123456789abcdef"
CLAVE_ANTERIOR = "5bo-clave-anterior-laboratorio-abcdef0123456789"
PASSWORD = "Lab5bo#Clave2026"
PASSWORD_NUEVA = "Lab5bo#Nueva2026"


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


def _sql_runtime(lab: dict, consulta: str, args=None):
    """Ejecuta como el principal restringido para probar denegaciones reales."""
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        return conn.execute(consulta, args).fetchall()


def _hash(password: str) -> str:
    from app.core.security import hash_password

    return hash_password(password)


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(api, correo, password, slug, contexto="personal"):
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def _rol_id(lab: dict, nombre: str) -> int:
    return int(_sql(
        lab, "SELECT id FROM public.rol WHERE nombre=%s AND estado IS TRUE",
        (nombre,))[0][0])


def _crear_empresa(api, admin_cab, lab, etiqueta: str, plan: str) -> dict:
    sufijo = secrets.token_hex(4)
    planes = {p["codigo"]: p for p in api.get(
        "/saas/superadmin/planes", headers=admin_cab).json()}
    ciudad_id = _sql(lab, "SELECT id FROM public.ciudad WHERE estado IS TRUE "
                          "ORDER BY id LIMIT 1")[0][0]
    hoy = date.today()
    slug = f"lab5bo-{etiqueta}-{sufijo}"
    alta = api.post("/saas/superadmin/empresas", headers=admin_cab, json={
        "razon_social": f"Empresa {etiqueta.upper()} 5BO {sufijo}",
        "nombre_comercial": f"Tienda {etiqueta.upper()} {sufijo}",
        "slug": slug,
        "nit": f"NIT-{etiqueta}-{sufijo}",
        "correo_contacto": f"contacto.{etiqueta}.{sufijo}@lab.invalid",
        "telefono": None,
        "plan_id": planes[plan]["id"],
        "fecha_inicio": hoy.isoformat(),
        "fecha_vencimiento": (hoy.replace(year=hoy.year + 1)).isoformat(),
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
        })
    assert primer.status_code == 201, primer.text
    sesion = _login(api, correo, PASSWORD, slug)
    assert sesion.status_code == 200, sesion.text
    return {
        "empresa_id": empresa_id,
        "slug": slug,
        "sufijo": sufijo,
        "correo": correo,
        "sucursal_id": primer.json()["sucursal_id"],
        "admin_usuario_id": primer.json()["usuario_id"],
        "plan": plan,
        "plan_id": planes[plan]["id"],
        "suscripcion_id": alta.json()["suscripcion"]["id"],
        "token": sesion.json()["access_token"],
        "ciudad_id": ciudad_id,
    }


def _alta_personal(api, empresa: dict, *, correo: str, rol_id: int,
                   sucursal_id: int | None = None, ci: str | None = None,
                   expira_horas: int = 72):
    return api.post(
        "/tenant/empresa/usuarios",
        headers=_cabecera(empresa["token"]),
        json={
            "correo": correo,
            "nombres": "Personal",
            "apellidos": "5BO",
            "ci": ci or f"CI-{secrets.token_hex(4)}",
            "telefono": None,
            "sucursal_id": sucursal_id if sucursal_id is not None else empresa["sucursal_id"],
            "rol_id": rol_id,
            "expira_horas": expira_horas,
        },
    )


def _aceptar(api, token: str, password: str = PASSWORD_NUEVA):
    return api.post("/auth/tenant/invitaciones/aceptar", json={
        "token": token,
        "password": password,
        "password_confirmacion": password,
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BO no aprovisionado; ejecute "
            "database/saas/78_laboratorio_operativo_5bo.py --init --confirm-desechable"
        )
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    from tests_saas._lab_readiness_5bs import motivo_no_disponible

    motivo = motivo_no_disponible(
        datos, 55445, "5BO",
        "python database/saas/101_runner_regresion_reproducible_5bs.py --lab 5bo")
    if motivo:
        pytest.skip(motivo)
    if not datos.get("migraciones_5bo"):
        pytest.skip("El laboratorio no tiene aplicadas las migraciones 5BO")
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
        # Dos claves vigentes: rotacion real con identificador (v1/v2).
        "SAAS_SUPERADMIN_KEY_SHA256": (
            f"v2:{hashlib.sha256(CLAVE_NUEVA.encode()).hexdigest()},"
            f"v1:{hashlib.sha256(CLAVE_ANTERIOR.encode()).hexdigest()}"
        ),
        "SAAS_SUPERADMIN_RATE_LIMIT_IP": "6",
        "SAAS_SUPERADMIN_RATE_LIMIT_CLAVE": "8",
        "SAAS_SUPERADMIN_RATE_LIMIT_VENTANA_SEGUNDOS": "120",
        "SAAS_RESERVAS_ENABLED": "true",
        "SAAS_VENTAS_ENABLED": "true",
        "SAAS_COMPRAS_ENABLED": "true",
        "SAAS_DEVOLUCIONES_ENABLED": "true",
        "SAAS_TENANT_ASSERTION_KEY": lab["assertion_key"],
        "SAAS_ACTOR_SQL_OWNER": lab["roles"]["actor_owner"],
        "APP_ENV": "lab-5bo",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bo_{puerto}.log"
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
        pytest.fail(f"uvicorn no arranco con los principales 5BO: {contenido}")
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
    return {"X-SaaS-Admin-Key": CLAVE_NUEVA, "X-SaaS-Admin-Key-Id": "v2"}


@pytest.fixture(scope="session")
def admin_cab_anterior():
    return {"X-SaaS-Admin-Key": CLAVE_ANTERIOR, "X-SaaS-Admin-Key-Id": "v1"}


@pytest.fixture(scope="session")
def circuito(api, admin_cab, lab):
    """Dos empresas con planes distintos y administradores propios."""
    a = _crear_empresa(api, admin_cab, lab, "a", "PROFESIONAL")
    b = _crear_empresa(api, admin_cab, lab, "b", "BASICO")
    a["rol_admin"] = _rol_id(lab, "ADMINISTRADOR")
    a["rol_cajero"] = _rol_id(lab, "CAJERO")
    a["rol_cliente"] = _rol_id(lab, "CLIENTE")
    b["rol_admin"] = a["rol_admin"]
    b["rol_cajero"] = a["rol_cajero"]
    b["rol_cliente"] = a["rol_cliente"]
    return {"a": a, "b": b}


@pytest.fixture(autouse=True)
def limpiar_contadores(lab):
    """Aisla el rate limit entre pruebas: los contadores son por proceso/base."""
    _sql(lab, "DELETE FROM saas_admin.intento_auth")
    yield
    _sql(lab, "DELETE FROM saas_admin.intento_auth")


# ---------------------------------------------------------------------------
# P0 - CU03/CU04: alta, invitacion, rol y sucursal
# ---------------------------------------------------------------------------


def test_p0_alta_personal_invitacion_un_solo_uso_y_login(api, lab, circuito):
    empresa = circuito["a"]
    correo = f"nuevo.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, empresa, correo=correo, rol_id=empresa["rol_cajero"])
    assert alta.status_code == 201, alta.text
    datos = alta.json()
    assert datos["correo"] == correo
    assert datos["rol"] == "CAJERO"
    assert datos["sucursal_id"] == empresa["sucursal_id"]
    assert datos["estado_membresia"] is True
    token = datos["invitacion"]["token"]
    assert len(token) >= 32
    assert datos["invitacion"]["uso"] == "unico"
    # Ninguna contrasena viaja ni se devuelve.
    assert "password" not in json.dumps(datos).lower()

    filas = _sql(
        lab,
        "SELECT u.rol_id, r.nombre, ue.rol_id, ur.nombre, em.sucursal_id, "
        "       i.usada_en, i.token_hash "
        "  FROM public.usuario u "
        "  JOIN public.rol r ON r.id = u.rol_id "
        "  JOIN public.usuario_empresa ue ON ue.usuario_id = u.id "
        "  JOIN public.rol ur ON ur.id = ue.rol_id "
        "  JOIN public.empleado em ON em.usuario_id = u.id "
        "  JOIN saas.invitacion_personal i ON i.usuario_id = u.id "
        " WHERE u.correo = %s",
        (correo,),
    )
    assert len(filas) == 1
    rol_global_codigo, rol_global, rol_empresa_id, rol_empresa, sucursal_id, usada, token_hash = filas[0]
    # El rol GLOBAL permanece CLIENTE: no hay escalada cross-tenant.
    assert rol_global == "CLIENTE"
    assert int(rol_global_codigo) == empresa["rol_cliente"]
    assert rol_empresa == "CAJERO"
    assert int(rol_empresa_id) == empresa["rol_cajero"]
    assert int(sucursal_id) == empresa["sucursal_id"]
    assert usada is None
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()

    # Antes de aceptar no hay contrasena utilizable.
    assert _login(api, correo, "Cualquiera#2026", empresa["slug"]).status_code == 401

    aceptada = _aceptar(api, token)
    assert aceptada.status_code == 200, aceptada.text
    assert aceptada.json()["aceptada"] is True

    sesion = _login(api, correo, PASSWORD_NUEVA, empresa["slug"])
    assert sesion.status_code == 200, sesion.text
    assert sesion.json()["rol"] == "CAJERO"
    cajero_token = sesion.json()["access_token"]

    # Segundo uso del mismo token: rechazado y sin filtrar informacion.
    repetida = _aceptar(api, token)
    assert repetida.status_code == 400
    assert repetida.json()["detail"] == "Invitación inválida o expirada"

    # El CAJERO existe y opera como personal, pero no administra.
    me = api.get("/auth/tenant/me", headers=_cabecera(cajero_token))
    assert me.status_code == 200
    assert me.json()["rol"] == "CAJERO"

    # Conteos de bitacora del alta (una fila por evento, sin duplicar triggers).
    eventos = _sql(
        lab,
        "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s AND accion='PERSONAL_ALTA'",
        (empresa["empresa_id"],),
    )[0][0]
    assert int(eventos) >= 1


def test_p0_invitacion_expirada_rechazada(api, lab, circuito):
    empresa = circuito["a"]
    correo = f"expirado.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, empresa, correo=correo, rol_id=empresa["rol_cajero"])
    assert alta.status_code == 201, alta.text
    token = alta.json()["invitacion"]["token"]
    _sql(
        lab,
        "UPDATE saas.invitacion_personal "
        "SET creada_en = now() - interval '2 days', "
        "    expira_en = now() - interval '1 day' "
        "WHERE usuario_id = %s",
        (alta.json()["usuario_id"],),
    )
    respuesta = _aceptar(api, token)
    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Invitación inválida o expirada"
    assert _login(api, correo, PASSWORD_NUEVA, empresa["slug"]).status_code == 401


def test_p0_cajero_no_administra_y_b_no_administra_a(api, lab, circuito):
    a, b = circuito["a"], circuito["b"]
    correo = f"cajero.a.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo=correo, rol_id=a["rol_cajero"])
    assert alta.status_code == 201, alta.text
    assert _aceptar(api, alta.json()["invitacion"]["token"]).status_code == 200
    sesion = _login(api, correo, PASSWORD_NUEVA, a["slug"])
    assert sesion.status_code == 200
    cajero = sesion.json()["access_token"]
    usuario_a = alta.json()["usuario_id"]

    # CAJERO no puede usar la administracion de personal ni la matriz RBAC.
    assert api.post("/tenant/empresa/usuarios", headers=_cabecera(cajero), json={
        "correo": f"x.{secrets.token_hex(3)}@lab.invalid", "nombres": "X",
        "apellidos": "Y", "ci": "CI-X", "sucursal_id": a["sucursal_id"],
        "rol_id": a["rol_cajero"],
    }).status_code == 403
    assert api.get("/tenant/empresa/permisos",
                   headers=_cabecera(cajero)).status_code == 403
    assert api.patch(f"/tenant/empresa/usuarios/{usuario_a}/sucursal",
                     headers=_cabecera(cajero),
                     json={"sucursal_id": a["sucursal_id"]}).status_code == 403
    assert api.patch(f"/tenant/empresa/roles/{a['rol_cajero']}/permisos",
                     headers=_cabecera(cajero),
                     json={"permisos": [{"funcion": "VENTAS", "accion": "REGISTRAR",
                                         "permitido": True}]}).status_code == 403

    # B no administra usuarios de A: todos los recursos ajenos son 404 neutros.
    assert api.patch(f"/tenant/empresa/usuarios/{usuario_a}/rol",
                     headers=_cabecera(b["token"]),
                     json={"rol_id": b["rol_cajero"]}).status_code == 404
    assert api.patch(f"/tenant/empresa/usuarios/{usuario_a}/sucursal",
                     headers=_cabecera(b["token"]),
                     json={"sucursal_id": b["sucursal_id"]}).status_code == 404
    assert api.patch(f"/tenant/empresa/usuarios/{usuario_a}/estado",
                     headers=_cabecera(b["token"]),
                     json={"estado": False}).status_code == 404

    # A sigue viendo a su propio personal; B no ve al personal de A.
    lista_b = api.get("/tenant/empresa/usuarios", headers=_cabecera(b["token"]))
    assert lista_b.status_code == 200
    assert all(int(fila["usuario_id"]) != usuario_a for fila in lista_b.json())
    lista_a = api.get("/tenant/empresa/usuarios", headers=_cabecera(a["token"]))
    assert any(int(fila["usuario_id"]) == usuario_a for fila in lista_a.json())


def test_p0_sin_escalada_global_ni_recursos_de_otra_empresa(api, lab, circuito):
    a, b = circuito["a"], circuito["b"]

    # 1) El principal tenant no puede escribir public.usuario directamente.
    privilegios = _sql(
        lab,
        "SELECT has_table_privilege('fs_saas_runtime','public.usuario','INSERT'), "
        "       has_table_privilege('fs_saas_runtime','public.usuario','UPDATE'), "
        "       has_schema_privilege('fs_saas_runtime','saas_admin','USAGE')",
    )[0]
    assert privilegios[0] is False
    assert privilegios[1] is False
    assert privilegios[2] is False

    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.usuario (rol_id, correo, password_hash, "
                "fecha_creacion, estado) VALUES (%s, %s, %s, now(), TRUE)",
                (a["rol_cliente"], f"directo.{secrets.token_hex(3)}@lab.invalid",
                 _hash("Directo#2026")),
            )
        conn.rollback()

    # 2) Sucursal de otra empresa: 404 indistinguible.
    ajeno = _alta_personal(api, a, correo=f"suc.{secrets.token_hex(4)}@lab.invalid",
                           rol_id=a["rol_cajero"], sucursal_id=b["sucursal_id"])
    assert ajeno.status_code == 404
    assert ajeno.json()["detail"] == "Sucursal no disponible"

    # 3) Rol exclusivo de otra empresa: no asignable.
    sufijo = secrets.token_hex(4)
    rol_ajeno = _sql(
        lab,
        "INSERT INTO public.rol (nombre, estado) VALUES (%s, TRUE) RETURNING id",
        (f"ROL-B-{sufijo.upper()}",),
    )[0][0]
    correo_b = f"rolb.{sufijo}@lab.invalid"
    usuario_b = _sql(
        lab,
        "INSERT INTO public.usuario (rol_id, correo, password_hash, "
        "fecha_creacion, estado) VALUES (%s,%s,%s,now(),TRUE) RETURNING id",
        (b["rol_cliente"], correo_b, _hash(PASSWORD)),
    )[0][0]
    _sql(
        lab,
        "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
        "VALUES (%s,%s,%s,TRUE)",
        (usuario_b, b["empresa_id"], rol_ajeno),
    )
    _sql(
        lab,
        "INSERT INTO public.empleado (empresa_id, usuario_id, sucursal_id, nombres, "
        "apellidos, ci, fecha_contratacion, estado) "
        "VALUES (%s,%s,%s,'Rol','Ajeno',%s,CURRENT_DATE,TRUE)",
        (b["empresa_id"], usuario_b, b["sucursal_id"], f"CI-RA-{sufijo}"),
    )
    intento_rol = _alta_personal(api, a, correo=f"rol.{sufijo}@lab.invalid",
                                 rol_id=rol_ajeno)
    assert intento_rol.status_code == 422
    assert intento_rol.json()["detail"] == "Rol no asignable al personal de esta empresa"

    # 4) CLIENTE nunca es rol de personal.
    cliente = _alta_personal(api, a, correo=f"cli.{sufijo}@lab.invalid",
                             rol_id=a["rol_cliente"])
    assert cliente.status_code == 422

    # 5) El cuerpo no puede fijar empresa, actor ni rol global.
    con_empresa = api.post(
        "/tenant/empresa/usuarios", headers=_cabecera(a["token"]),
        json={
            "correo": f"extra.{sufijo}@lab.invalid", "nombres": "X",
            "apellidos": "Y", "ci": "CI-Z", "sucursal_id": a["sucursal_id"],
            "rol_id": a["rol_cajero"], "empresa_id": b["empresa_id"],
        })
    assert con_empresa.status_code == 422

    # 6) Ni un solo usuario creado por los intentos rechazados.
    total = _sql(
        lab,
        "SELECT count(*) FROM public.usuario WHERE correo IN (%s,%s,%s)",
        (f"suc.{sufijo}@lab.invalid", f"rol.{sufijo}@lab.invalid",
         f"cli.{sufijo}@lab.invalid"),
    )[0][0]
    assert int(total) == 0


def test_p0_correo_existente_no_revela_ni_secuestra(api, lab, circuito):
    a, b = circuito["a"], circuito["b"]
    correo = f"duplicado.{secrets.token_hex(4)}@lab.invalid"
    primera = _alta_personal(api, a, correo=correo, rol_id=a["rol_cajero"])
    assert primera.status_code == 201, primera.text

    desde_a = _alta_personal(api, a, correo=correo, rol_id=a["rol_cajero"])
    desde_b = _alta_personal(api, b, correo=correo, rol_id=b["rol_cajero"])
    assert desde_a.status_code == 409
    assert desde_b.status_code == 409
    # Respuesta identica: no revela en que empresa vive la cuenta.
    assert desde_a.json() == desde_b.json()
    texto = json.dumps(desde_b.json()).lower()
    for prohibido in ("empresa", "slug", "usuario_id", "lab5bo", "tenant"):
        assert prohibido not in texto

    # No se creo membresia ni empleado en B para ese correo.
    filas = _sql(
        lab,
        "SELECT count(*) FROM public.usuario_empresa ue "
        "  JOIN public.usuario u ON u.id = ue.usuario_id "
        " WHERE u.correo = %s AND ue.empresa_id = %s",
        (correo, b["empresa_id"]),
    )[0][0]
    assert int(filas) == 0
    total = _sql(lab, "SELECT count(*) FROM public.usuario WHERE correo=%s",
                 (correo,))[0][0]
    assert int(total) == 1


def test_p0_revocacion_reactivacion_y_ultimo_admin(api, lab, circuito):
    a = circuito["a"]
    correo = f"revocado.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo=correo, rol_id=a["rol_cajero"])
    assert alta.status_code == 201
    assert _aceptar(api, alta.json()["invitacion"]["token"]).status_code == 200
    sesion = _login(api, correo, PASSWORD_NUEVA, a["slug"])
    assert sesion.status_code == 200
    token_cajero = sesion.json()["access_token"]
    usuario_id = alta.json()["usuario_id"]

    revocado = api.patch(f"/tenant/empresa/usuarios/{usuario_id}/estado",
                         headers=_cabecera(a["token"]), json={"estado": False})
    assert revocado.status_code == 200, revocado.text
    assert revocado.json()["membresia_activa"] is False
    # El JWT previo deja de servir en la siguiente peticion (revalidacion SQL).
    assert api.get("/auth/tenant/me",
                   headers=_cabecera(token_cajero)).status_code == 403
    assert _login(api, correo, PASSWORD_NUEVA, a["slug"]).status_code == 401

    reactivado = api.patch(f"/tenant/empresa/usuarios/{usuario_id}/estado",
                           headers=_cabecera(a["token"]), json={"estado": True})
    assert reactivado.status_code == 200
    assert _login(api, correo, PASSWORD_NUEVA, a["slug"]).status_code == 200

    # El administrador no puede desactivarse a si mismo.
    propio = api.patch(f"/tenant/empresa/usuarios/{a['admin_usuario_id']}/estado",
                       headers=_cabecera(a["token"]), json={"estado": False})
    assert propio.status_code == 403

    # Dos administradores se degradan mutuamente en paralelo: la empresa debe
    # conservar al menos uno. El lock por empresa serializa y el recuento
    # revalida en SQL.
    sufijo = secrets.token_hex(4)
    segundo_correo = f"admin2.{sufijo}@lab.invalid"
    segundo = _alta_personal(api, a, correo=segundo_correo, rol_id=a["rol_admin"])
    assert segundo.status_code == 201, segundo.text
    assert _aceptar(api, segundo.json()["invitacion"]["token"]).status_code == 200
    sesion2 = _login(api, segundo_correo, PASSWORD_NUEVA, a["slug"])
    assert sesion2.status_code == 200
    token2 = sesion2.json()["access_token"]
    admin2_id = segundo.json()["usuario_id"]

    def degradar(token, objetivo):
        return api.patch(f"/tenant/empresa/usuarios/{objetivo}/rol",
                         headers=_cabecera(token),
                         json={"rol_id": a["rol_cajero"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futuros = [
            pool.submit(degradar, a["token"], admin2_id),
            pool.submit(degradar, token2, a["admin_usuario_id"]),
        ]
        codigos = sorted(f.result().status_code for f in futuros)
    assert codigos[0] == 200, codigos
    assert codigos[1] in (403, 409), codigos
    admins = _sql(
        lab,
        "SELECT count(*) FROM public.usuario_empresa ue "
        "  JOIN public.rol r ON r.id = ue.rol_id "
        "  JOIN public.usuario u ON u.id = ue.usuario_id "
        "  JOIN public.empleado em ON em.usuario_id = ue.usuario_id "
        "    AND em.empresa_id = ue.empresa_id AND em.estado IS TRUE "
        " WHERE ue.empresa_id=%s AND ue.estado IS TRUE AND u.estado IS TRUE "
        "   AND r.nombre='ADMINISTRADOR'",
        (a["empresa_id"],),
    )[0][0]
    assert int(admins) >= 1

    # Restituir el estado del circuito para las pruebas siguientes.
    _sql(
        lab,
        "UPDATE public.usuario_empresa SET rol_id=%s "
        "WHERE empresa_id=%s AND usuario_id IN (%s,%s)",
        (a["rol_admin"], a["empresa_id"], a["admin_usuario_id"], admin2_id),
    )


def test_p0_cambio_de_sucursal_propia(api, lab, circuito):
    a = circuito["a"]
    segunda = api.post("/tenant/empresa/sucursales", headers=_cabecera(a["token"]),
                       json={"ciudad_id": a["ciudad_id"],
                             "nombre": f"Sucursal 5BO {secrets.token_hex(3)}",
                             "direccion": "Av. Laboratorio 456", "telefono": None})
    assert segunda.status_code == 201, segunda.text
    nueva_sucursal = segunda.json()["id"]

    correo = f"mueve.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, a, correo=correo, rol_id=a["rol_cajero"])
    assert alta.status_code == 201
    usuario_id = alta.json()["usuario_id"]

    cambio = api.patch(f"/tenant/empresa/usuarios/{usuario_id}/sucursal",
                       headers=_cabecera(a["token"]),
                       json={"sucursal_id": nueva_sucursal})
    assert cambio.status_code == 200, cambio.text
    assert cambio.json()["sucursal_id"] == nueva_sucursal

    # Sucursal de otra empresa: 404 neutro y sin cambios.
    ajeno = api.patch(f"/tenant/empresa/usuarios/{usuario_id}/sucursal",
                      headers=_cabecera(a["token"]),
                      json={"sucursal_id": circuito["b"]["sucursal_id"]})
    assert ajeno.status_code == 404
    actual = _sql(lab, "SELECT sucursal_id FROM public.empleado WHERE usuario_id=%s "
                       "AND empresa_id=%s", (usuario_id, a["empresa_id"]))[0][0]
    assert int(actual) == nueva_sucursal


# ---------------------------------------------------------------------------
# P0 - RBAC nominal por empresa
# ---------------------------------------------------------------------------


def test_p0_matriz_rbac_nominal_por_empresa(api, lab, circuito):
    a, b = circuito["a"], circuito["b"]

    matriz = api.get("/tenant/empresa/permisos", headers=_cabecera(a["token"]))
    assert matriz.status_code == 200, matriz.text
    cuerpo = matriz.json()
    assert cuerpo["empresa_id"] == a["empresa_id"]
    assert len(cuerpo["catalogo"]) >= 10
    roles = {rol["rol"]: rol for rol in cuerpo["roles"]}
    assert "ADMINISTRADOR" in roles and "CAJERO" in roles

    def permiso(roles_dict, nombre_rol, funcion, accion):
        for item in roles_dict[nombre_rol]["permisos"]:
            if item["funcion"] == funcion and item["accion"] == accion:
                return item
        return None

    admin_personal = permiso(roles, "ADMINISTRADOR", "PERSONAL", "ADMINISTRAR")
    cajero_personal = permiso(roles, "CAJERO", "PERSONAL", "ADMINISTRAR")
    assert admin_personal["permitido"] is True
    assert admin_personal["origen"] == "DEFAULT"
    assert cajero_personal["permitido"] is False
    assert permiso(roles, "CAJERO", "VENTAS", "REGISTRAR")["permitido"] is True

    # Excepcion por empresa: CAJERO recibe COMPRAS/GESTIONAR.
    ajuste = api.patch(f"/tenant/empresa/roles/{a['rol_cajero']}/permisos",
                       headers=_cabecera(a["token"]),
                       json={"permisos": [
                           {"funcion": "COMPRAS", "accion": "GESTIONAR",
                            "permitido": True},
                           {"funcion": "VENTAS", "accion": "REGISTRAR",
                            "permitido": False},
                       ]})
    assert ajuste.status_code == 200, ajuste.text
    assert ajuste.json()["actualizados"] == 2

    matriz2 = api.get("/tenant/empresa/permisos", headers=_cabecera(a["token"])).json()
    roles2 = {rol["rol"]: rol for rol in matriz2["roles"]}
    excepcion = permiso(roles2, "CAJERO", "COMPRAS", "GESTIONAR")
    denegado = permiso(roles2, "CAJERO", "VENTAS", "REGISTRAR")
    assert excepcion["permitido"] is True and excepcion["origen"] == "EXCEPCION"
    assert denegado["permitido"] is False and denegado["origen"] == "EXCEPCION"

    # La excepcion es SOLO de A: B mantiene el default.
    matriz_b = api.get("/tenant/empresa/permisos", headers=_cabecera(b["token"])).json()
    roles_b = {rol["rol"]: rol for rol in matriz_b["roles"]}
    assert permiso(roles_b, "CAJERO", "COMPRAS", "GESTIONAR")["permitido"] is False
    assert permiso(roles_b, "CAJERO", "VENTAS", "REGISTRAR")["permitido"] is True
    filas = _sql(
        lab,
        "SELECT count(*) FROM saas.permiso_tenant WHERE empresa_id=%s",
        (b["empresa_id"],),
    )[0][0]
    assert int(filas) == 0

    # ADMINISTRADOR no es editable (anti-lockout).
    protegido = api.patch(f"/tenant/empresa/roles/{a['rol_admin']}/permisos",
                          headers=_cabecera(a["token"]),
                          json={"permisos": [{"funcion": "PERSONAL",
                                              "accion": "ADMINISTRAR",
                                              "permitido": False}]})
    assert protegido.status_code == 409
    # Permiso inexistente en el catalogo: 422.
    invalido = api.patch(f"/tenant/empresa/roles/{a['rol_cajero']}/permisos",
                         headers=_cabecera(a["token"]),
                         json={"permisos": [{"funcion": "INVENTADO",
                                             "accion": "HACER",
                                             "permitido": True}]})
    assert invalido.status_code == 422


def test_p0_permiso_efectivo_habilita_y_deniega_backend(api, lab, circuito):
    """El permiso nominal gobierna el endpoint: default niega, excepcion concede."""
    b = circuito["b"]
    correo = f"cajero.b.{secrets.token_hex(4)}@lab.invalid"
    alta = _alta_personal(api, b, correo=correo, rol_id=b["rol_cajero"])
    assert alta.status_code == 201, alta.text
    assert _aceptar(api, alta.json()["invitacion"]["token"]).status_code == 200
    sesion = _login(api, correo, PASSWORD_NUEVA, b["slug"])
    assert sesion.status_code == 200
    token = sesion.json()["access_token"]

    def crear_como_cajero(sufijo):
        return api.post("/tenant/empresa/usuarios", headers=_cabecera(token), json={
            "correo": f"nuevo.{sufijo}@lab.invalid", "nombres": "N",
            "apellidos": "C", "ci": f"CI-N-{sufijo}",
            "sucursal_id": b["sucursal_id"], "rol_id": b["rol_cajero"],
        })

    assert crear_como_cajero(secrets.token_hex(3)).status_code == 403

    concedido = api.patch(f"/tenant/empresa/roles/{b['rol_cajero']}/permisos",
                          headers=_cabecera(b["token"]),
                          json={"permisos": [{"funcion": "PERSONAL",
                                              "accion": "ADMINISTRAR",
                                              "permitido": True}]})
    assert concedido.status_code == 200, concedido.text
    creado = crear_como_cajero(secrets.token_hex(3))
    assert creado.status_code == 201, creado.text

    revocado = api.patch(f"/tenant/empresa/roles/{b['rol_cajero']}/permisos",
                         headers=_cabecera(b["token"]),
                         json={"permisos": [{"funcion": "PERSONAL",
                                             "accion": "ADMINISTRAR",
                                             "permitido": False}]})
    assert revocado.status_code == 200
    assert crear_como_cajero(secrets.token_hex(3)).status_code == 403


# ---------------------------------------------------------------------------
# P1 - Superadministrador endurecido
# ---------------------------------------------------------------------------


def test_p1_superadmin_autenticacion_multiclave_y_rotacion(api, admin_cab,
                                                          admin_cab_anterior):
    assert api.get("/saas/superadmin/empresas").status_code == 401
    assert api.get("/saas/superadmin/empresas",
                   headers={"X-SaaS-Admin-Key": "clave-corta"}).status_code == 401
    assert api.get("/saas/superadmin/empresas", headers={
        "X-SaaS-Admin-Key": "x" * 64}).status_code == 401

    # Ambas claves vigentes durante la rotacion.
    assert api.get("/saas/superadmin/empresas", headers=admin_cab).status_code == 200
    assert api.get("/saas/superadmin/empresas",
                   headers=admin_cab_anterior).status_code == 200

    # El identificador selecciona la clave: no se puede declarar otra.
    assert api.get("/saas/superadmin/empresas", headers={
        "X-SaaS-Admin-Key": CLAVE_NUEVA,
        "X-SaaS-Admin-Key-Id": "v1"}).status_code == 401
    assert api.get("/saas/superadmin/empresas", headers={
        "X-SaaS-Admin-Key": CLAVE_ANTERIOR,
        "X-SaaS-Admin-Key-Id": "v9"}).status_code == 401

    claves = api.get("/saas/superadmin/claves", headers=admin_cab)
    assert claves.status_code == 200, claves.text
    cuerpo = claves.json()
    assert cuerpo["vigentes"] == 2
    assert cuerpo["rotacion_simultanea"] is True
    assert {c["kid"] for c in cuerpo["claves"]} == {"v1", "v2"}
    texto = json.dumps(cuerpo).lower()
    assert CLAVE_NUEVA.lower() not in texto
    assert CLAVE_ANTERIOR.lower() not in texto
    assert hashlib.sha256(CLAVE_NUEVA.encode()).hexdigest() not in texto


def test_p1_jwt_tenant_no_obtiene_autoridad_global(api, circuito):
    tenant = circuito["a"]["token"]
    for ruta in ("/saas/superadmin/empresas", "/saas/superadmin/claves",
                 "/saas/superadmin/auditoria"):
        respuesta = api.get(ruta, headers=_cabecera(tenant))
        assert respuesta.status_code == 401, ruta
    # Tampoco sirve enviar el JWT como clave administrativa.
    assert api.get("/saas/superadmin/empresas",
                   headers={"X-SaaS-Admin-Key": tenant}).status_code == 401


def test_p1_rate_limit_compartido_y_auditoria_sin_secretos(api, admin_cab, lab):
    # Limite configurado para el laboratorio: 6 fallas por IP.
    codigos: list[int] = []
    for intento in range(10):
        respuesta = api.get("/saas/superadmin/empresas", headers={
            "X-SaaS-Admin-Key": f"clave-invalida-{intento}-" + "z" * 40})
        codigos.append(respuesta.status_code)
        if respuesta.status_code == 429:
            assert respuesta.headers.get("Retry-After")
            break
        assert respuesta.status_code == 401, (intento, respuesta.text)
    assert codigos[-1] == 429, codigos
    assert codigos.count(401) >= 5, codigos
    # El bloqueo afecta tambien a la clave valida mientras dure la ventana.
    assert api.get("/saas/superadmin/empresas",
                   headers=admin_cab).status_code == 429

    # Los contadores viven en PostgreSQL: cada 401 observado quedo persistido.
    fallas = int(_sql(
        lab, "SELECT count(*) FROM saas_admin.intento_auth "
             "WHERE resultado='FALLA'")[0][0])
    assert fallas == codigos.count(401), (fallas, codigos)

    # Se libera la ventana (limpieza de laboratorio) y la clave vuelve a servir.
    _sql(lab, "DELETE FROM saas_admin.intento_auth")
    assert api.get("/saas/superadmin/empresas", headers=admin_cab).status_code == 200

    # La auditoria se escribe antes de entregar la respuesta; se tolera un
    # pequeno retardo de commit con reintentos acotados.
    eventos = []
    for _ in range(30):
        auditoria = api.get("/saas/superadmin/auditoria?limite=200", headers=admin_cab)
        assert auditoria.status_code == 200, auditoria.text
        eventos = auditoria.json()["eventos"]
        if eventos and any(e["resultado"] == "LIMITADO" for e in eventos):
            break
        time.sleep(0.1)
    assert eventos
    rutas = {evento["ruta"] for evento in eventos}
    assert "/saas/superadmin/empresas" in rutas
    assert any(evento["resultado"] == "LIMITADO" for evento in eventos)
    assert any(evento["resultado"] == "DENEGADO" for evento in eventos)
    texto = json.dumps(eventos).lower()
    for secreto in (CLAVE_NUEVA.lower(), CLAVE_ANTERIOR.lower(),
                    hashlib.sha256(CLAVE_NUEVA.encode()).hexdigest()):
        assert secreto not in texto
    for evento in eventos:
        if evento.get("fingerprint"):
            assert len(evento["fingerprint"]) == 12


def test_p1_separacion_de_principales_sql(lab):
    fila = _sql(
        lab,
        "SELECT has_table_privilege('saas_admin_runtime','public.empresa','SELECT'), "
        "       has_table_privilege('saas_admin_runtime','public.usuario','INSERT'), "
        "       has_table_privilege('saas_admin_runtime','saas.permiso_tenant','SELECT'), "
        "       has_function_privilege('saas_admin_runtime',"
        "         to_regprocedure('public.sp_alta_personal_tenant(bigint,bigint,"
        "text,text,text,text,text,bigint,bigint,text,text,timestamptz)'),'EXECUTE'), "
        "       has_function_privilege('fs_saas_runtime',"
        "         to_regprocedure('saas_admin.estado_acceso(text,text,integer,"
        "integer,integer)'),'EXECUTE')",
    )[0]
    assert fila == (False, False, False, False, False)
    grants = _sql(
        lab,
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE grantee='saas_admin_runtime'",
    )[0][0]
    assert int(grants) == 0


# ---------------------------------------------------------------------------
# P2 - Planes, sucursales y capacidades
# ---------------------------------------------------------------------------


def test_p2_limites_de_plan_y_concurrencia(api, admin_cab, lab):
    basico = _crear_empresa(api, admin_cab, lab, "lim", "BASICO")
    plan = api.get("/tenant/empresa/plan", headers=_cabecera(basico["token"]))
    assert plan.status_code == 200
    assert plan.json()["codigo"] == "BASICO"
    assert plan.json()["max_sucursales_activas"] == 2
    assert plan.json()["permite_reportes_ia"] is False
    assert plan.json()["sucursales_activas"] == 1

    segunda = api.post("/tenant/empresa/sucursales", headers=_cabecera(basico["token"]),
                       json={"ciudad_id": basico["ciudad_id"],
                             "nombre": f"S2 {secrets.token_hex(3)}",
                             "direccion": "Calle 2", "telefono": None})
    assert segunda.status_code == 201, segunda.text
    tercera = api.post("/tenant/empresa/sucursales", headers=_cabecera(basico["token"]),
                       json={"ciudad_id": basico["ciudad_id"],
                             "nombre": f"S3 {secrets.token_hex(3)}",
                             "direccion": "Calle 3", "telefono": None})
    assert tercera.status_code == 409
    activas = _sql(
        lab, "SELECT count(*) FROM public.sucursal WHERE empresa_id=%s AND estado IS TRUE",
        (basico["empresa_id"],),
    )[0][0]
    assert int(activas) == 2

    # Otra empresa BASICO no comparte el cupo.
    otro = _crear_empresa(api, admin_cab, lab, "lim2", "BASICO")
    assert api.post("/tenant/empresa/sucursales", headers=_cabecera(otro["token"]),
                    json={"ciudad_id": otro["ciudad_id"],
                          "nombre": f"S {secrets.token_hex(3)}",
                          "direccion": "Calle 4", "telefono": None}
                    ).status_code == 201

    # PROFESIONAL: 10 activas (1 inicial + 9 nuevas), la 11 no entra.
    pro = _crear_empresa(api, admin_cab, lab, "pro", "PROFESIONAL")
    creadas = 0
    for indice in range(9):
        respuesta = api.post("/tenant/empresa/sucursales",
                             headers=_cabecera(pro["token"]),
                             json={"ciudad_id": pro["ciudad_id"],
                                   "nombre": f"P{indice}-{secrets.token_hex(3)}",
                                   "direccion": f"Calle {indice}", "telefono": None})
        assert respuesta.status_code == 201, respuesta.text
        creadas += 1
    assert creadas == 9
    extra = api.post("/tenant/empresa/sucursales", headers=_cabecera(pro["token"]),
                     json={"ciudad_id": pro["ciudad_id"],
                           "nombre": f"P10-{secrets.token_hex(3)}",
                           "direccion": "Calle 10", "telefono": None})
    assert extra.status_code == 409
    activas_pro = _sql(
        lab, "SELECT count(*) FROM public.sucursal WHERE empresa_id=%s AND estado IS TRUE",
        (pro["empresa_id"],),
    )[0][0]
    assert int(activas_pro) == 10

    # Concurrencia: 5 altas simultaneas sobre un BASICO con 1 sucursal -> solo 1.
    carreras = _crear_empresa(api, admin_cab, lab, "carrera", "BASICO")

    def crear(_):
        return api.post("/tenant/empresa/sucursales",
                        headers=_cabecera(carreras["token"]),
                        json={"ciudad_id": carreras["ciudad_id"],
                              "nombre": f"CX-{secrets.token_hex(4)}",
                              "direccion": "Calle X", "telefono": None})

    with ThreadPoolExecutor(max_workers=5) as pool:
        codigos = sorted(r.status_code for r in pool.map(crear, range(5)))
    assert codigos.count(201) == 1, codigos
    assert codigos.count(409) == 4, codigos
    total = _sql(
        lab, "SELECT count(*) FROM public.sucursal WHERE empresa_id=%s AND estado IS TRUE",
        (carreras["empresa_id"],),
    )[0][0]
    assert int(total) == 2


def test_p2_downgrade_rechazado_y_suspension_renovacion(api, admin_cab, lab):
    empresa = _crear_empresa(api, admin_cab, lab, "dow", "PROFESIONAL")
    for indice in range(2):
        respuesta = api.post("/tenant/empresa/sucursales",
                             headers=_cabecera(empresa["token"]),
                             json={"ciudad_id": empresa["ciudad_id"],
                                   "nombre": f"D{indice}-{secrets.token_hex(3)}",
                                   "direccion": "Calle D", "telefono": None})
        assert respuesta.status_code == 201
    planes = {p["codigo"]: p for p in api.get(
        "/saas/superadmin/planes", headers=admin_cab).json()}
    hoy = date.today()
    downgrade = api.post(
        f"/saas/superadmin/empresas/{empresa['empresa_id']}/suscripciones",
        headers=admin_cab,
        json={"plan_id": planes["BASICO"]["id"],
              "fecha_inicio": hoy.isoformat(),
              "fecha_vencimiento": hoy.replace(year=hoy.year + 1).isoformat(),
              "observacion": "downgrade 5BO"})
    assert downgrade.status_code == 409, downgrade.text
    vigente = api.get("/tenant/empresa/plan", headers=_cabecera(empresa["token"]))
    assert vigente.status_code == 200
    assert vigente.json()["codigo"] == "PROFESIONAL"

    # Suspension: el acceso tenant se corta aunque el JWT siga vigente.
    suspension = api.patch(
        f"/saas/superadmin/empresas/{empresa['empresa_id']}/suscripciones/"
        f"{empresa['suscripcion_id']}/estado",
        headers=admin_cab, json={"estado": "SUSPENDIDA"})
    assert suspension.status_code == 200, suspension.text
    assert api.get("/tenant/empresa/plan",
                   headers=_cabecera(empresa["token"])).status_code == 403
    assert api.post("/tenant/empresa/sucursales",
                    headers=_cabecera(empresa["token"]),
                    json={"ciudad_id": empresa["ciudad_id"], "nombre": "SX",
                          "direccion": "Calle X", "telefono": None}
                    ).status_code == 403
    assert api.get("/auth/tenant/me",
                   headers=_cabecera(empresa["token"])).status_code == 403
    assert _login(api, empresa["correo"], PASSWORD, empresa["slug"]).status_code == 401

    # Renovacion administrada (no hay cobro automatico ni reactivacion magica).
    renovacion = api.post(
        f"/saas/superadmin/empresas/{empresa['empresa_id']}/suscripciones",
        headers=admin_cab,
        json={"plan_id": planes["PROFESIONAL"]["id"],
              "fecha_inicio": hoy.isoformat(),
              "fecha_vencimiento": hoy.replace(year=hoy.year + 1).isoformat(),
              "observacion": "renovacion 5BO"})
    assert renovacion.status_code == 201, renovacion.text
    assert _login(api, empresa["correo"], PASSWORD,
                  empresa["slug"]).status_code == 200


def test_p2_capacidades_y_rutas_legacy_cerradas(api, admin_cab, lab, circuito):
    a, b = circuito["a"], circuito["b"]
    plan_a = api.get("/tenant/empresa/plan", headers=_cabecera(a["token"]))
    plan_b = api.get("/tenant/empresa/plan", headers=_cabecera(b["token"]))
    assert plan_a.status_code == 200 and plan_b.status_code == 200
    assert plan_a.json()["codigo"] == "PROFESIONAL"
    assert plan_a.json()["max_sucursales_activas"] == 10
    assert plan_a.json()["permite_reportes_ia"] is True
    assert plan_b.json()["codigo"] == "BASICO"
    assert plan_b.json()["max_sucursales_activas"] == 2
    assert plan_b.json()["permite_reportes_ia"] is False
    # Capacidades convencionales vigentes en ambos planes.
    for cuerpo in (plan_a.json(), plan_b.json()):
        assert cuerpo["permite_ventas"] is True
        assert cuerpo["permite_reservas"] is True
        assert cuerpo["permite_offline"] is True
        assert cuerpo["permite_reportes_convencionales"] is True

    # El cambio de plan por autoservicio sigue cerrado (solo superadmin).
    assert api.patch("/tenant/empresa/plan", headers=_cabecera(a["token"]),
                     json={"codigo": "BASICO"}).status_code == 503
    # Rutas legacy comerciales siguen en 503 en modo SaaS.
    for metodo, ruta in (("GET", "/usuarios"), ("GET", "/empleados"),
                         ("GET", "/compras"), ("GET", "/ventas"),
                         ("GET", "/pagos"), ("GET", "/devoluciones")):
        assert api.request(metodo, ruta,
                           headers=_cabecera(a["token"])).status_code == 503, ruta

    # Un plan nuevo con IA deshabilitada es solo metadata del plano global.
    sufijo = secrets.token_hex(3)
    nuevo_plan = api.post("/saas/superadmin/planes", headers=admin_cab, json={
        "codigo": f"BASICO_5BO_{sufijo.upper()}",
        "nombre": f"Basico 5BO {sufijo}",
        "descripcion": "plan de prueba sin IA",
        "max_sucursales_activas": 2,
        "permite_productos": True,
        "permite_ventas": True,
        "permite_reservas": True,
        "permite_offline": True,
        "permite_reportes_convencionales": True,
        "permite_reportes_ia": False,
    })
    assert nuevo_plan.status_code == 201, nuevo_plan.text
    assert nuevo_plan.json()["permite_reportes_ia"] is False
