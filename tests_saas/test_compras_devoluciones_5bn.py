"""E2/5BN - Catalogo seguro, compras/recepcion y devoluciones sobre PostgreSQL real.

Requisitos
----------
Laboratorio 5BN aprovisionado (cluster NUEVO en 55444, distinto del E1/5BM)::

    python database/saas/77_laboratorio_operativo_5bn.py --init --confirm-desechable

Si el manifiesto no existe, la bateria se OMITE (nunca se simula con SQLite).

Que prueba
----------
* P0: creacion y edicion de variantes por HTTP con el principal restringido,
  sin HTTP 500, sin UPDATE sobre `talla`/`color`, con invariante en base.
* P1: ordenes de compra (BORRADOR -> ENVIADA -> PARCIAL -> RECIBIDA/CANCELADA),
  recepcion real de inventario, kardex `ENTRADA_COMPRA`, bitacora, idempotencia
  por UUID, concurrencia y aislamiento A/B.
* P2: devoluciones de venta (SOLICITADA -> APROBADA/RECHAZADA -> COMPLETADA),
  reposicion fisica opcional, kardex `DEVOLUCION`, reembolso declarado PENDIENTE
  sin ejecutar, idempotencia, concurrencia y aislamiento A/B.

Las pruebas HTTP usan `uvicorn` real sobre TCP y el principal SQL restringido
(`fs_saas_runtime`); las verificaciones puntuales usan el administrador del
laboratorio para leer stock, kardex, bitacora y filas persistidas.
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
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
MANIFIESTO = Path(
    os.environ.get(
        "FASHIONSTORE_LAB_5BN_MANIFEST",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Temp" / "opencode" / "fs_lab_5bn" / "lab_5bn_manifest.json"),
    )
)
MARCA = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
PASSWORD = "Lab5bn#Clave2026"
PASSWORD_CLIENTE = "Lab5bn#Cliente2026"


class Registro:
    """Acumula evidencias por modulo para escribir archivos 5BN unicos."""

    def __init__(self) -> None:
        self.secciones: dict[str, list[str]] = {
            "CATALOGO": [], "COMPRAS": [], "DEVOLUCIONES": [], "ROLES": [],
        }

    def ok(self, seccion: str, nombre: str, detalle: str) -> None:
        self.secciones[seccion].append(f"[PASS] {nombre}: {detalle}")

    def escribir(self, lab: dict) -> list[Path]:
        escritas: list[Path] = []
        if not any(self.secciones.values()):
            return escritas
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        for seccion, lineas in self.secciones.items():
            if not lineas:
                continue
            ruta = EVIDENCIAS / f"EVIDENCIA_{seccion}_5BN_{MARCA}.txt"
            base, n = ruta, 2
            while ruta.exists():
                ruta = base.with_name(f"EVIDENCIA_{seccion}_5BN_{MARCA}_v{n:02d}.txt")
                n += 1
            encabezado = [
                f"EVIDENCIA {seccion} - ETAPA E2 (5BN) - LABORATORIO",
                f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                "Cluster: " + lab["cluster"].get("version", "?") + " en "
                + lab["cluster"]["host"] + ":" + str(lab["cluster"]["puerto"])
                + " / " + lab["cluster"]["base"],
                "Principales: fs_saas_runtime (tenant) y saas_admin_runtime (global)",
                "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
                "-" * 78,
            ]
            ruta.write_text("\n".join(encabezado + lineas) + "\n", encoding="utf-8")
            escritas.append(ruta)
        return escritas


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
            "Laboratorio 5BN no aprovisionado; ejecute "
            "database/saas/77_laboratorio_operativo_5bn.py --init --confirm-desechable"
        )
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    from tests_saas._lab_readiness_5bs import motivo_no_disponible

    motivo = motivo_no_disponible(
        datos, 55444, "5BN",
        "python database/saas/101_runner_regresion_reproducible_5bs.py --lab 5bn")
    if motivo:
        pytest.skip(motivo)
    if datos.get("migracion_5bn") != "20261030000700_saas_compras_devoluciones_5bn.sql":
        pytest.skip("El laboratorio no tiene aplicada la migracion 5BN")
    return datos


@pytest.fixture(scope="session")
def servidor(lab: dict):
    puerto = _puerto_libre()
    clave_superadmin = secrets.token_urlsafe(32)
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
        "SAAS_COMPRAS_ENABLED": "true",
        "SAAS_DEVOLUCIONES_ENABLED": "true",
        "SAAS_TENANT_ASSERTION_KEY": lab["assertion_key"],
        "SAAS_ACTOR_SQL_OWNER": lab["roles"]["actor_owner"],
        "APP_ENV": "lab-5bn",
        "PYTHONPATH": str(ROOT),
    }
    registro = ROOT / ".pytest_cache" / f"uvicorn_5bn_{puerto}.log"
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
        pytest.fail(f"uvicorn no arranco con los principales 5BN: {contenido}")
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
    if MANIFIESTO.exists() and any(REGISTRO.secciones.values()):
        lab = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        rutas = REGISTRO.escribir(lab)
        print("\nEvidencias 5BN escritas:\n  " + "\n  ".join(str(r) for r in rutas))


@pytest.fixture(scope="session")
def api(servidor):
    with httpx.Client(base_url=servidor["base"], timeout=60.0) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def admin_cab(servidor):
    return {"X-SaaS-Admin-Key": servidor["clave"]}


# ---------------------------------------------------------------------------
# Utilidades SQL / HTTP
# ---------------------------------------------------------------------------


def _sql(lab: dict, consulta: str, args=None):
    import psycopg

    with psycopg.connect(lab["admin_dsn"]) as conn:
        cursor = conn.execute(consulta, args)
        if cursor.description is None:
            return []
        return cursor.fetchall()


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


def _nueva_variante(lab: dict, empresa: dict, etiqueta: str) -> dict:
    """Variante + temporada aisladas para una prueba (SQL de laboratorio)."""
    sufijo = f"{etiqueta}-{secrets.token_hex(3)}"
    variante_id = _sql(
        lab,
        "INSERT INTO public.variante_producto (empresa_id, producto_id, talla_id, "
        "color_id, sku, estado) VALUES (%s,%s,%s,%s,%s,TRUE) RETURNING id",
        (empresa["empresa_id"], empresa["producto_id"], 1, 1,
         f"SKU-{sufijo}".upper()[:80]))[0][0]
    temporada_id = _sql(
        lab,
        "INSERT INTO public.temporada (empresa_id, nombre, fecha_inicio, fecha_fin, "
        "estado) VALUES (%s,%s,'2026-01-01','2026-12-31',TRUE) RETURNING id",
        (empresa["empresa_id"], f"T-{sufijo}"))[0][0]
    return {"variante_id": variante_id, "temporada_id": temporada_id}


def _nuevo_inventario(lab: dict, empresa: dict, variante_id: int,
                      temporada_id: int, stock: int) -> int:
    return _sql(
        lab,
        "INSERT INTO public.inventario (empresa_id, sucursal_id, "
        "variante_producto_id, temporada_id, stock_actual, stock_reservado, "
        "fecha_actualizacion) VALUES (%s,%s,%s,%s,%s,0,NOW()) RETURNING id",
        (empresa["empresa_id"], empresa["sucursal_id"], variante_id,
         temporada_id, stock))[0][0]


def _stock(lab: dict, inventario_id: int) -> tuple[int, int]:
    fila = _sql(lab, "SELECT stock_actual, stock_reservado FROM public.inventario "
                     "WHERE id=%s", (inventario_id,))[0]
    return int(fila[0]), int(fila[1])


def _kardex(lab: dict, inventario_id: int, tipo: str) -> int:
    return int(_sql(
        lab, "SELECT count(*) FROM public.movimiento_inventario "
             "WHERE inventario_id=%s AND tipo=%s", (inventario_id, tipo))[0][0])


def _venta(api, empresa: dict, inventario_id: int, cantidad: int = 1):
    return api.post("/tenant/ventas/contado", headers=_cabecera(empresa["token"]), json={
        "sucursal_id": empresa["sucursal_id"],
        "operacion_id": str(uuid.uuid4()),
        "metodo_pago": "EFECTIVO",
        "lineas": [{"inventario_id": inventario_id, "cantidad": cantidad}],
    })


def _crear_orden(api, empresa: dict, lineas: list[dict], **extra):
    cuerpo = {
        "proveedor_id": empresa["proveedor_id"],
        "sucursal_id": empresa["sucursal_id"],
        "observacion": "prueba 5BN",
        "lineas": lineas,
    }
    cuerpo.update(extra)
    return api.post("/tenant/ordenes-compra", headers=_cabecera(empresa["token"]),
                    json=cuerpo)


def _recibir(api, empresa: dict, orden_id: int, lineas: list[dict],
             operacion_id: str | None = None):
    return api.post(f"/tenant/ordenes-compra/{orden_id}/recepciones",
                    headers=_cabecera(empresa["token"]),
                    json={"operacion_id": operacion_id or str(uuid.uuid4()),
                          "observacion": "recepcion 5BN", "lineas": lineas})


def _crear_devolucion(api, empresa: dict, venta_id: int, items: list[dict],
                      operacion_id: str | None = None, motivo="talla incorrecta"):
    return api.post("/tenant/devoluciones", headers=_cabecera(empresa["token"]),
                    json={"venta_id": venta_id, "motivo": motivo,
                          "observacion": "prueba 5BN",
                          "operacion_id": operacion_id or str(uuid.uuid4()),
                          "items": items})


def _detalles_venta(lab: dict, venta_id: int) -> list[tuple[int, int, int]]:
    filas = _sql(lab, "SELECT id, inventario_id, cantidad FROM public.detalle_venta "
                      "WHERE venta_id=%s ORDER BY id", (venta_id,))
    return [(int(f[0]), int(f[1]), int(f[2])) for f in filas]


# ---------------------------------------------------------------------------
# Circuito A/B
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def circuito(api, admin_cab, lab):
    sufijo = secrets.token_hex(4)
    planes = {p["codigo"]: p for p in api.get(
        "/saas/superadmin/planes", headers=admin_cab).json()}
    ciudad_id = _sql(lab, "SELECT id FROM public.ciudad WHERE estado IS TRUE "
                          "ORDER BY id LIMIT 1")[0][0]
    hoy = date.today()
    creadas: dict[str, dict] = {}
    tokens_catalogo: dict[str, str] = {}

    # Talla/color extra para poder editar variantes sin depender del seed.
    talla_extra_nombre = f"5BN-TALLA-{sufijo}"
    color_extra_nombre = f"5BN-COLOR-{sufijo}"
    talla_extra = _sql(
        lab, "INSERT INTO public.talla (nombre, estado) VALUES (%s, TRUE) "
             "RETURNING id", (talla_extra_nombre,))[0][0]
    color_extra = _sql(
        lab, "INSERT INTO public.color (nombre, estado) VALUES (%s, TRUE) "
             "RETURNING id", (color_extra_nombre,))[0][0]
    talla_inactiva = _sql(
        lab, "INSERT INTO public.talla (nombre, estado) VALUES (%s, FALSE) "
             "RETURNING id", (f"5BN-TALLA-OFF-{sufijo}",))[0][0]

    for etiqueta, plan, stock in (("a", "PROFESIONAL", 40), ("b", "BASICO", 10)):
        slug = f"lab5bn-{etiqueta}-{sufijo}"
        alta = api.post("/saas/superadmin/empresas", headers=admin_cab, json={
            "razon_social": f"Empresa {etiqueta.upper()} 5BN {sufijo}",
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
        bloque = {
            "empresa_id": empresa_id, "slug": slug, "correo": correo,
            "sucursal_id": primer.json()["sucursal_id"],
            "admin_usuario_id": primer.json()["usuario_id"],
            "plan": plan, "stock_inicial": stock,
        }
        sesion = _login(api, correo, PASSWORD, slug)
        assert sesion.status_code == 200, sesion.text
        bloque["token"] = sesion.json()["access_token"]

        categoria = api.post("/tenant/catalogo/categorias",
                             headers=_cabecera(bloque["token"]),
                             json={"nombre": f"Camisas {etiqueta.upper()} {sufijo}"})
        assert categoria.status_code == 201, categoria.text
        producto = api.post("/tenant/catalogo/productos",
                            headers=_cabecera(bloque["token"]),
                            json={"categoria_id": categoria.json()["id"],
                                  "nombre": f"Camisa {etiqueta.upper()} {sufijo}",
                                  "descripcion": "prenda ficticia 5BN",
                                  "precio": "150.00"})
        assert producto.status_code == 201, producto.text
        bloque["producto_id"] = producto.json()["id"]

        # P0: la variante se crea por HTTP con el principal restringido.
        variante = api.post(
            f"/tenant/catalogo/productos/{bloque['producto_id']}/variantes",
            headers=_cabecera(bloque["token"]),
            json={"talla_id": 1, "color_id": 1,
                  "sku": f"SKU-{etiqueta.upper()}-{sufijo}"})
        assert variante.status_code == 201, variante.text
        bloque["variante_id"] = variante.json()["id"]
        tokens_catalogo[f"{etiqueta}_variante_http"] = variante.text

        temporada_id = _sql(
            lab,
            "INSERT INTO public.temporada (empresa_id, nombre, fecha_inicio, "
            "fecha_fin, estado) VALUES (%s,%s,'2026-01-01','2026-12-31',TRUE) "
            "RETURNING id",
            (empresa_id, f"T-{etiqueta}-{sufijo}"))[0][0]
        bloque["temporada_id"] = temporada_id
        bloque["inventario_id"] = _nuevo_inventario(
            lab, bloque, bloque["variante_id"], temporada_id, stock)

        # Proveedor + asociacion producto-proveedor (no hay ruta tenant aun).
        proveedor_id = _sql(
            lab,
            "INSERT INTO public.proveedor (empresa_id, razon_social, nit, correo, "
            "telefono, direccion, estado) VALUES (%s,%s,%s,%s,%s,%s,TRUE) "
            "RETURNING id",
            (empresa_id, f"Proveedor {etiqueta.upper()} {sufijo}",
             f"NITP-{etiqueta}-{sufijo}", f"prov.{etiqueta}.{sufijo}@lab.invalid",
             None, "Av. Lab 1"))[0][0]
        _sql(
            lab,
            "INSERT INTO public.proveedor_producto (empresa_id, proveedor_id, "
            "producto_id, costo_referencia, estado) VALUES (%s,%s,%s,%s,TRUE)",
            (empresa_id, proveedor_id, bloque["producto_id"], 100))
        bloque["proveedor_id"] = proveedor_id

        # Cajero (personal sin permiso de compras/devoluciones) y cliente.
        cajero_correo = f"cajero.{etiqueta}.{sufijo}@lab.invalid"
        cajero_usuario = _sql(
            lab,
            "INSERT INTO public.usuario (rol_id, correo, password_hash, "
            "fecha_creacion, estado) SELECT r.id, %s, %s, NOW(), TRUE "
            "FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE "
            "RETURNING id",
            (cajero_correo, _hash(PASSWORD)))[0][0]
        _sql(
            lab,
            "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
            "SELECT %s, %s, r.id, TRUE FROM public.rol r "
            "WHERE r.nombre='CAJERO' AND r.estado IS TRUE",
            (cajero_usuario, empresa_id))
        _sql(
            lab,
            "INSERT INTO public.empleado (empresa_id, usuario_id, sucursal_id, "
            "nombres, apellidos, ci, telefono, fecha_contratacion, estado) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,CURRENT_DATE,TRUE)",
            (empresa_id, cajero_usuario, bloque["sucursal_id"], "Cajero",
             etiqueta.upper(), f"CI-CJ-{etiqueta.upper()}-{sufijo}"))
        sesion_cajero = _login(api, cajero_correo, PASSWORD, slug)
        assert sesion_cajero.status_code == 200, sesion_cajero.text
        bloque["cajero"] = {"correo": cajero_correo,
                            "token": sesion_cajero.json()["access_token"],
                            "usuario_id": cajero_usuario}

        cliente_correo = f"cliente.{etiqueta}.{sufijo}@lab.invalid"
        cliente_usuario = _sql(
            lab,
            "INSERT INTO public.usuario (rol_id, correo, password_hash, "
            "fecha_creacion, estado) SELECT r.id, %s, %s, NOW(), TRUE "
            "FROM public.rol r WHERE r.nombre='CLIENTE' AND r.estado IS TRUE "
            "RETURNING id",
            (cliente_correo, _hash(PASSWORD_CLIENTE)))[0][0]
        _sql(
            lab,
            "INSERT INTO public.usuario_empresa (usuario_id, empresa_id, rol_id, estado) "
            "SELECT %s, %s, r.id, TRUE FROM public.rol r "
            "WHERE r.nombre='CLIENTE' AND r.estado IS TRUE",
            (cliente_usuario, empresa_id))
        _sql(
            lab,
            "INSERT INTO public.cliente (empresa_id, usuario_id, nombre, apellido, "
            "ci, estado) VALUES (%s,%s,%s,%s,%s,TRUE)",
            (empresa_id, cliente_usuario, "Cliente", etiqueta.upper(),
             f"CI-CL-{etiqueta.upper()}-{sufijo}"))
        sesion_cliente = _login(api, cliente_correo, PASSWORD_CLIENTE, slug, "cliente")
        assert sesion_cliente.status_code == 200, sesion_cliente.text
        bloque["cliente"] = {"correo": cliente_correo,
                             "token": sesion_cliente.json()["access_token"],
                             "usuario_id": cliente_usuario}

        creadas[etiqueta] = bloque

    creadas["sufijo"] = sufijo
    creadas["talla_extra"] = talla_extra
    creadas["talla_extra_nombre"] = talla_extra_nombre
    creadas["color_extra"] = color_extra
    creadas["color_extra_nombre"] = color_extra_nombre
    creadas["talla_inactiva"] = talla_inactiva
    creadas["variante_http"] = tokens_catalogo
    return creadas


# ===========================================================================
# P0 - Catalogo: variantes sin HTTP 500
# ===========================================================================


def test_p0_crear_variante_http_201(api, circuito, lab):
    a = circuito["a"]
    sufijo = secrets.token_hex(3)
    respuesta = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": f"P0-{sufijo}"})
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["sku"] == f"P0-{sufijo}"
    assert cuerpo["producto_id"] == a["producto_id"]
    assert cuerpo["talla"] == "M" and cuerpo["color"] == "Negro"
    filas = _sql(lab, "SELECT count(*) FROM public.variante_producto "
                      "WHERE id=%s AND empresa_id=%s AND sku=%s",
                 (cuerpo["id"], a["empresa_id"], f"P0-{sufijo}"))[0][0]
    assert filas == 1
    REGISTRO.ok("CATALOGO", "crear_variante_201",
                f"POST HTTP 201 con principal restringido (variante {cuerpo['id']})")
    a["variante_p0"] = cuerpo["id"]


def test_p0_patch_variante_propia_200(api, circuito, lab):
    a = circuito["a"]
    creada = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["token"]),
        json={"talla_id": 1, "color_id": 1,
              "sku": f"P0-PATCH-{secrets.token_hex(3)}"})
    assert creada.status_code == 201, creada.text
    variante_id = creada.json()["id"]
    respuesta = api.patch(
        f"/tenant/catalogo/variantes/{variante_id}",
        headers=_cabecera(a["token"]),
        json={"talla_id": circuito["talla_extra"],
              "color_id": circuito["color_extra"],
              "sku": f"P0-EDIT-{secrets.token_hex(2)}"})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["talla"] == circuito["talla_extra_nombre"]
    assert cuerpo["color"] == circuito["color_extra_nombre"]
    persistido = _sql(lab, "SELECT talla_id, color_id FROM public.variante_producto "
                            "WHERE id=%s AND empresa_id=%s",
                      (variante_id, a["empresa_id"]))[0]
    assert persistido == (circuito["talla_extra"], circuito["color_extra"])
    REGISTRO.ok("CATALOGO", "patch_variante_200",
                "PATCH propio con talla/color distintos responde 200 (antes 500)")


def test_p0_variante_duplicada_409(api, circuito):
    a = circuito["a"]
    sku = f"P0-DUP-{secrets.token_hex(3)}"
    primera = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": sku})
    assert primera.status_code == 201, primera.text
    segunda = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": sku})
    assert segunda.status_code == 409, segunda.text
    REGISTRO.ok("CATALOGO", "variante_duplicada_409",
                "SKU repetido en la misma empresa => 409 (no 500)")


def test_p0_variante_ajena_404(api, circuito):
    a, b = circuito["a"], circuito["b"]
    ajeno = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(b["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": f"P0-B-{secrets.token_hex(3)}"})
    assert ajeno.status_code == 404, ajeno.text
    propia = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": f"P0-A-{secrets.token_hex(3)}"})
    assert propia.status_code == 201, propia.text
    edicion_ajena = api.patch(
        f"/tenant/catalogo/variantes/{propia.json()['id']}",
        headers=_cabecera(b["token"]), json={"sku": "P0-HACK"})
    assert edicion_ajena.status_code == 404, edicion_ajena.text
    inexistente = api.patch(
        "/tenant/catalogo/variantes/99999999",
        headers=_cabecera(b["token"]), json={"sku": "P0-HACK2"})
    assert inexistente.status_code == 404
    assert inexistente.json()["detail"] == edicion_ajena.json()["detail"]
    REGISTRO.ok("CATALOGO", "variante_ajena_404",
                "B no crea ni edita variantes de A y el detalle es indistinguible")


def test_p0_variante_no_admin_403(api, circuito):
    a = circuito["a"]
    cajero = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["cajero"]["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": f"P0-CJ-{secrets.token_hex(3)}"})
    assert cajero.status_code == 403, cajero.text
    cliente = api.post(
        f"/tenant/catalogo/productos/{a['producto_id']}/variantes",
        headers=_cabecera(a["cliente"]["token"]),
        json={"talla_id": 1, "color_id": 1, "sku": f"P0-CL-{secrets.token_hex(3)}"})
    assert cliente.status_code == 403, cliente.text
    REGISTRO.ok("ROLES", "catalogo_no_admin_403",
                "CAJERO y CLIENTE no pueden escribir catalogo (403)")


def test_p0_mapeo_42501_no_es_500(monkeypatch):
    """Contrato de error: un 42501 del principal restringido NO puede ser 500.

    5BZ: este caso es unitario y offline. Antes dependia del `DATABASE_URL`
    heredado del proceso (el `.env` del repo apunta a produccion); la guardia
    fail-closed de arranque lo rechaza, que es el comportamiento correcto.
    Se fija un DSN SQLite ANTES de importar el router para que la prueba no
    dependa del entorno del operador.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///./saas_auth_unit_test.db")

    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError

    from app.modules.catalogo.api.tenant_write_router import _operacion

    class _Origen(Exception):
        sqlstate = "42501"

    class _SesionFalsa:
        def __init__(self):
            self.revertida = False

        def rollback(self):
            self.revertida = True

    sesion = _SesionFalsa()

    def falla():
        raise DBAPIError("SELECT ... FOR UPDATE", {}, _Origen("permiso denegado"))

    with pytest.raises(HTTPException) as exc:
        _operacion(sesion, falla)
    assert sesion.revertida is True
    assert exc.value.status_code == 503
    detalle = str(exc.value.detail).lower()
    assert "permiso denegado" not in detalle
    assert "talla" not in detalle and "color" not in detalle
    REGISTRO.ok("CATALOGO", "mapeo_42501",
                "42501 => 503 con rollback y detalle neutro (sin SQL ni tabla)")


def test_p0_privilegios_y_trigger_catalogo(circuito, lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"]) as conn:
        privilegios = conn.execute(
            "SELECT has_table_privilege(current_user,'public.talla','UPDATE'),"
            " has_table_privilege(current_user,'public.color','UPDATE'),"
            " has_table_privilege(current_user,'public.talla','SELECT')"
        ).fetchone()
        assert privilegios == (False, False, True), privilegios
        for clausula in ("FOR UPDATE", "FOR SHARE", "FOR KEY SHARE"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(
                        f"SELECT id FROM public.talla ORDER BY id LIMIT 1 {clausula}"
                    ).fetchall()

    trigger = _sql(
        lab,
        "SELECT count(*) FROM pg_trigger WHERE tgrelid="
        "'public.variante_producto'::regclass AND NOT tgisinternal AND "
        "tgname='trg_5bn_validar_catalogos_globales'")[0][0]
    assert trigger == 1

    a = circuito["a"]
    import psycopg as pg

    with pg.connect(lab["admin_dsn"]) as conn:
        with pytest.raises(pg.Error) as exc:
            conn.execute(
                "INSERT INTO public.variante_producto (empresa_id, producto_id, "
                "talla_id, color_id, sku, estado) VALUES (%s,%s,%s,1,%s,TRUE)",
                (a["empresa_id"], a["producto_id"], circuito["talla_inactiva"],
                 f"P0-OFF-{secrets.token_hex(3)}"))
        assert exc.value.sqlstate == "23514", exc.value.sqlstate
        conn.rollback()

    REGISTRO.ok(
        "CATALOGO", "privilegios_talla_color",
        "runtime sin UPDATE en talla/color; los 3 modos de bloqueo dan 42501; "
        "trigger de invariante rechaza talla inactiva con 23514")


# ===========================================================================
# P1 - Compras y recepcion de mercaderia
# ===========================================================================


def test_p1_crear_orden_borrador_sin_stock(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1crear")
    respuesta = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 5, "costo_unitario": "100.00"}])
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "BORRADOR"
    assert cuerpo["stock_modificado"] is False
    assert cuerpo["total_lineas"] == 1
    orden_id = cuerpo["orden_compra_id"]
    inventarios = _sql(
        lab, "SELECT count(*) FROM public.inventario WHERE empresa_id=%s "
             "AND variante_producto_id=%s AND temporada_id=%s",
        (a["empresa_id"], contexto["variante_id"], contexto["temporada_id"]))[0][0]
    assert inventarios == 0
    bitacora = _sql(
        lab, "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s AND "
             "entidad_afectada='orden_compra' AND accion='ORDEN_COMPRA_CREADA' "
             "AND descripcion LIKE %s", (a["empresa_id"], f"%{orden_id}%"))[0][0]
    assert bitacora == 1
    detalle = api.get(f"/tenant/ordenes-compra/{orden_id}",
                      headers=_cabecera(a["token"]))
    assert detalle.status_code == 200, detalle.text
    linea = detalle.json()["lineas"][0]
    assert linea["cantidad_pendiente"] == 5 and linea["cantidad_recibida"] == 0
    REGISTRO.ok("COMPRAS", "crear_borrador_sin_stock",
                f"orden {orden_id} BORRADOR, 0 inventario, 1 bitacora")


def test_p1_enviar_sin_stock_y_replay(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1enviar")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 4, "costo_unitario": "10.00"}]).json()["orden_compra_id"]
    primero = api.post(f"/tenant/ordenes-compra/{orden}/enviar",
                       headers=_cabecera(a["token"]))
    assert primero.status_code == 200, primero.text
    assert primero.json()["estado"] == "ENVIADA" and primero.json()["idempotente"] is False
    segundo = api.post(f"/tenant/ordenes-compra/{orden}/enviar",
                       headers=_cabecera(a["token"]))
    assert segundo.status_code == 200 and segundo.json()["idempotente"] is True
    assert _sql(lab, "SELECT count(*) FROM public.inventario WHERE empresa_id=%s "
                     "AND variante_producto_id=%s",
                (a["empresa_id"], contexto["variante_id"]))[0][0] == 0
    REGISTRO.ok("COMPRAS", "enviar_sin_stock",
                "ENVIADA idempotente sin crear inventario ni kardex")


def test_p1_recepcion_parcial_y_total(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1recibir")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 5, "costo_unitario": "99.50"}]).json()["orden_compra_id"]
    assert api.post(f"/tenant/ordenes-compra/{orden}/enviar",
                    headers=_cabecera(a["token"])).status_code == 200
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]

    parcial = _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                       "cantidad": 2}])
    assert parcial.status_code == 201, parcial.text
    assert parcial.json()["estado_orden"] == "PARCIAL"
    assert parcial.json()["unidades_recibidas"] == 2
    inventario_id = parcial.json()["stock"][0]["inventario_id"]
    assert _stock(lab, inventario_id) == (2, 0)
    assert _kardex(lab, inventario_id, "ENTRADA_COMPRA") == 1

    total = _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                      "cantidad": 3}])
    assert total.status_code == 201, total.text
    assert total.json()["estado_orden"] == "RECIBIDA"
    assert _stock(lab, inventario_id) == (5, 0)
    assert _kardex(lab, inventario_id, "ENTRADA_COMPRA") == 2
    final = api.get(f"/tenant/ordenes-compra/{orden}", headers=_cabecera(a["token"])).json()
    assert final["estado"] == "RECIBIDA"
    assert final["lineas"][0]["cantidad_recibida"] == 5
    assert final["lineas"][0]["cantidad_pendiente"] == 0
    REGISTRO.ok("COMPRAS", "recepcion_parcial_y_total",
                f"2+3 sobre 5: stock {inventario_id}=5, 2 movimientos ENTRADA_COMPRA")


def test_p1_recepcion_excede_pendiente_409(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1excede")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 3, "costo_unitario": "5.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    respuesta = _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                          "cantidad": 4}])
    assert respuesta.status_code == 409, respuesta.text
    assert _sql(lab, "SELECT count(*) FROM public.inventario WHERE empresa_id=%s "
                     "AND variante_producto_id=%s",
                (a["empresa_id"], contexto["variante_id"]))[0][0] == 0
    assert _sql(lab, "SELECT count(*) FROM public.recepcion_compra WHERE "
                     "orden_compra_id=%s", (orden,))[0][0] == 0
    REGISTRO.ok("COMPRAS", "excede_pendiente_409",
                "recibir 4 de 3 => 409 sin inventario ni recepcion parcial")


def test_p1_recepcion_replay_12(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1replay")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 2, "costo_unitario": "7.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    operacion = str(uuid.uuid4())
    ids = set()
    for _ in range(12):
        respuesta = _recibir(api, a, orden,
                             [{"detalle_orden_compra_id": detalle_id, "cantidad": 1}],
                             operacion_id=operacion)
        assert respuesta.status_code in (200, 201), respuesta.text
        ids.add(respuesta.json()["recepcion_id"])
    assert len(ids) == 1
    inventario_id = _sql(lab, "SELECT id FROM public.inventario WHERE empresa_id=%s "
                              "AND variante_producto_id=%s",
                         (a["empresa_id"], contexto["variante_id"]))[0][0]
    assert _stock(lab, inventario_id) == (1, 0)
    assert _kardex(lab, inventario_id, "ENTRADA_COMPRA") == 1
    REGISTRO.ok("COMPRAS", "replay_12",
                "12 reenvios del mismo UUID => 1 recepcion y +1 unidad")


def test_p1_recepcion_concurrente_misma_operacion(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1conc")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 2, "costo_unitario": "3.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    operacion = str(uuid.uuid4())

    def enviar(_):
        with httpx.Client(base_url=str(api.base_url), timeout=60.0) as propio:
            return _recibir(propio, a, orden,
                            [{"detalle_orden_compra_id": detalle_id, "cantidad": 1}],
                            operacion_id=operacion)

    with ThreadPoolExecutor(max_workers=4) as pool:
        respuestas = list(pool.map(enviar, range(4)))
    assert all(r.status_code in (200, 201) for r in respuestas), \
        [r.text for r in respuestas]
    assert len({r.json()["recepcion_id"] for r in respuestas}) == 1
    inventario_id = _sql(lab, "SELECT id FROM public.inventario WHERE empresa_id=%s "
                              "AND variante_producto_id=%s",
                         (a["empresa_id"], contexto["variante_id"]))[0][0]
    assert _stock(lab, inventario_id) == (1, 0)
    REGISTRO.ok("COMPRAS", "concurrente_misma_operacion",
                "4 envios simultaneos del mismo UUID => 1 recepcion")


def test_p1_recepcion_concurrente_dos_operaciones(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1carrera")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 2, "costo_unitario": "3.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]

    def enviar(_):
        with httpx.Client(base_url=str(api.base_url), timeout=60.0) as propio:
            return _recibir(propio, a, orden,
                            [{"detalle_orden_compra_id": detalle_id, "cantidad": 2}],
                            operacion_id=str(uuid.uuid4())).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        estados = list(pool.map(enviar, range(2)))
    assert estados.count(201) == 1, estados
    assert all(e in (201, 409) for e in estados), estados
    inventario_id = _sql(lab, "SELECT id FROM public.inventario WHERE empresa_id=%s "
                              "AND variante_producto_id=%s",
                         (a["empresa_id"], contexto["variante_id"]))[0][0]
    assert _stock(lab, inventario_id) == (2, 0)
    assert _kardex(lab, inventario_id, "ENTRADA_COMPRA") == 1
    REGISTRO.ok("COMPRAS", "concurrente_dos_operaciones",
                f"estados={estados}: 2 unidades, 1 solo movimiento")


def test_p1_mismo_uuid_otra_carga_409(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1huella")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 3, "costo_unitario": "2.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    operacion = str(uuid.uuid4())
    primera = _recibir(api, a, orden,
                       [{"detalle_orden_compra_id": detalle_id, "cantidad": 1}],
                       operacion_id=operacion)
    assert primera.status_code == 201, primera.text
    inventario_id = primera.json()["stock"][0]["inventario_id"]
    conflicto = _recibir(api, a, orden,
                         [{"detalle_orden_compra_id": detalle_id, "cantidad": 2}],
                         operacion_id=operacion)
    assert conflicto.status_code == 409, conflicto.text
    assert _stock(lab, inventario_id) == (1, 0)
    REGISTRO.ok("COMPRAS", "uuid_otra_carga_409",
                "mismo UUID con cantidad distinta => 409 sin mutar inventario")


def test_p1_orden_recibida_no_recepciona(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1cerrada")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 1, "costo_unitario": "1.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    assert _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                     "cantidad": 1}]).status_code == 201
    repetida = _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                         "cantidad": 1}])
    assert repetida.status_code == 409, repetida.text
    cancelada = api.post(f"/tenant/ordenes-compra/{orden}/cancelar",
                         headers=_cabecera(a["token"]), json={"observacion": "x"})
    assert cancelada.status_code == 409, cancelada.text
    REGISTRO.ok("COMPRAS", "orden_recibida_cerrada",
                "RECIBIDA no vuelve a recibir ni se cancela (409)")


def test_p1_cancelacion_valida_y_transiciones(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p1cancel")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 2, "costo_unitario": "4.00"}]).json()["orden_compra_id"]
    cancelada = api.post(f"/tenant/ordenes-compra/{orden}/cancelar",
                         headers=_cabecera(a["token"]), json={"observacion": "ya no"})
    assert cancelada.status_code == 200, cancelada.text
    assert cancelada.json()["estado"] == "CANCELADA"
    repetida = api.post(f"/tenant/ordenes-compra/{orden}/cancelar",
                        headers=_cabecera(a["token"]))
    assert repetida.status_code == 200 and repetida.json()["idempotente"] is True
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    assert _recibir(api, a, orden, [{"detalle_orden_compra_id": detalle_id,
                                     "cantidad": 1}]).status_code == 409
    sin_lineas = api.post("/tenant/ordenes-compra", headers=_cabecera(a["token"]),
                          json={"proveedor_id": a["proveedor_id"],
                                "sucursal_id": a["sucursal_id"], "lineas": []})
    assert sin_lineas.status_code == 422
    con_empresa = api.post("/tenant/ordenes-compra", headers=_cabecera(a["token"]),
                           json={"proveedor_id": a["proveedor_id"],
                                 "sucursal_id": a["sucursal_id"],
                                 "empresa_id": a["empresa_id"],
                                 "lineas": [{"variante_producto_id":
                                             contexto["variante_id"],
                                             "temporada_id": contexto["temporada_id"],
                                             "cantidad": 1,
                                             "costo_unitario": "1.00"}]})
    assert con_empresa.status_code == 422
    REGISTRO.ok("COMPRAS", "cancelacion_y_cuerpo",
                "cancelar BORRADOR 200/idempotente; sin lineas y con empresa_id => 422")


def test_p1_cruce_empresa_404(api, circuito, lab):
    a, b = circuito["a"], circuito["b"]
    contexto = _nueva_variante(lab, a, "p1cruce")
    orden = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 2, "costo_unitario": "1.00"}]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    detalle_id = api.get(f"/tenant/ordenes-compra/{orden}",
                         headers=_cabecera(a["token"])).json()["lineas"][0]["id"]
    assert api.get(f"/tenant/ordenes-compra/{orden}",
                   headers=_cabecera(b["token"])).status_code == 404
    ajeno = _recibir(api, b, orden, [{"detalle_orden_compra_id": detalle_id,
                                      "cantidad": 1}])
    assert ajeno.status_code in (403, 404), ajeno.text
    proveedor_ajeno = _crear_orden(api, a, [{
        "variante_producto_id": contexto["variante_id"],
        "temporada_id": contexto["temporada_id"],
        "cantidad": 1, "costo_unitario": "1.00"}],
        proveedor_id=b["proveedor_id"])
    assert proveedor_ajeno.status_code in (403, 404), proveedor_ajeno.text
    assert _sql(lab, "SELECT count(*) FROM public.orden_compra WHERE id=%s "
                     "AND empresa_id=%s", (orden, b["empresa_id"]))[0][0] == 0
    REGISTRO.ok("COMPRAS", "cruce_empresa",
                "B no ve ni recibe la orden de A; proveedor ajeno rechazado")


def test_p1_rollback_multilinea(api, circuito, lab):
    a = circuito["a"]
    contexto_1 = _nueva_variante(lab, a, "p1roll1")
    contexto_2 = _nueva_variante(lab, a, "p1roll2")
    orden = _crear_orden(api, a, [
        {"variante_producto_id": contexto_1["variante_id"],
         "temporada_id": contexto_1["temporada_id"],
         "cantidad": 2, "costo_unitario": "1.00"},
        {"variante_producto_id": contexto_2["variante_id"],
         "temporada_id": contexto_2["temporada_id"],
         "cantidad": 1, "costo_unitario": "1.00"},
    ]).json()["orden_compra_id"]
    api.post(f"/tenant/ordenes-compra/{orden}/enviar", headers=_cabecera(a["token"]))
    lineas = api.get(f"/tenant/ordenes-compra/{orden}",
                     headers=_cabecera(a["token"])).json()["lineas"]
    detalle_1, detalle_2 = lineas[0]["id"], lineas[1]["id"]
    respuesta = _recibir(api, a, orden, [
        {"detalle_orden_compra_id": detalle_1, "cantidad": 2},
        {"detalle_orden_compra_id": detalle_2, "cantidad": 5},
    ])
    assert respuesta.status_code == 409, respuesta.text
    assert _sql(lab, "SELECT count(*) FROM public.recepcion_compra WHERE "
                     "orden_compra_id=%s", (orden,))[0][0] == 0
    assert _sql(lab, "SELECT count(*) FROM public.detalle_recepcion_compra r "
                     "JOIN public.recepcion_compra rc ON rc.id=r.recepcion_id "
                     "WHERE rc.orden_compra_id=%s", (orden,))[0][0] == 0
    for contexto in (contexto_1, contexto_2):
        assert _sql(lab, "SELECT count(*) FROM public.inventario WHERE empresa_id=%s "
                         "AND variante_producto_id=%s",
                    (a["empresa_id"], contexto["variante_id"]))[0][0] == 0
    estado = _sql(lab, "SELECT estado FROM public.orden_compra WHERE id=%s",
                  (orden,))[0][0]
    assert estado == "ENVIADA"
    REGISTRO.ok("COMPRAS", "rollback_multilinea",
                "linea 2 excede el saldo: 0 recepciones, 0 inventario y orden ENVIADA")


def test_p1_roles_y_suscripcion(api, circuito, admin_cab, lab):
    a, b = circuito["a"], circuito["b"]
    contexto = _nueva_variante(lab, a, "p1roles")
    linea = [{"variante_producto_id": contexto["variante_id"],
              "temporada_id": contexto["temporada_id"],
              "cantidad": 1, "costo_unitario": "1.00"}]
    cajero = _crear_orden(api, {"token": a["cajero"]["token"],
                                "proveedor_id": a["proveedor_id"],
                                "sucursal_id": a["sucursal_id"]}, linea)
    assert cajero.status_code == 403, cajero.text
    cliente = _crear_orden(api, {"token": a["cliente"]["token"],
                                 "proveedor_id": a["proveedor_id"],
                                 "sucursal_id": a["sucursal_id"]}, linea)
    assert cliente.status_code == 403, cliente.text

    subs = api.get(f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones",
                   headers=admin_cab).json()
    activa = next(s for s in subs if s["estado"] == "ACTIVA")
    suspendida = api.patch(
        f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones/"
        f"{activa['id']}/estado",
        headers=admin_cab, json={"estado": "SUSPENDIDA"})
    assert suspendida.status_code == 200, suspendida.text
    bloqueada = _crear_orden(api, b, [{
        "variante_producto_id": b["variante_id"],
        "temporada_id": b["temporada_id"],
        "cantidad": 1, "costo_unitario": "1.00"}])
    assert bloqueada.status_code == 403, bloqueada.text
    nueva = api.post(f"/saas/superadmin/empresas/{b['empresa_id']}/suscripciones",
                     headers=admin_cab,
                     json={"plan_id": activa["plan_saas_id"],
                           "fecha_inicio": date.today().isoformat(),
                           "fecha_vencimiento": (date.today().replace(
                               year=date.today().year + 1)).isoformat(),
                           "observacion": "reactivacion 5BN"})
    assert nueva.status_code == 201, nueva.text
    permitida = _crear_orden(api, b, [{
        "variante_producto_id": b["variante_id"],
        "temporada_id": b["temporada_id"],
        "cantidad": 1, "costo_unitario": "1.00"}])
    assert permitida.status_code == 201, permitida.text
    REGISTRO.ok("ROLES", "compras_roles_y_plan",
                "CAJERO/CLIENTE 403; suscripcion suspendida 403; reactivada 201")


# ===========================================================================
# P2 - Devoluciones de venta
# ===========================================================================


def _venta_con_stock(api, lab, empresa, cantidad=3, stock=None):
    contexto = _nueva_variante(lab, empresa, "p2venta")
    inventario_id = _nuevo_inventario(
        lab, empresa, contexto["variante_id"], contexto["temporada_id"],
        stock if stock is not None else cantidad + 5)
    venta = _venta(api, empresa, inventario_id, cantidad)
    assert venta.status_code == 201, venta.text
    devolucion = _devolucion_sql(lab, empresa, contexto, cantidad)
    return {
        "venta_id": venta.json()["venta_id"],
        "inventario_id": inventario_id,
        "stock_base": _stock(lab, inventario_id)[0],
        "contexto": contexto,
        "cantidad": cantidad,
        **devolucion,
    }


def _devolucion_sql(lab, empresa, contexto, cantidad):
    """Fila de detalle_venta y una segunda linea para escenarios parciales."""
    detalles_variante = _sql(
        lab,
        "SELECT dv.id FROM public.detalle_venta dv "
        "JOIN public.inventario i ON i.id = dv.inventario_id "
        "WHERE dv.empresa_id=%s AND i.variante_producto_id=%s "
        "ORDER BY dv.id DESC LIMIT 1",
        (empresa["empresa_id"], contexto["variante_id"]))[0][0]
    return {"detalle_venta_id": int(detalles_variante)}


def test_p2_crear_devolucion_sin_stock(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=3)
    respuesta = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 2,
         "motivo": "talla"}])
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "SOLICITADA"
    assert cuerpo["stock_modificado"] is False
    assert cuerpo["reembolso"]["estado"] == "PENDIENTE"
    assert cuerpo["reembolso"]["ejecutado"] is False
    assert cuerpo["monto_estimado"] == 300.0
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"]
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 0
    bitacora = _sql(
        lab, "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s AND "
             "entidad_afectada='devolucion' AND accion='DEVOLUCION_SOLICITADA' "
             "AND descripcion LIKE %s",
        (a["empresa_id"], f"%{cuerpo['devolucion_id']}%"))[0][0]
    assert bitacora == 1
    REGISTRO.ok("DEVOLUCIONES", "crear_sin_stock",
                f"devolucion {cuerpo['devolucion_id']} SOLICITADA, stock intacto, "
                "reembolso PENDIENTE/ejecutado=false")


def test_p2_aprobar_y_procesar_repone_stock(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=3)
    creada = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 2}])
    assert creada.status_code == 201, creada.text
    devolucion_id = creada.json()["devolucion_id"]
    aprobada = api.post(f"/tenant/devoluciones/{devolucion_id}/aprobar",
                        headers=_cabecera(a["token"]))
    assert aprobada.status_code == 200, aprobada.text
    assert aprobada.json()["estado"] == "APROBADA"
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"]

    procesada = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                         headers=_cabecera(a["token"]),
                         json={"operacion_id": str(uuid.uuid4())})
    assert procesada.status_code == 200, procesada.text
    cuerpo = procesada.json()
    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["unidades_repuestas"] == 2
    assert cuerpo["reembolso"]["estado"] == "PENDIENTE"
    assert cuerpo["reembolso"]["ejecutado"] is False
    assert cuerpo["reembolso"]["monto"] == 300.0
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"] + 2
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 1

    detalle = api.get(f"/tenant/devoluciones/{devolucion_id}",
                      headers=_cabecera(a["token"]))
    assert detalle.status_code == 200, detalle.text
    reembolso = detalle.json()["reembolso"]
    assert reembolso["estado"] == "PENDIENTE" and reembolso["ejecutado"] is False
    assert reembolso["fecha_ejecucion"] is None
    venta_estado = _sql(lab, "SELECT estado, total FROM public.venta WHERE id=%s",
                        (base["venta_id"],))[0]
    assert venta_estado[0] == "COMPLETADA"
    pagos = _sql(lab, "SELECT count(*) FROM public.pago WHERE venta_id=%s",
                 (base["venta_id"],))[0][0]
    assert pagos == 1
    REGISTRO.ok("DEVOLUCIONES", "procesar_repone",
                "COMPLETADA: stock +2, 1 kardex DEVOLUCION, reembolso PENDIENTE "
                "sin fecha de ejecucion y venta/pago intactos")


def test_p2_replay_procesar_12(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    devolucion = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert devolucion.status_code == 201, devolucion.text
    devolucion_id = devolucion.json()["devolucion_id"]
    assert api.post(f"/tenant/devoluciones/{devolucion_id}/aprobar",
                    headers=_cabecera(a["token"])).status_code == 200
    operacion = str(uuid.uuid4())
    for _ in range(12):
        respuesta = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                             headers=_cabecera(a["token"]),
                             json={"operacion_id": operacion})
        assert respuesta.status_code in (200, 201), respuesta.text
        assert respuesta.json()["devolucion_id"] == devolucion_id
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"] + 1
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 1
    reembolsos = _sql(lab, "SELECT count(*) FROM public.reembolso_devolucion "
                           "WHERE devolucion_id=%s", (devolucion_id,))[0][0]
    assert reembolsos == 1
    REGISTRO.ok("DEVOLUCIONES", "replay_procesar_12",
                "12 reenvios del mismo UUID => 1 reposicion y 1 reembolso declarado")


def test_p2_excede_cantidad_409(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=3)
    excedida = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 4}])
    assert excedida.status_code == 409, excedida.text
    parcial = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 2}])
    assert parcial.status_code == 201, parcial.text
    resto = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 2}])
    assert resto.status_code == 409, resto.text
    total = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert total.status_code == 201, total.text
    assert _sql(lab, "SELECT count(*) FROM public.detalle_devolucion WHERE "
                     "detalle_venta_id=%s", (base["detalle_venta_id"],))[0][0] == 2
    REGISTRO.ok("DEVOLUCIONES", "excede_cantidad",
                "4 de 3 => 409; 2+2 de 3 => 409; 2+1 => 201 (2 filas)")


def test_p2_concurrente_dos_operaciones(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=3)

    def enviar(_):
        with httpx.Client(base_url=str(api.base_url), timeout=60.0) as propio:
            return _crear_devolucion(
                propio, a, base["venta_id"],
                [{"detalle_venta_id": base["detalle_venta_id"], "cantidad": 2}]
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        estados = list(pool.map(enviar, range(2)))
    assert estados.count(201) == 1, estados
    assert all(e in (201, 409) for e in estados), estados
    solicitadas = _sql(
        lab, "SELECT count(*) FROM public.devolucion WHERE venta_id=%s "
             "AND estado='SOLICITADA'", (base["venta_id"],))[0][0]
    assert solicitadas == 1
    REGISTRO.ok("DEVOLUCIONES", "concurrente_dos_operaciones",
                f"estados={estados}: una sola devolucion compromete unidades")


def test_p2_cruce_empresa_404(api, circuito, lab):
    a, b = circuito["a"], circuito["b"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    ajeno = _crear_devolucion(api, b, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert ajeno.status_code in (403, 404), ajeno.text
    devolucion = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert devolucion.status_code == 201, devolucion.text
    devolucion_id = devolucion.json()["devolucion_id"]
    assert api.get(f"/tenant/devoluciones/{devolucion_id}",
                   headers=_cabecera(b["token"])).status_code == 404
    assert api.post(f"/tenant/devoluciones/{devolucion_id}/aprobar",
                    headers=_cabecera(b["token"])).status_code in (403, 404)
    assert _sql(lab, "SELECT count(*) FROM public.devolucion WHERE id=%s AND "
                     "empresa_id=%s", (devolucion_id, b["empresa_id"]))[0][0] == 0
    REGISTRO.ok("DEVOLUCIONES", "cruce_empresa",
                "B no crea, no lee ni aprueba devoluciones de A")


def test_p2_venta_no_completada_409(api, circuito, lab):
    a = circuito["a"]
    contexto = _nueva_variante(lab, a, "p2pend")
    inventario_id = _nuevo_inventario(
        lab, a, contexto["variante_id"], contexto["temporada_id"], 5)
    venta_id = _sql(
        lab,
        "INSERT INTO public.venta (empresa_id, sucursal_id, fecha_hora, canal, "
        "estado, total) VALUES (%s,%s,NOW(),'PRESENCIAL','PENDIENTE',150.00) "
        "RETURNING id", (a["empresa_id"], a["sucursal_id"]))[0][0]
    detalle_id = _sql(
        lab,
        "INSERT INTO public.detalle_venta (empresa_id, venta_id, inventario_id, "
        "cantidad, precio_unitario) VALUES (%s,%s,%s,1,150.00) RETURNING id",
        (a["empresa_id"], venta_id, inventario_id))[0][0]
    respuesta = _crear_devolucion(api, a, venta_id, [
        {"detalle_venta_id": detalle_id, "cantidad": 1}])
    assert respuesta.status_code == 409, respuesta.text
    assert _sql(lab, "SELECT count(*) FROM public.devolucion WHERE venta_id=%s",
                (venta_id,))[0][0] == 0
    REGISTRO.ok("DEVOLUCIONES", "venta_no_completada",
                "venta PENDIENTE => 409 sin devolucion persistida")


def test_p2_roles_403(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    cajero = _crear_devolucion(
        api, {"token": a["cajero"]["token"]}, base["venta_id"],
        [{"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert cajero.status_code == 403, cajero.text
    cliente = _crear_devolucion(
        api, {"token": a["cliente"]["token"]}, base["venta_id"],
        [{"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert cliente.status_code == 403, cliente.text
    REGISTRO.ok("ROLES", "devoluciones_no_autorizadas_403",
                "CAJERO y CLIENTE no registran devoluciones (403)")


def test_p2_rechazar_no_repone(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    devolucion = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    assert devolucion.status_code == 201, devolucion.text
    devolucion_id = devolucion.json()["devolucion_id"]
    rechazada = api.post(f"/tenant/devoluciones/{devolucion_id}/rechazar",
                         headers=_cabecera(a["token"]))
    assert rechazada.status_code == 200, rechazada.text
    assert rechazada.json()["estado"] == "RECHAZADA"
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"]
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 0
    procesar = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                        headers=_cabecera(a["token"]),
                        json={"operacion_id": str(uuid.uuid4())})
    assert procesar.status_code == 409, procesar.text
    REGISTRO.ok("DEVOLUCIONES", "rechazar_sin_reposicion",
                "RECHAZADA no toca stock ni kardex y no se puede procesar")


def test_p2_no_restockable_no_suma_stock(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    devolucion = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1,
         "reponer": False, "motivo": "prenda dañada"}])
    assert devolucion.status_code == 201, devolucion.text
    devolucion_id = devolucion.json()["devolucion_id"]
    assert api.post(f"/tenant/devoluciones/{devolucion_id}/aprobar",
                    headers=_cabecera(a["token"])).status_code == 200
    procesada = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                         headers=_cabecera(a["token"]),
                         json={"operacion_id": str(uuid.uuid4())})
    assert procesada.status_code == 200, procesada.text
    cuerpo = procesada.json()
    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["unidades"] == 1 and cuerpo["unidades_repuestas"] == 0
    assert cuerpo["stock"] == []
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"]
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 0
    assert cuerpo["reembolso"]["estado"] == "PENDIENTE"
    assert cuerpo["reembolso"]["monto"] == 150.0
    REGISTRO.ok("DEVOLUCIONES", "no_restockable",
                "reponer=false: COMPLETADA sin stock ni kardex, reembolso PENDIENTE 150")


def test_p2_doble_procesamiento_409(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    devolucion = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1}])
    devolucion_id = devolucion.json()["devolucion_id"]
    api.post(f"/tenant/devoluciones/{devolucion_id}/aprobar",
             headers=_cabecera(a["token"]))
    primera = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                       headers=_cabecera(a["token"]),
                       json={"operacion_id": str(uuid.uuid4())})
    assert primera.status_code == 200, primera.text
    segunda = api.post(f"/tenant/devoluciones/{devolucion_id}/procesar",
                       headers=_cabecera(a["token"]),
                       json={"operacion_id": str(uuid.uuid4())})
    assert segunda.status_code == 409, segunda.text
    assert _stock(lab, base["inventario_id"])[0] == base["stock_base"] + 1
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 1
    REGISTRO.ok("DEVOLUCIONES", "doble_procesamiento",
                "segundo procesamiento con UUID distinto => 409 sin reponer otra vez")


def test_p2_rollback_multilinea(api, circuito, lab):
    a = circuito["a"]
    base = _venta_con_stock(api, lab, a, cantidad=2)
    otra = _venta_con_stock(api, lab, a, cantidad=2)
    respuesta = _crear_devolucion(api, a, base["venta_id"], [
        {"detalle_venta_id": base["detalle_venta_id"], "cantidad": 1},
        {"detalle_venta_id": otra["detalle_venta_id"], "cantidad": 1},
    ])
    assert respuesta.status_code in (403, 409, 422), respuesta.text
    assert _sql(lab, "SELECT count(*) FROM public.devolucion WHERE venta_id=%s",
                (base["venta_id"],))[0][0] == 0
    assert _kardex(lab, base["inventario_id"], "DEVOLUCION") == 0
    REGISTRO.ok("DEVOLUCIONES", "rollback_multilinea",
                "detalle de otra venta rechazado sin persistir devolucion")


def test_rls_sin_asercion_y_guc_falsa(lab):
    import psycopg

    with psycopg.connect(lab["runtime_dsn"], autocommit=True) as conn:
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] is None
        for tabla in ("orden_compra", "detalle_orden_compra", "recepcion_compra",
                      "detalle_recepcion_compra", "devolucion", "reembolso_devolucion"):
            filas = conn.execute(f"SELECT count(*) FROM public.{tabla}").fetchone()[0]
            assert filas == 0, (tabla, filas)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.recepcion_compra "
                "(empresa_id, orden_compra_id, usuario_id) VALUES (1, 1, 1)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT public.sp_crear_orden_compra_tenant("
                "1::bigint, 1::bigint, 1::bigint, 1::bigint, NULL::timestamptz, "
                "NULL::text, '[]'::jsonb)")

        # GUC falsa: `app.empresa_id` por si sola no concede acceso.
        conn.execute("SET app.empresa_id = '1'")
        conn.execute("SET app.usuario_id = '1'")
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] is None
        assert conn.execute(
            "SELECT count(*) FROM public.orden_compra").fetchone()[0] == 0

        # Asercion HMAC forjada: tampoco concede acceso.
        conn.execute("SET app.tenant_assertion = '1|1|9999999999|0000|deadbeef'")
        assert conn.execute("SELECT saas.tenant_actual()").fetchone()[0] is None
        for tabla in ("recepcion_compra", "reembolso_devolucion",
                      "detalle_recepcion_compra"):
            assert conn.execute(
                f"SELECT count(*) FROM public.{tabla}").fetchone()[0] == 0
    REGISTRO.ok("ROLES", "rls_sin_asercion",
                "sin asercion y con GUC/HMAC falsos: 0 filas, INSERT 42501 y "
                "SP 42501 sobre las tablas nuevas")


def test_rutas_legacy_siguen_503(api, circuito):
    a = circuito["a"]
    assert api.get("/compras", headers=_cabecera(a["token"])).status_code == 503
    assert api.post("/devoluciones", headers=_cabecera(a["token"]),
                    json={}).status_code == 503
    assert api.get("/ventas", headers=_cabecera(a["token"])).status_code == 503
    assert api.get("/pagos", headers=_cabecera(a["token"])).status_code == 503
    REGISTRO.ok("ROLES", "legacy_503",
                "/compras, /devoluciones, /ventas y /pagos siguen en 503")
