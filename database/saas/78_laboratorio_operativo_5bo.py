"""5BO / Etapa F - Laboratorio DESECHABLE de nucleo SaaS (personal, RBAC, superadmin).

Cluster PostgreSQL 18 EXCLUSIVO en loopback y en un puerto NUEVO (55445, base
``fashionstore_lab_5bo``). NO reutiliza, detiene, recrea ni escribe en los
laboratorios E1/5BM (``127.0.0.1:55443``) ni E2/5BN (``127.0.0.1:55444``).

Que construye
-------------
1. Todo el laboratorio 5BN/E2 (esquema historico reconstruido, migraciones
   00100..00700, cuatro empresas ficticias, plano global y rutinas E1/E2),
   reutilizando los runners 76/77 SIN editarlos y repuntandolos a este cluster.
2. ``20261030000800_saas_personal_rbac_5bo.sql``: alta de personal con
   invitacion de un solo uso, asistente RBAC nominal por empresa y ayuda
   ``saas.actor_actual()``.
3. ``20261030000900_saas_superadmin_hardening_5bo.sql``: contadores de fuerza
   bruta compartidos y auditoria global del plano superadmin.
4. Verificacion de que los principales siguen aislados: el runtime tenant no
   puede insertar en ``public.usuario`` ni entrar a ``saas_admin``; el principal
   global no puede ejecutar rutinas tenant y no tiene privilegios de tabla.

Reglas de seguridad
-------------------
* Solo loopback y solo el puerto 55445; pgdata bajo el temporal del usuario y
  nombre de base con ``lab``.
* Exige ``--confirm-desechable`` para crear o recrear. El borrado solo afecta a
  ESTE cluster y a ESTA base.
* Nunca imprime contrasenas, DSN completos ni la clave HMAC.

Uso
---
    python database/saas/78_laboratorio_operativo_5bo.py --init --confirm-desechable
    python database/saas/78_laboratorio_operativo_5bo.py --recrear-base --confirm-desechable
    python database/saas/78_laboratorio_operativo_5bo.py --status
    python database/saas/78_laboratorio_operativo_5bo.py --evidencia
    python database/saas/78_laboratorio_operativo_5bo.py --stop
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
PUERTO_LAB = 55445
BASE_LAB = "fashionstore_lab_5bo"
HOST_LAB = "127.0.0.1"
MIGRACIONES_5BO = (
    "20261030000800_saas_personal_rbac_5bo.sql",
    "20261030000900_saas_superadmin_hardening_5bo.sql",
)

ROL_RUNTIME = "fs_saas_runtime"
ROL_ADMIN = "saas_admin_runtime"
ROL_ADMIN_OWNER = "saas_admin_owner"
ROL_ACTOR_OWNER = "saas_actor_sql_owner"
ROL_PERSONAL_OWNER = "saas_personal_owner"

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://staging@127.0.0.1/staging")
os.environ.setdefault("SECRET_KEY", "lab-5bo-placeholder-no-usar")


class LabError(RuntimeError):
    pass


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bo"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bo_manifest.json"


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
    modulo = _cargar("database/saas/76_laboratorio_operativo_5bm.py", "lab_5bm_para_5bo")
    modulo.PUERTO_LAB = PUERTO_LAB
    modulo.BASE_LAB = BASE_LAB
    modulo.HOST_LAB = HOST_LAB
    modulo.raiz_lab = raiz_lab
    modulo.ruta_pgdata = ruta_pgdata
    modulo.ruta_manifiesto = ruta_manifiesto
    modulo._dsn = lambda rol, password, base=None: _dsn(
        rol, password, base or BASE_LAB)
    return modulo


def _validar_destino() -> None:
    if HOST_LAB not in {"127.0.0.1", "localhost"}:
        raise LabError(f"Host no loopback rechazado: {HOST_LAB}")
    if PUERTO_LAB != 55445:
        raise LabError("Este laboratorio usa exclusivamente 127.0.0.1:55445")
    if BASE_LAB != "fashionstore_lab_5bo":
        raise LabError("Nombre de base 5BO alterado: abortar")
    pgdata = ruta_pgdata().resolve()
    temp = Path(os.environ.get("TEMP", ".")).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", str(temp))).resolve()
    if not (str(pgdata).lower().startswith(str(temp).lower())
            or str(pgdata).lower().startswith(str(local).lower())):
        raise LabError("pgdata debe estar bajo el directorio temporal del usuario")
    for ajeno in ("fs_lab_5bm", "fs_lab_5bn"):
        if ajeno in str(pgdata).lower():
            raise LabError(f"El pgdata 5BO no puede apuntar al laboratorio {ajeno}")


def _dsn(rol: str, password: str, base: str = BASE_LAB) -> str:
    return f"postgresql://{rol}:{password}@{HOST_LAB}:{PUERTO_LAB}/{base}"


# ---------------------------------------------------------------------------
# Migraciones 5BO
# ---------------------------------------------------------------------------


def aplicar_migracion_5bo(admin_dsn: str, archivo: str) -> None:
    import psycopg

    sql_migracion = (ROOT / "supabase" / "migrations" / archivo).read_text(
        encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.admin_runtime_role = '{ROL_ADMIN}'")
        conn.execute(f"SET saas.admin_owner_role = '{ROL_ADMIN_OWNER}'")
        conn.execute(f"SET saas.actor_owner_role = '{ROL_ACTOR_OWNER}'")
        conn.execute(sql_migracion)


def aplicar_5bn(admin_dsn: str) -> None:
    import psycopg

    archivo = "20261030000700_saas_compras_devoluciones_5bn.sql"
    sql_migracion = (ROOT / "supabase" / "migrations" / archivo).read_text(
        encoding="utf-8")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"SET saas.runtime_role = '{ROL_RUNTIME}'")
        conn.execute(f"SET saas.admin_runtime_role = '{ROL_ADMIN}'")
        conn.execute(f"SET saas.admin_owner_role = '{ROL_ADMIN_OWNER}'")
        conn.execute(f"SET saas.actor_owner_role = '{ROL_ACTOR_OWNER}'")
        conn.execute(sql_migracion)


def verificar_5bo(admin_dsn: str, runtime_dsn: str, admin_runtime_dsn: str) -> dict:
    import psycopg

    tablas = ["permiso_tenant", "invitacion_personal"]
    with psycopg.connect(admin_dsn) as conn:
        estructura = conn.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policies p
                     WHERE p.schemaname = 'saas' AND p.tablename = c.relname
                       AND p.policyname = 'tenant_aislamiento')
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'saas' AND c.relname = ANY(%s)
             ORDER BY c.relname
            """, (tablas,)).fetchall()
        rutinas = conn.execute(
            """
            SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner),
                   COALESCE(array_to_string(p.proconfig, ','), '<sin>')
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE (n.nspname = 'public' AND p.proname = 'sp_alta_personal_tenant')
                OR (n.nspname = 'saas' AND p.proname IN (
                    'aceptar_invitacion_personal', 'actor_actual',
                    'tiene_permiso_tenant'))
             ORDER BY p.proname
            """).fetchall()
        matriz = conn.execute(
            """
            SELECT r.rolname, p.proname,
                   has_function_privilege(r.oid, p.oid, 'EXECUTE')
              FROM pg_roles r CROSS JOIN pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE r.rolname IN ('fs_saas_runtime', 'saas_admin_runtime',
                                 'saas_personal_owner')
               AND ((n.nspname = 'public' AND p.proname = 'sp_alta_personal_tenant')
                 OR (n.nspname = 'saas' AND p.proname IN (
                     'aceptar_invitacion_personal', 'actor_actual',
                     'tiene_permiso_tenant')))
             ORDER BY r.rolname, p.proname
            """).fetchall()
        permisos_default = conn.execute(
            "SELECT count(*) FROM saas.permiso_default WHERE permitido"
        ).fetchone()[0]
        roles_owner = conn.execute(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolbypassrls
              FROM pg_roles WHERE rolname = 'saas_personal_owner'
            """
        ).fetchone()
        admin_tablas = conn.execute(
            """
            SELECT count(*) FROM information_schema.role_table_grants
             WHERE grantee = 'saas_admin_runtime'
            """
        ).fetchone()[0]
        tablas_admin_5bo = conn.execute(
            """
            SELECT count(*) FROM pg_class c JOIN pg_namespace n
              ON n.oid = c.relnamespace
             WHERE n.nspname = 'saas_admin' AND c.relkind = 'r'
               AND c.relname IN ('intento_auth', 'auditoria_operacion')
            """
        ).fetchone()[0]
        matriz_admin = conn.execute(
            """
            SELECT p.proname, has_function_privilege('saas_admin_runtime', p.oid, 'EXECUTE'),
                   has_function_privilege('fs_saas_runtime', p.oid, 'EXECUTE')
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'saas_admin'
               AND p.proname IN ('estado_acceso', 'registrar_intento',
                                 'auditar_operacion', 'listar_auditoria',
                                 'limpiar_intentos')
             ORDER BY p.proname
            """
        ).fetchall()
        # Privilegios evaluados por NOMBRE DE ROL desde el administrador: el
        # principal global no tiene USAGE en `saas` y consultarlos como el
        # propio rol lanzaria 42501 en lugar de devolver false.
        privilegios = conn.execute(
            """
            SELECT has_table_privilege('fs_saas_runtime', 'public.usuario', 'INSERT'),
                   has_table_privilege('fs_saas_runtime', 'public.usuario', 'UPDATE'),
                   has_schema_privilege('fs_saas_runtime', 'saas_admin', 'USAGE'),
                   has_table_privilege('fs_saas_runtime', 'saas.permiso_tenant', 'SELECT'),
                   has_table_privilege('fs_saas_runtime', 'saas.invitacion_personal', 'INSERT')
            """
        ).fetchone()
        admin_privilegios = conn.execute(
            """
            SELECT has_table_privilege('saas_admin_runtime', 'public.empresa', 'SELECT'),
                   has_table_privilege('saas_admin_runtime', 'saas_admin.intento_auth', 'SELECT'),
                   has_schema_privilege('saas_admin_runtime', 'saas', 'USAGE')
            """
        ).fetchone()

    # Los dos principales deben poder autenticarse (LOGIN real).
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute("SELECT 1").fetchone()
    with psycopg.connect(admin_runtime_dsn) as conn:
        conn.execute("SELECT 1").fetchone()

    resultado = {
        "tablas_nuevas": [
            {"tabla": t, "rls": r, "force_rls": f, "politicas": p}
            for t, r, f, p in estructura
        ],
        "rutinas": [
            {"nombre": n, "definer": d, "propietario": o, "config": c}
            for n, d, o, c in rutinas
        ],
        "matriz_execute": {
            f"{rol}|{nombre}": bool(permitido) for rol, nombre, permitido in matriz
        },
        "permisos_default": permisos_default,
        "owner_personal": {
            "rol": roles_owner[0],
            "login": roles_owner[1],
            "super": roles_owner[2],
            "bypassrls": roles_owner[3],
        } if roles_owner else None,
        "admin_grants_tabla": admin_tablas,
        "tablas_saas_admin_5bo": tablas_admin_5bo,
        "matriz_admin": [
            {"rutina": n, "admin": a, "tenant": t} for n, a, t in matriz_admin
        ],
        "runtime": {
            "insert_usuario": privilegios[0],
            "update_usuario": privilegios[1],
            "usage_saas_admin": privilegios[2],
            "select_permiso_tenant": privilegios[3],
            "insert_invitacion": privilegios[4],
        },
        "admin_runtime": {
            "select_empresa": admin_privilegios[0],
            "select_intento_auth": admin_privilegios[1],
            "usage_saas": admin_privilegios[2],
        },
    }

    for fila in resultado["tablas_nuevas"]:
        if not (fila["rls"] and fila["force_rls"] and fila["politicas"] == 1):
            raise LabError(f"RLS incompleta en {fila['tabla']}")
    por_nombre = {fila["nombre"]: fila for fila in resultado["rutinas"]}
    if not por_nombre.get("sp_alta_personal_tenant", {}).get("definer"):
        raise LabError("sp_alta_personal_tenant debe ser SECURITY DEFINER")
    if por_nombre["sp_alta_personal_tenant"]["propietario"] != ROL_PERSONAL_OWNER:
        raise LabError("sp_alta_personal_tenant debe pertenecer al owner NOLOGIN")
    if "search_path" not in (por_nombre["sp_alta_personal_tenant"]["config"] or ""):
        raise LabError("sp_alta_personal_tenant sin search_path fijo")
    if not por_nombre.get("aceptar_invitacion_personal", {}).get("definer"):
        raise LabError("aceptar_invitacion_personal debe ser SECURITY DEFINER")
    if por_nombre.get("tiene_permiso_tenant", {}).get("definer"):
        raise LabError("tiene_permiso_tenant debe ser SECURITY INVOKER")
    matriz_exec = resultado["matriz_execute"]
    for nombre in ("sp_alta_personal_tenant", "aceptar_invitacion_personal",
                   "actor_actual", "tiene_permiso_tenant"):
        if not matriz_exec.get(f"fs_saas_runtime|{nombre}"):
            raise LabError(f"El runtime no puede ejecutar {nombre}")
        if matriz_exec.get(f"saas_admin_runtime|{nombre}"):
            raise LabError(f"El principal global ejecuta la rutina tenant {nombre}")
    if resultado["permisos_default"] < 30:
        raise LabError("La matriz RBAC nominal no quedo sembrada")
    owner = resultado["owner_personal"]
    if not owner or owner["login"] or owner["super"] or owner["bypassrls"]:
        raise LabError("saas_personal_owner debe ser NOLOGIN sin privilegios altos")
    runtime = resultado["runtime"]
    if runtime["insert_usuario"] or runtime["update_usuario"]:
        raise LabError("El runtime tenant no debe escribir public.usuario directamente")
    if runtime["usage_saas_admin"]:
        raise LabError("El runtime tenant puede resolver el plano global")
    if not (runtime["select_permiso_tenant"] and runtime["insert_invitacion"]):
        raise LabError("El runtime no puede usar la matriz/invitaciones")
    admin = resultado["admin_runtime"]
    if any(admin.values()):
        raise LabError("El principal global no debe tener privilegios de tabla")
    if resultado["admin_grants_tabla"] != 0:
        raise LabError("saas_admin_runtime conserva grants de tabla")
    if resultado["tablas_saas_admin_5bo"] != 2:
        raise LabError("Faltan las tablas de seguridad del superadmin")
    for fila in resultado["matriz_admin"]:
        if not fila["admin"] or fila["tenant"]:
            raise LabError(f"ACL incorrecta en saas_admin.{fila['rutina']}")
    return resultado


# ---------------------------------------------------------------------------
# Cluster propio
# ---------------------------------------------------------------------------


def recrear_base(password_admin: str) -> None:
    if BASE_LAB != "fashionstore_lab_5bo" or PUERTO_LAB != 55445:
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
    admin_runtime_dsn = _dsn(ROL_ADMIN, password_admin_runtime)

    manifiesto = lab.provisionar(password_admin, password_runtime,
                                 password_admin_runtime)
    aplicar_5bn(admin_dsn)
    for archivo in MIGRACIONES_5BO:
        aplicar_migracion_5bo(admin_dsn, archivo)
    # Segunda aplicacion: idempotencia real del DDL/DCL nuevo.
    for archivo in MIGRACIONES_5BO:
        aplicar_migracion_5bo(admin_dsn, archivo)
    verificacion = verificar_5bo(admin_dsn, runtime_dsn, admin_runtime_dsn)

    manifiesto["etapa"] = "5BO / Etapa F"
    manifiesto["migracion_5bn"] = "20261030000700_saas_compras_devoluciones_5bn.sql"
    manifiesto["migraciones_5bo"] = list(MIGRACIONES_5BO)
    manifiesto["verificacion_5bo"] = verificacion
    manifiesto["cluster"]["etapa"] = "5BO / Etapa F"
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return manifiesto


def escribir_evidencia() -> Path:
    datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
    v = datos.get("verificacion_5bo", {})
    lineas = [
        "EVIDENCIA DE LABORATORIO - ETAPA F (5BO) - ESQUEMA Y PRIVILEGIOS",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "Cluster: " + datos["cluster"].get("version", "?") + " en "
        + datos["cluster"]["host"] + ":" + str(datos["cluster"]["puerto"])
        + " / " + datos["cluster"]["base"],
        "Migraciones nuevas: " + ", ".join(datos.get("migraciones_5bo", []))
        + " (aplicadas dos veces: idempotentes)",
        "Sin secretos: no se incluyen DSN, JWT, contrasenas ni datos personales.",
        "-" * 78,
        "TABLAS NUEVAS (tabla|rls|force_rls|politicas tenant_aislamiento):",
    ]
    for fila in v.get("tablas_nuevas", []):
        lineas.append("  {tabla}|{rls}|{force_rls}|{politicas}".format(**fila))
    lineas.append("RUTINAS NUEVAS (nombre|definer|propietario|search_path):")
    for fila in v.get("rutinas", []):
        lineas.append("  {nombre}|definer={definer}|owner={propietario}|{config}".format(**fila))
    lineas.append("EXECUTE por OID (rol|rutina|permiso):")
    for clave, permitido in sorted(v.get("matriz_execute", {}).items()):
        rol, rutina = clave.split("|", 1)
        lineas.append(f"  {rol}|{rutina}|{permitido}")
    lineas.append("Permisos nominales sembrados: " + str(v.get("permisos_default")))
    lineas.append("Owner del bootstrap: " + json.dumps(v.get("owner_personal")))
    lineas.append("Privilegios del runtime tenant: " + json.dumps(v.get("runtime")))
    lineas.append("Privilegios del principal global: " + json.dumps(v.get("admin_runtime")))
    lineas.append("Grants de tabla del principal global: " + str(v.get("admin_grants_tabla")))
    lineas.append("Rutinas de seguridad superadmin (rutina|admin|tenant):")
    for fila in v.get("matriz_admin", []):
        lineas.append("  {rutina}|{admin}|{tenant}".format(**fila))
    lineas += [
        "-" * 78,
        "Nota: el alta de personal se ejecuta por una funcion SECURITY DEFINER",
        "con propietario NOLOGIN (saas_personal_owner). El runtime tenant no",
        "recibe INSERT/UPDATE en public.usuario ni USAGE en saas_admin.",
    ]
    evidencias = ROOT / "database" / "saas" / "evidencias"
    evidencias.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ruta = evidencias / f"EVIDENCIA_LABORATORIO_5BO_{marca}.txt"
    base, n = ruta, 2
    while ruta.exists():
        ruta = base.with_name(f"EVIDENCIA_LABORATORIO_5BO_{marca}_v{n:02d}.txt")
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
            print("No hay manifiesto 5BO; use --init", file=sys.stderr)
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
        "verificacion_5bo": manifiesto["verificacion_5bo"],
        "manifiesto": str(ruta_manifiesto()),
    }, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
