"""E1/5BM - Circuito SaaS operativo sobre PostgreSQL REAL y HTTP TCP A/B.

Requisitos
----------
Laboratorio aprovisionado con::

    python database/saas/76_laboratorio_operativo_5bm.py --init --confirm-desechable

Si el manifiesto no existe, la bateria se OMITE (no se simula con SQLite).

Que prueba
----------
* Aprovisionamiento CU01/CU02 por superadmin autenticado con el principal GLOBAL
  separado (`saas_admin_runtime`): dos empresas, suscripciones, primera sucursal
  y primer administrador; login tenant A/B real.
* Aislamiento del plano administrativo: sin privilegios de tabla, sin EXECUTE de
  rutinas tenant; el runtime tenant no puede ejecutar funciones globales ni
  insertar en `public.usuario`.
* Reserva real CU16 (stock reservado, cancelacion y finalizacion idempotentes,
  carrera por la ultima unidad con un solo ganador).
* Venta presencial de contado con precio/total server-side, pago, kardex,
  auditoria, replay idempotente y conflicto de carga.
* Rutas legacy y PATCH de plan tenant siguen en 503.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BM_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bm" / "lab_5bm_manifest.json"),
    )
)
MARCA = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
PASSWORD = "Lab5bm#Clave2026"
PASSWORD_CLIENTE = "Lab5bm#Cliente2026"


class Registro:
    def __init__(self) -> None:
        self.lineas: list[str] = []

    def ok(self, nombre: str, detalle: str) -> None:
        self.lineas.append(f"[PASS] {nombre}: {detalle}")

    def escribir(self, lab: dict) -> Path | None:
        if not self.lineas:
            return None
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        ruta = EVIDENCIAS / f"EVIDENCIA_OPERATIVA_5BM_HTTP_{MARCA}.txt"
        base, n = ruta, 2
        while ruta.exists():
            ruta = base.with_name(f"EVIDENCIA_OPERATIVA_5BM_HTTP_{MARCA}_v{n:02d}.txt")
            n += 1
        encabezado = [
            "EVIDENCIA HTTP A/B - ETAPA E1 (5BM) - APROVISIONAMIENTO / RESERVA / VENTA",
            f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Cluster: {lab['cluster']['version']} en "
            f"{lab['cluster']['host']}:{lab['cluster']['puerto']} / {lab['cluster']['base']}",
            "Principales: fs_saas_runtime (tenant) y saas_admin_runtime (global)",
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


def _dsn_sqlalchemy(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def lab() -> dict:
    if not MANIFIESTO.exists():
        pytest.skip(
            "Laboratorio 5BM no aprovisionado; ejecute "
            "database/saas/76_laboratorio_operativo_5bm.py --init --confirm-desechable"
        )
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    from tests_saas._lab_readiness_5bs import motivo_no_disponible

    motivo = motivo_no_disponible(
        datos, 55443, "5BM",
        "python database/saas/101_runner_regresion_reproducible_5bs.py --lab 5bm")
    if motivo:
        pytest.skip(motivo)
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    clave_superadmin = secrets.token_urlsafe(32)
    import hashlib

    env = {
        **os.environ,
        "DATABASE_URL": _dsn_sqlalchemy(lab["runtime_dsn"]),
        "SAAS_ADMIN_DATABASE_URL": _dsn_sqlalchemy(lab["admin_runtime_dsn"]),
        "SECRET_KEY": secrets.token_urlsafe(32),
        "SAAS_AUTH_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "true",
        "SAAS_SUPERADMIN_KEY_SHA256": hashlib.sha256(
            clave_superadmin.encode("utf-8")).hexdigest(),
        "SAAS_RESERVAS_ENABLED": "true",
        "SAAS_VENTAS_ENABLED": "true",
        "SAAS_TENANT_ASSERTION_KEY": lab["assertion_key"],
        "SAAS_ACTOR_SQL_OWNER": lab["roles"]["actor_owner"],
        "APP_ENV": "lab-5bm",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bm_{puerto}.log"
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
        contenido = registro.read_text(encoding="utf-8", errors="replace")[-3000:]
        pytest.fail(f"uvicorn no arranco con los principales 5BM: {contenido}")
    yield {"base": base, "clave": clave_superadmin, "proceso": proceso}
    proceso.terminate()
    try:
        proceso.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proceso.kill()
    salida.close()


@pytest.fixture(scope="session", autouse=True)
def evidencia():
    yield
    if MANIFIESTO.exists() and REGISTRO.lineas:
        lab = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        ruta = REGISTRO.escribir(lab)
        print(f"\nEvidencia HTTP escrita: {ruta}")


@pytest.fixture(scope="session")
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=45.0) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def admin_cab(servidor):
    return {"X-SaaS-Admin-Key": servidor["clave"]}


def _sql(lab: dict, consulta: str, args=None):
    import psycopg

    with psycopg.connect(lab["admin_dsn"]) as conn:
        cursor = conn.execute(consulta, args)
        # INSERT/UPDATE sin RETURNING no produce filas: se devuelve lista vacia.
        if cursor.description is None:
            return []
        return cursor.fetchall()


def _hash(password: str) -> str:
    from app.core.security import hash_password

    return hash_password(password)


def _password_de(lab: dict) -> str:
    return lab["password_usuarios"]


def _login(api, correo, password, slug, contexto="personal"):
    return api.post("/auth/tenant/login", json={
        "correo": correo, "password": password,
        "empresa_slug": slug, "contexto": contexto,
    })


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def circuito(api, admin_cab, lab):
    """Crea A/B (y C suspendida) por superadmin + clientes e inventario real."""
    sufijo = secrets.token_hex(4)
    planes = {p["codigo"]: p for p in api.get(
        "/saas/superadmin/planes", headers=admin_cab).json()}
    ciudad_id = _sql(lab, "SELECT id FROM public.ciudad WHERE estado IS TRUE ORDER BY id LIMIT 1")[0][0]
    hoy = date.today()
    creadas: dict[str, dict] = {}

    for etiqueta, plan, stock in (("a", "PROFESIONAL", 5), ("b", "BASICO", 5),
                                  ("c", "BASICO", 1)):
        slug = f"lab-{etiqueta}-{sufijo}"
        alta = api.post("/saas/superadmin/empresas", headers=admin_cab, json={
            "razon_social": f"Empresa {etiqueta.upper()} 5BM {sufijo}",
            "nombre_comercial": f"Tienda {etiqueta.upper()} {sufijo}",
            "slug": slug,
            "nit": f"NIT-{etiqueta}-{sufijo}",
            "correo_contacto": f"contacto.{etiqueta}.{sufijo}@lab.invalid",
            "telefono": None,
            "plan_id": planes[plan]["id"],
            "fecha_inicio": hoy.isoformat(),
            "fecha_vencimiento": (hoy + timedelta(days=365)).isoformat(),
        })
        assert alta.status_code == 201, alta.text
        empresa = alta.json()["empresa"]
        correo = f"admin.{etiqueta}.{sufijo}@lab.invalid"
        primer = api.post(
            f"/saas/superadmin/empresas/{empresa['id']}/administrador-inicial",
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
        bloque = {
            "empresa_id": empresa["id"], "slug": slug, "correo": correo,
            "sucursal_id": primer.json()["sucursal_id"],
            "admin_usuario_id": primer.json()["usuario_id"],
            "plan": plan, "stock_inicial": stock,
        }
        if etiqueta == "c":
            creadas[etiqueta] = bloque
            continue
        tokens = _login(api, correo, PASSWORD, slug)
        assert tokens.status_code == 200, tokens.text
        bloque["token"] = tokens.json()["access_token"]

        categoria = api.post("/tenant/catalogo/categorias",
                             headers=_cabecera(bloque["token"]),
                             json={"nombre": f"Camisas {etiqueta.upper()} {sufijo}"})
        assert categoria.status_code == 201, categoria.text
        producto = api.post("/tenant/catalogo/productos",
                            headers=_cabecera(bloque["token"]),
                            json={"categoria_id": categoria.json()["id"],
                                  "nombre": f"Camisa {etiqueta.upper()} {sufijo}",
                                  "descripcion": "prenda ficticia 5BM",
                                  "precio": "150.00"})
        assert producto.status_code == 201, producto.text
        # La variante se provisiona por SQL de laboratorio: el endpoint tenant
        # usa FOR UPDATE sobre los catalogos globales talla/color, privilegio que
        # el principal restringido no tiene (brecha documentada, fuera de E1).
        variante_id = _sql(
            lab,
            "INSERT INTO public.variante_producto (empresa_id, producto_id, "
            "talla_id, color_id, sku, estado) VALUES (%s,%s,1,1,%s,TRUE) "
            "RETURNING id",
            (bloque["empresa_id"], producto.json()["id"],
             f"SKU-{etiqueta.upper()}-{sufijo}"))[0][0]

        temporada = _sql(
            lab,
            "INSERT INTO public.temporada (empresa_id, nombre, fecha_inicio, "
            "fecha_fin, estado) VALUES (%s,%s,'2026-01-01','2026-12-31',TRUE) "
            "RETURNING id",
            (bloque["empresa_id"], f"T-{etiqueta}-{sufijo}"))[0][0]
        inventario = _sql(
            lab,
            "INSERT INTO public.inventario (empresa_id, sucursal_id, "
            "variante_producto_id, temporada_id, stock_actual, stock_reservado, "
            "fecha_actualizacion) VALUES (%s,%s,%s,%s,%s,0,NOW()) RETURNING id",
            (bloque["empresa_id"], bloque["sucursal_id"],
             variante_id, temporada, stock))[0][0]
        bloque.update({"variante_id": variante_id,
                       "inventario_id": inventario,
                       "producto_id": producto.json()["id"]})
        creadas[etiqueta] = bloque

    # Clientes A1/A2/B: perfil cliente real + membresia CLIENTE.
    for etiqueta, cuantos, empresa in (("a", 2, creadas["a"]), ("b", 1, creadas["b"])):
        clientes = []
        for indice in range(1, cuantos + 1):
            correo = f"cliente{indice}.{etiqueta}.{sufijo}@lab.invalid"
            usuario_id = _sql(
                lab,
                "INSERT INTO public.usuario (rol_id, correo, password_hash, "
                "fecha_creacion, estado) SELECT r.id, %s, %s, NOW(), TRUE "
                "FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE "
                "RETURNING id",
                (correo, _hash(PASSWORD_CLIENTE)))[0][0]
            # La FK compuesta cliente->usuario_empresa exige la membresia primero.
            _sql(lab,
                 "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
                 "SELECT %s, %s, r.id, TRUE FROM public.rol r "
                 "WHERE r.nombre='CLIENTE' AND r.estado IS TRUE",
                 (usuario_id, empresa["empresa_id"]))
            cliente_id = _sql(
                lab,
                "INSERT INTO public.cliente (empresa_id, usuario_id, nombre, "
                "apellido, ci, estado) VALUES (%s,%s,%s,%s,%s,TRUE) RETURNING id",
                (empresa["empresa_id"], usuario_id, f"Cliente{indice}",
                 etiqueta.upper(), f"CI-C{indice}-{etiqueta.upper()}-{sufijo}"))[0][0]
            sesion = _login(api, correo, PASSWORD_CLIENTE, empresa["slug"], "cliente")
            assert sesion.status_code == 200, sesion.text
            clientes.append({"correo": correo, "usuario_id": usuario_id,
                             "cliente_id": cliente_id,
                             "token": sesion.json()["access_token"]})
        creadas[etiqueta]["clientes"] = clientes
    creadas["sufijo"] = sufijo
    return creadas


# ---------------------------------------------------------------------------
# Plano global vs tenant
# ---------------------------------------------------------------------------


def test_admin_sin_dsn_falla_cerrado(monkeypatch):
    from fastapi import HTTPException

    from app.core.config import settings
    from app.core.saas_admin_db import get_saas_admin_db

    monkeypatch.setattr(settings, "saas_admin_database_url", None)
    generador = get_saas_admin_db()
    with pytest.raises(HTTPException) as exc:
        next(generador)
    assert exc.value.status_code == 503
    REGISTRO.ok("admin_dsn", "sin SAAS_ADMIN_DATABASE_URL => 503 fail closed")


def test_superadmin_exige_credencial_propia(api, lab):
    assert api.get("/saas/superadmin/empresas").status_code == 401
    assert api.get("/saas/superadmin/empresas",
                   headers={"X-SaaS-Admin-Key": "X" * 48}).status_code == 401
    REGISTRO.ok("superadmin_credencial", "sin cabecera y clave falsa => 401")


def test_planos_sql_aislados(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"], autocommit=True) as conn:
        assert conn.execute(
            "SELECT has_table_privilege(current_user,'public.usuario','INSERT')"
        ).fetchone()[0] is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO public.usuario (rol_id, correo, password_hash, "
                         "fecha_creacion, estado) VALUES (1,'x@lab.invalid','x',NOW(),TRUE)")
        # El esquema administrativo ni siquiera es resoluble por el tenant.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT saas_admin.listar_empresas()")
    with psycopg.connect(lab["admin_runtime_dsn"], autocommit=True) as conn:
        empresas = conn.execute("SELECT saas_admin.listar_empresas()").fetchone()[0]
        assert isinstance(empresas, list) and empresas
        assert conn.execute(
            "SELECT has_table_privilege(current_user,'public.usuario','SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_schema_privilege(current_user,'saas','USAGE')"
        ).fetchone()[0] is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM public.usuario")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT public.sp_registrar_venta_contado_tenant("
                "NULL::bigint,NULL::bigint,NULL::bigint,NULL::uuid,"
                "NULL::text,NULL::jsonb,NULL::text)")
    REGISTRO.ok("planos_sql", "runtime sin funciones globales; admin sin tablas ni SP tenant")


def test_bootstrap_dos_empresas_y_login_tenant(api, circuito, lab, admin_cab):
    a, b = circuito["a"], circuito["b"]
    me_a = api.get("/auth/tenant/me", headers=_cabecera(a["token"])).json()
    me_b = api.get("/auth/tenant/me", headers=_cabecera(b["token"])).json()
    assert me_a["empresa_slug"] == a["slug"] and me_a["plan_codigo"] == "PROFESIONAL"
    assert me_b["empresa_slug"] == b["slug"] and me_b["plan_codigo"] == "BASICO"

    global_role, local_role = _sql(
        lab,
        "SELECT u.rol_id, ue.rol_id FROM public.usuario u "
        "JOIN public.usuario_empresa ue ON ue.usuario_id=u.id "
        "JOIN public.rol r1 ON r1.id=u.rol_id "
        "JOIN public.rol r2 ON r2.id=ue.rol_id "
        "WHERE ue.empresa_id=%s AND r1.nombre='CLIENTE' AND r2.nombre='ADMINISTRADOR'",
        (a["empresa_id"],))[0]
    assert global_role != local_role
    listado = api.get("/saas/superadmin/empresas", headers=admin_cab).json()
    ids = {e["id"] for e in listado}
    assert {a["empresa_id"], b["empresa_id"]} <= ids
    REGISTRO.ok("bootstrap_A_B", "dos empresas, login separado y rol global CLIENTE")


def test_bootstrap_no_se_repite(api, circuito, admin_cab, lab):
    a = circuito["a"]
    repetido = api.post(
        f"/saas/superadmin/empresas/{a['empresa_id']}/administrador-inicial",
        headers=admin_cab,
        json={"correo": f"otro.{a['empresa_id']}@lab.invalid", "password": PASSWORD,
              "nombres": "Otro", "apellidos": "Admin", "ci": "CI-OTRO-5BM",
              "ciudad_id": 1, "nombre_sucursal": "Otra",
              "direccion_sucursal": "Otra 1", "telefono": None})
    assert repetido.status_code == 409, repetido.text
    sucursales = _sql(lab, "SELECT count(*) FROM public.sucursal WHERE empresa_id=%s",
                      (a["empresa_id"],))[0][0]
    assert sucursales == 1
    REGISTRO.ok("bootstrap_repetido", "segundo administrador inicial => 409 sin sucursal extra")


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------


def test_reserva_real_reserva_stock(api, circuito, lab):
    a = circuito["a"]
    cliente = a["clientes"][0]
    carrito = api.post("/tenant/carritos/items", headers=_cabecera(cliente["token"]),
                       json={"sucursal_id": a["sucursal_id"],
                             "inventario_id": a["inventario_id"], "cantidad": 2})
    assert carrito.status_code == 200, carrito.text
    fecha = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    reserva = api.post("/tenant/reservas", headers=_cabecera(cliente["token"]),
                       json={"carrito_id": carrito.json()["carrito_id"],
                             "fecha_atencion": fecha})
    assert reserva.status_code == 201, reserva.text
    reserva_id = reserva.json()["reserva_id"]
    stock = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                      "WHERE id=%s", (a["inventario_id"],))[0]
    assert stock == (a["stock_inicial"], 2), stock
    movimientos = _sql(lab, "SELECT count(*) FROM public.movimiento_inventario "
                            "WHERE referencia_tipo='RESERVA' AND referencia_id=%s "
                            "AND tipo='RESERVA'", (reserva_id,))[0][0]
    assert movimientos == 1
    bitacora = _sql(lab, "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s "
                         "AND entidad_afectada='reserva' AND descripcion LIKE %s",
                    (a["empresa_id"], f"%{reserva_id}%"))[0][0]
    assert bitacora >= 1
    a.setdefault("reservas", []).append(reserva_id)
    REGISTRO.ok("reserva_creada", f"stock_reservado=2, kardex y bitacora de empresa {a['empresa_id']}")


def test_reserva_ajena_no_visible(api, circuito, lab):
    a, b = circuito["a"], circuito["b"]
    reserva_id = a["reservas"][0]
    assert api.get(f"/tenant/comercial/reservas/{reserva_id}",
                   headers=_cabecera(b["token"])).status_code == 404
    con_b = _sql(lab, "SELECT count(*) FROM public.reserva WHERE id=%s AND empresa_id=%s",
                 (reserva_id, b["empresa_id"]))[0][0]
    assert con_b == 0
    REGISTRO.ok("reserva_cruce", "B recibe 404 y no existe fila de A en su empresa")


def test_cancelar_reserva_libera_una_vez(api, circuito, lab):
    a = circuito["a"]
    reserva_id = a["reservas"][0]
    token = a["clientes"][0]["token"]
    primera = api.patch(f"/tenant/reservas/{reserva_id}/cancelar",
                        headers=_cabecera(token), json={"observacion": "prueba 5BM"})
    assert primera.status_code == 200, primera.text
    assert primera.json()["estado"] == "CANCELADA"
    segunda = api.patch(f"/tenant/reservas/{reserva_id}/cancelar",
                        headers=_cabecera(token), json={"observacion": "replay"})
    assert segunda.status_code == 200 and segunda.json()["estado"] == "CANCELADA"
    stock = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                      "WHERE id=%s", (a["inventario_id"],))[0]
    assert stock == (a["stock_inicial"], 0), stock
    liberaciones = _sql(lab, "SELECT count(*) FROM public.movimiento_inventario "
                             "WHERE tipo='LIBERACION_RESERVA' AND referencia_id=%s",
                        (reserva_id,))[0][0]
    assert liberaciones == 1
    REGISTRO.ok("reserva_cancelada", "stock_reservado=0 y una sola liberacion")


def test_finalizar_reserva_sin_compra(api, circuito, lab):
    a = circuito["a"]
    cliente = a["clientes"][0]
    carrito = api.post("/tenant/carritos/items", headers=_cabecera(cliente["token"]),
                       json={"sucursal_id": a["sucursal_id"],
                             "inventario_id": a["inventario_id"], "cantidad": 1})
    assert carrito.status_code == 200, carrito.text
    reserva = api.post("/tenant/reservas", headers=_cabecera(cliente["token"]),
                       json={"carrito_id": carrito.json()["carrito_id"],
                             "fecha_atencion": (datetime.now(timezone.utc)
                                                + timedelta(days=3)).isoformat()})
    assert reserva.status_code == 201, reserva.text
    reserva_id = reserva.json()["reserva_id"]
    final = api.patch(f"/tenant/reservas/{reserva_id}/finalizar-sin-compra",
                      headers=_cabecera(a["token"]), json={"observacion": "sin compra"})
    assert final.status_code == 200, final.text
    assert final.json()["estado"] == "ATENDIDA"
    stock = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                      "WHERE id=%s", (a["inventario_id"],))[0]
    assert stock == (a["stock_inicial"], 0), stock
    REGISTRO.ok("reserva_finalizada", "ATENDIDA y stock reservado liberado")


def test_carrera_ultima_unidad_reserva(api, circuito, lab):
    a = circuito["a"]
    temporada = _sql(lab, "SELECT id FROM public.temporada WHERE empresa_id=%s LIMIT 1",
                     (a["empresa_id"],))[0][0]
    inventario_unico = _sql(
        lab,
        "INSERT INTO public.inventario (empresa_id, sucursal_id, variante_producto_id, "
        "temporada_id, stock_actual, stock_reservado, fecha_actualizacion) "
        "VALUES (%s,%s,%s,%s,1,0,NOW()) RETURNING id",
        (a["empresa_id"], a["sucursal_id"], a["variante_id"], temporada))[0][0]
    clientes = a["clientes"][:2]

    def reservar(cliente):
        with httpx.Client(base_url=str(api.base_url), timeout=45.0) as propio:
            carrito = propio.post("/tenant/carritos/items",
                                  headers=_cabecera(cliente["token"]),
                                  json={"sucursal_id": a["sucursal_id"],
                                        "inventario_id": inventario_unico, "cantidad": 1})
            if carrito.status_code != 200:
                return carrito.status_code
            reserva = propio.post("/tenant/reservas",
                                  headers=_cabecera(cliente["token"]),
                                  json={"carrito_id": carrito.json()["carrito_id"],
                                        "fecha_atencion": (datetime.now(timezone.utc)
                                                           + timedelta(days=4)).isoformat()})
            return reserva.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        estados = list(pool.map(reservar, clientes))
    assert estados.count(201) == 1, estados
    assert all(e in (201, 403, 409) for e in estados), estados
    stock = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                      "WHERE id=%s", (inventario_unico,))[0]
    assert stock == (1, 1), stock
    REGISTRO.ok("reserva_carrera", f"estados={estados}, stock_reservado=1 (un solo ganador)")


# ---------------------------------------------------------------------------
# Venta de contado
# ---------------------------------------------------------------------------


def _venta(api, token, empresa, cantidad=1, operacion_id=None, inventario_id=None):
    return api.post("/tenant/ventas/contado", headers=_cabecera(token), json={
        "sucursal_id": empresa["sucursal_id"],
        "operacion_id": operacion_id or str(__import__("uuid").uuid4()),
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": inventario_id or empresa["inventario_id"],
                    "cantidad": cantidad}],
    })


def test_venta_contado_descuenta_stock_y_paga(api, circuito, lab):
    a = circuito["a"]
    antes = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                 (a["inventario_id"],))[0][0]
    respuesta = _venta(api, a["token"], a)
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 150.0, cuerpo
    assert cuerpo["estado"] == "COMPLETADA" and cuerpo["canal"] == "PRESENCIAL"
    despues = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                        "WHERE id=%s", (a["inventario_id"],))[0]
    assert despues == (antes - 1, 0), despues
    pago = _sql(lab, "SELECT monto, metodo, estado FROM public.pago WHERE venta_id=%s",
                (cuerpo["venta_id"],))[0]
    assert float(pago[0]) == 150.0 and pago[1] == "EFECTIVO" and pago[2] == "APROBADO"
    movimientos = _sql(lab, "SELECT count(*) FROM public.movimiento_inventario "
                            "WHERE tipo='SALIDA_VENTA' AND referencia_id=%s",
                       (cuerpo["venta_id"],))[0][0]
    assert movimientos == 1
    auditoria = _sql(lab, "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s "
                          "AND entidad_afectada IN ('venta','pago')", (a["empresa_id"],))[0][0]
    assert auditoria >= 2
    a["venta_id"] = cuerpo["venta_id"]
    a["venta_base"] = {"cantidad": 1, "stock": despues[0]}
    REGISTRO.ok("venta_contado", "total server-side 150.00, stock -1, pago APROBADO y auditoria")


def test_venta_cliente_no_puede_vender(api, circuito):
    a = circuito["a"]
    respuesta = _venta(api, a["clientes"][0]["token"], a)
    assert respuesta.status_code == 403, respuesta.text
    REGISTRO.ok("venta_rol", "contexto cliente rechazado con 403")


def test_venta_ajena_404(api, circuito, lab):
    a, b = circuito["a"], circuito["b"]
    assert api.get(f"/tenant/comercial/ventas/{a['venta_id']}",
                   headers=_cabecera(b["token"])).status_code == 404
    assert api.get(f"/tenant/comercial/ventas/{a['venta_id']}",
                   headers=_cabecera(a["token"])).status_code == 200
    REGISTRO.ok("venta_cruce", "B no ve la venta de A; A si la ve")


def test_venta_replay_idempotente_12(api, circuito, lab):
    a = circuito["a"]
    operacion = str(__import__("uuid").uuid4())
    stock_antes = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                       (a["inventario_id"],))[0][0]
    ids = set()
    for _ in range(12):
        respuesta = _venta(api, a["token"], a, cantidad=1, operacion_id=operacion)
        assert respuesta.status_code in (200, 201), respuesta.text
        ids.add(respuesta.json()["venta_id"])
    assert len(ids) == 1, ids
    stock_despues = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                         (a["inventario_id"],))[0][0]
    assert stock_despues == stock_antes - 1
    filas = _sql(lab, "SELECT count(*) FROM public.venta WHERE id=%s",
                 (next(iter(ids)),))[0][0]
    assert filas == 1
    REGISTRO.ok("venta_replay", "12 reenvios => 1 venta y 1 descuento de stock")


def test_venta_replay_concurrente(api, circuito, lab):
    a = circuito["a"]
    operacion = str(__import__("uuid").uuid4())
    stock_antes = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                       (a["inventario_id"],))[0][0]

    def enviar(_):
        with httpx.Client(base_url=str(api.base_url), timeout=45.0) as propio:
            return _venta(propio, a["token"], a, cantidad=1, operacion_id=operacion)

    with ThreadPoolExecutor(max_workers=4) as pool:
        respuestas = list(pool.map(enviar, range(4)))
    assert all(r.status_code in (200, 201) for r in respuestas), [r.text for r in respuestas]
    assert len({r.json()["venta_id"] for r in respuestas}) == 1
    stock_despues = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                         (a["inventario_id"],))[0][0]
    assert stock_despues == stock_antes - 1
    REGISTRO.ok("venta_replay_concurrente", "4 envios simultaneos => 1 venta")


def test_venta_mismo_uuid_otra_carga_409(api, circuito, lab):
    a = circuito["a"]
    operacion = str(__import__("uuid").uuid4())
    primera = _venta(api, a["token"], a, cantidad=1, operacion_id=operacion)
    assert primera.status_code == 201, primera.text
    stock = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                 (a["inventario_id"],))[0][0]
    conflicto = _venta(api, a["token"], a, cantidad=2, operacion_id=operacion)
    assert conflicto.status_code == 409, conflicto.text
    assert _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                (a["inventario_id"],))[0][0] == stock
    REGISTRO.ok("venta_conflicto_carga", "mismo UUID con cantidad distinta => 409 sin mutar stock")


def test_venta_mismo_uuid_otra_empresa(api, circuito, lab):
    a, b = circuito["a"], circuito["b"]
    operacion = str(__import__("uuid").uuid4())
    respuesta_a = _venta(api, a["token"], a, operacion_id=operacion)
    respuesta_b = _venta(api, b["token"], b, operacion_id=operacion)
    assert respuesta_a.status_code == 201, respuesta_a.text
    assert respuesta_b.status_code == 201, respuesta_b.text
    assert respuesta_a.json()["venta_id"] != respuesta_b.json()["venta_id"]
    b["venta_id"] = respuesta_b.json()["venta_id"]
    REGISTRO.ok("venta_uuid_empresa", "mismo UUID en A y B produce ventas independientes")


def test_venta_rollback_total(api, circuito, lab):
    a = circuito["a"]
    ventas_antes = _sql(lab, "SELECT count(*) FROM public.venta WHERE empresa_id=%s",
                        (a["empresa_id"],))[0][0]
    pagos_antes = _sql(lab, "SELECT count(*) FROM public.pago WHERE empresa_id=%s",
                       (a["empresa_id"],))[0][0]
    stock = _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                 (a["inventario_id"],))[0][0]
    operacion = str(__import__("uuid").uuid4())
    respuesta = api.post("/tenant/ventas/contado", headers=_cabecera(a["token"]), json={
        "sucursal_id": a["sucursal_id"], "operacion_id": operacion,
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": a["inventario_id"], "cantidad": 1},
                   {"inventario_id": a["inventario_id"], "cantidad": 1}],
    })
    assert respuesta.status_code == 422, respuesta.text
    respuesta = api.post("/tenant/ventas/contado", headers=_cabecera(a["token"]), json={
        "sucursal_id": a["sucursal_id"],
        "operacion_id": str(__import__("uuid").uuid4()),
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": a["inventario_id"], "cantidad": 999}],
    })
    assert respuesta.status_code == 409, respuesta.text
    assert _sql(lab, "SELECT count(*) FROM public.venta WHERE empresa_id=%s",
                (a["empresa_id"],))[0][0] == ventas_antes
    assert _sql(lab, "SELECT count(*) FROM public.pago WHERE empresa_id=%s",
                (a["empresa_id"],))[0][0] == pagos_antes
    assert _sql(lab, "SELECT stock_actual FROM public.inventario WHERE id=%s",
                (a["inventario_id"],))[0][0] == stock
    REGISTRO.ok("venta_rollback", "linea duplicada 422 y stock insuficiente 409 sin filas parciales")


def test_venta_carrera_ultima_unidad_12(api, circuito, lab):
    a = circuito["a"]
    temporada = _sql(lab, "SELECT id FROM public.temporada WHERE empresa_id=%s LIMIT 1",
                     (a["empresa_id"],))[0][0]
    unico = _sql(
        lab,
        "INSERT INTO public.inventario (empresa_id, sucursal_id, variante_producto_id, "
        "temporada_id, stock_actual, stock_reservado, fecha_actualizacion) "
        "VALUES (%s,%s,%s,%s,1,0,NOW()) RETURNING id",
        (a["empresa_id"], a["sucursal_id"], a["variante_id"], temporada))[0][0]
    operaciones = [str(__import__("uuid").uuid4()) for _ in range(12)]

    def vender(operacion):
        with httpx.Client(base_url=str(api.base_url), timeout=60.0) as propio:
            return _venta(propio, a["token"], a, cantidad=1,
                          operacion_id=operacion, inventario_id=unico).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        estados = list(pool.map(vender, operaciones))
    assert estados.count(201) == 1, estados
    assert all(e in (201, 403, 409) for e in estados), estados
    stock = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                      "WHERE id=%s", (unico,))[0]
    ventas = _sql(lab, "SELECT count(*) FROM public.venta WHERE sucursal_id=%s "
                       "AND total=150.00", (a["sucursal_id"],))[0][0]
    assert stock == (0, 0), stock
    assert ventas >= 1
    REGISTRO.ok("venta_carrera_12",
                f"estados={estados}, stock final={stock} (un solo ganador)")


def test_venta_plan_suspendido_denegada(api, circuito, admin_cab, lab):
    b = circuito["b"]
    subs = api.get(f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones",
                   headers=admin_cab).json()
    activa = next(s for s in subs if s["estado"] == "ACTIVA")
    suspendida = api.patch(
        f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones/{activa['id']}/estado",
        headers=admin_cab, json={"estado": "SUSPENDIDA"})
    assert suspendida.status_code == 200, suspendida.text
    bloqueada = _venta(api, b["token"], b)
    assert bloqueada.status_code == 403, bloqueada.text
    nueva = api.post(f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones",
                     headers=admin_cab,
                     json={"plan_id": activa["plan_saas_id"],
                           "fecha_inicio": date.today().isoformat(),
                           "fecha_vencimiento": (date.today() + timedelta(days=30)).isoformat(),
                           "observacion": "reactivacion 5BM"})
    assert nueva.status_code == 201, nueva.text
    permitida = _venta(api, b["token"], b)
    assert permitida.status_code == 201, permitida.text
    REGISTRO.ok("venta_suscripcion", "suscripcion suspendida => 403; nueva suscripcion => 201")


def test_empresa_suspendida_no_hace_login(api, circuito, admin_cab):
    c = circuito["c"]
    cambio = api.patch(f"/saas/superadmin/empresas/{c['empresa_id']}/estado",
                       headers=admin_cab, json={"estado": False})
    assert cambio.status_code == 200, cambio.text
    assert cambio.json()["estado"] is False
    bloqueado = _login(api, c["correo"], PASSWORD, c["slug"])
    assert bloqueado.status_code == 401, bloqueado.text
    REGISTRO.ok("empresa_suspendida", "empresa inactiva no puede iniciar sesion")


def test_rutas_legacy_y_plan_tenant_siguen_cerradas(api, circuito):
    a = circuito["a"]
    assert api.get("/ventas", headers=_cabecera(a["token"])).status_code == 503
    assert api.post("/pagos", headers=_cabecera(a["token"]), json={}).status_code == 503
    assert api.patch("/tenant/empresa/plan", headers=_cabecera(a["token"]),
                     json={"codigo": "BASICO"}).status_code == 503
    assert api.post("/tenant/reservas/prevalidar", headers=_cabecera(
        a["clientes"][0]["token"]),
        json={"carrito_id": 1,
              "fecha_atencion": (datetime.now(timezone.utc)
                                 + timedelta(days=1)).isoformat()}).status_code in (200, 404, 409)
    REGISTRO.ok("legacy_cerrado", "ventas/pagos legacy y PATCH plan tenant en 503")
