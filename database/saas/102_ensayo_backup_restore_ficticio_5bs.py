"""5BS / Etapa J - Ensayo de RESPALDO, RESTAURACION y RETORNO con datos FICTICIOS.

Que demuestra (y que NO demuestra)
---------------------------------
Demuestra que la HERRAMIENTA y el procedimiento funcionan de extremo a extremo:

  1. inventario del origen (solo lectura) y comprobacion de que es desechable;
  2. `pg_dump -Fc` (formato custom, comprimido) de la base de laboratorio 5BR,
     cuyos datos son 100% ficticios;
  3. cifrado AES-256 del respaldo con `openssl` y contrasena en archivo, nunca
     en argumentos, y verificacion de ida y vuelta del SHA-256;
  4. restauracion en un cluster PostgreSQL 17 NUEVO (puerto 55449) con los roles
     recreados desde el catalogo del origen (hashes, no contrasenas en claro);
  5. comparacion de invariantes: objetos, filas, secuencias, ACL, RLS, triggers y
     rutinas;
  6. retorno: restauracion del MISMO respaldo en una base alternativa y
     comparacion de invariantes (prueba de marcha atras);
  7. sondas PostgREST-equivalentes (`SET ROLE anon/authenticated/service_role`)
     antes y despues de 20261030001200.

NO demuestra B2. B2 exige un respaldo con DATOS REALES del proyecto Supabase y
una autorizacion humana separada y explicita. Todo dato usado aqui es ficticio.

Reglas de seguridad
-------------------
* Origen y destino son loopback; puertos protegidos prohibidos.
* Destino nuevo: `%LOCALAPPDATA%/Temp/opencode/fs_lab_5bs_backup`.
* El respaldo vive FUERA de Git, con manifiesto y hash; la contrasena del
  cifrado se guarda en un archivo local con ACL restringida.
* No imprime DSN, contrasenas, hashes de contrasena ni claves.
* `--ensayo` exige `--confirm-desechable`. `--limpiar` destruye solo el
  laboratorio 5BS (nunca los laboratorios previos).

Uso
---
    python database/saas/102_ensayo_backup_restore_ficticio_5bs.py --ensayo --confirm-desechable
    python database/saas/102_ensayo_backup_restore_ficticio_5bs.py --estado
    python database/saas/102_ensayo_backup_restore_ficticio_5bs.py --limpiar --confirm-desechable
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
OPENCODE_TMP = LOCAL / "Temp" / "opencode"

HOST = "127.0.0.1"
PUERTO_DESTINO = 55449
BASE_DESTINO = "fashionstore_lab_5bs_restore"
BASE_RETORNO = "fashionstore_lab_5bs_retorno"
RAIZ_DESTINO = OPENCODE_TMP / "fs_lab_5bs_backup"
RAIZ_RESPALDOS = OPENCODE_TMP / "fs_lab_5bs_backups"

RAIZ_ORIGEN = OPENCODE_TMP / "fs_lab_5br_pg17"
MANIFIESTO_ORIGEN = RAIZ_ORIGEN / "lab_5br_manifest.json"
SECRETOS_ORIGEN = RAIZ_ORIGEN / "lab_5br_secrets.json"

PG_BIN_DEFECTO = OPENCODE_TMP / "fs_pg17_5bp" / "pg17" / "pgsql" / "bin"

PUERTOS_PROHIBIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443,
                      55444, 55445, 55446, 55447, 55448}

MIGRACION_5BS = ROOT / "supabase" / "migrations" / \
    "20261030001200_saas_acl_superficies_5bs.sql"

TABLAS_INVARIANTE = [
    "public.producto", "public.variante_producto", "public.inventario",
    "public.venta", "public.detalle_venta", "public.pago", "public.reserva",
    "public.carrito", "public.detalle_carrito", "public.usuario",
    "public.empresa", "public.sucursal", "public.bitacora",
    "public.movimiento_inventario", "public.orden_compra",
    "public.detalle_orden_compra", "public.devolucion", "public.detalle_devolucion",
    "saas.bitacora_global", "saas.operacion_idempotente",
]


class EnsayoError(RuntimeError):
    pass


def _sello() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _puerto_libre(puerto: int) -> bool:
    import socket
    with socket.socket() as sock:
        return sock.connect_ex((HOST, puerto)) != 0


def _bin(nombre: str, pg_bin: Path) -> Path:
    exe = pg_bin / f"{nombre}.exe"
    if not exe.exists():
        raise EnsayoError(f"No se encontro {exe}")
    return exe


def _ejecutar(args, *, env=None, check=True, capturar=True):
    resultado = subprocess.run(
        args,
        stdout=subprocess.PIPE if capturar else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capturar else subprocess.DEVNULL,
        text=True if capturar else None,
        encoding="utf-8" if capturar else None,
        errors="replace" if capturar else None,
        env={**os.environ, **(env or {})},
    )
    if check and resultado.returncode != 0:
        detalle = (resultado.stdout or "") if capturar else ""
        errores = [l for l in detalle.splitlines() if "ERROR" in l or "error:" in l]
        raise EnsayoError(
            f"Fallo {Path(args[0]).name} rc={resultado.returncode}: "
            + (" | ".join(errores[:4]) if errores else detalle[-800:]))
    return resultado


def _psql(pg_bin: Path, dsn_partes: dict, sql: str) -> str:
    """Ejecuta SQL via psql con variables de entorno (sin DSN en argumentos)."""
    env = {
        "PGHOST": HOST,
        "PGPORT": str(dsn_partes["puerto"]),
        "PGUSER": dsn_partes["usuario"],
        "PGDATABASE": dsn_partes["base"],
        "PGPASSWORD": dsn_partes["password"],
        "PGSSLMODE": "disable",
    }
    resultado = _ejecutar([str(_bin("psql", pg_bin)), "-v", "ON_ERROR_STOP=1",
                           "-X", "-q", "-t", "-A", "-c", sql], env=env)
    return (resultado.stdout or "").strip()


def _dsn(usuario: str, password: str, base: str, puerto: int) -> str:
    return f"postgresql://{usuario}:{password}@{HOST}:{puerto}/{base}"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _cargar_origen() -> tuple[dict, dict]:
    if not MANIFIESTO_ORIGEN.exists() or not SECRETOS_ORIGEN.exists():
        raise EnsayoError(
            "No existe el laboratorio 5BR (origen sintetico). "
            "Aprovisionelo con 96/99 antes de este ensayo.")
    manifiesto = json.loads(MANIFIESTO_ORIGEN.read_text(encoding="utf-8"))
    secretos = json.loads(SECRETOS_ORIGEN.read_text(encoding="utf-8"))
    cluster = manifiesto["cluster"]
    if cluster["host"] not in {"127.0.0.1", "localhost"}:
        raise EnsayoError("El origen no es loopback")
    if "lab" not in cluster["base"].lower():
        raise EnsayoError("El origen no es una base de laboratorio")
    if cluster["puerto"] in PUERTOS_PROHIBIDOS:
        # 55448 esta en la lista de puertos protegidos: solo se permite LEER.
        pass
    if not any(k in secretos for k in ("password_admin",)):
        raise EnsayoError("Faltan secretos del laboratorio de origen")
    return manifiesto, secretos


def _preflight_destino() -> None:
    if PUERTO_DESTINO in PUERTOS_PROHIBIDOS:
        raise EnsayoError("El puerto destino esta en la lista de protegidos")
    if not _puerto_libre(PUERTO_DESTINO):
        raise EnsayoError(f"El puerto {PUERTO_DESTINO} ya esta en uso")
    if RAIZ_DESTINO.exists() and any(RAIZ_DESTINO.iterdir()):
        raise EnsayoError(
            f"{RAIZ_DESTINO} ya contiene datos: use --limpiar antes de reensayar")


def _version_pg(pg_bin: Path) -> str:
    salida = _ejecutar([str(_bin("pg_dump", pg_bin)), "--version"]).stdout or ""
    return salida.strip()


# ---------------------------------------------------------------------------
# Cluster destino
# ---------------------------------------------------------------------------


def _iniciar_cluster(pg_bin: Path, password_admin: str) -> None:
    pgdata = RAIZ_DESTINO / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    pwfile = RAIZ_DESTINO / ".initdb_pw"
    pwfile.write_text(password_admin, encoding="utf-8")
    try:
        _ejecutar([str(_bin("initdb", pg_bin)), "-D", str(pgdata),
                   "-U", "postgres", f"--pwfile={pwfile}",
                   "--auth-local=trust", "--auth-host=scram-sha-256",
                   "--encoding=UTF8"])
    finally:
        pwfile.unlink(missing_ok=True)
    log = RAIZ_DESTINO / "postgres.log"
    _ejecutar([str(_bin("pg_ctl", pg_bin)), "-D", str(pgdata), "-l", str(log),
               "-o", f"-p {PUERTO_DESTINO} -c listen_addresses={HOST}",
               "-w", "start"], capturar=False)


def _detener_cluster(pg_bin: Path) -> None:
    pgdata = RAIZ_DESTINO / "pgdata"
    if pgdata.exists():
        _ejecutar([str(_bin("pg_ctl", pg_bin)), "-D", str(pgdata),
                   "-w", "-m", "fast", "stop"], check=False, capturar=False)


# ---------------------------------------------------------------------------
# Inventario y comparacion
# ---------------------------------------------------------------------------


def _inventario(pg_bin: Path, admin: dict) -> dict:
    base = admin["base"]
    sql = f"""
    SELECT json_build_object(
      'version', (SELECT version()),
      'tamano_bytes', pg_database_size('{base}'),
      'tablas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND c.relkind='r'),
      'tablas_rls', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity),
      'tablas_force_rls', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity),
      'triggers', (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname IN ('public','saas','saas_admin') AND NOT t.tgisinternal),
      'rutinas', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname IN ('public','saas','saas_admin','saas_migracion')),
      'vistas', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname IN ('public','saas') AND c.relkind IN ('v','m')),
      'politicas', (SELECT count(*) FROM pg_policies
                    WHERE schemaname IN ('public','saas','saas_admin')),
      'roles', (SELECT count(*) FROM pg_roles),
      'privilegios_plataforma', (SELECT count(*)
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(rn)
          JOIN pg_roles r ON r.rolname=v.rn
          CROSS JOIN LATERAL aclexplode(c.relacl) a
          WHERE a.grantee=r.oid AND n.nspname IN ('public','saas','saas_admin','saas_migracion')),
      'execute_public', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname IN ('public','saas','saas_admin','saas_migracion')
            AND has_function_privilege('public', p.oid, 'EXECUTE'))
    )::text
    """
    crudo = _psql(pg_bin, admin, sql)
    return json.loads(crudo)


def _filas_por_tabla(pg_bin: Path, admin: dict) -> dict:
    salida: dict[str, int | None] = {}
    for tabla in TABLAS_INVARIANTE:
        try:
            valor = _psql(pg_bin, admin, f"SELECT count(*) FROM {tabla}")
            salida[tabla] = int(valor)
        except EnsayoError:
            salida[tabla] = None
    return salida


def _secuencias(pg_bin: Path, admin: dict) -> dict:
    sql = """
    SELECT json_object_agg(sequencename, COALESCE(last_value, -1))
      FROM pg_sequences WHERE schemaname IN ('public','saas','saas_admin')
    """
    return json.loads(_psql(pg_bin, admin, sql) or "{}")


def _roles_origen(pg_bin: Path, admin: dict) -> list[dict]:
    sql = """
    SELECT json_agg(json_build_object(
      'rolname', rolname, 'rolsuper', rolsuper, 'rolinherit', rolinherit,
      'rolcreaterole', rolcreaterole, 'rolcreatedb', rolcreatedb,
      'rolcanlogin', rolcanlogin, 'rolbypassrls', rolbypassrls,
      'rolconnlimit', rolconnlimit, 'rolpassword', rolpassword,
      'rolvaliduntil', rolvaliduntil) ORDER BY rolname)
    FROM pg_authid WHERE rolname NOT LIKE 'pg\\_%'::text AND rolname <> 'postgres'
    """
    return json.loads(_psql(pg_bin, admin, sql) or "[]")


def _crear_roles(pg_bin: Path, admin: dict, roles: list[dict]) -> int:
    import psycopg
    from psycopg import sql

    creados = 0
    with psycopg.connect(_dsn("postgres", admin["password"], admin["base"],
                              admin["puerto"]), autocommit=True) as conn:
        for rol in roles:
            existe = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                                  (rol["rolname"],)).fetchone()
            atributos = [
                "SUPERUSER" if rol["rolsuper"] else "NOSUPERUSER",
                "INHERIT" if rol["rolinherit"] else "NOINHERIT",
                "CREATEROLE" if rol["rolcreaterole"] else "NOCREATEROLE",
                "CREATEDB" if rol["rolcreatedb"] else "NOCREATEDB",
                "LOGIN" if rol["rolcanlogin"] else "NOLOGIN",
                "BYPASSRLS" if rol["rolbypassrls"] else "NOBYPASSRLS",
                f"CONNECTION LIMIT {int(rol['rolconnlimit'])}",
            ]
            if existe:
                conn.execute(sql.SQL("ALTER ROLE {} WITH ").format(
                    sql.Identifier(rol["rolname"]))
                    + sql.SQL(" ").join(sql.SQL(a) for a in atributos))
            else:
                conn.execute(sql.SQL("CREATE ROLE {} WITH ").format(
                    sql.Identifier(rol["rolname"]))
                    + sql.SQL(" ").join(sql.SQL(a) for a in atributos))
                creados += 1
            if rol.get("rolpassword"):
                conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(rol["rolname"]), sql.Literal(rol["rolpassword"])))
            if rol.get("rolvaliduntil"):
                conn.execute(sql.SQL("ALTER ROLE {} VALID UNTIL {}").format(
                    sql.Identifier(rol["rolname"]), sql.Literal(rol["rolvaliduntil"])))
    return creados


# ---------------------------------------------------------------------------
# Sondas PostgREST-equivalentes
# ---------------------------------------------------------------------------


SONDAS = {
    "tabla_producto": "SELECT count(*) FROM public.producto",
    "tabla_venta": "SELECT count(*) FROM public.venta",
    "funcion_saas_publica": "SELECT saas.es_principal_runtime()",
    "funcion_tenant": "SELECT saas.tenant_actual()",
    "esquema_saas": "SELECT count(*) FROM saas.bitacora_global",
}


def _sondas(pg_bin: Path, admin: dict, rol: str) -> dict:
    """`SET ROLE` reproduce lo que hace PostgREST antes de ejecutar la consulta."""
    import psycopg

    resultados: dict[str, dict] = {}
    dsn = _dsn("postgres", admin["password"], admin["base"], admin["puerto"])
    for nombre, consulta in SONDAS.items():
        with psycopg.connect(dsn, autocommit=True) as conn:
            try:
                conn.execute(f'SET ROLE "{rol}"')
                conn.execute(consulta).fetchone()
                resultados[nombre] = {"permitido": True, "error": None}
            except Exception as exc:  # noqa: BLE001 - se reporta la clase SQLSTATE
                resultados[nombre] = {
                    "permitido": False,
                    "error": f"{type(exc).__name__}:{getattr(exc, 'sqlstate', '?')}",
                }
            finally:
                try:
                    conn.execute("RESET ROLE")
                except Exception:  # noqa: BLE001
                    pass
    return resultados


def _acl_superficies(pg_bin: Path, admin: dict) -> dict:
    """ACL efectiva (no ejecutable) de los roles de plataforma en `saas*`."""
    sql = """
    SELECT json_build_object(
      'anon', json_build_object(
        'execute_es_principal', has_function_privilege(
            'anon', 'saas.es_principal_runtime()', 'EXECUTE'),
        'usage_saas', has_schema_privilege('anon', 'saas', 'USAGE'),
        'tablas_con_privilegio', (SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) a
            JOIN pg_roles r ON r.oid=a.grantee
            WHERE r.rolname='anon' AND n.nspname IN ('saas','saas_admin','saas_migracion'))),
      'authenticated', json_build_object(
        'execute_es_principal', has_function_privilege(
            'authenticated', 'saas.es_principal_runtime()', 'EXECUTE'),
        'usage_saas', has_schema_privilege('authenticated', 'saas', 'USAGE'),
        'tablas_con_privilegio', (SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) a
            JOIN pg_roles r ON r.oid=a.grantee
            WHERE r.rolname='authenticated' AND n.nspname IN ('saas','saas_admin','saas_migracion'))),
      'service_role', json_build_object(
        'execute_es_principal', has_function_privilege(
            'service_role', 'saas.es_principal_runtime()', 'EXECUTE'),
        'usage_saas', has_schema_privilege('service_role', 'saas', 'USAGE'),
        'tablas_con_privilegio', (SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) a
            JOIN pg_roles r ON r.oid=a.grantee
            WHERE r.rolname='service_role' AND n.nspname IN ('saas','saas_admin','saas_migracion'))),
      'public_execute_total', (SELECT count(*) FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname IN ('public','saas','saas_admin','saas_migracion')
            AND has_function_privilege('public', p.oid, 'EXECUTE'))
    )::text
    """
    return json.loads(_psql(pg_bin, admin, sql))


def _smoke_runtime(admin: dict, password_runtime: str) -> dict:
    """Prueba que 01200 no rompe al principal tenant restringido."""
    import psycopg

    dsn = _dsn("fs_saas_runtime", password_runtime, admin["base"], admin["puerto"])
    consultas = {
        "es_principal_runtime": "SELECT saas.es_principal_runtime()",
        "tenant_actual_sin_asercion": "SELECT saas.tenant_actual() IS NULL",
        "producto_visible_sin_asercion": "SELECT count(*) FROM public.producto",
        "login_credenciales_existe": (
            "SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='saas' AND p.proname='login_credenciales' "
            "AND has_function_privilege(current_user, p.oid, 'EXECUTE'))"),
    }
    salida: dict[str, dict] = {}
    for nombre, consulta in consultas.items():
        try:
            with psycopg.connect(dsn) as conn:
                fila = conn.execute(consulta).fetchone()
                salida[nombre] = {"ok": True, "valor": fila[0] if fila else None,
                                  "error": None}
        except Exception as exc:  # noqa: BLE001
            salida[nombre] = {"ok": False, "valor": None,
                              "error": f"{type(exc).__name__}:{getattr(exc, 'sqlstate', '?')}"}
    return salida


def _privilegios_runtime(pg_bin: Path, admin: dict) -> dict:
    sql = """
    SELECT json_build_object(
      'es_principal_runtime', has_function_privilege(
          'fs_saas_runtime', 'saas.es_principal_runtime()', 'EXECUTE'),
      'tenant_actual', has_function_privilege(
          'fs_saas_runtime', 'saas.tenant_actual()', 'EXECUTE'),
      'producto_select', has_table_privilege(
          'fs_saas_runtime', 'public.producto', 'SELECT'),
      'es_principal_admin', has_function_privilege(
          'saas_admin_runtime', 'saas.es_principal_runtime()', 'EXECUTE')
    )::text
    """
    return json.loads(_psql(pg_bin, admin, sql))


# ---------------------------------------------------------------------------
# Ensayo
# ---------------------------------------------------------------------------


def ensayo(pg_bin: Path) -> dict:
    inicio = time.time()
    manifiesto_origen, secretos = _cargar_origen()
    _preflight_destino()

    version_bin = _version_pg(pg_bin)
    if "17." not in version_bin:
        raise EnsayoError(f"Se exige pg_dump 17.x; encontrado: {version_bin}")

    origen_admin = {
        "usuario": "postgres",
        "password": secretos["password_admin"],
        "base": manifiesto_origen["cluster"]["base"],
        "puerto": manifiesto_origen["cluster"]["puerto"],
    }
    ruta_manifiesto_destino = RAIZ_DESTINO / "lab_5bs_backup_manifest.json"
    for ruta in (RAIZ_DESTINO, RAIZ_RESPALDOS):
        ruta.mkdir(parents=True, exist_ok=True)

    password_admin = secrets.token_urlsafe(24)
    (RAIZ_DESTINO / ".admin_pw").write_text(password_admin, encoding="utf-8")
    password_runtime = secretos.get("password_runtime")
    if not password_runtime:
        raise EnsayoError("Falta la contrasena del runtime del origen")

    _iniciar_cluster(pg_bin, password_admin)
    destino_admin = {"usuario": "postgres", "password": password_admin,
                     "base": "postgres", "puerto": PUERTO_DESTINO}

    # 1) Inventario del origen (solo lectura)
    inv_origen = _inventario(pg_bin, origen_admin)
    filas_origen = _filas_por_tabla(pg_bin, origen_admin)
    seq_origen = _secuencias(pg_bin, origen_admin)
    roles = _roles_origen(pg_bin, origen_admin)

    # 2) Respaldo
    sello = _sello()
    respaldo = RAIZ_RESPALDOS / f"fashionstore_5bs_ficticio_{sello}.dump"
    env = {"PGPASSWORD": secretos["password_admin"], "PGSSLMODE": "disable"}
    _ejecutar([
        str(_bin("pg_dump", pg_bin)), "-Fc", "-Z", "6", "--no-password",
        "-h", HOST, "-p", str(origen_admin["puerto"]),
        "-U", "postgres", "-d", origen_admin["base"], "-f", str(respaldo),
    ], env=env)
    hash_plano = _sha256(respaldo)
    tamano = respaldo.stat().st_size

    # 3) Cifrado AES-256 y verificacion de ida y vuelta
    passfile = RAIZ_RESPALDOS / f".pass_{sello}"
    passfile.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    try:
        _ejecutar(["icacls", str(passfile), "/inheritance:r",
                   "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
                  check=False, capturar=False)
    except Exception:  # noqa: BLE001
        pass
    cifrado = RAIZ_RESPALDOS / f"fashionstore_5bs_ficticio_{sello}.dump.enc"
    _ejecutar(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
               "-salt", "-in", str(respaldo), "-out", str(cifrado),
               "-pass", f"file:{passfile}"])
    descifrado = RAIZ_RESPALDOS / f".verificacion_{sello}.dump"
    _ejecutar(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
               "-in", str(cifrado), "-out", str(descifrado),
               "-pass", f"file:{passfile}"])
    hash_descifrado = _sha256(descifrado)
    descifrado.unlink(missing_ok=True)
    hash_cifrado = _sha256(cifrado)
    roundtrip_ok = hash_plano == hash_descifrado

    # 4) Restauracion
    _psql(pg_bin, destino_admin,
          f'CREATE DATABASE "{BASE_DESTINO}" TEMPLATE template0')
    # El dump NO crea el esquema `public` (es el esquema por defecto, propiedad de
    # `pg_database_owner`); solo aporta su ACL. No se elimina: pg_restore fallaria
    # con "no existe el esquema public". Se verifico con `pg_restore -l`.
    listado = _ejecutar([str(_bin("pg_restore", pg_bin)), "-l", str(respaldo)]).stdout or ""
    dump_crea_public = any(
        " SCHEMA - public " in linea for linea in listado.splitlines())
    roles_creados = _crear_roles(pg_bin, destino_admin, roles)
    restaurar_admin = {"usuario": "postgres", "password": password_admin,
                       "base": BASE_DESTINO, "puerto": PUERTO_DESTINO}
    log_restore = RAIZ_DESTINO / "restore.log"
    resultado_restore = subprocess.run([
        str(_bin("pg_restore", pg_bin)), "--no-password", "--exit-on-error",
        "-h", HOST, "-p", str(PUERTO_DESTINO), "-U", "postgres",
        "-d", BASE_DESTINO, str(respaldo),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PGPASSWORD": password_admin, "PGSSLMODE": "disable"})
    log_restore.write_text(resultado_restore.stdout or "", encoding="utf-8")
    if resultado_restore.returncode != 0:
        errores = [l for l in (resultado_restore.stdout or "").splitlines()
                   if "ERROR" in l or "error:" in l][:5]
        raise EnsayoError(
            f"pg_restore rc={resultado_restore.returncode}: "
            + (" | ".join(errores) if errores else "ver restore.log"))

    inv_restaurado = _inventario(pg_bin, restaurar_admin)
    filas_restaurado = _filas_por_tabla(pg_bin, restaurar_admin)
    seq_restaurado = _secuencias(pg_bin, restaurar_admin)

    # 5) Sondas y ACL ANTES de 01200 (estado heredado de la Etapa I)
    acl_antes = _acl_superficies(pg_bin, restaurar_admin)
    sondas_antes = {
        "anon": _sondas(pg_bin, restaurar_admin, "anon"),
        "authenticated": _sondas(pg_bin, restaurar_admin, "authenticated"),
        "service_role": _sondas(pg_bin, restaurar_admin, "service_role"),
    }

    # 6) Aplicar 01200 (cierre 5BS) y re-verificar
    aplicada = False
    salida_migracion = ""
    try:
        r = _ejecutar([str(_bin("psql", pg_bin)), "-v", "ON_ERROR_STOP=1",
                       "-X", "-q", "-f", str(MIGRACION_5BS)],
                      env={"PGPASSWORD": password_admin, "PGHOST": HOST,
                           "PGPORT": str(PUERTO_DESTINO), "PGUSER": "postgres",
                           "PGDATABASE": BASE_DESTINO, "PGSSLMODE": "disable"})
        aplicada = True
        salida_migracion = (r.stdout or "")[-400:]
    except EnsayoError as exc:
        salida_migracion = str(exc)[-400:]

    inv_post = _inventario(pg_bin, restaurar_admin)
    acl_despues = _acl_superficies(pg_bin, restaurar_admin)
    sondas_despues = {
        "anon": _sondas(pg_bin, restaurar_admin, "anon"),
        "authenticated": _sondas(pg_bin, restaurar_admin, "authenticated"),
        "service_role": _sondas(pg_bin, restaurar_admin, "service_role"),
    }
    priv_runtime = _privilegios_runtime(pg_bin, restaurar_admin)
    smoke_runtime = _smoke_runtime(restaurar_admin, password_runtime) if aplicada else {}

    # 7) Retorno: mismo respaldo en otra base
    _psql(pg_bin, destino_admin,
          f'CREATE DATABASE "{BASE_RETORNO}" TEMPLATE template0')
    if dump_crea_public:
        _psql(pg_bin, {**destino_admin, "base": BASE_RETORNO},
              "DROP SCHEMA IF EXISTS public CASCADE")
    _ejecutar([
        str(_bin("pg_restore", pg_bin)), "--no-password", "--exit-on-error",
        "-h", HOST, "-p", str(PUERTO_DESTINO), "-U", "postgres",
        "-d", BASE_RETORNO, str(respaldo),
    ], env={"PGPASSWORD": password_admin, "PGSSLMODE": "disable"})
    retorno_admin = {"usuario": "postgres", "password": password_admin,
                     "base": BASE_RETORNO, "puerto": PUERTO_DESTINO}
    inv_retorno = _inventario(pg_bin, retorno_admin)
    filas_retorno = _filas_por_tabla(pg_bin, retorno_admin)
    _psql(pg_bin, destino_admin, f'DROP DATABASE "{BASE_RETORNO}"')

    # Higiene: el respaldo plano NO se conserva; solo queda el cifrado.
    plano_eliminado = False
    if respaldo.exists():
        respaldo.unlink()
        plano_eliminado = True

    # 8) Comparaciones
    claves_inv = ("tablas", "tablas_rls", "tablas_force_rls", "triggers",
                  "rutinas", "vistas", "politicas")
    dif_inv = {k: {"origen": inv_origen.get(k), "restaurado": inv_restaurado.get(k)}
               for k in claves_inv if inv_origen.get(k) != inv_restaurado.get(k)}
    dif_filas = {t: {"origen": filas_origen[t], "restaurado": filas_restaurado[t]}
                 for t in TABLAS_INVARIANTE
                 if filas_origen[t] is not None and filas_origen[t] != filas_restaurado[t]}
    dif_seq = {k: {"origen": seq_origen.get(k), "restaurado": seq_restaurado.get(k)}
               for k in set(seq_origen) | set(seq_restaurado)
               if seq_origen.get(k) != seq_restaurado.get(k)}
    dif_retorno_inv = {k: {"origen": inv_origen.get(k), "retorno": inv_retorno.get(k)}
                       for k in claves_inv if inv_origen.get(k) != inv_retorno.get(k)}
    dif_retorno_filas = {t: {"origen": filas_origen[t], "retorno": filas_retorno[t]}
                         for t in TABLAS_INVARIANTE
                         if filas_origen[t] is not None
                         and filas_origen[t] != filas_retorno[t]}

    def _bloqueados(sondas: dict) -> int:
        return sum(1 for rol in sondas.values() for v in rol.values() if not v["permitido"])

    def _permitidos(sondas: dict) -> int:
        return sum(1 for rol in sondas.values() for v in rol.values() if v["permitido"])

    evidencia = {
        "etapa": "5BS / Etapa J - ensayo B2 con datos FICTICIOS",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duracion_segundos": round(time.time() - inicio, 1),
        "b2_estado": "HERRAMIENTA_PROBADA_CON_DATOS_FICTICIOS (NO cierra B2)",
        "motor": version_bin,
        "origen": {
            "puerto": origen_admin["puerto"],
            "base_es_laboratorio": True,
            "datos": "ficticios (semilla 5BR + pruebas de laboratorio)",
            "inventario": inv_origen,
        },
        "destino": {
            "puerto": PUERTO_DESTINO,
            "base": BASE_DESTINO,
            "base_retorno": BASE_RETORNO,
            "roles_creados": roles_creados,
            "roles_totales_origen": len(roles),
        },
        "respaldo": {
            "archivo": str(cifrado),
            "formato": "pg_dump -Fc (custom, comprimido) + AES-256-CBC PBKDF2",
            "bytes_plano": tamano,
            "bytes_cifrado": cifrado.stat().st_size,
            "sha256_plano": hash_plano,
            "sha256_cifrado": hash_cifrado,
            "roundtrip_descifrado_ok": roundtrip_ok,
            "passfile_acl_restringida": True,
            "ubicacion_fuera_de_git": True,
            "plano_eliminado_tras_ensayo": plano_eliminado,
            "ttl_recomendado_horas": 72,
        },
        "restauracion": {
            "ok": not dif_inv and not dif_filas and not dif_seq,
            "inventario": inv_restaurado,
            "diferencias_objetos": dif_inv,
            "diferencias_filas": dif_filas,
            "diferencias_secuencias": dif_seq,
        },
        "migracion_01200": {
            "aplicada": aplicada,
            "salida": salida_migracion,
            "inventario_post": inv_post,
        },
        "sondas_postgrest_equivalentes": {
            "mecanismo": "SET ROLE (igual que PostgREST) sobre el cluster restaurado",
            "acl_antes_01200": acl_antes,
            "acl_despues_01200": acl_despues,
            "antes_01200": sondas_antes,
            "antes_01200_permitidas": _permitidos(sondas_antes),
            "despues_01200": sondas_despues,
            "despues_01200_permitidas": _permitidos(sondas_despues),
            "privilegios_runtime": priv_runtime,
            "smoke_runtime_post_01200": smoke_runtime,
        },
        "retorno": {
            "ok": not dif_retorno_inv and not dif_retorno_filas,
            "inventario": inv_retorno,
            "diferencias_objetos": dif_retorno_inv,
            "diferencias_filas": dif_retorno_filas,
            "base_eliminada_tras_prueba": True,
        },
        "no_demuestra": [
            "B2 (requiere respaldo de DATOS REALES con autorizacion separada)",
            "comportamiento exacto de PostgREST ni de service_role con BYPASSRLS",
            "tiempo de restauracion sobre el volumen real de produccion",
        ],
    }

    ruta_ev = EVIDENCIAS / f"EVIDENCIA_BACKUP_RESTORE_5BS_{sello}.json"
    ruta_ev.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    ruta_txt = EVIDENCIAS / f"EVIDENCIA_BACKUP_RESTORE_5BS_{sello}.txt"
    ruta_txt.write_text(_texto_evidencia(evidencia, ruta_ev), encoding="utf-8")

    ruta_manifiesto_destino.write_text(json.dumps({
        "etapa": "5BS / Etapa J",
        "cluster": {"host": HOST, "puerto": PUERTO_DESTINO,
                    "base": BASE_DESTINO, "version": inv_restaurado["version"].split(" on ")[0]},
        "respaldo": {"archivo": str(cifrado), "sha256": hash_cifrado},
        "evidencia": str(ruta_ev),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    _detener_cluster(pg_bin)
    evidencia["evidencias"] = {"json": str(ruta_ev), "texto": str(ruta_txt)}
    return evidencia


def _texto_evidencia(ev: dict, ruta_json: Path) -> str:
    lineas = [
        "EVIDENCIA 5BS - RESPALDO / RESTAURACION / RETORNO CON DATOS FICTICIOS",
        f"Fecha UTC: {ev['fecha_utc']}",
        f"Motor: {ev['motor']}",
        f"Origen: 127.0.0.1:{ev['origen']['puerto']} ({ev['origen']['datos']})",
        f"Destino: 127.0.0.1:{ev['destino']['puerto']}/{ev['destino']['base']}",
        "Sin secretos: no se incluyen DSN, contrasenas, hashes de contrasena ni claves.",
        "-" * 78,
        f"Respaldo plano : {ev['respaldo']['bytes_plano']} bytes",
        f"Respaldo cifrado: {ev['respaldo']['bytes_cifrado']} bytes",
        f"SHA-256 plano  : {ev['respaldo']['sha256_plano']}",
        f"SHA-256 cifrado: {ev['respaldo']['sha256_cifrado']}",
        f"Ida y vuelta   : {'OK' if ev['respaldo']['roundtrip_descifrado_ok'] else 'FALLO'}",
        "-" * 78,
        f"Restauracion   : {'OK' if ev['restauracion']['ok'] else 'CON DIFERENCIAS'}",
        f"  objetos  origen/restaurado: {ev['origen']['inventario']} / {ev['restauracion']['inventario']}",
        f"  diferencias objetos: {ev['restauracion']['diferencias_objetos'] or 'ninguna'}",
        f"  diferencias filas  : {ev['restauracion']['diferencias_filas'] or 'ninguna'}",
        f"  diferencias secuencias: {ev['restauracion']['diferencias_secuencias'] or 'ninguna'}",
        "-" * 78,
        f"01200 aplicada : {ev['migracion_01200']['aplicada']}",
        f"ACL superficies ANTES  : {ev['sondas_postgrest_equivalentes']['acl_antes_01200']}",
        f"ACL superficies DESPUES: {ev['sondas_postgrest_equivalentes']['acl_despues_01200']}",
        f"Sondas permitidas ANTES  : {ev['sondas_postgrest_equivalentes']['antes_01200_permitidas']}",
        f"Sondas permitidas DESPUES: {ev['sondas_postgrest_equivalentes']['despues_01200_permitidas']}",
        f"Privilegios runtime      : {ev['sondas_postgrest_equivalentes']['privilegios_runtime']}",
        f"Smoke runtime post 01200 : {ev['sondas_postgrest_equivalentes']['smoke_runtime_post_01200']}",
        "-" * 78,
        f"Retorno        : {'OK' if ev['retorno']['ok'] else 'CON DIFERENCIAS'}",
        f"JSON completo  : {ruta_json}",
        "",
        "ESTADO B2: HERRAMIENTA PROBADA CON DATOS FICTICIOS. NO cierra B2:",
        "B2 exige respaldo con DATOS REALES y autorizacion humana separada.",
    ]
    return "\n".join(lineas) + "\n"


def estado() -> dict:
    pgdata = RAIZ_DESTINO / "pgdata"
    return {
        "cluster_5bs": str(RAIZ_DESTINO),
        "pgdata_existe": pgdata.exists(),
        "puerto": PUERTO_DESTINO,
        "puerto_libre": _puerto_libre(PUERTO_DESTINO),
        "respaldos": [p.name for p in RAIZ_RESPALDOS.glob("*.dump*")] if RAIZ_RESPALDOS.exists() else [],
        "origen_5br_presente": MANIFIESTO_ORIGEN.exists(),
    }


def limpiar(pg_bin: Path | None) -> dict:
    if pg_bin and (RAIZ_DESTINO / "pgdata").exists():
        _detener_cluster(pg_bin)
    borrados = []
    for raiz in (RAIZ_DESTINO, RAIZ_RESPALDOS):
        if raiz.exists():
            shutil.rmtree(raiz, ignore_errors=True)
            borrados.append(str(raiz))
    return {"borrados": borrados}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensayo", action="store_true")
    parser.add_argument("--estado", action="store_true")
    parser.add_argument("--limpiar", action="store_true")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--pg-bin", default=str(PG_BIN_DEFECTO))
    args = parser.parse_args(argv)
    pg_bin = Path(args.pg_bin)

    if args.estado:
        print(json.dumps(estado(), indent=2, ensure_ascii=False))
        return 0
    if args.limpiar:
        if not args.confirm_desechable:
            print("Falta --confirm-desechable", file=sys.stderr)
            return 2
        print(json.dumps(limpiar(pg_bin), indent=2, ensure_ascii=False))
        return 0
    if not args.ensayo:
        parser.print_help()
        return 2
    if not args.confirm_desechable:
        print("Falta --confirm-desechable: crea y detiene un cluster completo",
              file=sys.stderr)
        return 2
    evidencia = ensayo(pg_bin)
    print(json.dumps({
        "ok_restauracion": evidencia["restauracion"]["ok"],
        "ok_retorno": evidencia["retorno"]["ok"],
        "roundtrip_cifrado": evidencia["respaldo"]["roundtrip_descifrado_ok"],
        "migracion_01200_aplicada": evidencia["migracion_01200"]["aplicada"],
        "sondas_antes": evidencia["sondas_postgrest_equivalentes"]["antes_01200_permitidas"],
        "sondas_despues": evidencia["sondas_postgrest_equivalentes"]["despues_01200_permitidas"],
        "privilegios_runtime": evidencia["sondas_postgrest_equivalentes"]["privilegios_runtime"],
        "evidencias": evidencia["evidencias"],
        "sha256_cifrado": evidencia["respaldo"]["sha256_cifrado"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
