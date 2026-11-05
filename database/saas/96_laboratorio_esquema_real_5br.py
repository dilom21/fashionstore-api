"""5BR / Etapa I - Restauracion del ESQUEMA HISTORICO REAL en PostgreSQL 17 aislado.

Que resuelve
------------
Las etapas anteriores solo pudieron ensayar sobre una reconstruccion PARCIAL del
esquema historico (40 de 41 tablas, 14 de 27 triggers, 12 de 24 rutinas). Desde
la extraccion 5BQ existe el ``pg_dump --schema-only`` REAL del esquema ``public``:

    C:\\Users\\josia\\Documents\\fashionstore-esquema-5bq\\esquema_public_20261031_115806.sql
    SHA-256 1cbe562bc3939db74354127658978e6dd13a82a205c29b6d455b2b2bca36f80e

Este runner restaura **solo estructura** de ese dump en un cluster PostgreSQL 17
NUEVO, desechable y de loopback, para poder aplicar encima el pipeline SaaS de la
Etapa G y demostrar compatibilidad real.

Que NO hace
-----------
* No se conecta a Supabase ni a ningun origen remoto.
* No exporta ni restaura datos de negocio: el dump es ``--schema-only`` y la
  semilla posterior es 100% ficticia.
* No copia el dump al repositorio. Solo escribe inventarios sanitizados
  (nombres de objeto, atributos y hashes), nunca DDL completo.

Reglas de seguridad
-------------------
* Puerto fijo ``55448`` (E1 :55443, E2 :55444, F :55445, G :55446 y H :55447
  quedan prohibidos de forma dura).
* ``--confirm-desechable`` obligatorio para crear/recrear/restaurar.
* Verifica SHA-256 del dump antes de leerlo y aborta si discrepa.
* ``initdb`` local con password aleatoria guardada fuera del repositorio.

Uso
---
    python database/saas/96_laboratorio_esquema_real_5br.py --inspeccionar
    python database/saas/96_laboratorio_esquema_real_5br.py --init --confirm-desechable
    python database/saas/96_laboratorio_esquema_real_5br.py --restaurar --confirm-desechable
    python database/saas/96_laboratorio_esquema_real_5br.py --catalogo <salida.json>
    python database/saas/96_laboratorio_esquema_real_5br.py --status
    python database/saas/96_laboratorio_esquema_real_5br.py --stop
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[2]
HOST_LAB = "127.0.0.1"
PUERTO_LAB = 55448
BASE_LAB = "fashionstore_lab_5br_esquema_real"

PUERTOS_PROHIBIDOS = {
    5432, 55432, 55433, 55440, 55441, 55442, 55443, 55444, 55445, 55446, 55447,
}

RUTA_ORIGEN_DEFECTO = Path(
    r"C:\Users\josia\Documents\fashionstore-esquema-5bq"
    r"\esquema_public_20261031_115806.sql"
)
HASH_ORIGEN_ESPERADO = (
    "1cbe562bc3939db74354127658978e6dd13a82a205c29b6d455b2b2bca36f80e"
)
RUTA_CATALOGO_ORIGEN = Path(
    r"C:\Users\josia\Documents\fashionstore-esquema-5bq"
    r"\catalogo_20261031_115806.json"
)
HASH_CATALOGO_ESPERADO = (
    "a18c9cb673036abe82664b7977ec53a68fab85e709933f34b4a6b039bc98aa23"
)

# Roles de PLATAFORMA que el dump referencia en OWNER/ACL. En el origen son
# globales de Supabase; aqui se recrean LOCALES, NOLOGIN y sin privilegios.
ROLES_PLATAFORMA = ("anon", "authenticated", "service_role", "supabase_admin")

CONTEOS_ORIGEN = {
    "tablas": 41, "triggers": 27, "rutinas": 24, "vistas": 1,
}

# Sentencias que harian del dump un script no confiable.
PATRONES_PROHIBIDOS = {
    r"\bDROP\s+DATABASE\b": "DROP DATABASE",
    r"\bCREATE\s+DATABASE\b": "CREATE DATABASE",
    r"\bALTER\s+SYSTEM\b": "ALTER SYSTEM",
    r"\bCOPY\b[^\n]*\bFROM\b\s+(stdin|'[^']*')": "COPY FROM (carga de datos)",
    r"^\s*\\copy\b": "meta-comando \\copy",
    r"\bpg_read_file\b|\bpg_ls_dir\b|\blo_import\b": "funcion de sistema peligrosa",
    r"\bCREATE\s+EXTENSION\b": "CREATE EXTENSION (requiere binarios extra)",
    r"\bSECURITY\s+INVOKER\b.*\bEXECUTE\b": "EXECUTE dinamico",
}

VARIABLE_PASSWORD_ADMIN = "FASHIONSTORE_LAB_5BR_DB_PASSWORD"


class LabError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Rutas y binarios
# ---------------------------------------------------------------------------


def raiz_lab() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Temp" / "opencode" / "fs_lab_5br_pg17"


def ruta_pgdata() -> Path:
    return raiz_lab() / "pgdata"


def ruta_manifiesto() -> Path:
    return raiz_lab() / "lab_5br_manifest.json"


def ruta_secretos() -> Path:
    return raiz_lab() / "lab_5br_secrets.json"


def _candidatos_bin() -> list[Path]:
    candidatos: list[Path] = []
    if os.environ.get("FASHIONSTORE_PG17_BIN"):
        candidatos.append(Path(os.environ["FASHIONSTORE_PG17_BIN"]))
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    candidatos.append(Path(base) / "Temp" / "opencode" / "fs_pg17_5bp"
                      / "pg17" / "pgsql" / "bin")
    candidatos.append(Path(r"C:\Program Files\PostgreSQL\17\bin"))
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
        "No hay binarios PostgreSQL 17 disponibles. Definir FASHIONSTORE_PG17_BIN. "
        "NO se sustituye por 18.")


def version_pg() -> dict:
    salida = subprocess.run([str(_bin("postgres")), "--version"],
                            capture_output=True, text=True, check=True).stdout
    texto = salida.strip()
    if "PostgreSQL) 17" not in texto:
        raise LabError(f"Los binarios localizados no son PostgreSQL 17: {texto}")
    return {"postgres": texto,
            "pg_dump": subprocess.run([str(_bin("pg_dump")), "--version"],
                                      capture_output=True, text=True,
                                      check=True).stdout.strip()}


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------


def _validar_destino() -> None:
    if PUERTO_LAB in PUERTOS_PROHIBIDOS:
        raise LabError("El puerto 5BR colisiona con un laboratorio previo")
    if PUERTO_LAB != 55448 or BASE_LAB != "fashionstore_lab_5br_esquema_real":
        raise LabError("Guardas del laboratorio 5BR alteradas: abortar")
    temporal = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
    if temporal.resolve() not in raiz_lab().resolve().parents:
        raise LabError("La raiz del laboratorio no esta bajo el temporal del usuario")


def _puerto_libre(puerto: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST_LAB, puerto)) != 0


def _ejecutar(args: list[str], *, check: bool = True, capturar: bool = True,
              env: dict | None = None):
    resultado = subprocess.run(args, capture_output=capturar, text=True, env=env)
    if check and resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "")[-800:]
        raise LabError(f"Fallo {' '.join(Path(a).name for a in args)}: {detalle}")
    return resultado


def _dsn(rol: str, password: str, base: str = BASE_LAB) -> str:
    return (f"postgresql://{rol}:{quote(password, safe='')}"
            f"@{HOST_LAB}:{PUERTO_LAB}/{base}")


# ---------------------------------------------------------------------------
# Custodia del insumo (P0)
# ---------------------------------------------------------------------------


def _sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as manejador:
        for bloque in iter(lambda: manejador.read(1 << 20), b""):
            digest.update(bloque)
    return digest.hexdigest()


def verificar_insumo(ruta: Path, hash_esperado: str) -> dict:
    if not ruta.exists():
        raise LabError(f"No existe el insumo autorizado: {ruta}")
    real = _sha256(ruta)
    if real != hash_esperado:
        raise LabError(
            f"SHA-256 divergente para {ruta.name}: esperado {hash_esperado[:12]}..., "
            f"real {real[:12]}... NO se procesa un insumo alterado.")
    return {"archivo": ruta.name, "bytes": ruta.stat().st_size, "sha256": real}


def leer_sql_limpio(ruta: Path) -> tuple[str, dict]:
    """Quita meta-comandos psql (``\\restrict``) y clasifica el contenido.

    El dump se ejecuta con el protocolo simple de psycopg, que no entiende
    meta-comandos de ``psql``. Se eliminan SOLO esas lineas; no se altera ni una
    sentencia SQL.
    """
    texto = ruta.read_text(encoding="utf-8")
    lineas = texto.splitlines()
    meta = [ln.strip() for ln in lineas if ln.lstrip().startswith("\\")]
    limpio = "\n".join(ln for ln in lineas if not ln.lstrip().startswith("\\"))

    hallazgos: list[str] = []
    for patron, etiqueta in PATRONES_PROHIBIDOS.items():
        if etiqueta == "meta-comando \\copy":
            if any(ln.lower().startswith("\\copy") for ln in meta):
                hallazgos.append(etiqueta)
            continue
        if re.search(patron, limpio, flags=re.IGNORECASE | re.MULTILINE):
            hallazgos.append(etiqueta)

    inspeccion = {
        "meta_comandos_eliminados": sorted(set(meta)),
        "sentencias_prohibidas": hallazgos,
        "create_table": len(re.findall(r"^CREATE TABLE ", limpio, re.M)),
        "create_function": len(re.findall(r"^CREATE FUNCTION ", limpio, re.M)),
        "create_procedure": len(re.findall(r"^CREATE PROCEDURE ", limpio, re.M)),
        "create_trigger": len(re.findall(r"^CREATE TRIGGER ", limpio, re.M)),
        "create_view": len(re.findall(r"^CREATE VIEW ", limpio, re.M)),
        "create_schema": len(re.findall(r"^CREATE SCHEMA ", limpio, re.M)),
        "security_definer": len(re.findall(r"\bSECURITY DEFINER\b", limpio)),
        "search_path_fijo": len(re.findall(r"^\s*SET search_path TO ", limpio, re.M)),
        "grant": len(re.findall(r"^GRANT ", limpio, re.M)),
        "revoke": len(re.findall(r"^REVOKE ", limpio, re.M)),
        "alter_default_privileges": len(
            re.findall(r"^ALTER DEFAULT PRIVILEGES ", limpio, re.M)),
        "enable_row_level_security": len(
            re.findall(r"ENABLE ROW LEVEL SECURITY", limpio)),
        "create_policy": len(re.findall(r"^CREATE POLICY ", limpio, re.M)),
        "schemas_referenciados": sorted(set(
            re.findall(r"\b(?:public|auth|storage|vault|extensions|realtime)\."
                       r"[a-zA-Z_][a-zA-Z0-9_]*", limpio)))[:50],
    }
    if inspeccion["sentencias_prohibidas"]:
        raise LabError(
            "El SQL contiene sentencias no admitidas para una restauracion de "
            f"estructura: {inspeccion['sentencias_prohibidas']}")
    if inspeccion["create_table"] != CONTEOS_ORIGEN["tablas"]:
        raise LabError(
            f"El dump declara {inspeccion['create_table']} CREATE TABLE; el catalogo "
            f"autorizado dice {CONTEOS_ORIGEN['tablas']}: no se restaura a ciegas")
    return limpio, inspeccion


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def iniciar_cluster(password_admin: str) -> None:
    pgdata = ruta_pgdata()
    _validar_destino()
    if not _puerto_libre(PUERTO_LAB):
        raise LabError(
            f"El puerto {PUERTO_LAB} ya esta ocupado. No se reutiliza un cluster ajeno.")
    if pgdata.exists() and any(pgdata.iterdir()):
        raise LabError(f"{pgdata} ya contiene datos: usar --recrear")
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
    raise LabError(f"El cluster no acepta conexiones en {HOST_LAB}:{PUERTO_LAB}")


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


def _asegurar_secretos() -> dict:
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
        "password_usuarios": (previos.get("password_usuarios")
                              or secrets.token_urlsafe(18)),
    }
    ruta_secretos().parent.mkdir(parents=True, exist_ok=True)
    ruta_secretos().write_text(json.dumps(secretos, indent=2), encoding="utf-8")
    os.environ[VARIABLE_PASSWORD_ADMIN] = secretos["password_admin"]
    os.environ["FASHIONSTORE_LAB_5BR_RUNTIME_PASSWORD"] = secretos["password_runtime"]
    os.environ["FASHIONSTORE_LAB_5BR_ADMIN_PASSWORD"] = secretos["password_admin_runtime"]
    os.environ["FASHIONSTORE_LAB_5BR_ASSERTION_KEY"] = secretos["assertion_key"]
    return secretos


def _crear_base(password_admin: str) -> None:
    import psycopg

    with psycopg.connect(_dsn("postgres", password_admin, "postgres"),
                         autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                        (BASE_LAB,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{BASE_LAB}"')


def preflight_admin(admin_dsn: str) -> dict:
    import psycopg

    _validar_destino()
    parsed = urlparse(admin_dsn)
    if (parsed.hostname or "") != HOST_LAB or (parsed.port or 0) != PUERTO_LAB:
        raise LabError("El DSN no apunta al laboratorio 5BR")
    with psycopg.connect(admin_dsn) as conn:
        fila = conn.execute(
            "SELECT current_database(), current_user, version(), "
            "current_setting('data_directory'), current_setting('port')"
        ).fetchone()
    base, usuario, version, data_dir, puerto = fila
    if str(Path(data_dir).resolve()).lower() != str(ruta_pgdata().resolve()).lower():
        raise LabError("data_directory no coincide con el pgdata del laboratorio 5BR")
    if str(puerto) != str(PUERTO_LAB):
        raise LabError("El puerto efectivo no es 55448")
    if usuario != "postgres" or base != BASE_LAB:
        raise LabError("Base o usuario administrador inesperados")
    if not version.startswith("PostgreSQL 17"):
        raise LabError(f"El servidor no es PostgreSQL 17: {version}")
    return {"base": base, "usuario": usuario,
            "version": version.split(" on ")[0], "puerto": int(puerto)}


# ---------------------------------------------------------------------------
# Restauracion
# ---------------------------------------------------------------------------


def crear_roles_plataforma(admin_dsn: str) -> dict:
    """Recrea LOCALMENTE los roles que el dump referencia en OWNER/ACL.

    Es una traduccion explicita: en Supabase son roles globales con atributos
    propios. Aqui se crean NOLOGIN/NOSUPERUSER/NOBYPASSRLS para poder materializar
    los GRANT del origen sin conceder acceso real de runtime. ``postgres`` (owner
    de todos los objetos) ya existe en el cluster.
    """
    import psycopg
    from psycopg import sql

    creados: list[str] = []
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for rol in ROLES_PLATAFORMA:
            existe = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (rol,)).fetchone()
            if existe is None:
                conn.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                    "NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(rol)))
                creados.append(rol)
            else:
                conn.execute(sql.SQL(
                    "ALTER ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                    "NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(rol)))
    return {"creados": creados, "gestionados": list(ROLES_PLATAFORMA)}


def restaurar(admin_dsn: str, ruta: Path) -> dict:
    import psycopg

    limpio, inspeccion = leer_sql_limpio(ruta)
    inicio = time.monotonic()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        # El dump incluye su propio ``CREATE SCHEMA public``: se elimina el
        # esquema previo para que la restauracion sea la del origen, no un
        # ``CREATE SCHEMA IF NOT EXISTS`` silencioso sobre un public heredado.
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute(limpio)
    duracion = int((time.monotonic() - inicio) * 1000)
    return {"inspeccion": inspeccion, "duracion_ms": duracion,
            "sentencias_ejecutadas": True}


def verificar_restauracion(admin_dsn: str) -> dict:
    import psycopg

    with psycopg.connect(admin_dsn) as conn:
        conteos = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace
                WHERE c.relkind='r' AND n.nspname='public'),
              (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                 JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname='public'),
              (SELECT count(*) FROM pg_proc p JOIN pg_namespace n
                 ON n.oid=p.pronamespace
                WHERE n.nspname='public'),
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace
                WHERE c.relkind='v' AND n.nspname='public'),
              (SELECT count(*) FROM information_schema.columns
                WHERE column_name='empresa_id' AND table_schema='public'),
              (SELECT count(*) FROM pg_policies WHERE schemaname='public'),
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n
                 ON n.oid=c.relnamespace
                WHERE c.relkind='r' AND n.nspname='public'
                  AND c.relrowsecurity),
              (SELECT count(*) FROM pg_proc p JOIN pg_namespace n
                 ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.prosecdef)
            """
        ).fetchone()
        claves = ("tablas", "triggers", "rutinas", "vistas", "columnas_empresa_id",
                  "politicas", "tablas_con_rls", "rutinas_security_definer")
        conteos = dict(zip(claves, conteos))

        esquemas = conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' "
            "AND nspname <> 'information_schema' ORDER BY 1").fetchall()

        # Fidelidad objeto por objeto contra el catalogo autorizado.
        origen = json.loads(RUTA_CATALOGO_ORIGEN.read_text(encoding="utf-8"))
        tablas_lab = {f[0] for f in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='public'"
        ).fetchall()}
        tablas_origen = {f[1] for f in origen["objetos"]["tablas"]}
        triggers_lab = {f[0] for f in conn.execute(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE NOT t.tgisinternal AND n.nspname='public'").fetchall()}
        triggers_origen = {f[2] for f in origen["objetos"]["triggers"]}
        rutinas_lab = {f[0] for f in conn.execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public'").fetchall()}
        rutinas_origen = {f[1] for f in origen["objetos"]["rutinas"]}
        vistas_lab = {f[0] for f in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE c.relkind='v' AND n.nspname='public'"
        ).fetchall()}
        vistas_origen = {f[1] for f in origen["objetos"]["vistas"]}

    fidelidad = {
        "tablas": {"origen": len(tablas_origen), "lab": len(tablas_lab),
                   "faltan": sorted(tablas_origen - tablas_lab),
                   "sobran": sorted(tablas_lab - tablas_origen)},
        "triggers": {"origen": len(triggers_origen), "lab": len(triggers_lab),
                     "faltan": sorted(triggers_origen - triggers_lab),
                     "sobran": sorted(triggers_lab - triggers_origen)},
        "rutinas": {"origen": len(rutinas_origen), "lab": len(rutinas_lab),
                    "faltan": sorted(rutinas_origen - rutinas_lab),
                    "sobran": sorted(rutinas_lab - rutinas_origen)},
        "vistas": {"origen": len(vistas_origen), "lab": len(vistas_lab),
                   "faltan": sorted(vistas_origen - vistas_lab),
                   "sobran": sorted(vistas_lab - vistas_origen)},
    }

    fallos: list[str] = []
    for clave, esperado in CONTEOS_ORIGEN.items():
        if conteos[clave] != esperado:
            fallos.append(f"{clave}_distintas:{conteos[clave]}!={esperado}")
    for clase, datos in fidelidad.items():
        if datos["faltan"] or datos["sobran"]:
            fallos.append(f"{clase}_no_identicas")
    if conteos["columnas_empresa_id"] != 0:
        fallos.append("el origen ya trae empresa_id")
    if conteos["politicas"] != 0:
        fallos.append("el origen ya trae politicas RLS")
    if conteos["rutinas_security_definer"] != 0:
        fallos.append("el origen trae SECURITY DEFINER")

    return {"conteos": conteos, "esquemas": [f[0] for f in esquemas],
            "fidelidad": fidelidad, "fallos": fallos, "ok": not fallos}


def exportar_catalogo(admin_dsn: str) -> dict:
    import importlib.util

    ruta = ROOT / "database" / "saas" / "87_comparar_catalogo_historico_5bp.py"
    spec = importlib.util.spec_from_file_location("comparador_5bp_5br", ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["comparador_5bp_5br"] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo.exportar_catalogo(admin_dsn)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BR: restauracion del esquema historico REAL en PG17 aislado")
    parser.add_argument("--inspeccionar", action="store_true",
                        help="analiza el dump sin abrir conexion")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--recrear", action="store_true")
    parser.add_argument("--restaurar", action="store_true")
    parser.add_argument("--catalogo", default=None,
                        help="exporta el catalogo JSON del laboratorio")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--origen", default=str(RUTA_ORIGEN_DEFECTO))
    parser.add_argument("--hash", default=HASH_ORIGEN_ESPERADO)
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.inspeccionar:
            ruta = Path(args.origen)
            info = verificar_insumo(ruta, args.hash)
            _, inspeccion = leer_sql_limpio(ruta)
            catalogo_info = verificar_insumo(RUTA_CATALOGO_ORIGEN,
                                             HASH_CATALOGO_ESPERADO)
            salida = {"dump": info, "catalogo": catalogo_info,
                      "inspeccion": inspeccion,
                      "veredicto": "INSUMO VERIFICADO Y ADMISIBLE"}
            print(json.dumps(salida, indent=2, ensure_ascii=False))
            return 0

        if args.stop:
            print(f"Cluster 5BR: {detener_cluster()} (E1/E2/F/G/H no se tocan)")
            return 0

        if args.status:
            datos = {}
            if ruta_manifiesto().exists():
                datos = json.loads(ruta_manifiesto().read_text(encoding="utf-8"))
            salida = {
                "cluster": estado_cluster(),
                "puerto": PUERTO_LAB,
                "base": BASE_LAB,
                "pgdata": str(ruta_pgdata()),
                "binarios": version_pg() if _candidatos_bin() else "NO DISPONIBLE",
                "restauracion": datos.get("restauracion"),
                "manifiesto": str(ruta_manifiesto()) if datos else None,
            }
            print(json.dumps(salida, indent=2, ensure_ascii=False, default=str))
            return 0

        if args.catalogo:
            if not args.confirm_desechable:
                raise LabError("Falta --confirm-desechable")
            secretos = _asegurar_secretos()
            admin_dsn = _dsn("postgres", secretos["password_admin"])
            preflight_admin(admin_dsn)
            catalogo = exportar_catalogo(admin_dsn)
            destino = Path(args.catalogo)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(json.dumps(catalogo, indent=2, ensure_ascii=False),
                               encoding="utf-8")
            print(f"Catalogo escrito: {destino}")
            print(json.dumps(catalogo.get("conteos", {}), indent=2,
                             ensure_ascii=False))
            return 0

        if not (args.init or args.recrear or args.restaurar):
            parser.print_help()
            return 2

        if not args.confirm_desechable:
            raise LabError(
                "Falta --confirm-desechable: el laboratorio crea/recrea/restaura una base.")
        _validar_destino()
        secretos = _asegurar_secretos()

        if args.init:
            iniciar_cluster(secretos["password_admin"])
            _crear_base(secretos["password_admin"])
        elif args.recrear:
            if estado_cluster() != "activo":
                raise LabError("El cluster no esta activo: ejecutar --init primero")
            _ejecutar([str(_bin("dropdb")), "--force", "-h", HOST_LAB,
                       "-p", str(PUERTO_LAB), "-U", "postgres", BASE_LAB],
                      env={**os.environ, "PGPASSWORD": secretos["password_admin"]})
            _crear_base(secretos["password_admin"])

        admin_dsn = _dsn("postgres", secretos["password_admin"])
        info = preflight_admin(admin_dsn)

        if args.restaurar:
            ruta = Path(args.origen)
            insumo = verificar_insumo(ruta, args.hash)
            roles = crear_roles_plataforma(admin_dsn)
            restauracion = restaurar(admin_dsn, ruta)
            verificacion = verificar_restauracion(admin_dsn)
            datos = {
                "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "etapa": "5BR / Etapa I",
                "cluster": {"host": HOST_LAB, "puerto": PUERTO_LAB, "base": BASE_LAB,
                            "pgdata": str(ruta_pgdata()), "version": info["version"],
                            "binarios": version_pg()},
                "insumo": insumo,
                "roles_plataforma": roles,
                "restauracion": {**restauracion, "verificacion": verificacion},
            }
            ruta_manifiesto().parent.mkdir(parents=True, exist_ok=True)
            ruta_manifiesto().write_text(
                json.dumps(datos, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8")
            if args.json:
                print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))
            else:
                print(f"Esquema ORIGINAL restaurado en {HOST_LAB}:{PUERTO_LAB}/{BASE_LAB}")
                print(f"  servidor        : {info['version']}")
                print(f"  dump            : {insumo['bytes']} bytes  "
                      f"sha256={insumo['sha256'][:16]}...")
                print(f"  restaurado en   : {restauracion['duracion_ms']} ms")
                print("  conteos         : " + ", ".join(
                    f"{k}={v}" for k, v in verificacion["conteos"].items()))
                print(f"  esquemas        : {', '.join(verificacion['esquemas'])}")
                for clase, valor in verificacion["fidelidad"].items():
                    print(f"  {clase:<15}: origen={valor['origen']} "
                          f"lab={valor['lab']} faltan={valor['faltan']} "
                          f"sobran={valor['sobran']}")
                print(f"  fallos          : "
                      f"{verificacion['fallos'] if verificacion['fallos'] else 'ninguno'}")
                print(f"  manifiesto      : {ruta_manifiesto()}")
            return 0 if verificacion["ok"] else 1

        # --init / --recrear sin restaurar
        estado = verificar_restauracion(admin_dsn)
        print(json.dumps({"cluster": info, "conteos": estado["conteos"],
                          "ok": estado["ok"]}, indent=2, ensure_ascii=False))
        return 0

    except LabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
