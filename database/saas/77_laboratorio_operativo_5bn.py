"""5BN / Etapa E2 - Laboratorio DESECHABLE de catalogo, compras y devoluciones.

Cluster PostgreSQL 18 EXCLUSIVO en loopback y en un puerto NUEVO (55444, base
``fashionstore_lab_5bn``). NO reutiliza ni toca el laboratorio E1/5BM de
``127.0.0.1:55443/fashionstore_lab_5bm``.

Que construye
-------------
1. Todo el laboratorio 5BM (esquema historico reconstruido, migraciones
   00100..00500, cuatro empresas ficticias, plano global y rutinas de E1),
   reutilizando el runner 76 SIN editarlo y repuntandolo a este cluster.
2. La migracion nueva ``20261030000700_saas_compras_devoluciones_5bn.sql``:
   recepciones de compra, reembolso declarado, helpers de alcance y las siete
   rutinas tenant de compras y devoluciones.
3. Verificacion de que el runtime conserva privilegios minimos: sin UPDATE en
   ``talla``/``color``, sin acceso al plano global, con EXECUTE solo en las
   rutinas nuevas.

Reglas de seguridad
-------------------
* Solo loopback y solo el puerto 55444; el pgdata vive bajo el temporal del
  usuario y el nombre de base contiene ``lab``.
* Exige ``--confirm-desechable`` para crear o recrear. El borrado solo afecta a
  ESTE cluster y a ESTA base.
* Nunca imprime contrasenas, DSN completos ni la clave HMAC.

Uso
---
    python database/saas/77_laboratorio_operativo_5bn.py --init --confirm-desechable
    python database/saas/77_laboratorio_operativo_5bn.py --recrear-base --confirm-desechable
    python database/saas/77_laboratorio_operativo_5bn.py --status
    python database/saas/77_laboratorio_operativo_5bn.py --evidencia
    python database/saas/77_laboratorio_operativo_5bn.py --stop
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUERTO_LAB = 55444
BASE_LAB = "fashionstore_lab_5bn"
HOST_LAB = "127.0.0.1"
MIGRACION_5BN = "20261030000700_saas_compras_devoluciones_5bn.sql"

ROL_RUNTIME = "fs_saas_runtime"
ROL_ADMIN = "saas_admin_runtime"
ROL_ADMIN_OWNER = "saas_admin_owner"
ROL_ACTOR_OWNER = "saas_actor_sql_owner"

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "lab-5bn-placeholder-no-usar")


class LabError(RuntimeError):
    pass


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bn"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bn_manifest.json"


def _cargar(relativa: str, alias: str):
    ruta = ROOT / relativa
    spec = importlib.util.spec_from_file_location(alias, ruta)
    if spec is None or spec.loader is None:
        raise LabError(f"No se pudo cargar {relativa}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def modulo_5bm():
    """Runner 5BM reapuntado a este cluster, sin modificar el archivo original."""
    modulo = _cargar("database/saas/76_laboratorio_operativo_5bm.py", "lab_5bm_para_5bn")
    modulo.PUERTO_LAB = PUERTO_LAB
    modulo.BASE_LAB = BASE_LAB
    modulo.HOST_LAB = HOST_LAB
    modulo.raiz_lab = raiz_lab
    modulo.ruta_pgdata = ruta_pgdata
    modulo.ruta_manifiesto = ruta_manifiesto
    # `_dsn` tiene `base=BASE_LAB` como valor por defecto, evaluado al definir la
    # funcion: hay que reemplazarla, no solo el global.
    modulo._dsn = lambda rol, password, base=None: _dsn(
        rol, password, base or BASE_LAB)
    return modulo


def _validar_destino() -> None:
    if HOST_LAB not in {"127.0.0.1", "localhost"}:
        raise LabError(f"Host no loopback rechazado: {HOST_LAB}")
    if PUERTO_LAB != 55444:
        raise LabError("Este laboratorio usa exclusivamente 127.0.0.1:55444")
    if BASE_LAB != "fashionstore_lab_5bn":
        raise LabError("Nombre de base 5BN alterado: abortar")
    pgdata = ruta_pgdata().resolve()
    temp = Path(os.environ.get("TEMP", ".")).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", str(temp))).resolve()
    if not (str(pgdata).lower().startswith(str(temp).lower())
            or str(pgdata).lower().startswith(str(local).lower())):
        raise LabError("pgdata debe estar bajo el directorio temporal del usuario")
    # Guarda dura: nunca el directorio del laboratorio E1.
    if "fs_lab_5bm" in str(pgdata).lower():
        raise LabError("El pgdata 5BN no puede apuntar al laboratorio E1/5BM")


def _dsn(rol: str, password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://{rol}:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


# ---------------------------------------------------------------------------
# Migracion 5BN
# ---------------------------------------------------------------------------


def aplicar_5bn(admin_dsn: str) -> None:
    import psycopg

    sql_migracion = (ROOT / "supabase" / "migrations" / MIGRACION_5BN).read_text(
        encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.admin_runtime_role = '{ROL_ADMIN}'")
        conn.execute(f"SET saas.admin_owner_role = '{ROL_ADMIN_OWNER}'")
        conn.execute(f"SET saas.actor_owner_role = '{ROL_ACTOR_OWNER}'")
        conn.execute(sql_migracion)


def verificar_5bn(admin_dsn: str, runtime_dsn: str) -> dict:
    import psycopg

    tablas = ["recepcion_compra", "detalle_recepcion_compra", "reembolso_devolucion"]
    sps = [
        "sp_crear_orden_compra_tenant", "sp_enviar_orden_compra_tenant",
        "sp_cancelar_orden_compra_tenant", "sp_recibir_orden_compra_tenant",
        "sp_crear_devolucion_tenant", "sp_resolver_devolucion_tenant",
        "sp_procesar_devolucion_tenant",
    ]
    with psycopg.connect(admin_dsn) as conn:
        estructura = conn.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policies p
                     WHERE p.schemaname='public' AND p.tablename=c.relname
                       AND p.policyname='tenant_aislamiento')
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relname = ANY(%s)
             ORDER BY c.relname
            """, (tablas,)).fetchall()
        rutinas = conn.execute(
            """
            SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner),
                   COALESCE(array_to_string(p.proconfig, ','), '<sin>')
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname = ANY(%s)
             ORDER BY p.proname
            """, (sps,)).fetchall()
        helpers = conn.execute(
            """
            SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner)
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='saas'
               AND p.proname IN ('fs_actor_empresa','fs_plan_vigente','fn_bitacora_evento')
             ORDER BY p.proname
            """
        ).fetchall()
        matriz = conn.execute(
            """
            SELECT r.rolname, p.proname, has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles r CROSS JOIN pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname = ANY(%s)
               AND r.rolname IN ('fs_saas_runtime','saas_admin_runtime')
             ORDER BY r.rolname, p.proname
            """, (sps,)).fetchall()
        idem = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='saas.operacion_idempotente'::regclass "
            "AND conname='ck_operacion_idempotente_tipo'"
        ).fetchone()
        columna_reponer = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='detalle_devolucion' "
            "AND column_name='reponer' AND column_default LIKE 'true%'"
        ).fetchone()[0]
        fk_recepcion = conn.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE contype='f' AND conrelid='public.recepcion_compra'::regclass"
        ).fetchone()[0]
        trigger_catalogo = conn.execute(
            """
            SELECT count(*)
              FROM pg_trigger t
             WHERE t.tgrelid = 'public.variante_producto'::regclass
               AND t.tgname = 'trg_5bn_validar_catalogos_globales'
               AND NOT t.tgisinternal
            """
        ).fetchone()[0]
        trigger_catalogo_definer = conn.execute(
            "SELECT prosecdef FROM pg_proc WHERE oid = "
            "'public.fn_5bn_validar_catalogos_globales()'::regprocedure"
        ).fetchone()

    with psycopg.connect(runtime_dsn) as conn:
        privilegios = conn.execute(
            """
            SELECT has_table_privilege(current_user, 'public.talla', 'UPDATE'),
                   has_table_privilege(current_user, 'public.color', 'UPDATE'),
                   has_table_privilege(current_user, 'public.talla', 'SELECT'),
                   has_table_privilege(current_user, 'public.recepcion_compra', 'INSERT'),
                   has_table_privilege(current_user, 'public.reembolso_devolucion', 'INSERT'),
                   has_schema_privilege(current_user, 'saas_admin', 'USAGE')
            """
        ).fetchone()
        bloqueos = {}
        for etiqueta, clausula in (
            ("for_update", "FOR UPDATE"),
            ("for_share", "FOR SHARE"),
            ("for_key_share", "FOR KEY SHARE"),
        ):
            try:
                with conn.transaction():
                    conn.execute(
                        f"SELECT id FROM public.talla ORDER BY id LIMIT 1 {clausula}"
                    ).fetchall()
                bloqueos[etiqueta] = "permitido"
            except psycopg.errors.InsufficientPrivilege:
                bloqueos[etiqueta] = "42501 denegado"

    matriz_dict: dict[str, dict[str, bool]] = {}
    for rol, funcion, permitido in matriz:
        matriz_dict.setdefault(rol, {})[funcion] = bool(permitido)

    resultado = {
        "tablas_nuevas": [
            {"tabla": t, "rls": r, "force_rls": f, "politicas": p}
            for t, r, f, p in estructura
        ],
        "rutinas": [
            {"nombre": n, "definer": d, "propietario": o, "config": c}
            for n, d, o, c in rutinas
        ],
        "helpers": [
            {"nombre": n, "definer": d, "propietario": o} for n, d, o in helpers
        ],
        "matriz_execute": matriz_dict,
        "idempotencia_tipos": idem[0] if idem else None,
        "detalle_devolucion_reponer": bool(columna_reponer),
        "fk_recepcion_compra": fk_recepcion,
        "trigger_catalogo_global": trigger_catalogo,
        "trigger_catalogo_global_definer": (
            trigger_catalogo_definer[0] if trigger_catalogo_definer else None
        ),
        "runtime": {
            "update_talla": privilegios[0],
            "update_color": privilegios[1],
            "select_talla": privilegios[2],
            "insert_recepcion": privilegios[3],
            "insert_reembolso": privilegios[4],
            "usage_saas_admin": privilegios[5],
            "bloqueos_fila_talla": bloqueos,
        },
    }
    # Invariantes obligatorios del 5BN.
    for fila in resultado["tablas_nuevas"]:
        if not (fila["rls"] and fila["force_rls"] and fila["politicas"] == 1):
            raise LabError(f"RLS incompleta en {fila['tabla']}")
    for fila in resultado["rutinas"]:
        if fila["definer"] is not False:
            raise LabError(f"{fila['nombre']} no debe ser SECURITY DEFINER")
        if "search_path" not in (fila["config"] or ""):
            raise LabError(f"{fila['nombre']} sin search_path fijo")
    runtime = resultado["runtime"]
    if runtime["update_talla"] or runtime["update_color"] or not runtime["select_talla"]:
        raise LabError("Privilegios de talla/color alterados")
    if runtime["usage_saas_admin"]:
        raise LabError("El runtime tenant puede resolver el plano global")
    if not all(runtime[k] for k in ("insert_recepcion", "insert_reembolso")):
        raise LabError("El runtime no puede escribir las tablas nuevas")
    if any(v != "42501 denegado" for v in runtime["bloqueos_fila_talla"].values()):
        raise LabError(
            "El runtime no deberia poder bloquear filas de talla con ningun modo")
    if resultado["trigger_catalogo_global"] != 1:
        raise LabError("Falta el trigger de invariante de catalogo global (P0)")
    if resultado["trigger_catalogo_global_definer"] is not False:
        raise LabError("El trigger de catalogo global debe ser SECURITY INVOKER")
    tenant = matriz_dict.get("fs_saas_runtime", {})
    admin = matriz_dict.get("saas_admin_runtime", {})
    if not all(tenant.get(n) for n in sps):
        raise LabError("El runtime no puede ejecutar todas las rutinas 5BN")
    if any(admin.get(n) for n in sps):
        raise LabError("El principal global ejecuta rutinas tenant")
    if "RECEPCION_COMPRA" not in (resultado["idempotencia_tipos"] or ""):
        raise LabError("La idempotencia no admite RECEPCION_COMPRA")
    if not resultado["detalle_devolucion_reponer"]:
        raise LabError("Falta la columna detalle_devolucion.reponer")
    return resultado


# ---------------------------------------------------------------------------
# Cluster propio
# ---------------------------------------------------------------------------


def recrear_base(password_admin: str) -> None:
    if BASE_LAB != "fashionstore_lab_5bn" or PUERTO_LAB != 55444:
        raise LabError("Guardas de recreacion alteradas: abortar")
    lab = modulo_5bm()
    lab._ejecutar(
        [str(lab._bin("dropdb")), "--force", "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={"PGPASSWORD": password_admin},
    )
    lab._crear_base_si_falta(password_admin)


def provisionar(password_admin: str, password_runtime: str,
                password_admin_runtime: str) -> dict:
    lab = modulo_5bm()
    admin_dsn = _dsn("postgres", password_admin)
    runtime_dsn = _dsn(ROL_RUNTIME, password_runtime)

    manifiesto = lab.provisionar(password_admin, password_runtime,
                                 password_admin_runtime)
    aplicar_5bn(admin_dsn)
    # Segunda aplicacion: prueba real de idempotencia del DDL/DCL nuevo.
    aplicar_5bn(admin_dsn)
    verificacion = verificar_5bn(admin_dsn, runtime_dsn)

    manifiesto["etapa"] = "5BN / E2"
    manifiesto["migracion_5bn"] = MIGRACION_5BN
    manifiesto["verificacion_5bn"] = verificacion
    manifiesto["cluster"]["etapa"] = "5BN / E2"
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return manifiesto


def escribir_evidencia() -> Path:
    datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
    v = datos.get("verificacion_5bn", {})
    lineas = [
        "EVIDENCIA DE LABORATORIO - ETAPA E2 (5BN) - ESQUEMA Y PRIVILEGIOS",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Cluster: " + datos["cluster"].get("version", "?") + " en "
        + datos["cluster"]["host"] + ":" + str(datos["cluster"]["puerto"])
        + " / " + datos["cluster"]["base"],
        "Migracion nueva: " + MIGRACION_5BN + " (aplicada dos veces: idempotente)",
        "Sin secretos: no se incluyen DSN, JWT, contrasenas, HMAC ni datos personales.",
        "-" * 78,
        "TABLAS NUEVAS (tabla|rls|force_rls|politicas tenant_aislamiento):",
    ]
    for fila in v.get("tablas_nuevas", []):
        lineas.append("  {tabla}|{rls}|{force_rls}|{politicas}".format(**fila))
    lineas.append("RUTINAS NUEVAS (nombre|definer|propietario|search_path):")
    for fila in v.get("rutinas", []):
        lineas.append("  {nombre}|definer={definer}|owner={propietario}|{config}".format(**fila))
    lineas.append("HELPERS saas.* (nombre|definer|propietario):")
    for fila in v.get("helpers", []):
        lineas.append("  {nombre}|definer={definer}|owner={propietario}".format(**fila))
    lineas.append("EXECUTE por OID (rol|rutina|permiso):")
    for rol, funciones in sorted(v.get("matriz_execute", {}).items()):
        for nombre, permitido in sorted(funciones.items()):
            lineas.append(f"  {rol}|{nombre}|{permitido}")
    lineas.append("Restriccion de idempotencia: " + str(v.get("idempotencia_tipos")))
    lineas.append("detalle_devolucion.reponer presente (default TRUE): "
                  + str(v.get("detalle_devolucion_reponer")))
    lineas.append("FK de recepcion_compra: " + str(v.get("fk_recepcion_compra")))
    lineas.append("Privilegios del runtime tenant: " + json.dumps(v.get("runtime"), ensure_ascii=False))
    lineas += [
        "-" * 78,
        "Nota: con el principal tenant, TODOS los modos de bloqueo de fila sobre",
        "`talla` fallan con 42501 (FOR UPDATE / FOR SHARE / FOR KEY SHARE). Por eso",
        "el fix P0 no usa bloqueo: lee sin bloquear y el invariante lo impone el",
        "trigger trg_5bn_validar_catalogos_globales (23514), sin conceder UPDATE.",
    ]
    evidencias = ROOT / "database" / "saas" / "evidencias"
    evidencias.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ruta = evidencias / f"EVIDENCIA_LABORATORIO_5BN_{marca}.txt"
    base, n = ruta, 2
    while ruta.exists():
        ruta = base.with_name(f"EVIDENCIA_LABORATORIO_5BN_{marca}_v{n:02d}.txt")
        n += 1
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--recrear-base", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evidencia", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    _validar_destino()
    lab = modulo_5bm()
    pgdata = ruta_pgdata()

    if args.status:
        print(json.dumps({
            "pgdata": str(pgdata),
            "estado": lab.estado_cluster(pgdata),
            "manifiesto": ruta_manifiesto().exists(),
            "puerto": PUERTO_LAB,
            "base": BASE_LAB,
        }, indent=2))
        return 0
    if args.evidencia:
        if not ruta_manifiesto().exists():
            print("No hay manifiesto 5BN; use --init", file=sys.stderr)
            return 2
        print(str(escribir_evidencia()))
        return 0
    if args.stop:
        lab.detener_cluster(pgdata)
        print(json.dumps({"cluster": "detenido", "pgdata": str(pgdata)}, indent=2))
        return 0
    if not args.confirm_desechable:
        print("Falta --confirm-desechable: este script crea un cluster completo",
              file=sys.stderr)
        return 2

    raiz_lab().mkdir(parents=True, exist_ok=True)
    archivo_admin = raiz_lab() / ".admin_pw"
    password_admin = archivo_admin.read_text(encoding="utf-8").strip() if archivo_admin.exists() else ""
    if not password_admin:
        password_admin = secrets.token_urlsafe(24)
        archivo_admin.write_text(password_admin, encoding="utf-8")
    password_runtime = secrets.token_urlsafe(24)
    password_admin_runtime = secrets.token_urlsafe(24)

    if args.init:
        lab.iniciar_cluster(pgdata, password_admin)
        lab._crear_base_si_falta(password_admin)
    else:
        if not archivo_admin.exists():
            print("No hay credencial admin local; use --init", file=sys.stderr)
            return 2
        if lab.estado_cluster(pgdata) != "activo":
            lab.arrancar_cluster(pgdata)
        if args.recrear_base:
            recrear_base(password_admin)

    manifiesto = provisionar(password_admin, password_runtime, password_admin_runtime)
    print(json.dumps({
        "cluster": manifiesto["cluster"],
        "verificacion_5bn": manifiesto["verificacion_5bn"],
        "manifiesto": str(ruta_manifiesto()),
    }, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
