"""5BR / Etapa I - Ensayo controlado del SaaS sobre el ESQUEMA ORIGINAL restaurado.

Contrato
--------
Este modulo NO crea tablas historicas. El esquema original se restaura con
``96_laboratorio_esquema_real_5br.py`` y aqui solo se:

1. certifica que el destino ES el original exacto (41/27/24/1 y, sobre todo, los
   13 triggers y 12 rutinas que la reconstruccion 5BL no tenia);
2. siembra datos **ficticios** respetando los dominios CHECK reales;
3. aplica el pipeline versionado (manifiesto ``98_...``) y la migracion de
   compatibilidad nueva;
4. ejecuta pruebas SQL de operacion comercial y de aislamiento A/B sobre el
   principal restringido;
5. escribe evidencia sanitizada.

Reglas
------
* Solo loopback, solo el puerto 55448 y solo la base ``..._lab_5br_...``.
* Nunca imprime DSN completos, passwords ni la clave HMAC.
* Los datos son ficticios; no se restaura ni se copia ningun dato de negocio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HOST_LAB = "127.0.0.1"
PUERTO_LAB = 55448
BASE_LAB = "fashionstore_lab_5br_esquema_real"
RUTA_SECRETOS = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                 / "Temp" / "opencode" / "fs_lab_5br_pg17" / "lab_5br_secrets.json")
RUTA_MANIFIESTO = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                   / "Temp" / "opencode" / "fs_lab_5br_pg17" / "lab_5br_ensayo.json")
RUTA_RESTAURACION = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                     / "Temp" / "opencode" / "fs_lab_5br_pg17" / "lab_5br_manifest.json")
SEMILLA_SQL = ROOT / "database" / "saas" / "99_semilla_esquema_real_5br.sql"
MANIFIESTO_PIPELINE = (ROOT / "database" / "saas"
                       / "98_manifiesto_migracion_esquema_real_5br.json")
RUTA_CATALOGO_ORIGEN = Path(
    r"C:\Users\josia\Documents\fashionstore-esquema-5bq"
    r"\catalogo_20261031_115806.json")

TABLAS_ORIGINALES = 41
TRIGGERS_ORIGINALES = 27
RUTINAS_ORIGINALES = 24
VISTAS_ORIGINALES = 1

# Objetos que la reconstruccion 5BL NO tenia (evidencia 5BR, matriz 97).
TABLA_AUSENTE_EN_5BL = "token_recuperacion"
TRIGGERS_AUSENTES_EN_5BL = (
    "trg_bitacora_devolucion", "trg_bitacora_empleado", "trg_bitacora_orden_compra",
    "trg_bitacora_pago", "trg_bitacora_rol", "trg_bitacora_variante_producto",
    "trg_bitacora_venta", "trg_normalizar_sku", "trg_proteger_eliminacion_venta",
    "trg_recalcular_total_venta", "trg_validar_detalle_devolucion",
    "trg_validar_detalle_venta_sucursal", "trg_validar_producto_proveedor_orden",
)
RUTINAS_AUSENTES_EN_5BL = (
    "fn_normalizar_sku", "fn_proteger_eliminacion_venta",
    "fn_recalcular_total_venta_trigger", "fn_validar_detalle_devolucion",
    "fn_validar_detalle_venta_sucursal", "fn_validar_producto_proveedor_orden",
    "sp_ajustar_inventario", "sp_confirmar_venta", "sp_crear_reserva",
    "sp_recalcular_total_venta", "sp_recibir_orden_compra", "sp_registrar_devolucion",
)

ROL_RUNTIME = "fs_saas_runtime"
ROL_ADMIN = "saas_admin_runtime"
EMPRESA_A = "empresa-a"
EMPRESA_B = "empresa-b"


class EnsayoError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _secretos() -> dict:
    if not RUTA_SECRETOS.exists():
        raise EnsayoError(
            "No existen secretos del laboratorio 5BR: ejecutar antes "
            "96_laboratorio_esquema_real_5br.py --init --confirm-desechable")
    return json.loads(RUTA_SECRETOS.read_text(encoding="utf-8"))


def _dsn(rol: str, password: str) -> str:
    from urllib.parse import quote

    return (f"postgresql://{rol}:{quote(password, safe='')}"
            f"@{HOST_LAB}:{PUERTO_LAB}/{BASE_LAB}")


def dsn_admin() -> str:
    return _dsn("postgres", _secretos()["password_admin"])


def dsn_runtime() -> str:
    return _dsn(ROL_RUNTIME, _secretos()["password_runtime"])


def dsn_admin_runtime() -> str:
    return _dsn(ROL_ADMIN, _secretos()["password_admin_runtime"])


def _validar_dsn(dsn: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(dsn)
    if (parsed.hostname or "") != HOST_LAB:
        raise EnsayoError("El ensayo 5BR solo opera en loopback")
    if (parsed.port or 0) != PUERTO_LAB:
        raise EnsayoError("El ensayo 5BR solo opera en el puerto 55448")
    if (parsed.path or "").lstrip("/") != BASE_LAB:
        raise EnsayoError("El ensayo 5BR solo opera sobre la base del laboratorio")


def _modulo(relativa: str, alias: str):
    import importlib.util

    if alias in sys.modules:
        return sys.modules[alias]
    ruta = ROOT / relativa
    if not ruta.exists():
        raise EnsayoError(f"Modulo no encontrado: {relativa}")
    spec = importlib.util.spec_from_file_location(alias, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
# 1. Certificacion del esquema original
# ---------------------------------------------------------------------------


def verificar_esquema_real(admin_dsn: str) -> dict:
    """Falla si el destino no es el esquema ORIGINAL restaurado.

    No basta con los conteos: se exige la presencia de la tabla, los 13 triggers
    y las 12 rutinas que la reconstruccion no tenia, y la ausencia de cualquier
    ``empresa_id`` o politica RLS previa.
    """
    import psycopg

    _validar_dsn(admin_dsn)
    with psycopg.connect(admin_dsn) as conn:
        conteos = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='public'),
              (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                 JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname='public'),
              (SELECT count(*) FROM pg_proc p JOIN pg_namespace n
                 ON n.oid=p.pronamespace WHERE n.nspname='public'),
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace WHERE c.relkind='v' AND n.nspname='public'),
              (SELECT count(*) FROM information_schema.columns
                WHERE column_name='empresa_id' AND table_schema='public'),
              (SELECT count(*) FROM pg_policies WHERE schemaname='public')
            """
        ).fetchone()
        claves = ("tablas", "triggers", "rutinas", "vistas", "columnas_empresa_id",
                  "politicas")
        conteos = dict(zip(claves, conteos))

        triggers = {f[0] for f in conn.execute(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE NOT t.tgisinternal AND n.nspname='public'").fetchall()}
        rutinas = {f[0] for f in conn.execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public'").fetchall()}
        token = conn.execute(
            "SELECT to_regclass('public.token_recuperacion') IS NOT NULL").fetchone()[0]

    fallos: list[str] = []
    esperados = {"tablas": TABLAS_ORIGINALES, "triggers": TRIGGERS_ORIGINALES,
                 "rutinas": RUTINAS_ORIGINALES, "vistas": VISTAS_ORIGINALES}
    for clave, esperado in esperados.items():
        if conteos[clave] != esperado:
            fallos.append(f"{clave}: {conteos[clave]} != {esperado}")
    if not token:
        fallos.append(f"falta public.{TABLA_AUSENTE_EN_5BL}")
    faltan_triggers = sorted(set(TRIGGERS_AUSENTES_EN_5BL) - triggers)
    faltan_rutinas = sorted(set(RUTINAS_AUSENTES_EN_5BL) - rutinas)
    if faltan_triggers:
        fallos.append(f"triggers originales ausentes: {faltan_triggers}")
    if faltan_rutinas:
        fallos.append(f"rutinas originales ausentes: {faltan_rutinas}")
    if conteos["columnas_empresa_id"]:
        fallos.append("el destino ya tiene empresa_id: no es el estado previo a SaaS")
    if conteos["politicas"]:
        fallos.append("el destino ya tiene politicas RLS: no es el estado previo a SaaS")

    if fallos:
        raise EnsayoError(
            "El destino NO es el esquema original restaurado: " + " | ".join(fallos))

    return {"conteos": conteos, "tabla_original_presente": token,
            "triggers_originales_verificados": list(TRIGGERS_AUSENTES_EN_5BL),
            "rutinas_originales_verificadas": list(RUTINAS_AUSENTES_EN_5BL),
            "ok": True}


# ---------------------------------------------------------------------------
# 2. Semilla ficticia
# ---------------------------------------------------------------------------


def sembrar_esquema_real(admin_dsn: str) -> dict:
    """Carga la semilla ficticia sin disparar triggers (como un pg_restore)."""
    import psycopg

    _validar_dsn(admin_dsn)
    sql = SEMILLA_SQL.read_text(encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SET search_path = public")
        conn.execute("SET session_replication_role = replica")
        try:
            conn.execute(sql)
        finally:
            conn.execute("SET session_replication_role = origin")
        conteos = {}
        for tabla in ("usuario", "producto", "variante_producto", "inventario",
                      "reserva", "venta", "devolucion", "bitacora"):
            conteos[tabla] = conn.execute(
                f"SELECT count(*) FROM public.{tabla}").fetchone()[0]
    return conteos


# ---------------------------------------------------------------------------
# 3. Aplicacion del pipeline
# ---------------------------------------------------------------------------


def aplicar_pipeline() -> dict:
    preflight = _modulo("database/saas/86_preflight_migracion_pg17_5bp.py",
                        "preflight_5bp_5br")
    manifiesto = preflight.cargar_manifiesto(MANIFIESTO_PIPELINE)
    secretos = _secretos()

    # 86 lee los secretos de las variables de la Etapa G. Se reutiliza su lector
    # sin duplicar codigo: se publican los mismos valores con los nombres que el
    # runner ya conoce. Nunca se imprimen ni se escriben en el manifiesto.
    os.environ["FASHIONSTORE_LAB_5BP_RUNTIME_PASSWORD"] = secretos["password_runtime"]
    os.environ["FASHIONSTORE_LAB_5BP_ADMIN_PASSWORD"] = secretos["password_admin_runtime"]
    os.environ["FASHIONSTORE_LAB_5BP_ASSERTION_KEY"] = secretos["assertion_key"]

    from urllib.parse import urlparse

    parsed = urlparse(dsn_admin())
    destino = preflight.Destino(host=parsed.hostname, puerto=parsed.port,
                                base=parsed.path.lstrip("/"),
                                password=secretos["password_admin"])
    resultado = preflight.aplicar_lab(manifiesto, destino)

    # La fase de fixtures genera un password aleatorio para los usuarios de
    # prueba. Se recupera de la MISMA instancia de modulo que uso el pipeline
    # (mismo alias) y se persiste fuera del repositorio, para que las pruebas
    # HTTP puedan autenticarse. No se imprime ni se devuelve al caller.
    modulo_74 = preflight._modulo(
        "database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_5bp")
    password_usuarios = getattr(modulo_74, "PASSWORD_USUARIOS", None)

    registro = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifiesto": manifiesto.get("manifiesto_id"),
        "version_manifiesto": manifiesto.get("version"),
        "version_servidor": resultado["version_servidor"],
        "aplicadas": resultado["aplicadas"],
        "omitidas": resultado["omitidas"],
        "registro": resultado["registro"],
        "contexto": {k: v for k, v in resultado.get("contexto", {}).items()
                     if k != "password_usuarios"},
        "password_usuarios": password_usuarios,
    }
    RUTA_MANIFIESTO.parent.mkdir(parents=True, exist_ok=True)
    RUTA_MANIFIESTO.write_text(
        json.dumps(registro, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")

    return {
        "version_servidor": resultado["version_servidor"],
        "aplicadas": [a["id"] for a in resultado["aplicadas"]],
        "omitidas": [o["id"] for o in resultado["omitidas"]],
        "versiones_registradas": len(resultado["registro"]),
        "registro": resultado["registro"],
        "manifiesto_local": str(RUTA_MANIFIESTO),
    }


# ---------------------------------------------------------------------------
# 4. Verificacion post-migracion
# ---------------------------------------------------------------------------


def verificar_migracion() -> dict:
    import psycopg

    fallos: list[str] = []
    datos: dict[str, Any] = {}
    with psycopg.connect(dsn_admin()) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        conteos = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='public'),
              (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                 JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname='public'),
              (SELECT count(*) FROM information_schema.columns
                WHERE column_name='empresa_id' AND table_schema='public'),
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='public'
                 AND c.relforcerowsecurity),
              (SELECT count(*) FROM pg_policies WHERE schemaname IN ('public','saas')),
              (SELECT count(*) FROM saas_migracion.version_aplicada)
            """
        ).fetchone()
        datos["conteos"] = dict(zip(
            ("tablas_public", "triggers_public", "columnas_empresa_id",
             "tablas_force_rls", "politicas", "versiones_aplicadas"), conteos))

        datos["triggers_originales"] = conn.execute(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal "
            "AND n.nspname='public' AND t.tgname = ANY(%s)",
            (list(TRIGGERS_AUSENTES_EN_5BL),)).fetchone()[0]
        datos["rutinas_originales"] = conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname = ANY(%s)",
            (list(RUTINAS_AUSENTES_EN_5BL),)).fetchone()[0]

        datos["roles"] = {
            f[0]: {"login": f[1], "super": f[2], "bypassrls": f[3]}
            for f in conn.execute(
                "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = ANY(%s) ORDER BY rolname",
                ([ROL_RUNTIME, ROL_ADMIN, "saas_rls_owner", "saas_admin_owner",
                  "saas_actor_sql_owner", "saas_personal_owner"],)).fetchall()}

        datos["definer_peligrosos"] = [
            list(f) for f in conn.execute(
                """
                SELECT n.nspname, p.proname, pg_get_userbyid(p.proowner),
                       COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>')
                  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  JOIN pg_roles r ON r.rolname = pg_get_userbyid(p.proowner)
                 WHERE n.nspname = 'public' AND p.prosecdef
                   AND (r.rolcanlogin
                        OR NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) c
                                        WHERE c LIKE 'search_path=%'))
                 ORDER BY 1, 2""").fetchall()]

        datos["definer_legitimos"] = [
            list(f) for f in conn.execute(
                """
                SELECT n.nspname, p.proname, pg_get_userbyid(p.proowner),
                       COALESCE(array_to_string(p.proconfig, ','), '<sin search_path>')
                  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  JOIN pg_roles r ON r.rolname = pg_get_userbyid(p.proowner)
                 WHERE n.nspname = 'public' AND p.prosecdef
                   AND NOT r.rolcanlogin
                   AND EXISTS (SELECT 1 FROM unnest(p.proconfig) c
                                WHERE c LIKE 'search_path=%')
                 ORDER BY 1, 2""").fetchall()]

        datos["acl_plataforma"] = [
            list(f) for f in conn.execute(
                """
                SELECT c.relname, r.rolname, string_agg(DISTINCT a.privilege_type, ',')
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  CROSS JOIN (VALUES ('anon'), ('authenticated'), ('service_role')) v(r)
                  JOIN pg_roles r ON r.rolname = v.r
                  CROSS JOIN LATERAL aclexplode(c.relacl) a
                 WHERE n.nspname = 'public' AND c.relkind = 'r'
                   AND a.grantee = r.oid
                 GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()]

        datos["rutinas_public_execute"] = [
            list(f) for f in conn.execute(
                """
                SELECT p.proname FROM pg_proc p JOIN pg_namespace n
                  ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public'
                   AND has_function_privilege('public', p.oid, 'EXECUTE')
                   AND (p.proname LIKE 'sp\\_%' ESCAPE '\\'
                        OR p.proname LIKE 'fn\\_%' ESCAPE '\\')
                 ORDER BY 1""").fetchall()]

    if not version.startswith("17."):
        fallos.append(f"version_no_17:{version}")
    if datos["triggers_originales"] != len(TRIGGERS_AUSENTES_EN_5BL):
        fallos.append("triggers_originales_incompletos")
    if datos["rutinas_originales"] != len(RUTINAS_AUSENTES_EN_5BL):
        fallos.append("rutinas_originales_incompletas")
    if datos["conteos"]["tablas_force_rls"] < 31:
        fallos.append(f"force_rls_insuficiente:{datos['conteos']['tablas_force_rls']}")
    if datos["definer_peligrosos"]:
        fallos.append(f"security_definer_public:{len(datos['definer_peligrosos'])}")
    if datos["rutinas_public_execute"]:
        fallos.append(
            f"rutinas_legacy_con_execute_public:{len(datos['rutinas_public_execute'])}")
    for rol in (ROL_RUNTIME, ROL_ADMIN):
        info = datos["roles"].get(rol) or {}
        if not info or info["super"] or info["bypassrls"]:
            fallos.append(f"principal_inseguro:{rol}")
    for rol in ("saas_rls_owner", "saas_admin_owner", "saas_actor_sql_owner",
                "saas_personal_owner"):
        info = datos["roles"].get(rol) or {}
        if not info or info["login"]:
            fallos.append(f"propietario_puede_login:{rol}")

    datos["version_servidor"] = version
    datos["fallos"] = fallos
    datos["ok"] = not fallos
    return datos


def _siguiente_evidencia(prefijo: str, extension: str) -> Path:
    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destino = ROOT / "database" / "saas" / "evidencias" / f"{prefijo}_{sello}.{extension}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


# ---------------------------------------------------------------------------
# 5. Pruebas de operacion comercial sobre el esquema ORIGINAL
# ---------------------------------------------------------------------------


def _asercion(empresa_id: int, usuario_id: int, clave: str) -> str:
    import hashlib
    import hmac
    import secrets as _secrets
    import time

    nonce = _secrets.token_hex(8)
    expira = int(time.time()) + 120
    mensaje = f"{empresa_id}|{usuario_id}|{expira}|{nonce}"
    firma = hmac.new(clave.encode("utf-8"), mensaje.encode("utf-8"),
                     hashlib.sha256).hexdigest()
    return f"{mensaje}|{firma}"


def _huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _contexto_runtime(conn, empresa_id: int, usuario_id: int, clave: str) -> None:
    # Nivel SESION (is_local = false): el escenario usa autocommit para que una
    # sentencia rechazada no envenene la conexion de pruebas. Con is_local=true el
    # GUC se perderia al terminar cada sentencia.
    conn.execute("SELECT set_config('app.tenant_assertion', %s, false)",
                 (_asercion(empresa_id, usuario_id, clave),))
    conn.execute("SELECT set_config('app.usuario_id', %s, false)", (str(usuario_id),))


def ejecutar_pruebas() -> dict:
    """Escenario A/B + operacion comercial con el principal restringido.

    Cada paso registra: nombre, resultado esperado, resultado observado y veredicto.
    Se ejercitan los triggers y rutinas ORIGINALES (los 13/12 que la
    reconstruccion no tenia), no solo los objetos nuevos del SaaS.
    """
    import uuid

    import psycopg

    secretos = _secretos()
    clave = secretos["assertion_key"]
    pruebas: list[dict] = []

    def registrar(nombre: str, ok: bool, esperado: str, observado: Any,
                  detalle: str = "") -> None:
        pruebas.append({"prueba": nombre, "ok": bool(ok), "esperado": esperado,
                        "observado": observado, "detalle": detalle})

    admin = psycopg.connect(dsn_admin(), autocommit=True)

    # --- Preparacion de fixture de proveedor para la empresa B -------------
    admin.execute(
        "INSERT INTO public.proveedor (id, empresa_id, razon_social, nit, estado) "
        "VALUES (500, 2, 'Proveedor B 5BR', 'NIT-5BR-B', TRUE) "
        "ON CONFLICT (id) DO NOTHING")
    admin.execute(
        "INSERT INTO public.proveedor_producto "
        "(empresa_id, proveedor_id, producto_id, costo_referencia, estado) "
        "VALUES (2, 500, 100, 40.00, TRUE) ON CONFLICT DO NOTHING")

    pruebas_previas = admin.execute(
        "SELECT count(*) FROM public.bitacora").fetchone()[0]

    # --- 1. Login por funcion cerrada (SECURITY DEFINER del propietario) ---
    with psycopg.connect(dsn_runtime(), autocommit=True) as conn:
        fila = conn.execute(
            "SELECT usuario_id FROM saas.login_credenciales(%s)",
            ("admin.b@staging.invalid",)).fetchone()
        registrar("login_credenciales_B", fila is not None and fila[0] == 3,
                  "usuario_id=3", fila[0] if fila else None)

        # --- 2. Sin asercion no hay tenant (deny-all) -----------------------
        sin_tenant = conn.execute(
            "SELECT saas.tenant_actual(), "
            "(SELECT count(*) FROM public.inventario)").fetchone()
        registrar("sin_asercion_deny_all", sin_tenant[0] is None and sin_tenant[1] == 0,
                  "tenant NULL y 0 filas", {"tenant": sin_tenant[0],
                                            "filas": sin_tenant[1]})

    # --- 3. Operacion comercial de la empresa B ----------------------------
    with psycopg.connect(dsn_runtime(), autocommit=True) as conn:
        _contexto_runtime(conn, 2, 3, clave)
        tenant = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
        registrar("tenant_B_resuelto", tenant == 2, "2", tenant)

        stock0 = conn.execute(
            "SELECT stock_actual FROM public.inventario WHERE id = 100").fetchone()[0]

        # 3.1 trg_normalizar_sku (trigger ORIGINAL recuperado)
        conn.execute(
            "UPDATE public.variante_producto SET sku = '  sku-b-100  ' WHERE id = 100")
        sku = conn.execute(
            "SELECT sku FROM public.variante_producto WHERE id = 100").fetchone()[0]
        registrar("trigger_normalizar_sku", sku == "SKU-B-100", "SKU-B-100", sku,
                  "trg_normalizar_sku (ausente en la reconstruccion 5BL)")

        # 3.2 Carrito + detalle (triggers de sucursal y actividad)
        # Se cierra cualquier carrito ACTIVO previo del mismo cliente/sucursal:
        # uq_carrito_activo_cliente_sucursal lo exige y el escenario debe poder
        # reejecutarse sin intervencion manual.
        conn.execute(
            "UPDATE public.carrito SET estado = 'ELIMINADO' "
            "WHERE empresa_id = 2 AND cliente_id = 100 AND sucursal_id = 100 "
            "AND estado = 'ACTIVO'")
        carrito = conn.execute(
            "INSERT INTO public.carrito (empresa_id, cliente_id, sucursal_id, estado) "
            "VALUES (2, 100, 100, 'ACTIVO') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO public.detalle_carrito "
            "(empresa_id, carrito_id, inventario_id, cantidad) VALUES (2, %s, 100, 1)",
            (carrito,))
        fecha = conn.execute(
            "SELECT fecha_actualizacion FROM public.carrito WHERE id = %s",
            (carrito,)).fetchone()[0]
        registrar("triggers_carrito_detalle", fecha is not None,
                  "fecha_actualizacion actualizada", str(fecha))

        # 3.3 Reserva tenant (detalle_reserva dispara el trigger original)
        conn.execute(
            "CALL public.sp_crear_reserva_tenant(2, %s, 4, NOW() + INTERVAL '2 days',"
            " 'Reserva 5BR', NULL)", (carrito,))
        reserva = conn.execute(
            "SELECT id, estado FROM public.reserva WHERE carrito_id = %s",
            (carrito,)).fetchone()
        stock_res = conn.execute(
            "SELECT stock_reservado FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        registrar("reserva_tenant_y_trigger_detalle",
                  reserva is not None and reserva[1] == "PENDIENTE" and stock_res == 1,
                  "reserva PENDIENTE y stock_reservado=1",
                  {"reserva": reserva, "stock_reservado": stock_res})

        # 3.4 Cancelacion de la reserva
        conn.execute(
            "CALL public.sp_cancelar_reserva_tenant(2, %s, 4, 'Cancelada 5BR')",
            (reserva[0],))
        estado_res = conn.execute(
            "SELECT estado FROM public.reserva WHERE id = %s", (reserva[0],)
        ).fetchone()[0]
        stock_res = conn.execute(
            "SELECT stock_reservado FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        registrar("cancelacion_reserva", estado_res == "CANCELADA" and stock_res == 0,
                  "CANCELADA y stock_reservado=0",
                  {"estado": estado_res, "stock_reservado": stock_res})

        # 3.5 Venta de contado: dispara validar_detalle_venta_sucursal,
        #     recalcular_total_venta, proteger_detalle_venta y bitacora_venta.
        op = uuid.uuid4()
        huella = _huella(f"venta-5br-{op}")
        resultado = conn.execute(
            "SELECT public.sp_registrar_venta_contado_tenant("
            "2, 5, 100, %s, %s, %s, 'EFECTIVO')",
            (op, huella,
             json.dumps([{"inventario_id": 100, "cantidad": 2}]))).fetchone()[0]
        venta_id = resultado["venta"]["venta_id"]
        total = conn.execute(
            "SELECT total FROM public.venta WHERE id = %s", (venta_id,)).fetchone()[0]
        detalle = conn.execute(
            "SELECT count(*) FROM public.detalle_venta WHERE venta_id = %s",
            (venta_id,)).fetchone()[0]
        stock = conn.execute(
            "SELECT stock_actual FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        registrar("venta_contado_con_triggers_originales",
                  float(total) == 300.0 and detalle == 1 and stock == stock0 - 2,
                  f"total=300 (recalculado por trigger), 1 detalle, "
                  f"stock={stock0 - 2}",
                  {"venta_id": venta_id, "total": str(total), "detalles": detalle,
                   "stock_actual": stock, "stock_inicial": stock0})

        # 3.6 Idempotencia: mismo UUID y huella no vuelve a tocar stock
        repetido = conn.execute(
            "SELECT public.sp_registrar_venta_contado_tenant("
            "2, 5, 100, %s, %s, %s, 'EFECTIVO')",
            (op, huella,
             json.dumps([{"inventario_id": 100, "cantidad": 2}]))).fetchone()[0]
        stock2 = conn.execute(
            "SELECT stock_actual FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        registrar("idempotencia_venta",
                  bool(repetido.get("idempotente")) and stock2 == stock0 - 2,
                  "idempotente=True y stock sin cambio",
                  {"idempotente": repetido.get("idempotente"), "stock": stock2})

        # 3.7 trg_proteger_eliminacion_venta (trigger ORIGINAL recuperado)
        bloqueo_delete = False
        try:
            conn.execute("DELETE FROM public.venta WHERE id = %s", (venta_id,))
        except psycopg.Error as exc:
            bloqueo_delete = "no se puede eliminar" in str(exc).lower()
        registrar("trigger_proteger_eliminacion_venta", bloqueo_delete,
                  "DELETE rechazado por el trigger original", str(bloqueo_delete))

        # 3.8 trg_proteger_detalle_venta (venta COMPLETADA)
        bloqueo_detalle = False
        try:
            conn.execute(
                "UPDATE public.detalle_venta SET cantidad = 5 WHERE venta_id = %s",
                (venta_id,))
        except psycopg.Error as exc:
            bloqueo_detalle = "detalle" in str(exc).lower()
        registrar("trigger_proteger_detalle_venta", bloqueo_detalle,
                  "UPDATE rechazado por venta COMPLETADA", str(bloqueo_detalle))

        # 3.9 Orden de compra: trg_validar_producto_proveedor_orden
        orden = conn.execute(
            "SELECT public.sp_crear_orden_compra_tenant("
            "2, 3, 500, 100, NULL, 'Orden 5BR', %s)",
            (json.dumps([{"variante_producto_id": 100, "temporada_id": 100,
                          "cantidad": 3, "costo_unitario": "40.00"}]),)).fetchone()[0]
        orden_id = orden["orden_compra_id"]
        enviada = conn.execute(
            "SELECT public.sp_enviar_orden_compra_tenant(2, %s, 3)",
            (orden_id,)).fetchone()[0]
        detalle_orden = conn.execute(
            "SELECT id, cantidad FROM public.detalle_orden_compra "
            "WHERE orden_compra_id = %s", (orden_id,)).fetchone()
        registrar("orden_compra_y_trigger_proveedor",
                  orden["ok"] and enviada.get("estado") == "ENVIADA"
                  and detalle_orden is not None,
                  "orden ENVIADA con detalle validado por trigger",
                  {"orden": orden_id, "estado": enviada.get("estado"),
                   "detalle": detalle_orden})

        # 3.10 Recepcion: entrada en inventario + kardex
        op_rec = uuid.uuid4()
        recepcion = conn.execute(
            "SELECT public.sp_recibir_orden_compra_tenant(2, %s, 3, %s, %s, %s, %s)",
            (orden_id, op_rec, _huella(f"recepcion-{op_rec}"),
             json.dumps([{"detalle_orden_compra_id": detalle_orden[0],
                          "cantidad": 3}]), "Recepcion 5BR")).fetchone()[0]
        stock_rec = conn.execute(
            "SELECT stock_actual FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        kardex = conn.execute(
            "SELECT count(*) FROM public.movimiento_inventario "
            "WHERE tipo = 'ENTRADA_COMPRA' AND empresa_id = 2").fetchone()[0]
        registrar("recepcion_compra_y_kardex",
                  recepcion.get("ok") and stock_rec == stock0 + 1 and kardex >= 1,
                  f"stock={stock0 + 1} y kardex ENTRADA_COMPRA",
                  {"recepcion": recepcion.get("ok"), "stock": stock_rec,
                   "kardex": kardex})

        # 3.11 Devolucion: trg_validar_detalle_devolucion
        op_dev = uuid.uuid4()
        detalle_venta = conn.execute(
            "SELECT id FROM public.detalle_venta WHERE venta_id = %s", (venta_id,)
        ).fetchone()[0]
        devolucion = conn.execute(
            "SELECT public.sp_crear_devolucion_tenant(2, %s, 3, %s, %s, %s, %s, %s)",
            (venta_id, op_dev, _huella(f"devolucion-{op_dev}"), "Talla incorrecta",
             "Devolucion 5BR",
             json.dumps([{"detalle_venta_id": detalle_venta, "cantidad": 1,
                          "reponer": True, "motivo": "Talla"}]),)).fetchone()[0]
        dev_id = devolucion["devolucion"]["devolucion_id"]
        resuelta = conn.execute(
            "SELECT public.sp_resolver_devolucion_tenant(2, %s, 3, 'APROBAR')",
            (dev_id,)).fetchone()[0]
        op_proc = uuid.uuid4()
        procesada = conn.execute(
            "SELECT public.sp_procesar_devolucion_tenant(2, %s, 3, %s, %s)",
            (dev_id, op_proc, _huella(f"procesar-{op_proc}"))).fetchone()[0]
        stock_dev = conn.execute(
            "SELECT stock_actual FROM public.inventario WHERE id = 100"
        ).fetchone()[0]
        registrar("devolucion_y_trigger_detalle",
                  procesada.get("ok") and stock_dev == stock0 + 2,
                  f"devolucion procesada y stock repuesto={stock0 + 2}",
                  {"crear": devolucion.get("ok"), "resolver": resuelta.get("estado"),
                   "procesar": procesada.get("ok"), "stock": stock_dev})

        # 3.12 Invariante de stock y bitacora empresarial
        inv = conn.execute(
            "SELECT stock_actual >= stock_reservado FROM public.inventario "
            "WHERE id = 100").fetchone()[0]
        bitacora_b = conn.execute(
            "SELECT count(*) FROM public.bitacora WHERE empresa_id = 2").fetchone()[0]
        registrar("invariante_stock_y_bitacora",
                  inv is True and bitacora_b > 0,
                  "stock_actual >= stock_reservado y bitacora del tenant",
                  {"invariante": inv, "bitacora_b": bitacora_b})
        conn.commit()

    # --- 4. Aislamiento A/B y SQL adversarial ------------------------------
    with psycopg.connect(dsn_runtime(), autocommit=True) as conn:
        _contexto_runtime(conn, 2, 3, clave)
        cruce = conn.execute(
            "SELECT count(*) FROM public.inventario WHERE empresa_id = 1").fetchone()[0]
        solo_b = conn.execute(
            "SELECT count(DISTINCT empresa_id) FROM public.inventario").fetchone()[0]
        registrar("select_cruzado_denegado", cruce == 0 and solo_b == 1,
                  "0 filas de A y una sola empresa visible",
                  {"filas_a": cruce, "empresas_visibles": solo_b})

        update_ajeno = conn.execute(
            "UPDATE public.inventario SET stock_actual = 999 WHERE id = 1").rowcount
        registrar("update_cruzado_denegado", update_ajeno == 0, "0 filas", update_ajeno)

        insert_ajeno = False
        try:
            conn.execute(
                "INSERT INTO public.carrito (empresa_id, cliente_id, sucursal_id, "
                "estado) VALUES (1, 1, 1, 'ACTIVO')")
        except psycopg.Error as exc:
            insert_ajeno = getattr(exc, "sqlstate", "") == "42501"
        registrar("insert_cruzado_denegado", insert_ajeno,
                  "42501 por WITH CHECK de RLS", insert_ajeno)

        detalle_cruzado = False
        detalle_error = "PERMITIDO"
        try:
            conn.execute(
                "INSERT INTO public.detalle_venta "
                "(empresa_id, venta_id, inventario_id, cantidad, precio_unitario) "
                "VALUES (2, 1, 1, 1, 10)")
        except psycopg.Error as exc:
            detalle_cruzado = True
            detalle_error = str(exc)[:160]
        registrar("detalle_venta_cruzado_denegado", detalle_cruzado,
                  "el trigger original rechaza la venta ajena", detalle_error)

        secreto = False
        try:
            conn.execute("SELECT clave FROM saas.rls_secret").fetchone()
        except psycopg.Error as exc:
            secreto = getattr(exc, "sqlstate", "") == "42501"
        registrar("secreto_hmac_denegado", secreto, "42501", secreto)

        hash_denegado = False
        try:
            conn.execute("SELECT password_hash FROM public.usuario LIMIT 1").fetchone()
        except psycopg.Error as exc:
            hash_denegado = getattr(exc, "sqlstate", "") == "42501"
        registrar("password_hash_denegado", hash_denegado, "42501", hash_denegado)

        global_denegado = False
        try:
            conn.execute("SELECT count(*) FROM saas.bitacora_global").fetchone()
        except psycopg.Error as exc:
            global_denegado = getattr(exc, "sqlstate", "") == "42501"
        registrar("bitacora_global_denegada", global_denegado, "42501", global_denegado)

        legacy = False
        try:
            conn.execute("CALL public.sp_ajustar_inventario(100, 'AJUSTE_ENTRADA', "
                         "1, 5, 'legacy')")
        except psycopg.Error as exc:
            legacy = getattr(exc, "sqlstate", "") == "42501"
        registrar("rutina_legacy_sin_execute_para_runtime", legacy,
                  "42501: la rutina original se conserva pero no es invocable", legacy)

    # --- 5. El principal administrativo no lee tablas de negocio ----------
    with psycopg.connect(dsn_admin_runtime(), autocommit=True) as conn:
        admin_denegado = False
        try:
            conn.execute("SELECT count(*) FROM public.empresa").fetchone()
        except psycopg.Error as exc:
            admin_denegado = getattr(exc, "sqlstate", "") == "42501"
        registrar("admin_runtime_sin_tablas", admin_denegado, "42501", admin_denegado)

    pruebas_despues = admin.execute(
        "SELECT count(*) FROM public.bitacora").fetchone()[0]
    global_despues = admin.execute(
        "SELECT count(*) FROM saas.bitacora_global").fetchone()[0]
    admin.close()

    fallos = [p["prueba"] for p in pruebas if not p["ok"]]
    return {
        "pruebas": pruebas,
        "total": len(pruebas),
        "aprobadas": len(pruebas) - len(fallos),
        "fallidas": fallos,
        "bitacora_empresarial": {"antes": pruebas_previas, "despues": pruebas_despues},
        "bitacora_global": {"despues": global_despues},
        "ok": not fallos,
    }


def escribir_evidencia() -> list[Path]:
    """Escribe evidencia sanitizada de restauracion, pipeline y verificacion."""
    rutas: list[Path] = []
    ensayo = (json.loads(RUTA_MANIFIESTO.read_text(encoding="utf-8"))
              if RUTA_MANIFIESTO.exists() else {})
    restauracion = (json.loads(RUTA_RESTAURACION.read_text(encoding="utf-8"))
                    if RUTA_RESTAURACION.exists() else {})

    verificacion = verificar_migracion()
    pruebas = ejecutar_pruebas()

    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destino = ROOT / "database" / "saas" / "evidencias"
    destino.mkdir(parents=True, exist_ok=True)

    json_ruta = destino / f"EVIDENCIA_ENSAYO_5BR_{sello}.json"
    json_ruta.write_text(json.dumps({
        "etapa": "5BR / Etapa I",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "insumo": restauracion.get("insumo"),
        "roles_plataforma": restauracion.get("roles_plataforma"),
        "restauracion": restauracion.get("restauracion", {}).get("verificacion"),
        "pipeline": {
            "manifiesto": ensayo.get("manifiesto"),
            "version_manifiesto": ensayo.get("version_manifiesto"),
            "version_servidor": ensayo.get("version_servidor"),
            "aplicadas": ensayo.get("aplicadas"),
            "omitidas": ensayo.get("omitidas"),
        },
        "verificacion": verificacion,
        "pruebas_comerciales": pruebas,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    rutas.append(json_ruta)

    txt_ruta = destino / f"EVIDENCIA_PIPELINE_5BR_{sello}.txt"
    lineas = [
        "EVIDENCIA 5BR - ESQUEMA HISTORICO ORIGINAL SOBRE PostgreSQL 17",
        "=" * 74,
        f"Generado UTC : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Cluster      : {restauracion.get('cluster', {}).get('version', '17.7')} "
        f"en {HOST_LAB}:{PUERTO_LAB}/{BASE_LAB}",
        f"Insumo       : {(restauracion.get('insumo') or {}).get('archivo')} "
        f"({(restauracion.get('insumo') or {}).get('bytes')} bytes, "
        f"sha256={(restauracion.get('insumo') or {}).get('sha256', '')[:16]}...)",
        "",
        "1. RESTAURACION (objetos ORIGINALES)",
    ]
    rest = restauracion.get("restauracion", {}).get("verificacion", {})
    for clave, valor in (rest.get("conteos") or {}).items():
        lineas.append(f"   {clave:<24} {valor}")
    for clase, datos in (rest.get("fidelidad") or {}).items():
        lineas.append(f"   {clase:<24} origen={datos['origen']} lab={datos['lab']} "
                      f"faltan={datos['faltan']} sobran={datos['sobran']}")
    lineas.append("")
    lineas.append("2. PIPELINE SAAS (fases aplicadas)")
    for fase in ensayo.get("aplicadas", []):
        lineas.append(f"   {fase['id']:<44} {fase.get('duracion_ms')} ms")
    for fase in ensayo.get("omitidas", []):
        lineas.append(f"   {fase['id']:<44} SKIP ({fase.get('motivo')})")
    lineas.append("")
    lineas.append("3. VERIFICACION POST-MIGRACION")
    for clave, valor in (verificacion.get("conteos") or {}).items():
        lineas.append(f"   {clave:<24} {valor}")
    lineas.append(f"   {'definer_peligrosos':<24} "
                  f"{len(verificacion.get('definer_peligrosos') or [])}")
    lineas.append(f"   {'acl_plataforma':<24} "
                  f"{len(verificacion.get('acl_plataforma') or [])}")
    lineas.append(f"   {'execute_public':<24} "
                  f"{len(verificacion.get('rutinas_public_execute') or [])}")
    lineas.append(f"   {'fallos':<24} "
                  f"{verificacion.get('fallos') if verificacion.get('fallos') else 'ninguno'}")
    lineas.append("")
    lineas.append("4. PRUEBAS DE OPERACION Y AISLAMIENTO")
    for prueba in pruebas["pruebas"]:
        estado = "PASS" if prueba["ok"] else "FAIL"
        lineas.append(f"   [{estado}] {prueba['prueba']}: {prueba['esperado']}")
    lineas.append("")
    lineas.append(f"   total={pruebas['total']} aprobadas={pruebas['aprobadas']} "
                  f"fallidas={len(pruebas['fallidas'])}")
    lineas.append("")
    lineas.append("Nota: datos ficticios. Sin DSN, passwords, JWT ni PII.")
    txt_ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    rutas.append(txt_ruta)
    return rutas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BR: ensayo del SaaS sobre el esquema original restaurado")
    parser.add_argument("--verificar-esquema", action="store_true")
    parser.add_argument("--sembrar", action="store_true")
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--verificar", action="store_true")
    parser.add_argument("--pruebas", action="store_true")
    parser.add_argument("--evidencia", action="store_true")
    parser.add_argument("--todo", action="store_true",
                        help="verificar esquema + aplicar pipeline + verificar "
                             "(la semilla es una fase del pipeline: F02R)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    salida: dict[str, Any] = {"etapa": "5BR / Etapa I",
                              "fecha_utc": datetime.now(timezone.utc).isoformat(
                                  timespec="seconds")}
    try:
        if args.todo or args.verificar_esquema:
            salida["esquema_real"] = verificar_esquema_real(dsn_admin())
        if args.sembrar:
            salida["semilla"] = sembrar_esquema_real(dsn_admin())
        if args.todo or args.aplicar:
            salida["pipeline"] = aplicar_pipeline()
        if args.todo or args.verificar:
            salida["verificacion"] = verificar_migracion()
        if args.pruebas:
            salida["pruebas"] = ejecutar_pruebas()
        if args.evidencia:
            rutas = escribir_evidencia()
            salida["evidencias"] = [str(r) for r in rutas]
    except Exception as exc:  # noqa: BLE001 - se reporta tal cual
        salida["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(salida, indent=2, ensure_ascii=False, default=str))
        return 1

    print(json.dumps(salida, indent=2, ensure_ascii=False, default=str))
    verificacion = salida.get("verificacion")
    pruebas = salida.get("pruebas")
    if verificacion and not verificacion.get("ok"):
        return 1
    if pruebas and not pruebas.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
