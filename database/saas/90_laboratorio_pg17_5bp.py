"""5BP / Etapa G - Laboratorio PostgreSQL 17 REAL, desechable y aislado.

Que hace
--------
1. Localiza binarios PostgreSQL 17 (portables descargados o instalacion explicita
   via ``FASHIONSTORE_PG17_BIN``) y verifica que sean 17.x. Nunca usa 18.
2. Crea un cluster EXCLUSIVO en ``127.0.0.1:55446`` con pgdata bajo el temporal
   del usuario y base ``fashionstore_lab_pg17_5bp``.
3. Aplica el pipeline del manifiesto controlado (``86_preflight_...py``) DESDE
   CERO: esquema heredado sintetico -> 00100..01000, con registro de versiones.
4. Verifica aislamiento de principales, RLS/FORCE RLS, funciones SECURITY
   DEFINER, ACL y la migracion nueva de invitaciones (B9).
5. Escribe un manifiesto local (fuera de Git) con los DSN del laboratorio para
   que las pruebas HTTP lo consuman, y evidencia de solo lectura.

Que NO es
----------
No es staging. El esquema heredado es la reconstruccion parcial 5BL con datos
ficticios; sirve para validar motor, privilegios, RLS y SQL, no el esquema real.

Reglas de seguridad
-------------------
* Puerto fijo 55446. E1 (:55443), E2 (:55444) y F (:55445) estan prohibidos de
  forma dura, igual que las bases historicas.
* ``--confirm-desechable`` obligatorio para crear/recrear.
* No imprime passwords, DSN completos ni la clave HMAC.

Uso
---
    python database/saas/90_laboratorio_pg17_5bp.py --init --confirm-desechable
    python database/saas/90_laboratorio_pg17_5bp.py --status
    python database/saas/90_laboratorio_pg17_5bp.py --evidencia
    python database/saas/90_laboratorio_pg17_5bp.py --stop
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
HOST_LAB = "127.0.0.1"
PUERTO_LAB = 55446
BASE_LAB = "fashionstore_lab_pg17_5bp"
PUERTOS_PROHIBIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443, 55444, 55445}

ROL_RUNTIME = "fs_saas_runtime"
ROL_ADMIN = "saas_admin_runtime"
ROLES_NOLOGIN = (
    "saas_admin_owner", "saas_actor_sql_owner", "saas_personal_owner",
    "saas_rls_owner",
)

VARIABLE_PASSWORD_ADMIN = "FASHIONSTORE_LAB_5BP_DB_PASSWORD"
VARIABLE_RUNTIME = "FASHIONSTORE_LAB_5BP_RUNTIME_PASSWORD"
VARIABLE_ADMIN = "FASHIONSTORE_LAB_5BP_ADMIN_PASSWORD"
VARIABLE_ASSERTION = "FASHIONSTORE_LAB_5BP_ASSERTION_KEY"


class LabError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Rutas y binarios
# ---------------------------------------------------------------------------


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Temp" / "opencode" / "fs_lab_5bp_pg17"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5bp_manifest.json"


def ruta_secretos() -> Path:
    """Secretos del laboratorio, fuera del repositorio y del manifiesto."""
    return raiz_lab() / "lab_5bp_secrets.json"


def _candidatos_bin() -> list[Path]:
    candidatos: list[Path] = []
    if os.environ.get("FASHIONSTORE_PG17_BIN"):
        candidatos.append(Path(os.environ["FASHIONSTORE_PG17_BIN"]))
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    candidatos.append(Path(base) / "Temp" / "opencode" / "fs_pg17_5bp"
                      / "pg17" / "pgsql" / "bin")
    for ruta in (Path(r"C:\Program Files\PostgreSQL\17\bin"),):
        candidatos.append(ruta)
    encontrado = shutil.which("pg_ctl")
    if encontrado:
        candidatos.append(Path(encontrado).parent)
    return [c for c in candidatos if (c / "pg_ctl.exe").exists()]


def _bin(nombre: str) -> Path:
    for carpeta in _candidatos_bin():
        ruta = carpeta / f"{nombre}.exe"
        if ruta.exists():
            return ruta
    raise LabError(
        "No hay binarios PostgreSQL 17 disponibles. Definir FASHIONSTORE_PG17_BIN "
        "o instalar/descomprimir PostgreSQL 17 portable. NO se sustituye por 18.")


def version_pg17() -> dict:
    salida = subprocess.run([str(_bin("postgres")), "--version"],
                            capture_output=True, text=True, check=True).stdout
    texto = salida.strip()
    if "PostgreSQL) 17" not in texto:
        raise LabError(
            f"Los binarios localizados no son PostgreSQL 17: {texto}. "
            "Abortado: no se usa 18 como evidencia de 17.")
    return {"postgres": texto,
            "pg_dump": subprocess.run([str(_bin("pg_dump")), "--version"],
                                      capture_output=True, text=True,
                                      check=True).stdout.strip()}


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def _ejecutar(args: list[str], *, check: bool = True, capturar: bool = True,
              env: dict | None = None):
    resultado = subprocess.run(args, capture_output=capturar, text=True, env=env)
    if check and resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "")[-500:]
        raise LabError(f"Fallo {' '.join(Path(a).name for a in args)}: {detalle}")
    return resultado


def _validar_destino() -> None:
    if PUERTO_LAB in PUERTOS_PROHIBIDOS:
        raise LabError("Puerto del laboratorio 5BP colisiona con uno prohibido")
    if PUERTO_LAB != 55446 or BASE_LAB != "fashionstore_lab_pg17_5bp":
        raise LabError("Guardas del laboratorio alteradas: abortar")
    temporal = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
    if temporal.resolve() not in raiz_lab().resolve().parents:
        raise LabError("La raiz del laboratorio no esta bajo el temporal del usuario")


def _puerto_libre(puerto: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST_LAB, puerto)) != 0


def iniciar_cluster(password_admin: str) -> None:
    pgdata = ruta_pgdata()
    _validar_destino()
    if not _puerto_libre(PUERTO_LAB):
        raise LabError(
            f"El puerto {PUERTO_LAB} ya esta ocupado. No se reutiliza un cluster "
            "ajeno: liberar el puerto o abortar.")
    if pgdata.exists() and any(pgdata.iterdir()):
        raise LabError(f"{pgdata} ya contiene datos: usar --recrear-base")
    pgdata.mkdir(parents=True, exist_ok=True)
    pwfile = raiz_lab() / ".initdb_pw"
    pwfile.write_text(password_admin, encoding="utf-8")
    try:
        _ejecutar([
            str(_bin("initdb")), "-D", str(pgdata), "-U", "postgres",
            f"--pwfile={pwfile}", "--auth-local=trust",
            "--auth-host=scram-sha-256", "--encoding=UTF8",
        ])
    finally:
        pwfile.unlink(missing_ok=True)
    arrancar_cluster()


def arrancar_cluster() -> None:
    """Arranca el cluster y espera a que acepte conexiones.

    ``pg_ctl -w`` se lanza con stdout/stderr en DEVNULL: el proceso ``postgres``
    hereda esos descriptores y, si el runner corre con la salida canalizada, el
    pipe quedaria abierto para siempre. El log real lo escribe ``-l``.
    """
    log = raiz_lab() / "postgres.log"
    subprocess.run(
        [str(_bin("pg_ctl")), "-D", str(ruta_pgdata()), "-l", str(log),
         "-o", f"-p {PUERTO_LAB} -c listen_addresses={HOST_LAB}", "-w", "start"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    )
    for _ in range(40):
        if not _puerto_libre(PUERTO_LAB):
            return
        time.sleep(0.5)
    raise LabError(
        f"El cluster no acepta conexiones en {HOST_LAB}:{PUERTO_LAB}. "
        f"Revisar {log}")


def detener_cluster() -> str:
    if not ruta_pgdata().exists():
        return "ausente"
    _ejecutar([str(_bin("pg_ctl")), "-D", str(ruta_pgdata()), "-w", "-m", "fast",
               "stop"], check=False, capturar=False)
    return "detenido"


def estado_cluster() -> str:
    if not ruta_pgdata().exists():
        return "ausente"
    resultado = _ejecutar([str(_bin("pg_ctl")), "-D", str(ruta_pgdata()), "status"],
                          check=False)
    return "activo" if resultado.returncode == 0 else "detenido"


def _dsn(rol: str, password: str, base: str = BASE_LAB) -> str:
    from urllib.parse import quote

    return (f"postgresql://{rol}:{quote(password, safe='')}"
            f"@{HOST_LAB}:{PUERTO_LAB}/{base}")


def _crear_base_si_falta(password_admin: str) -> None:
    import psycopg

    with psycopg.connect(_dsn("postgres", password_admin, "postgres"),
                         autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                        (BASE_LAB,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{BASE_LAB}"')


def recrear_base(password_admin: str) -> None:
    _validar_destino()
    if estado_cluster() != "activo":
        raise LabError("El cluster no esta activo: ejecutar --init primero")
    _ejecutar(
        [str(_bin("dropdb")), "--force", "-h", HOST_LAB, "-p", str(PUERTO_LAB),
         "-U", "postgres", BASE_LAB],
        env={**os.environ, "PGPASSWORD": password_admin},
    )
    _crear_base_si_falta(password_admin)


def preflight_admin(admin_dsn: str) -> dict:
    import psycopg

    _validar_destino()
    parsed = urlparse(admin_dsn)
    if (parsed.hostname or "") != HOST_LAB or (parsed.port or 0) != PUERTO_LAB:
        raise LabError("El DSN no apunta al laboratorio 5BP")
    with psycopg.connect(admin_dsn) as conn:
        fila = conn.execute(
            "SELECT current_database(), current_user, version(), "
            "current_setting('data_directory'), current_setting('port')"
        ).fetchone()
    base, usuario, version, data_dir, puerto = fila
    if str(Path(data_dir).resolve()).lower() != str(ruta_pgdata().resolve()).lower():
        raise LabError("data_directory no coincide con el pgdata del laboratorio")
    if str(puerto) != str(PUERTO_LAB):
        raise LabError("El puerto efectivo no es 55446")
    if usuario != "postgres" or base != BASE_LAB:
        raise LabError("Base o usuario administrador inesperados")
    if not version.startswith("PostgreSQL 17"):
        raise LabError(f"El servidor no es PostgreSQL 17: {version}")
    return {"base": base, "usuario": usuario,
            "version": version.split(" on ")[0], "puerto": int(puerto)}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def modulo_preflight():
    ruta = ROOT / "database" / "saas" / "86_preflight_migracion_pg17_5bp.py"
    alias = "preflight_5bp"
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def _asegurar_secretos() -> dict:
    """Genera (o reutiliza) los secretos del laboratorio.

    Se persisten en ``lab_5bp_secrets.json`` ANTES de aprovisionar: si el
    pipeline falla a mitad, una nueva ejecucion debe poder reconectar al mismo
    cluster con la misma contrasena de ``postgres`` (initdb ya la fijo).
    """
    previos: dict = {}
    if ruta_secretos().exists():
        try:
            previos = json.loads(ruta_secretos().read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previos = {}

    secretos = {
        "password_admin": previos.get("password_admin") or secrets.token_urlsafe(24),
        "password_runtime": previos.get("password_runtime") or secrets.token_urlsafe(24),
        "password_admin_runtime": (previos.get("password_admin_runtime")
                                   or secrets.token_urlsafe(24)),
        "assertion_key": previos.get("assertion_key") or secrets.token_urlsafe(48),
    }
    ruta_secretos().parent.mkdir(parents=True, exist_ok=True)
    ruta_secretos().write_text(json.dumps(secretos, indent=2), encoding="utf-8")

    os.environ[VARIABLE_PASSWORD_ADMIN] = secretos["password_admin"]
    os.environ[VARIABLE_RUNTIME] = secretos["password_runtime"]
    os.environ[VARIABLE_ADMIN] = secretos["password_admin_runtime"]
    os.environ[VARIABLE_ASSERTION] = secretos["assertion_key"]
    return secretos


def provisionar(secretos: dict) -> dict:
    preflight = modulo_preflight()
    manifiesto_pipeline = preflight.cargar_manifiesto()
    destino = preflight.Destino(host=HOST_LAB, puerto=PUERTO_LAB, base=BASE_LAB,
                               password=secretos["password_admin"])
    admin_dsn = _dsn("postgres", secretos["password_admin"])
    info = preflight_admin(admin_dsn)

    resultado = preflight.aplicar_lab(manifiesto_pipeline, destino)

    runtime_dsn = _dsn(ROL_RUNTIME, secretos["password_runtime"])
    admin_runtime_dsn = _dsn(ROL_ADMIN, secretos["password_admin_runtime"])

    modulo_74 = preflight._modulo(
        "database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_5bp")
    password_usuarios = getattr(modulo_74, "PASSWORD_USUARIOS", None)

    verificacion = verificar(admin_dsn, runtime_dsn, admin_runtime_dsn)

    datos = {
        "fecha": date.today().isoformat(),
        "etapa": "5BP / Etapa G",
        "cluster": {
            "host": HOST_LAB, "puerto": PUERTO_LAB, "base": BASE_LAB,
            "pgdata": str(ruta_pgdata()), "version": info["version"],
            "binarios": version_pg17(),
        },
        "admin_dsn": admin_dsn,
        "runtime_dsn": runtime_dsn,
        "admin_runtime_dsn": admin_runtime_dsn,
        "assertion_key": secretos["assertion_key"],
        "password_admin": secretos["password_admin"],
        "password_runtime": secretos["password_runtime"],
        "password_admin_runtime": secretos["password_admin_runtime"],
        "password_usuarios": password_usuarios,
        "roles": {
            "runtime": ROL_RUNTIME, "admin_runtime": ROL_ADMIN,
            "nologin": list(ROLES_NOLOGIN),
        },
        "empresas": resultado.get("contexto", {}).get("empresas"),
        "usuarios": {
            "a_admin": 1, "a_cliente": 2, "b_admin": 3, "b_cliente": 4,
            "b_cajero": 5, "a_deshabilitado": 6, "a_revocado": 7,
            "c_suspendida": 8, "d_inactiva": 9,
        },
        "correos": {
            "a_admin": "admin@staging.invalid",
            "b_admin": "admin.b@staging.invalid",
            "b_cajero": "cajero.b@staging.invalid",
        },
        "pipeline": {
            "version_servidor": resultado["version_servidor"],
            "aplicadas": resultado["aplicadas"],
            "omitidas": resultado["omitidas"],
            "registro": resultado["registro"],
        },
        "verificacion": verificacion,
    }
    ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto().write_text(
        json.dumps(datos, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return datos


# ---------------------------------------------------------------------------
# Verificacion
# ---------------------------------------------------------------------------


def verificar(admin_dsn: str, runtime_dsn: str, admin_runtime_dsn: str) -> dict:
    import psycopg

    fallos: list[str] = []
    with psycopg.connect(admin_dsn) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        if not version.startswith("17."):
            fallos.append(f"version_no_17:{version}")

        conteos = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind='r' AND n.nspname='public'),
              (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                 JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname='public'),
              (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public'),
              (SELECT count(*) FROM information_schema.columns
                WHERE column_name='empresa_id' AND table_schema='public'),
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind='r' AND n.nspname='public' AND c.relforcerowsecurity),
              (SELECT count(*) FROM pg_policies WHERE schemaname IN ('public','saas'))
            """
        ).fetchone()
        conteos = dict(zip(
            ("tablas_public", "triggers_public", "rutinas_public",
             "tablas_empresa_id", "tablas_force_rls", "politicas"), conteos))

        if conteos["tablas_empresa_id"] < 31:
            fallos.append(f"empresa_id_insuficiente:{conteos['tablas_empresa_id']}")
        if conteos["tablas_force_rls"] < 31:
            fallos.append(f"force_rls_insuficiente:{conteos['tablas_force_rls']}")

        roles = conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit "
            "FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
            (list(ROLES_NOLOGIN) + [ROL_RUNTIME, ROL_ADMIN],),
        ).fetchall()
        mapa_roles = {f[0]: {"login": f[1], "super": f[2], "bypassrls": f[3],
                            "inherit": f[4]} for f in roles}
        for rol in ROLES_NOLOGIN:
            datos = mapa_roles.get(rol)
            if not datos:
                fallos.append(f"rol_ausente:{rol}")
                continue
            if datos["login"] or datos["super"] or datos["bypassrls"]:
                fallos.append(f"rol_inseguro:{rol}")
        for rol in (ROL_RUNTIME, ROL_ADMIN):
            datos = mapa_roles.get(rol)
            if not datos or datos["super"] or datos["bypassrls"]:
                fallos.append(f"principal_inseguro:{rol}")

        privilegios = conn.execute(
            """
            SELECT
              has_table_privilege(%(rt)s, 'public.usuario', 'INSERT'),
              has_table_privilege(%(rt)s, 'public.usuario', 'UPDATE'),
              has_schema_privilege(%(rt)s, 'saas_admin', 'USAGE'),
              has_function_privilege(%(rt)s, 'saas_admin.estado_acceso(text,text,integer,integer,integer)', 'EXECUTE'),
              has_table_privilege(%(rt)s, 'saas_admin.intento_invitacion', 'INSERT'),
              has_function_privilege(%(rt)s, 'saas_admin.estado_invitacion(text,text,integer,integer,integer)', 'EXECUTE'),
              has_function_privilege(%(ad)s, 'saas_admin.estado_invitacion(text,text,integer,integer,integer)', 'EXECUTE'),
              has_function_privilege(%(ad)s, 'public.sp_alta_personal_tenant(bigint,bigint,text,text,text,text,text,bigint,bigint,text,text,timestamp with time zone)', 'EXECUTE')
            """,
            {"rt": ROL_RUNTIME, "ad": ROL_ADMIN},
        ).fetchone()
        claves = ("rt_insert_usuario", "rt_update_usuario", "rt_usage_saas_admin",
                  "rt_exec_estado_acceso", "rt_insert_intento_invitacion",
                  "rt_exec_estado_invitacion", "ad_exec_estado_invitacion",
                  "ad_exec_sp_alta_personal")
        privilegios = dict(zip(claves, privilegios))
        if privilegios["rt_insert_usuario"] or privilegios["rt_update_usuario"]:
            fallos.append("runtime_escribe_usuario")
        if privilegios["rt_usage_saas_admin"] or privilegios["rt_exec_estado_acceso"]:
            fallos.append("runtime_accede_plano_global")
        if privilegios["rt_insert_intento_invitacion"]:
            fallos.append("runtime_escribe_tabla_rate_limit")
        if not privilegios["ad_exec_estado_invitacion"]:
            fallos.append("admin_sin_exec_estado_invitacion")
        if privilegios["ad_exec_sp_alta_personal"]:
            fallos.append("admin_ejecuta_sp_tenant")

        grants_admin = conn.execute(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee = %s", (ROL_ADMIN,)).fetchone()[0]
        if grants_admin != 0:
            fallos.append(f"admin_con_grants_de_tabla:{grants_admin}")

        definer = conn.execute(
            """
            SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner),
                   COALESCE(array_to_string(p.proconfig, ','), '<sin>')
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE (n.nspname='public' AND p.proname='sp_alta_personal_tenant')
                OR (n.nspname='saas' AND p.proname IN
                    ('tiene_permiso_tenant','aceptar_invitacion_personal','actor_actual'))
             ORDER BY 1
            """
        ).fetchall()
        mapa_definer = {f[0]: {"secdef": f[1], "duenio": f[2], "search_path": f[3]}
                        for f in definer}
        alta = mapa_definer.get("sp_alta_personal_tenant")
        if not alta or not alta["secdef"] or alta["duenio"] != "saas_personal_owner":
            fallos.append("sp_alta_personal_tenant_inseguro")
        elif "search_path" not in alta["search_path"]:
            fallos.append("sp_alta_personal_tenant_sin_search_path")
        permiso = mapa_definer.get("tiene_permiso_tenant")
        if not permiso or permiso["secdef"]:
            fallos.append("tiene_permiso_tenant_no_es_invoker")

        invitacion = conn.execute(
            """
            SELECT to_regclass('saas_admin.intento_invitacion') IS NOT NULL,
                   to_regclass('saas_admin.estado_invitacion') IS NOT NULL,
                   (SELECT count(*) FROM pg_proc p JOIN pg_namespace n
                      ON n.oid=p.pronamespace
                     WHERE n.nspname='saas_admin' AND p.proname LIKE '%invitacion%')
            """
        ).fetchone()
        b9 = {"tabla": bool(invitacion[0]), "funciones": int(invitacion[2])}
        if not b9["tabla"] or b9["funciones"] < 3:
            fallos.append("b9_rate_limit_incompleto")

        registro = conn.execute(
            "SELECT count(*) FROM saas_migracion.version_aplicada").fetchone()[0]

    # Comprobacion funcional: el runtime no puede insertar en public.usuario.
    try:
        with psycopg.connect(runtime_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO public.usuario (rol_id, correo, password_hash, "
                "fecha_creacion, estado) VALUES (1, 'prohibido@lab.invalid', "
                "'x', NOW(), TRUE)")
        fallos.append("runtime_insert_usuario_permitido")
    except psycopg.Error as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate != "42501":
            fallos.append(f"runtime_insert_error_inesperado:{sqlstate}")

    # Comprobacion funcional: el principal global no tiene acceso a tablas.
    try:
        with psycopg.connect(admin_runtime_dsn, autocommit=True) as conn:
            conn.execute("SELECT count(*) FROM public.empresa").fetchone()
        fallos.append("admin_lee_tablas")
    except psycopg.Error as exc:
        if getattr(exc, "sqlstate", None) != "42501":
            fallos.append(f"admin_error_inesperado:{getattr(exc, 'sqlstate', None)}")

    return {
        "version_servidor": version,
        "conteos": conteos,
        "roles": mapa_roles,
        "privilegios": privilegios,
        "grants_admin": grants_admin,
        "definer": mapa_definer,
        "b9": b9,
        "versiones_registradas": registro,
        "fallos": fallos,
        "ok": not fallos,
    }


# ---------------------------------------------------------------------------
# Evidencia y CLI
# ---------------------------------------------------------------------------


def escribir_evidencia() -> Path:
    import psycopg

    datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destino = ROOT / "database" / "saas" / "evidencias" / f"EVIDENCIA_PG17_5BP_{sello}.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(datos["admin_dsn"]) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        objetos = conn.execute(
            """
            SELECT n.nspname, c.relkind, count(*)
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname IN ('public','saas','saas_admin','saas_migracion')
             GROUP BY 1, 2 ORDER BY 1, 2
            """
        ).fetchall()
        politicas = conn.execute(
            "SELECT schemaname, count(*) FROM pg_policies "
            "WHERE schemaname IN ('public','saas') GROUP BY 1 ORDER BY 1"
        ).fetchall()
        rutinas = conn.execute(
            "SELECT n.nspname, count(*), count(*) FILTER (WHERE p.prosecdef) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname IN ('public','saas','saas_admin') "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()
        versiones = conn.execute(
            "SELECT fase, orden, checksum, aplicado_en, duracion_ms "
            "FROM saas_migracion.version_aplicada ORDER BY orden"
        ).fetchall()

    lineas = [
        "EVIDENCIA 5BP - LABORATORIO PostgreSQL 17",
        "=" * 60,
        f"Generado UTC        : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Host/puerto         : {datos['cluster']['host']}:{datos['cluster']['puerto']}",
        f"Base                : {datos['cluster']['base']}",
        f"pgdata              : {datos['cluster']['pgdata']}",
        f"Servidor            : PostgreSQL {version}",
        f"Binarios            : {datos['cluster']['binarios']['postgres']}",
        "",
        "1. OBJETOS POR ESQUEMA (nombre, tipo, cantidad)",
    ]
    for schema, relkind, cantidad in objetos:
        lineas.append(f"   {schema:<16} {relkind}  {cantidad}")
    lineas.append("")
    lineas.append("2. RUTINAS Y SECURITY DEFINER")
    for schema, total, definer in rutinas:
        lineas.append(f"   {schema:<16} total={total} definer={definer}")
    lineas.append("")
    lineas.append("3. POLITICAS RLS")
    for schema, cantidad in politicas:
        lineas.append(f"   {schema:<16} {cantidad}")
    lineas.append("")
    lineas.append("4. VERSIONES APLICADAS (registro del manifiesto)")
    for fase, orden, checksum, aplicado, ms in versiones:
        lineas.append(f"   {orden:>4} {fase:<32} {checksum[:12]} {aplicado} {ms} ms")
    lineas.append("")
    lineas.append("5. VERIFICACION DE AISLAMIENTO Y ACL")
    for clave, valor in datos["verificacion"]["conteos"].items():
        lineas.append(f"   {clave:<24} {valor}")
    for clave, valor in datos["verificacion"]["privilegios"].items():
        lineas.append(f"   {clave:<32} {valor}")
    lineas.append(f"   grants_tabla_admin               "
                  f"{datos['verificacion']['grants_admin']}")
    lineas.append("")
    lineas.append("6. RESULTADO")
    fallos = datos["verificacion"]["fallos"]
    lineas.append("   fallos: " + ("ninguno" if not fallos else ", ".join(fallos)))
    lineas.append("   estado: " + ("OK" if not fallos else "REVISAR"))
    lineas.append("")
    lineas.append("Nota: datos sinteticos. No contiene passwords, DSN completos ni PII.")
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return destino


def _resumen(datos: dict) -> dict:
    verificacion = datos["verificacion"]
    return {
        "cluster": datos["cluster"],
        "pipeline_aplicadas": len(datos["pipeline"]["aplicadas"]),
        "pipeline_omitidas": len(datos["pipeline"]["omitidas"]),
        "versiones_registradas": verificacion["versiones_registradas"],
        "conteos": verificacion["conteos"],
        "b9": verificacion["b9"],
        "fallos": verificacion["fallos"],
        "ok": verificacion["ok"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BP: laboratorio PostgreSQL 17 desechable en 127.0.0.1:55446")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--recrear-base", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evidencia", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.stop:
            print(f"Cluster 5BP: {detener_cluster()} (E1/E2/F no se tocan)")
            return 0

        if args.status:
            estado = estado_cluster()
            datos = {}
            if ruta_manifiesto().exists():
                datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
            salida = {
                "cluster": estado,
                "puerto": PUERTO_LAB,
                "base": BASE_LAB,
                "pgdata": str(ruta_pgdata()),
                "binarios": version_pg17() if _candidatos_bin() else "NO DISPONIBLE",
                "resumen": _resumen(datos) if datos else None,
            }
            print(json.dumps(salida, indent=2, ensure_ascii=False, default=str))
            return 0

        if args.evidencia:
            if not ruta_manifiesto().exists():
                raise LabError("No hay manifiesto: ejecutar --init primero")
            ruta = escribir_evidencia()
            print(f"Evidencia escrita: {ruta}")
            return 0

        if not (args.init or args.recrear_base):
            parser.print_help()
            return 2

        if not args.confirm_desechable:
            raise LabError(
                "Falta --confirm-desechable: el laboratorio crea/recrea una base.")
        _validar_destino()
        secretos = _asegurar_secretos()

        if args.init:
            iniciar_cluster(secretos["password_admin"])
            _crear_base_si_falta(secretos["password_admin"])
        else:
            recrear_base(secretos["password_admin"])

        datos = provisionar(secretos)
        resumen = _resumen(datos)
        if args.json:
            print(json.dumps(resumen, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"Laboratorio 5BP listo en {HOST_LAB}:{PUERTO_LAB}/{BASE_LAB}")
            print(f"  servidor          : {datos['cluster']['version']} "
                  f"({datos['cluster']['binarios']['postgres']})")
            print(f"  fases aplicadas   : {resumen['pipeline_aplicadas']}")
            print(f"  fases omitidas    : {resumen['pipeline_omitidas']}")
            print(f"  versiones registro: {resumen['versiones_registradas']}")
            print(f"  tablas public     : {resumen['conteos']['tablas_public']}")
            print(f"  triggers public   : {resumen['conteos']['triggers_public']}")
            print(f"  rutinas public    : {resumen['conteos']['rutinas_public']}")
            print(f"  politicas RLS     : {resumen['conteos']['politicas']}")
            print(f"  B9 rate limit     : {resumen['b9']}")
            print(f"  fallos            : "
                  f"{resumen['fallos'] if resumen['fallos'] else 'ninguno'}")
            print(f"  manifiesto        : {ruta_manifiesto()}")
        return 0 if resumen["ok"] else 1

    except LabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
