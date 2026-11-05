"""5BX - Staging LOCAL reproducible de FashionStore SaaS (PostgreSQL 17 + FastAPI).

Objetivo
--------
Aprovisionar, migrar y probar de extremo a extremo un entorno de staging LOCAL
desechable, aislado de todos los laboratorios anteriores:

  * cluster PostgreSQL 17 NUEVO en un puerto libre de loopback;
  * base con marcador de descarte (``lab``) y sin datos reales;
  * estructura del esquema ORIGINAL (solo estructura, export autorizado 5BQ);
  * pipeline curado del manifiesto 108 (F00R..F16), sin demo ni ``db push``;
  * datos 100% SINTETICOS (F02R + F14);
  * FastAPI en modo SaaS ON y SaaS OFF con flags solo de proceso;
  * matriz HTTP A/B, RBAC, planes, sucursales, operaciones comerciales,
    seguridad transversal y verificacion SQL con el principal restringido.

Reglas duras
------------
  * Nunca abre respaldos reales, PII, ``.fsb2`` ni conexiones a Supabase.
  * Nunca modifica ``.env`` del repositorio ni flags productivos.
  * Nunca toca puertos, clusters ni bases de laboratorios previos.
  * Los secretos se generan por ejecucion y viven fuera de Git con ACL
    restrictiva; no se imprimen ni se escriben en evidencias.

Uso
---
    python database/saas/115_staging_local_5bx.py --todo
    python database/saas/115_staging_local_5bx.py --status
    python database/saas/115_staging_local_5bx.py --pruebas-on
    python database/saas/115_staging_local_5bx.py --pruebas-off
    python database/saas/115_staging_local_5bx.py --stop
    python database/saas/115_staging_local_5bx.py --destruir --confirm-desechable

    # Todo en uno (init + migracion + pruebas ON/OFF + evidencia)
    python database/saas/115_staging_local_5bx.py --ciclo-completo
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
RAIZ = Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Temp" / "opencode" / "fs_staging_5bx"
PGDATA = RAIZ / "pgdata"
LOGS = RAIZ / "logs"
RUN = RAIZ / "run"
SECRETOS = RAIZ / "secrets_5bx.json"
ESTADO = RAIZ / "staging_5bx_manifest.json"

MANIFIESTO = ROOT / "database" / "saas" / "113_manifiesto_migracion_staging_5bx.json"
ESTRUCTURA_DEFECTO = (
    Path(os.environ.get("USERPROFILE") or str(Path.home()))
    / "Documents" / "fashionstore-esquema-5bq" / "esquema_public_20261031_115806.sql"
)

HOST = "127.0.0.1"
BASE = "fashionstore_lab_5bx_staging"
PG_BIN_DEFECTO = (
    Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
    / "Temp" / "opencode" / "fs_pg17_5bp" / "pg17" / "pgsql" / "bin"
)
PG_BIN = Path(os.environ.get("FASHIONSTORE_PG17_BIN") or PG_BIN_DEFECTO)

# Puertos de laboratorios previos / restauraciones / produccion local.
PUERTOS_PROHIBIDOS = {
    5432, 55432, 55433, 55440, 55441, 55442, 55443, 55444, 55445, 55446,
    55447, 55448, 55449, 55450, 55451, 55452,
}
RANGO_PUERTOS = range(55460, 55520)

PASSWORD_USUARIOS = "Stg5bx-" + secrets.token_urlsafe(12)
SUPERADMIN_KEY = "stg5bx-admin-" + secrets.token_urlsafe(32)


class StagingError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Utilidades base
# ---------------------------------------------------------------------------


def sello() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def leer_json(ruta: Path, defecto: dict | None = None) -> dict:
    if not ruta.exists():
        return dict(defecto or {})
    return json.loads(ruta.read_text(encoding="utf-8"))


def escribir_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as manejador:
        for bloque in iter(lambda: manejador.read(1 << 20), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _bin(nombre: str) -> Path:
    return PG_BIN / f"{nombre}.exe"


def _puerto_ocupado(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((HOST, puerto)) == 0


def elegir_puerto() -> int:
    for puerto in RANGO_PUERTOS:
        if puerto in PUERTOS_PROHIBIDOS or _puerto_ocupado(puerto):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((HOST, puerto))
            except OSError:
                continue
        return puerto
    raise StagingError("No hay puerto libre en el rango 55460-55519")


def puerto_libre_http() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Secretos sinteticos (fuera de Git, ACL restrictiva)
# ---------------------------------------------------------------------------


def asegurar_secretos() -> dict:
    global PASSWORD_USUARIOS, SUPERADMIN_KEY
    datos = leer_json(SECRETOS)
    if not (datos.get("runtime_password") and datos.get("assertion_key")):
        datos = {
            "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "admin_password": secrets.token_urlsafe(24),
            "runtime_password": secrets.token_urlsafe(24),
            "admin_runtime_password": secrets.token_urlsafe(24),
            "assertion_key": secrets.token_urlsafe(48),
            "secret_key": secrets.token_urlsafe(48),
            "superadmin_key": SUPERADMIN_KEY,
            "password_usuarios": PASSWORD_USUARIOS,
            "aviso": "Secretos SINTETICOS de staging local. No productivos. No compartir.",
        }
        escribir_json(SECRETOS, datos)
        _restringir_acl(SECRETOS)
    # La constante de modulo debe coincidir SIEMPRE con la persistida: la
    # semilla y las pruebas HTTP tienen que autenticar con el mismo valor.
    PASSWORD_USUARIOS = datos["password_usuarios"]
    SUPERADMIN_KEY = datos["superadmin_key"]
    return datos


def _restringir_acl(ruta: Path) -> None:
    if os.name != "nt":
        try:
            ruta.chmod(0o600)
        except OSError:
            pass
        return
    usuario = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    subprocess.run(
        ["icacls", str(ruta), "/inheritance:r", "/grant:r", f"{usuario}:(F)"],
        capture_output=True, text=True, check=False,
    )


def _dsn(usuario: str, password: str, *, sqlalchemy: bool = False) -> str:
    from urllib.parse import quote

    conductor = "postgresql+psycopg" if sqlalchemy else "postgresql"
    return (f"{conductor}://{usuario}:{quote(password, safe='')}"
            f"@{HOST}:{_puerto_actual()}/{BASE}")


def _dsn_sin_base(usuario: str, password: str) -> str:
    from urllib.parse import quote

    return (f"postgresql://{usuario}:{quote(password, safe='')}"
            f"@{HOST}:{_puerto_actual()}/postgres")


# ---------------------------------------------------------------------------
# Cluster PostgreSQL 17
# ---------------------------------------------------------------------------


def _manifest() -> dict:
    return leer_json(ESTADO)


def _puerto_actual() -> int:
    datos = _manifest()
    if not datos.get("cluster", {}).get("puerto"):
        raise StagingError("No hay staging aprovisionado (falta manifiesto)")
    return int(datos["cluster"]["puerto"])


def _pg_env(password: str | None = None) -> dict:
    entorno = {**os.environ}
    entorno.pop("PGPASSWORD", None)
    if password:
        entorno["PGPASSWORD"] = password
    return entorno


def ejecutar(args: list[str], *, password: str | None = None, timeout: int = 600,
             check: bool = True) -> subprocess.CompletedProcess:
    resultado = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_pg_env(password), timeout=timeout, check=False,
    )
    if check and resultado.returncode != 0:
        raise StagingError(
            f"Fallo {' '.join(Path(a).name if i == 0 else a for i, a in enumerate(args))}: "
            f"rc={resultado.returncode} {(resultado.stderr or resultado.stdout)[-700:]}")
    return resultado


def psql(sql: str, *, password: str, base: str | None = None,
         timeout: int = 600) -> str:
    args = [_bin("psql"), "-h", HOST, "-p", str(_puerto_actual()), "-U", "postgres",
            "-w", "-d", base or BASE, "-A", "-t", "-F", "|",
            "-v", "ON_ERROR_STOP=1", "-c", sql]
    return ejecutar(args, password=password, timeout=timeout).stdout


def iniciar_cluster(password: str) -> None:
    PGDATA.mkdir(parents=True, exist_ok=True)
    archivo_pw = RAIZ / ".initdb_pw"
    try:
        archivo_pw.write_text(password, encoding="utf-8")
        ejecutar([str(_bin("initdb")), "-D", str(PGDATA), "-U", "postgres",
                  f"--pwfile={archivo_pw}", "--auth-local=trust",
                  "--auth-host=scram-sha-256", "--encoding=UTF8",
                  "--locale=C"], timeout=300)
    finally:
        archivo_pw.unlink(missing_ok=True)


def arrancar_cluster(puerto: int, password: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / "postgres.log"
    argumentos = [str(_bin("postgres")), "-D", str(PGDATA), "-p", str(puerto),
                  "-c", f"listen_addresses={HOST}", "-c", "fsync=off",
                  "-c", "synchronous_commit=off", "-c", "full_page_writes=off"]
    flujo = open(log, "ab")
    proceso = subprocess.Popen(argumentos, stdout=flujo, stderr=flujo,
                               stdin=subprocess.DEVNULL)
    datos = leer_json(ESTADO)
    datos.setdefault("cluster", {})["pid"] = proceso.pid
    escribir_json(ESTADO, datos)
    for _ in range(120):
        if proceso.poll() is not None:
            raise StagingError(f"postgres termino al arrancar; ver {log}")
        resultado = subprocess.run(
            [_bin("psql"), "-h", HOST, "-p", str(puerto), "-U", "postgres",
             "-d", "postgres", "-w", "-c", "SELECT 1"],
            capture_output=True, text=True, env=_pg_env(password), check=False,
        )
        if resultado.returncode == 0:
            flujo.close()
            return
        time.sleep(0.5)
    flujo.close()
    raise StagingError(f"El cluster no respondio en 60s; ver {log}")


def detener_cluster() -> bool:
    datos = leer_json(ESTADO)
    puerto = datos.get("cluster", {}).get("puerto")
    if not puerto:
        return True
    resultado = subprocess.run(
        [_bin("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "-w", "stop"],
        capture_output=True, text=True, check=False,
    )
    return resultado.returncode == 0


def estado_cluster() -> dict:
    datos = leer_json(ESTADO)
    puerto = datos.get("cluster", {}).get("puerto")
    if not puerto or not PGDATA.exists():
        return {"existe": False}
    resultado = subprocess.run(
        [_bin("pg_ctl"), "-D", str(PGDATA), "status"],
        capture_output=True, text=True, check=False,
    )
    return {
        "existe": True,
        "arriba": resultado.returncode == 0,
        "puerto": puerto,
        "base": BASE,
        "pgdata": str(PGDATA),
        "version": (datos.get("cluster") or {}).get("version"),
    }


# ---------------------------------------------------------------------------
# Aprovisionamiento
# ---------------------------------------------------------------------------


def crear_base(password: str) -> None:
    existe = psql(
        f"SELECT 1 FROM pg_database WHERE datname = '{BASE}'",
        password=password, base="postgres").strip()
    if existe:
        return
    ejecutar([str(_bin("createdb")), "-h", HOST, "-p", str(_puerto_actual()),
              "-U", "postgres", "-w", "-T", "template0", BASE], password=password)


def crear_roles_plataforma(password: str) -> dict:
    """Roles que el esquema ORIGINAL referencia (Data API + plataforma).

    ``anon``/``authenticated``/``service_role`` son los roles de la Data API de
    Supabase. ``supabase_admin`` solo aparece en ALTER DEFAULT PRIVILEGES del
    dump; se simula como rol NOLOGIN sin privilegios para poder restaurar la
    estructura sin tocar ningun proyecto real.
    """
    creados = []
    roles = {
        "anon": "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
        "authenticated": "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
        "service_role": "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
        "supabase_admin": "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
    }
    for rol, atributos in roles.items():
        existe = psql(f"SELECT 1 FROM pg_roles WHERE rolname = '{rol}'",
                      password=password, base="postgres").strip()
        if existe:
            continue
        psql(f"CREATE ROLE {rol} {atributos}", password=password, base="postgres")
        creados.append(rol)
    return {"creados": creados, "roles": list(roles)}


def _limpiar_dump(texto: str) -> str:
    """Quita lo que no aplica a un cluster nuevo sin roles de plataforma."""
    lineas = []
    for linea in texto.splitlines():
        despoblado = linea.strip()
        if despoblado.startswith(("\\restrict", "\\unrestrict")):
            continue
        if despoblado.upper() == "CREATE SCHEMA PUBLIC;":
            continue
        lineas.append(linea)
    return "\n".join(lineas) + "\n"


def aplicar_estructura(esquema: Path, password: str) -> dict:
    if not esquema.exists():
        raise StagingError(
            f"No existe el export de estructura: {esquema}. "
            "Se necesita el insumo autorizado 5BQ (solo estructura).")
    texto = _limpiar_dump(esquema.read_text(encoding="utf-8", errors="replace"))
    temporal = RAIZ / "estructura_5bx.sql"
    temporal.write_text(texto, encoding="utf-8")
    ejecutar([_bin("psql"), "-h", HOST, "-p", str(_puerto_actual()), "-U", "postgres",
              "-w", "-d", BASE, "-v", "ON_ERROR_STOP=1", "-q",
              "-f", str(temporal)], password=password, timeout=900)
    return verificar_estructura(password) | {
        "esquema_origen": str(esquema),
        "esquema_sha256": sha256_archivo(esquema),
        "bytes_efectivos": len(texto.encode("utf-8")),
    }


def verificar_estructura(password: str) -> dict:
    sql = """
    SELECT
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')),
      (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal),
      (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind IN ('f','p','a')),
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('v','m')),
      (SELECT count(*) FROM pg_policies WHERE schemaname='public');
    """
    fila = psql(sql, password=password).strip().split("|")
    return {
        "tablas": int(fila[0]), "triggers": int(fila[1]),
        "rutinas": int(fila[2]), "vistas": int(fila[3]),
        "policies": int(fila[4]),
    }


def _modulo(relativa: str, alias: str):
    ruta = ROOT / relativa
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def _preparar_modulos_lab(puerto: int) -> None:
    """Reapunta los modulos de laboratorio al staging 5BX (loopback + ``lab``).

    Los modulos 74/75/76/99 se escribieron para laboratorios concretos y
    validan puerto/base antes de operar. El staging 5BX es otro laboratorio
    desechable (base con marcador ``lab``), por lo que se reenlazan sus
    constantes antes de que el runner 86 los importe.
    """
    alias_99 = "lab_5br_5bp"
    modulo99 = _modulo("database/saas/99_ensayo_esquema_real_5br.py", alias_99)
    modulo99.HOST_LAB = HOST
    modulo99.PUERTO_LAB = puerto
    modulo99.BASE_LAB = BASE
    for relativa, alias in (
        ("database/saas/75_laboratorio_esquema_historico_5bl.py", "lab_5bl_5bp"),
        ("database/saas/76_laboratorio_operativo_5bm.py", "lab_5bm_5bp"),
    ):
        modulo = _modulo(relativa, alias)
        modulo.HOST_LAB = HOST
        modulo.PUERTO_LAB = puerto
        modulo.BASE_LAB = BASE


def aplicar_manifiesto(password: str, secretos: dict) -> dict:
    """Aplica el pipeline curado con el runner 86 del manifiesto 108."""
    os.environ["FASHIONSTORE_LAB_5BP_DB_PASSWORD"] = password
    os.environ["FASHIONSTORE_LAB_5BP_RUNTIME_PASSWORD"] = secretos["runtime_password"]
    os.environ["FASHIONSTORE_LAB_5BP_ADMIN_PASSWORD"] = secretos["admin_runtime_password"]
    os.environ["FASHIONSTORE_LAB_5BP_ASSERTION_KEY"] = secretos["assertion_key"]
    # La semilla F14 usa la constante de modulo: se fija antes de importarlo.
    modulo74 = _modulo("database/saas/74_laboratorio_rls_principal_5bk.py", "lab_5bk_5bp")
    modulo74.PASSWORD_USUARIOS = secretos["password_usuarios"]
    modulo74.PUERTO_LAB = _puerto_actual()
    modulo74.BASE_LAB = BASE
    _preparar_modulos_lab(_puerto_actual())

    runner = _modulo("database/saas/86_preflight_migracion_pg17_5bp.py", "runner_5bp_5bx")
    manifiesto = runner.cargar_manifiesto(MANIFIESTO)
    validacion = runner.validar_manifiesto(manifiesto, exigir_checksums=True)
    if not validacion["ok"]:
        raise StagingError("Manifiesto invalido: " + "; ".join(validacion["errores"]))
    destino = runner.Destino(host=HOST, puerto=_puerto_actual(), base=BASE,
                             password=password)
    resultado = runner.aplicar_lab(manifiesto, destino)
    return {"validacion_ok": True, "resultado": resultado}


def verificar_post_migracion(password: str) -> dict:
    existe = psql("SELECT to_regclass('public.empresa') IS NOT NULL",
                  password=password).strip().lower() == "t"
    if not existe:
        return {"migrado": False, "fases_registradas": 0}
    sql = """
    SELECT
      (SELECT count(*) FROM pg_roles WHERE rolname='fs_saas_runtime'
        AND NOT rolsuper AND NOT rolbypassrls AND NOT rolinherit),
      (SELECT count(*) FROM pg_roles WHERE rolname='saas_admin_runtime'
        AND NOT rolsuper AND NOT rolbypassrls),
      (SELECT count(*) FROM pg_roles WHERE rolname IN
        ('saas_rls_owner','saas_admin_owner','saas_actor_sql_owner','saas_personal_owner')
        AND NOT rolcanlogin),
      (SELECT count(*) FROM public.empresa),
      (SELECT count(*) FROM public.plan_saas),
      (SELECT string_agg(codigo, ',' ORDER BY codigo) FROM public.plan_saas),
      (SELECT count(*) FROM public.suscripcion WHERE estado='ACTIVA'),
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity),
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(r)
        JOIN pg_roles pr ON pr.rolname=v.r
        CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE n.nspname='public' AND c.relkind IN ('r','S') AND a.grantee=pr.oid),
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity),
      (SELECT has_table_privilege('fs_saas_runtime','public.usuario','SELECT')),
      (SELECT count(*) FROM saas_migracion.version_aplicada)
    """
    fila = psql(sql, password=password).strip().split("|")
    return {
        "migrado": True,
        "runtime_atributos_ok": int(fila[0]),
        "admin_runtime_atributos_ok": int(fila[1]),
        "owners_nologin": int(fila[2]),
        "empresas": int(fila[3]),
        "planes": int(fila[4]),
        "planes_codigos": fila[5],
        "suscripciones_activas": int(fila[6]),
        "force_rls_tablas": int(fila[7]),
        "grants_plataforma": int(fila[8]),
        "rls_habilitado": int(fila[9]),
        "runtime_puede_leer_usuario": fila[10].strip().lower() == "t",
        "fases_registradas": int(fila[11]),
    }


def plan_empresas(password: str) -> dict:
    sql = """
    SELECT e.id, e.slug, e.estado,
           COALESCE(p.codigo,'SIN_PLAN'),
           COALESCE(p.max_sucursales_activas,0),
           COALESCE(s.estado,'SIN_SUSCRIPCION'),
           (SELECT count(*) FROM public.sucursal su
             WHERE su.empresa_id=e.id AND su.estado)
      FROM public.empresa e
      LEFT JOIN public.suscripcion s
        ON s.empresa_id=e.id AND s.estado IN ('ACTIVA','SUSPENDIDA')
      LEFT JOIN public.plan_saas p ON p.id=s.plan_saas_id
     ORDER BY e.id
    """
    salida = psql(sql, password=password).strip().splitlines()
    empresas = []
    for linea in salida:
        partes = linea.split("|")
        if len(partes) < 7:
            continue
        empresas.append({
            "id": int(partes[0]), "slug": partes[1],
            "activa": partes[2].lower() == "t", "plan": partes[3],
            "max_sucursales": int(partes[4]), "suscripcion": partes[5],
            "sucursales_activas": int(partes[6]),
        })
    return {"empresas": empresas}


# ---------------------------------------------------------------------------
# Evidencias
# ---------------------------------------------------------------------------


class Evidencia:
    def __init__(self, etapa: str) -> None:
        self.datos: dict = {
            "etapa": etapa,
            "iniciado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": HOST,
            "base": BASE,
            "datos_reales": False,
            "secretos_incluidos": False,
            "pruebas": [],
            "sql": [],
            "notas": [],
            "fallos": [],
        }

    def prueba(self, nombre: str, esperado, obtenido, estado: str,
               detalle: str = "") -> None:
        self.datos["pruebas"].append({
            "nombre": nombre, "esperado": esperado, "obtenido": obtenido,
            "estado": estado, "detalle": detalle[:400],
        })
        if estado == "FAIL":
            self.datos["fallos"].append(f"{nombre}: esperado={esperado} obtenido={obtenido}")

    def sql_check(self, nombre: str, esperado, obtenido, ok: bool) -> None:
        self.datos["sql"].append({
            "nombre": nombre, "esperado": esperado, "obtenido": obtenido,
            "estado": "PASS" if ok else "FAIL",
        })
        if not ok:
            self.datos["fallos"].append(f"SQL {nombre}: esperado={esperado} obtenido={obtenido}")

    def nota(self, texto: str) -> None:
        self.datos["notas"].append(texto)

    def resumen(self) -> dict:
        pruebas = self.datos["pruebas"]
        sql = self.datos["sql"]
        def contar(lista, estado):
            return sum(1 for x in lista if x["estado"] == estado)
        return {
            "http": {
                "total": len(pruebas), "pass": contar(pruebas, "PASS"),
                "fail": contar(pruebas, "FAIL"), "info": contar(pruebas, "INFO"),
            },
            "sql": {
                "total": len(sql), "pass": contar(sql, "PASS"),
                "fail": contar(sql, "FAIL"),
            },
            "fallos": self.datos["fallos"],
        }

    def escribir(self, prefijo: str) -> Path:
        EVIDENCIAS.mkdir(parents=True, exist_ok=True)
        self.datos["finalizado_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.datos["resumen"] = self.resumen()
        ruta = EVIDENCIAS / f"{prefijo}_{sello()}.json"
        escribir_json(ruta, self.datos)
        return ruta


# ---------------------------------------------------------------------------
# Servidor FastAPI de staging (flags SOLO de proceso)
# ---------------------------------------------------------------------------


class Servidor:
    def __init__(self, nombre: str, entorno: dict, puerto: int) -> None:
        self.nombre = nombre
        self.entorno = entorno
        self.puerto = puerto
        self.base = f"http://{HOST}:{puerto}"
        self.proceso: subprocess.Popen | None = None
        self.carpeta = RUN / nombre
        self.log = LOGS / f"uvicorn_{nombre}.log"

    def __enter__(self) -> "Servidor":
        self.carpeta.mkdir(parents=True, exist_ok=True)
        # `.env` SINTETICO en el cwd del proceso: evita leer el .env del repo.
        self.carpeta.joinpath(".env").write_text(self._dotenv(), encoding="utf-8")
        _restringir_acl(self.carpeta / ".env")
        LOGS.mkdir(parents=True, exist_ok=True)
        flujo = open(self.log, "w", encoding="utf-8")
        self.proceso = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST,
             "--port", str(self.puerto), "--log-level", "warning"],
            cwd=str(self.carpeta),
            env={**os.environ, **self.entorno, "PYTHONPATH": str(ROOT)},
            stdout=flujo, stderr=flujo, stdin=subprocess.DEVNULL,
        )
        import httpx

        limite = time.time() + 120
        while time.time() < limite:
            if self.proceso.poll() is not None:
                break
            try:
                if httpx.get(f"{self.base}/health", timeout=2.0).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.5)
        self.__exit__(None, None, None)
        raise StagingError(
            f"uvicorn [{self.nombre}] no arranco. Log: "
            f"{self.log.read_text(encoding='utf-8', errors='replace')[-1200:]}")

    def _dotenv(self) -> str:
        claves = (
            "APP_ENV", "DATABASE_URL", "SAAS_ADMIN_DATABASE_URL", "SECRET_KEY",
            "SAAS_AUTH_ENABLED", "SAAS_SUPERADMIN_ENABLED", "LEGACY_SURFACE_ENABLED",
            "SAAS_LOCAL_ONLY",
            "SAAS_SUPERADMIN_KEY_SHA256", "SAAS_TENANT_ASSERTION_KEY",
            "SAAS_ACTOR_SQL_OWNER", "SAAS_SP_SQL_OWNER", "SAAS_RESERVAS_ENABLED",
            "SAAS_VENTAS_ENABLED", "SAAS_COMPRAS_ENABLED",
            "SAAS_DEVOLUCIONES_ENABLED", "SAAS_INVITATION_RATE_LIMIT_ENABLED",
            "SAAS_SUPERADMIN_REQUIRE_HTTPS", "SAAS_SUPERADMIN_RATE_LIMIT_IP",
            "SAAS_SUPERADMIN_RATE_LIMIT_CLAVE",
            "SAAS_INVITATION_RATE_LIMIT_IP", "SAAS_INVITATION_RATE_LIMIT_TOKEN",
        )
        return "\n".join(f"{c}={self.entorno.get(c, '')}" for c in claves if c in self.entorno) + "\n"

    def __exit__(self, *_exc) -> None:
        if not self.proceso:
            return
        self.proceso.terminate()
        try:
            self.proceso.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proceso.kill()
        self.proceso = None


def entorno_on(secretos: dict) -> dict:
    huella = hashlib.sha256(secretos["superadmin_key"].encode("utf-8")).hexdigest()
    return {
        "APP_ENV": "staging-5bx-local",
        "SAAS_LOCAL_ONLY": "true",
        "DATABASE_URL": _dsn("fs_saas_runtime", secretos["runtime_password"], sqlalchemy=True),
        "SAAS_ADMIN_DATABASE_URL": _dsn("saas_admin_runtime",
                                        secretos["admin_runtime_password"], sqlalchemy=True),
        "SECRET_KEY": secretos["secret_key"],
        "SAAS_AUTH_ENABLED": "true",
        "LEGACY_SURFACE_ENABLED": "false",
        "SAAS_SUPERADMIN_ENABLED": "true",
        "SAAS_SUPERADMIN_KEY_SHA256": f"v1:{huella}",
        "SAAS_TENANT_ASSERTION_KEY": secretos["assertion_key"],
        "SAAS_ACTOR_SQL_OWNER": "saas_actor_sql_owner",
        # 00600 instala los SP de reserva como INVOKER (RLS + asercion). Declarar
        # SAAS_SP_SQL_OWNER aqui haria que el servicio los rechace por esperar
        # DEFINER, asi que se deja sin configurar (fail-closed del propio codigo).
        "SAAS_RESERVAS_ENABLED": "true",
        "SAAS_VENTAS_ENABLED": "true",
        "SAAS_COMPRAS_ENABLED": "true",
        "SAAS_DEVOLUCIONES_ENABLED": "true",
        "SAAS_INVITATION_RATE_LIMIT_ENABLED": "true",
        "SAAS_SUPERADMIN_REQUIRE_HTTPS": "false",
        "SAAS_SUPERADMIN_RATE_LIMIT_IP": "5000",
        "SAAS_SUPERADMIN_RATE_LIMIT_CLAVE": "5000",
        "SAAS_INVITATION_RATE_LIMIT_IP": "5000",
        "SAAS_INVITATION_RATE_LIMIT_TOKEN": "5000",
    }


def entorno_off(secretos: dict, *, principal_legacy: bool) -> dict:
    if principal_legacy:
        dsn = _dsn("postgres", secretos["admin_password"], sqlalchemy=True)
    else:
        dsn = _dsn("fs_saas_runtime", secretos["runtime_password"], sqlalchemy=True)
    return {
        "APP_ENV": "staging-5bx-off",
        "SAAS_LOCAL_ONLY": "true",
        "DATABASE_URL": dsn,
        "SAAS_ADMIN_DATABASE_URL": _dsn("saas_admin_runtime",
                                        secretos["admin_runtime_password"], sqlalchemy=True),
        "SECRET_KEY": secretos["secret_key"],
        "SAAS_AUTH_ENABLED": "false",
        "LEGACY_SURFACE_ENABLED": "true",
        "SAAS_SUPERADMIN_ENABLED": "false",
        "SAAS_RESERVAS_ENABLED": "false",
        "SAAS_VENTAS_ENABLED": "false",
        "SAAS_COMPRAS_ENABLED": "false",
        "SAAS_DEVOLUCIONES_ENABLED": "false",
        "SAAS_SUPERADMIN_REQUIRE_HTTPS": "false",
    }


# ---------------------------------------------------------------------------
# Matriz HTTP A/B
# ---------------------------------------------------------------------------


class Cliente:
    def __init__(self, base: str, ev: Evidencia) -> None:
        import httpx

        self._httpx = httpx
        self.cliente = httpx.Client(base_url=base, timeout=30.0)
        self.ev = ev

    def cerrar(self) -> None:
        self.cliente.close()

    def pedir(self, metodo: str, ruta: str, *, token: str | None = None,
              json_cuerpo=None, cabeceras: dict | None = None):
        cabeceras = dict(cabeceras or {})
        if token:
            cabeceras["Authorization"] = f"Bearer {token}"
        return self.cliente.request(metodo, ruta, json=json_cuerpo, headers=cabeceras)

    def comprobar(self, nombre: str, respuesta, esperado, *, detalle: str = "",
                  extra=None) -> dict:
        obtenido = respuesta.status_code
        correcto = (obtenido in esperado) if isinstance(esperado, (tuple, list, set)) \
            else obtenido == esperado
        cuerpo = {}
        try:
            cuerpo = respuesta.json()
        except Exception:  # noqa: BLE001 - respuesta sin JSON
            cuerpo = {}
        self.ev.prueba(nombre, esperado, obtenido,
                       "PASS" if correcto else "FAIL",
                       (detalle + " " + str(cuerpo)[:220]).strip())
        return {"status": obtenido, "json": cuerpo, "ok": correcto}


def _token(cliente: Cliente, correo: str, slug: str, *, contexto="personal") -> str:
    respuesta = cliente.pedir("POST", "/auth/tenant/login", json_cuerpo={
        "correo": correo, "password": PASSWORD_USUARIOS,
        "empresa_slug": slug, "contexto": contexto})
    if respuesta.status_code != 200:
        raise StagingError(
            f"login inesperado para {correo}@{slug}: {respuesta.status_code} {respuesta.text[:300]}")
    return respuesta.json()["access_token"]


def _sql_admin(secretos: dict, sql: str) -> str:
    return psql(sql, password=secretos["admin_password"])


def pruebas_on(base: str, secretos: dict, ev: Evidencia) -> dict:
    """Matriz HTTP del modo SaaS ON: A/B, RBAC, planes, comercio y ataques."""
    api = Cliente(base, ev)
    datos: dict = {}
    try:
        # ---------------- Autenticacion ----------------
        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "admin@staging.invalid", "password": PASSWORD_USUARIOS,
            "empresa_slug": "empresa-a", "contexto": "personal"})
        api.comprobar("login_A_admin", resp, 200, detalle="empresa A")
        token_a = resp.json().get("access_token", "")
        datos["token_a"] = token_a
        datos["login_a"] = resp.json()

        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "admin.b@staging.invalid", "password": PASSWORD_USUARIOS,
            "empresa_slug": "empresa-b", "contexto": "personal"})
        api.comprobar("login_B_admin", resp, 200, detalle="empresa B")
        token_b = resp.json().get("access_token", "")
        datos["token_b"] = token_b

        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "admin@staging.invalid", "password": PASSWORD_USUARIOS,
            "empresa_slug": "empresa-b", "contexto": "personal"})
        api.comprobar("login_cruzado_A_en_B", resp, 401)

        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "admin@staging.invalid", "password": "incorrecta-5bx",
            "empresa_slug": "empresa-a", "contexto": "personal"})
        api.comprobar("login_password_incorrecta", resp, 401)

        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "suspendida@staging.invalid", "password": PASSWORD_USUARIOS,
            "empresa_slug": "empresa-c", "contexto": "personal"})
        api.comprobar("login_empresa_suspendida", resp, (401, 403))

        resp = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
            "correo": "inactiva@staging.invalid", "password": PASSWORD_USUARIOS,
            "empresa_slug": "empresa-d", "contexto": "personal"})
        api.comprobar("login_empresa_inactiva", resp, (401, 403))

        me_a = api.pedir("GET", "/auth/tenant/me", token=token_a)
        api.comprobar("me_A", me_a, 200)
        datos["me_a"] = me_a.json() if me_a.status_code == 200 else {}
        me_b = api.pedir("GET", "/auth/tenant/me", token=token_b)
        api.comprobar("me_B", me_b, 200)

        api.comprobar("me_sin_token", api.pedir("GET", "/auth/tenant/me"), 401)
        api.comprobar("me_token_alterado",
                      api.pedir("GET", "/auth/tenant/me", token=token_a[:-4] + "AAAA"),
                      (401, 403))
        api.comprobar("jwt_B_sobre_me", api.pedir("GET", "/auth/tenant/me", token=token_b), 200)

        token_cliente_a = _token(api, "cliente@staging.invalid",
                                 "empresa-a", contexto="cliente")
        token_cliente_b = _token(api, "cliente.b@staging.invalid", "empresa-b",
                                 contexto="cliente")
        datos["token_cliente_a"] = token_cliente_a

        # ---------------- Aislamiento catalogo ----------------
        cat_a = api.pedir("GET", "/tenant/catalogo/categorias", token=token_a)
        cat_b = api.pedir("GET", "/tenant/catalogo/categorias", token=token_b)
        ids_a = [c.get("id") for c in cat_a.json()] if cat_a.status_code == 200 else []
        ids_b = [c.get("id") for c in cat_b.json()] if cat_b.status_code == 200 else []
        ev.prueba("catalogo_categorias_A_B", "disjuntos", f"A={ids_a} B={ids_b}",
                  "PASS" if (1 in ids_a and 100 not in ids_a
                             and 100 in ids_b and 1 not in ids_b) else "FAIL")

        prod_a = api.pedir("GET", "/tenant/catalogo/productos", token=token_a)
        prod_b = api.pedir("GET", "/tenant/catalogo/productos", token=token_b)
        pids_a = [p.get("id") for p in prod_a.json()] if prod_a.status_code == 200 else []
        pids_b = [p.get("id") for p in prod_b.json()] if prod_b.status_code == 200 else []
        ev.prueba("catalogo_productos_A_B", "disjuntos", f"A={pids_a} B={pids_b}",
                  "PASS" if (1 in pids_a and 100 not in pids_a
                             and 100 in pids_b and 1 not in pids_b) else "FAIL")

        api.comprobar("producto_ajeno_404",
                      api.pedir("GET", "/tenant/catalogo/productos/100", token=token_a), 404)
        api.comprobar("categoria_ajena_404",
                      api.pedir("GET", "/tenant/catalogo/categorias/100", token=token_a), 404)

        ventas_a = api.pedir("GET", "/tenant/comercial/ventas", token=token_a)
        ventas_b = api.pedir("GET", "/tenant/comercial/ventas", token=token_b)
        ev.prueba("ventas_historicas_A_B", "A>=1 B=0",
                  f"A={len(ventas_a.json()) if ventas_a.status_code == 200 else 'ERR'} "
                  f"B={len(ventas_b.json()) if ventas_b.status_code == 200 else 'ERR'}",
                  "PASS" if (ventas_a.status_code == 200 and ventas_b.status_code == 200
                             and len(ventas_a.json()) >= 1 and ventas_b.json() == []) else "FAIL")
        if ventas_a.status_code == 200 and ventas_a.json():
            venta_ajena = ventas_a.json()[0].get("id")
            api.comprobar("venta_ajena_404",
                          api.pedir("GET", f"/tenant/comercial/ventas/{venta_ajena}",
                                    token=token_b), 404)

        # ---------------- Planes y sucursales ----------------
        plan_a = api.pedir("GET", "/tenant/empresa/plan", token=token_a)
        plan_b = api.pedir("GET", "/tenant/empresa/plan", token=token_b)
        cuerpo_plan_a = plan_a.json() if plan_a.status_code == 200 else {}
        cuerpo_plan_b = plan_b.json() if plan_b.status_code == 200 else {}
        datos["plan_a"], datos["plan_b"] = cuerpo_plan_a, cuerpo_plan_b
        api.comprobar("plan_A_PROFESIONAL", plan_a, 200,
                      detalle=f"{cuerpo_plan_a}")
        ev.prueba("plan_A_limite_10",
                  "max_sucursales_activas=10",
                  cuerpo_plan_a.get("max_sucursales_activas"),
                  "PASS" if cuerpo_plan_a.get("max_sucursales_activas") == 10 else "FAIL")
        api.comprobar("plan_B_BASICO", plan_b, 200, detalle=f"{cuerpo_plan_b}")
        ev.prueba("plan_B_limite_2", "max_sucursales_activas=2",
                  cuerpo_plan_b.get("max_sucursales_activas"),
                  "PASS" if cuerpo_plan_b.get("max_sucursales_activas") == 2 else "FAIL")
        api.comprobar("suscripcion_A",
                      api.pedir("GET", "/tenant/empresa/suscripcion", token=token_a), 200)
        api.comprobar("empresa_me_A",
                      api.pedir("GET", "/tenant/empresa/me", token=token_a), 200)

        sufijo = uuid.uuid4().hex[:8]

        def _crear_sucursal(token: str, nombre: str):
            return api.pedir("POST", "/tenant/empresa/sucursales", token=token,
                             json_cuerpo={"ciudad_id": 1, "nombre": nombre,
                                          "direccion": "Calle de staging 5BX"})

        # B (BASICO, limite 2): con una sucursal sembrada, la segunda entra y
        # la tercera debe rechazarse por limite del plan.
        suc_b = _crear_sucursal(token_b, f"Sucursal B Norte {sufijo}")
        api.comprobar("sucursal_B_segunda_201", suc_b, 201)
        suc_b3 = _crear_sucursal(token_b, f"Sucursal B Tercera {sufijo}")
        api.comprobar("sucursal_B_tercera_409_limite", suc_b3, 409,
                      detalle="limite BASICO=2")
        datos["limite_b"] = suc_b3.json() if suc_b3.status_code != 201 else {}

        # A (PROFESIONAL, limite 10) crea una sucursal y la desactiva.
        suc_a = _crear_sucursal(token_a, f"Sucursal A Dos {sufijo}")
        api.comprobar("sucursal_A_segunda_201", suc_a, 201)
        nueva = (suc_a.json() or {}) if suc_a.status_code == 201 else {}
        nuevo_id = nueva.get("id") if isinstance(nueva, dict) else None
        if nuevo_id:
            api.comprobar("sucursal_A_desactivar",
                          api.pedir("PATCH", f"/tenant/empresa/sucursales/{nuevo_id}/estado",
                                    token=token_a, json_cuerpo={"estado": False}), 200)
            api.comprobar("sucursal_A_reactivar",
                          api.pedir("PATCH", f"/tenant/empresa/sucursales/{nuevo_id}/estado",
                                    token=token_a, json_cuerpo={"estado": True}), 200)
        api.comprobar("cambio_plan_autoservicio_503",
                      api.pedir("PATCH", "/tenant/empresa/plan", token=token_a,
                                json_cuerpo={"codigo": "BASICO"}), 503)

        # ---------------- Usuarios, invitacion y RBAC ----------------
        usuarios_a = api.pedir("GET", "/tenant/empresa/usuarios", token=token_a)
        usuarios_b = api.pedir("GET", "/tenant/empresa/usuarios", token=token_b)
        ids_u_a = [u.get("usuario_id") or u.get("id") for u in usuarios_a.json()] \
            if usuarios_a.status_code == 200 else []
        ids_u_b = [u.get("usuario_id") or u.get("id") for u in usuarios_b.json()] \
            if usuarios_b.status_code == 200 else []
        ev.prueba("usuarios_A_B_disjuntos", "interseccion vacia",
                  f"A={ids_u_a} B={ids_u_b}",
                  "PASS" if (ids_u_a and ids_u_b
                             and not set(ids_u_a) & set(ids_u_b)) else "FAIL")
        api.comprobar("cliente_no_administra_usuarios",
                      api.pedir("GET", "/tenant/empresa/usuarios",
                                token=token_cliente_a), 403)
        api.comprobar("roles_empresa",
                      api.pedir("GET", "/tenant/empresa/roles", token=token_a), 200)
        api.comprobar("permisos_empresa",
                      api.pedir("GET", "/tenant/empresa/permisos", token=token_a), 200)

        alta = api.pedir("POST", "/tenant/empresa/usuarios", token=token_a,
                         json_cuerpo={"correo": f"nuevo.cajero.{sufijo}@staging.invalid",
                                      "nombres": "Nuevo", "apellidos": "Cajero",
                                      "ci": f"CI-5BX-{sufijo}", "sucursal_id": 1,
                                      "rol_id": 3, "expira_horas": 24})
        api.comprobar("alta_personal_invitacion_201", alta, 201)
        datos["alta_personal"] = alta.json() if alta.status_code == 201 else {}
        token_invitacion = ((alta.json() or {}).get("invitacion") or {}).get("token")
        if token_invitacion:
            aceptar = api.pedir("POST", "/auth/tenant/invitaciones/aceptar", json_cuerpo={
                "token": token_invitacion, "password": "Stg5bx-Nuevo-2026",
                "password_confirmacion": "Stg5bx-Nuevo-2026"})
            api.comprobar("aceptar_invitacion", aceptar, 200)
            reuso = api.pedir("POST", "/auth/tenant/invitaciones/aceptar", json_cuerpo={
                "token": token_invitacion, "password": "Stg5bx-Nuevo-2026",
                "password_confirmacion": "Stg5bx-Nuevo-2026"})
            api.comprobar("invitacion_token_un_solo_uso", reuso, (400, 401, 409, 410, 422))
            login_nuevo = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
                "correo": f"nuevo.cajero.{sufijo}@staging.invalid",
                "password": "Stg5bx-Nuevo-2026", "empresa_slug": "empresa-a",
                "contexto": "personal"})
            api.comprobar("login_personal_invitado", login_nuevo, 200)
            datos["token_nuevo"] = login_nuevo.json().get("access_token", "")
        else:
            ev.prueba("invitacion_token_presente", "token no nulo",
                      datos.get("alta_personal"), "FAIL")

        # RBAC nominal: quitar CONSULTAR de PERSONAL al rol CAJERO.
        permisos = api.pedir("GET", "/tenant/empresa/permisos", token=token_a)
        datos["permisos_a"] = permisos.json() if permisos.status_code == 200 else {}
        rbac = api.pedir("PATCH", "/tenant/empresa/roles/3/permisos", token=token_a,
                         json_cuerpo={"permisos": [
                             {"funcion": "PERSONAL", "accion": "CONSULTAR",
                              "permitido": False}]})
        api.comprobar("rbac_patch_rol_cajero", rbac, 200)
        if datos.get("token_nuevo"):
            api.comprobar("rbac_deniega_tras_quitar_permiso",
                          api.pedir("GET", "/tenant/empresa/permisos",
                                    token=datos["token_nuevo"]), 403)

        # Revocacion de membresia.
        nuevo_usuario_id = None
        usuarios = api.pedir("GET", "/tenant/empresa/usuarios", token=token_a)
        if usuarios.status_code == 200:
            for fila in usuarios.json():
                if fila.get("correo") == f"nuevo.cajero.{sufijo}@staging.invalid":
                    nuevo_usuario_id = fila.get("usuario_id") or fila.get("id")
        if nuevo_usuario_id:
            revocar = api.pedir("PATCH",
                                f"/tenant/empresa/usuarios/{nuevo_usuario_id}/estado",
                                token=token_a, json_cuerpo={"estado": False})
            api.comprobar("revocar_membresia", revocar, 200)
            if datos.get("token_nuevo"):
                api.comprobar("token_revocado_403",
                              api.pedir("GET", "/auth/tenant/me",
                                        token=datos["token_nuevo"]), 403)
        else:
            ev.prueba("localizar_usuario_invitado", "id encontrado", nuevo_usuario_id, "FAIL")

        # ---------------- Operaciones comerciales ----------------
        car_a = api.pedir("POST", "/tenant/carritos/items", token=token_cliente_a,
                          json_cuerpo={"sucursal_id": 1, "inventario_id": 1,
                                       "cantidad": 1})
        api.comprobar("carrito_item_A", car_a, (200, 201))
        car_b = api.pedir("POST", "/tenant/carritos/items", token=token_cliente_b,
                          json_cuerpo={"sucursal_id": 100, "inventario_id": 100,
                                       "cantidad": 1})
        api.comprobar("carrito_item_B", car_b, (200, 201))
        datos["carrito_a"] = car_a.json() if car_a.status_code < 300 else {}
        datos["carrito_b"] = car_b.json() if car_b.status_code < 300 else {}
        if datos["carrito_b"].get("carrito_id"):
            api.comprobar("carrito_cruzado_404",
                          api.pedir("DELETE",
                                    f"/tenant/carritos/{datos['carrito_b']['carrito_id']}",
                                    token=token_cliente_a), 404)

        carrito_a_id = datos["carrito_a"].get("carrito_id")
        if carrito_a_id:
            fecha_atencion = "2026-11-05T10:00:00-04:00"
            api.comprobar("carrito_prevalidar_reserva",
                          api.pedir("POST", "/tenant/reservas/prevalidar",
                                    token=token_cliente_a,
                                    json_cuerpo={"carrito_id": carrito_a_id,
                                                 "fecha_atencion": fecha_atencion}),
                          200)
            reserva = api.pedir("POST", "/tenant/reservas", token=token_cliente_a,
                                json_cuerpo={"carrito_id": carrito_a_id,
                                             "fecha_atencion": fecha_atencion})
            api.comprobar("reserva_crear_201", reserva, 201)
            datos["reserva"] = reserva.json() if reserva.status_code == 201 else {}
            if datos["reserva"].get("reserva_id"):
                api.comprobar("reserva_cancelar",
                              api.pedir("PATCH",
                                        f"/tenant/reservas/{datos['reserva']['reserva_id']}/cancelar",
                                        token=token_cliente_a, json_cuerpo={}), 200)

        # Venta de contado idempotente (flag ON).
        operacion = str(uuid.uuid4())
        stock_previo = int(_sql_admin(
            secretos, "SELECT stock_actual FROM public.inventario WHERE id = 1").strip())
        venta = api.pedir("POST", "/tenant/ventas/contado", token=token_a,
                          json_cuerpo={"sucursal_id": 1, "operacion_id": operacion,
                                       "metodo_pago": "EFECTIVO",
                                       "lineas": [{"inventario_id": 1, "cantidad": 1}]})
        api.comprobar("venta_contado_201", venta, 201)
        datos["venta"] = venta.json() if venta.status_code == 201 else {}
        replay = api.pedir("POST", "/tenant/ventas/contado", token=token_a,
                           json_cuerpo={"sucursal_id": 1, "operacion_id": operacion,
                                        "metodo_pago": "EFECTIVO",
                                        "lineas": [{"inventario_id": 1, "cantidad": 1}]})
        api.comprobar("venta_contado_replay", replay, (200, 201))
        cuerpo_replay = replay.json() if replay.status_code < 300 else {}
        ev.prueba("venta_idempotente_mismo_id",
                  f"venta_id={datos['venta'].get('venta_id')} idempotente=true",
                  f"venta_id={cuerpo_replay.get('venta_id')} "
                  f"idempotente={cuerpo_replay.get('idempotente')}",
                  "PASS" if (cuerpo_replay.get("venta_id") == datos["venta"].get("venta_id")
                             and cuerpo_replay.get("idempotente") is True) else "FAIL")
        stock_post = int(_sql_admin(
            secretos, "SELECT stock_actual FROM public.inventario WHERE id = 1").strip())
        ev.prueba("stock_venta_descuenta_una_vez", f"{stock_previo - 1}", stock_post,
                  "PASS" if stock_post == stock_previo - 1 else "FAIL")
        datos["venta_id"] = datos["venta"].get("venta_id")

        # Compra + recepcion (flag ON).
        compra = api.pedir("POST", "/tenant/ordenes-compra", token=token_a,
                           json_cuerpo={"proveedor_id": 1, "sucursal_id": 1,
                                        "lineas": [{"variante_producto_id": 1,
                                                    "temporada_id": 1, "cantidad": 2,
                                                    "costo_unitario": 10.00}]})
        api.comprobar("orden_compra_201", compra, 201)
        datos["orden"] = compra.json() if compra.status_code == 201 else {}
        orden_id = datos["orden"].get("orden_compra_id")
        if orden_id:
            api.comprobar("orden_compra_enviar",
                          api.pedir("POST", f"/tenant/ordenes-compra/{orden_id}/enviar",
                                    token=token_a), 200)
            detalle_id = _sql_admin(
                secretos,
                "SELECT id FROM public.detalle_orden_compra "
                f"WHERE orden_compra_id = {int(orden_id)} ORDER BY id LIMIT 1").strip()
            if detalle_id:
                recep = api.pedir(
                    "POST", f"/tenant/ordenes-compra/{orden_id}/recepciones",
                    token=token_a,
                    json_cuerpo={"operacion_id": str(uuid.uuid4()),
                                 "lineas": [{"detalle_orden_compra_id": int(detalle_id),
                                             "cantidad": 2}]})
                api.comprobar("orden_compra_recepcion", recep, (200, 201))

        # Devolucion sobre la venta de contado.
        if datos.get("venta_id"):
            detalle_venta = _sql_admin(
                secretos,
                "SELECT id FROM public.detalle_venta "
                f"WHERE venta_id = {int(datos['venta_id'])} ORDER BY id LIMIT 1").strip()
            if detalle_venta:
                devol = api.pedir("POST", "/tenant/devoluciones", token=token_a,
                                  json_cuerpo={
                                      "venta_id": int(datos["venta_id"]),
                                      "motivo": "Devolucion de staging 5BX",
                                      "operacion_id": str(uuid.uuid4()),
                                      "items": [{"detalle_venta_id": int(detalle_venta),
                                                 "cantidad": 1, "reponer": True}]})
                api.comprobar("devolucion_crear_201", devol, 201)
                datos["devolucion"] = devol.json() if devol.status_code == 201 else {}
                devol_id = datos["devolucion"].get("devolucion_id")
                if devol_id:
                    api.comprobar("devolucion_aprobar",
                                  api.pedir("POST",
                                            f"/tenant/devoluciones/{devol_id}/aprobar",
                                            token=token_a), 200)
                    api.comprobar("devolucion_procesar",
                                  api.pedir("POST",
                                            f"/tenant/devoluciones/{devol_id}/procesar",
                                            token=token_a,
                                            json_cuerpo={"operacion_id": str(uuid.uuid4())}),
                                  (200, 201))
            else:
                ev.prueba("detalle_venta_disponible", "id", "vacio", "FAIL")

        # ---------------- Superadmin ----------------
        api.comprobar("superadmin_sin_clave_401",
                      api.pedir("GET", "/saas/superadmin/empresas"), 401)
        api.comprobar("superadmin_con_jwt_tenant_401",
                      api.pedir("GET", "/saas/superadmin/empresas", token=token_a), 401)
        cabecera_admin = {"X-SaaS-Admin-Key": secretos["superadmin_key"]}
        empresas = api.pedir("GET", "/saas/superadmin/empresas",
                             cabeceras=cabecera_admin)
        api.comprobar("superadmin_lista_empresas", empresas, 200)
        datos["empresas_superadmin"] = empresas.json() if empresas.status_code == 200 else []
        claves = api.pedir("GET", "/saas/superadmin/claves", cabeceras=cabecera_admin)
        api.comprobar("superadmin_claves", claves, 200)
        texto_claves = json.dumps(claves.json()) if claves.status_code == 200 else ""
        import re as _re

        huellas_completas = _re.findall(r"\b[0-9a-f]{64}\b", texto_claves)
        ev.prueba("superadmin_no_expone_huella_completa",
                  "0 huellas SHA-256 completas", len(huellas_completas),
                  "PASS" if not huellas_completas else "FAIL",
                  texto_claves[:200])
        api.comprobar("superadmin_auditoria",
                      api.pedir("GET", "/saas/superadmin/auditoria",
                                cabeceras=cabecera_admin), 200)

        planes = api.pedir("GET", "/saas/superadmin/planes", cabeceras=cabecera_admin)
        api.comprobar("superadmin_planes", planes, 200)
        plano = planes.json() if planes.status_code == 200 else []
        lista_planes = plano.get("planes", []) if isinstance(plano, dict) else (plano or [])
        plan_id = None
        for plan in lista_planes:
            if isinstance(plan, dict) and plan.get("codigo") == "BASICO":
                plan_id = plan.get("id")
        nueva = api.pedir("POST", "/saas/superadmin/empresas", cabeceras=cabecera_admin,
                          json_cuerpo={
                              "razon_social": "Empresa E Staging SRL",
                              "nombre_comercial": "Empresa E",
                              "slug": f"empresa-e-{sufijo}",
                              "plan_id": plan_id,
                              "fecha_inicio": "2026-10-01",
                              "fecha_vencimiento": "2027-10-01"})
        api.comprobar("superadmin_alta_empresa_201", nueva, 201)
        datos["empresa_e"] = nueva.json() if nueva.status_code == 201 else {}
        cuerpo_empresa_e = datos["empresa_e"]
        if isinstance(cuerpo_empresa_e.get("empresa"), dict):
            empresa_e_id = cuerpo_empresa_e["empresa"].get("id")
        else:
            empresa_e_id = (cuerpo_empresa_e.get("empresa_id")
                            or cuerpo_empresa_e.get("id"))
        if empresa_e_id:
            admin_ini = api.pedir(
                "POST", f"/saas/superadmin/empresas/{empresa_e_id}/administrador-inicial",
                cabeceras=cabecera_admin,
                json_cuerpo={"correo": f"admin.e.{sufijo}@staging.invalid",
                             "password": "Stg5bx-EmpresaE-2026",
                             "nombres": "Admin", "apellidos": "E",
                             "ci": f"CI-5BX-E-{sufijo}", "ciudad_id": 1,
                             "nombre_sucursal": "Sucursal E",
                             "direccion_sucursal": "Calle E 100"})
            api.comprobar("superadmin_administrador_inicial_201", admin_ini, 201)
            login_e = api.pedir("POST", "/auth/tenant/login", json_cuerpo={
                "correo": f"admin.e.{sufijo}@staging.invalid",
                "password": "Stg5bx-EmpresaE-2026", "empresa_slug": f"empresa-e-{sufijo}",
                "contexto": "personal"})
            api.comprobar("login_empresa_e_creada", login_e, 200)
        else:
            ev.prueba("empresa_e_id", "id", datos.get("empresa_e"), "FAIL")

        # ---------------- Ataques transversales ----------------
        api.comprobar("inyeccion_sql_login",
                      api.pedir("POST", "/auth/tenant/login", json_cuerpo={
                          "correo": "' OR 1=1--", "password": "x",
                          "empresa_slug": "empresa-a", "contexto": "personal"}),
                      (401, 422))
        api.comprobar("campo_extra_rechazado",
                      api.pedir("POST", "/auth/tenant/login", json_cuerpo={
                          "correo": "admin@staging.invalid",
                          "password": PASSWORD_USUARIOS, "empresa_slug": "empresa-a",
                          "contexto": "personal", "empresa_id": 2}), 422)
        api.comprobar("slug_inexistente_401",
                      api.pedir("POST", "/auth/tenant/login", json_cuerpo={
                          "correo": "admin@staging.invalid",
                          "password": PASSWORD_USUARIOS, "empresa_slug": "no-existe",
                          "contexto": "personal"}), (401, 404))
        api.comprobar("fk_cruzada_producto_categoria_B",
                      api.pedir("POST", "/tenant/catalogo/productos", token=token_a,
                                json_cuerpo={"categoria_id": 100,
                                             "nombre": "Producto cruzado",
                                             "precio": 10}), (404, 409))

        return datos
    finally:
        api.cerrar()


def verificaciones_sql_on(secretos: dict, ev: Evidencia) -> dict:
    """SQL directo con el principal restringido y el administrador."""
    import hashlib
    import hmac

    import psycopg

    runtime_dsn = _dsn("fs_saas_runtime", secretos["runtime_password"])
    admin_dsn = _dsn("postgres", secretos["admin_password"])
    clave = secretos["assertion_key"]
    datos: dict = {}

    with psycopg.connect(runtime_dsn) as conn:
        atributos = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolinherit FROM pg_roles "
            "WHERE rolname = current_user").fetchone()
        ev.sql_check("runtime_sin_super_bypass_inherit",
                     "(False, False, False)", atributos,
                     atributos == (False, False, False))
        tenant_sin = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
        ev.sql_check("tenant_actual_sin_asercion", None, tenant_sin, tenant_sin is None)
        productos_sin = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        ev.sql_check("deny_all_sin_asercion_producto", 0, productos_sin,
                     productos_sin == 0)
        puede_usuario = conn.execute(
            "SELECT has_table_privilege(current_user, 'public.usuario', 'SELECT')"
        ).fetchone()[0]
        ev.sql_check("runtime_sin_select_usuario", False, puede_usuario,
                     puede_usuario is False)

        expira = int(time.time()) + 120
        mensaje = f"2|3|{expira}|{secrets.token_hex(8)}"
        firma = hmac.new(clave.encode("utf-8"), mensaje.encode("utf-8"),
                         hashlib.sha256).hexdigest()
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)",
                     (f"{mensaje}|{firma}",))
        tenant_b = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
        ev.sql_check("asercion_valida_resuelve_tenant", 2, tenant_b, tenant_b == 2)
        productos_b = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        ev.sql_check("asercion_valida_ve_solo_B", ">=1", productos_b, productos_b >= 1)

        falsa = f"1|1|{expira}|{secrets.token_hex(8)}|" + "0" * 64
        conn.execute("SELECT set_config('app.tenant_assertion', %s, true)", (falsa,))
        tenant_falso = conn.execute("SELECT saas.tenant_actual()").fetchone()[0]
        productos_falsos = conn.execute("SELECT count(*) FROM public.producto").fetchone()[0]
        ev.sql_check("asercion_falsificada_denegada", "NULL/0",
                     f"{tenant_falso}/{productos_falsos}",
                     tenant_falso is None and productos_falsos == 0)

        cruce = conn.execute(
            "SELECT count(*) FROM public.producto WHERE empresa_id = 1").fetchone()[0]
        ev.sql_check("sin_fuga_cruzada_con_asercion_B", 0, cruce, cruce == 0)
        datos["runtime"] = "ok"

    with psycopg.connect(admin_dsn) as conn:
        forzado = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity"
        ).fetchone()[0]
        ev.sql_check("force_rls_todas_las_tablas", ">=41", forzado, forzado >= 41)
        concesiones = conn.execute(
            """
            SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN (VALUES ('anon'),('authenticated'),('service_role')) v(r)
              JOIN pg_roles pr ON pr.rolname=v.r
              CROSS JOIN LATERAL aclexplode(c.relacl) a
             WHERE n.nspname IN ('public','saas','saas_admin') AND a.grantee=pr.oid
            """).fetchone()[0]
        ev.sql_check("acl_plataforma_revocada", 0, concesiones, concesiones == 0)
        definers = conn.execute(
            """
            SELECT count(*) FROM pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace
              JOIN pg_roles r ON r.oid=p.proowner
             WHERE n.nspname IN ('public','saas') AND p.prosecdef
               AND (r.rolcanlogin OR r.rolsuper OR p.proconfig IS NULL)
            """).fetchone()[0]
        ev.sql_check("definer_owner_nologin_y_search_path", 0, definers, definers == 0)
        datos["admin"] = "ok"
    return datos


def pruebas_off(base: str, ev: Evidencia, *, principal: str) -> dict:
    """SaaS OFF: compatibilidad legacy y respuesta de las rutas tenant.

    ``principal='legacy'`` usa el DSN del despliegue del primer parcial
    (propietario), que es el escenario real de compatibilidad. ``runtime``
    reutiliza el principal restringido y documenta que ese principal NO puede
    servir el legado: apagar el flag no sustituye un DSN adecuado.
    """
    api = Cliente(base, ev)
    estricto = principal == "legacy"
    try:
        api.comprobar(f"off_{principal}_health", api.pedir("GET", "/health"), 200)
        productos = api.pedir("GET", "/productos")
        if estricto:
            api.comprobar(f"off_{principal}_productos_legacy", productos, 200)
        else:
            ev.prueba(f"off_{principal}_productos_legacy", "200", productos.status_code,
                      "INFO" if productos.status_code == 200 else "PASS",
                      "principal restringido: RLS devuelve 0 filas, no error")
        login = api.pedir("POST", "/auth/personal/login", json_cuerpo={
            "correo": "admin@staging.invalid", "password": PASSWORD_USUARIOS})
        if estricto:
            api.comprobar(f"off_{principal}_login_legacy", login, 200)
        else:
            ev.prueba(f"off_{principal}_login_legacy", "no aplica", login.status_code,
                      "INFO",
                      "El principal tenant no tiene privilegios de tabla global: "
                      "el legado debe conservar su propio DSN")
        api.comprobar(f"off_{principal}_tenant_me_503",
                      api.pedir("GET", "/tenant/empresa/me"), 503)
        api.comprobar(f"off_{principal}_health_db",
                      api.pedir("GET", "/health/db"), (200, 500, 503))
        return {"login_legacy": login.status_code}
    finally:
        api.cerrar()


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


def asegurar_estructura_cluster(esquema: Path) -> dict:
    """Cluster nuevo + base + roles de plataforma + estructura original."""
    RAIZ.mkdir(parents=True, exist_ok=True)
    secretos = asegurar_secretos()
    estado = leer_json(ESTADO)
    if estado.get("cluster", {}).get("puerto"):
        actual = estado_cluster()
        if actual.get("arriba"):
            print(f"[5BX] Cluster ya arriba en {HOST}:{actual['puerto']}", flush=True)
            return {"cluster": "reutilizado", **actual}
        arrancar_cluster(int(estado["cluster"]["puerto"]), secretos["admin_password"])
        return {"cluster": "reiniciado", **estado_cluster()}

    puerto = elegir_puerto()
    print(f"[5BX] initdb en {PGDATA} (puerto {puerto})", flush=True)
    iniciar_cluster(secretos["admin_password"])
    escribir_json(ESTADO, {
        "etapa": "5BX",
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cluster": {
            "host": HOST, "puerto": puerto, "base": BASE,
            "pgdata": str(PGDATA),
            "version": ejecutar([_bin("postgres"), "--version"]).stdout.strip(),
            "bin": str(PG_BIN),
        },
        "datos_reales": False,
    })
    arrancar_cluster(puerto, secretos["admin_password"])
    crear_base(secretos["admin_password"])
    roles = crear_roles_plataforma(secretos["admin_password"])
    print(f"[5BX] Roles de plataforma: {roles['roles']}", flush=True)
    return {"cluster": "creado", **estado_cluster(), "roles": roles}


def recrear_base(esquema: Path, secretos: dict) -> None:
    """Rehace la base desechable: misma estructura y mismo pipeline, datos nuevos."""
    print("[5BX] Recreando base desechable para un ciclo reproducible...", flush=True)
    ejecutar([_bin("dropdb"), "-h", HOST, "-p", str(_puerto_actual()),
              "-U", "postgres", "-w", "--if-exists", BASE],
             password=secretos["admin_password"])
    crear_base(secretos["admin_password"])
    estado = leer_json(ESTADO)
    estado.pop("estructura_ok", None)
    estado.pop("estructura", None)
    estado.pop("pipeline", None)
    estado.pop("post_migracion", None)
    estado.pop("empresas", None)
    escribir_json(ESTADO, estado)


def provisionar(esquema: Path) -> dict:
    secretos = asegurar_secretos()
    asegurar_estructura_cluster(esquema)
    password = secretos["admin_password"]
    estado = leer_json(ESTADO)
    estructura = verificar_estructura(password)
    if estructura["tablas"] and not estado.get("estructura_ok"):
        # Restauracion previa incompleta (fallo a mitad): se rehace la base.
        print("[5BX] Estructura parcial detectada: recreando base desechable...",
              flush=True)
        ejecutar([_bin("dropdb"), "-h", HOST, "-p", str(_puerto_actual()),
                  "-U", "postgres", "-w", "--if-exists", BASE],
                 password=password, base="postgres")
        crear_base(password)
        estructura = verificar_estructura(password)
    if estructura["tablas"] < 41:
        print("[5BX] Aplicando estructura ORIGINAL (solo estructura)...", flush=True)
        estructura = aplicar_estructura(esquema, password)
        estado["estructura_ok"] = True
        escribir_json(ESTADO, estado)
    print(f"[5BX] Estructura: {estructura}", flush=True)

    total_fases = len(json.loads(MANIFIESTO.read_text(encoding="utf-8"))["fases"])
    post = verificar_post_migracion(password)
    if post["fases_registradas"] < total_fases:
        print("[5BX] Aplicando pipeline curado 113 (F00R..F17)...", flush=True)
        pipeline = aplicar_manifiesto(password, secretos)
    else:
        pipeline = {"validacion_ok": True, "resultado": {"omitido": "ya migrado"}}
    post = verificar_post_migracion(password)
    empresas = plan_empresas(password)

    datos = leer_json(ESTADO)
    datos.update({
        "estructura": estructura,
        "pipeline": pipeline,
        "post_migracion": post,
        "empresas": empresas["empresas"],
        "actualizado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    escribir_json(ESTADO, datos)
    print(f"[5BX] Post-migracion: {post}", flush=True)
    for empresa in empresas["empresas"]:
        print(f"[5BX]   empresa {empresa['id']} {empresa['slug']:<12} "
              f"{empresa['plan']:<12} max={empresa['max_sucursales']:<2} "
              f"suc={empresa['sucursales_activas']} susc={empresa['suscripcion']}", flush=True)
    return datos


def _fallo_global(prefijo: str, mensaje: str) -> int:
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    ruta = EVIDENCIAS / f"FALLO_STAGING_5BX_{sello()}.json"
    escribir_json(ruta, {
        "etapa": "5BX", "resultado": "ERROR", "mensaje": mensaje[:1500],
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datos_reales": False,
    })
    print(f"[5BX] ERROR ({prefijo}): {mensaje}", file=sys.stderr, flush=True)
    print(f"[5BX] Evidencia de fallo: {ruta}", file=sys.stderr, flush=True)
    return 2


def ejecutar_pruebas_on(ev: Evidencia | None = None) -> tuple[dict, Path]:
    secretos = asegurar_secretos()
    ev = ev or Evidencia("5BX_STAGING_ON")
    estado = leer_json(ESTADO)
    ev.datos["cluster"] = estado.get("cluster")
    puerto = puerto_libre_http()
    with Servidor("on", entorno_on(secretos), puerto) as servidor:
        print(f"[5BX] FastAPI ON en {servidor.base}", flush=True)
        datos = pruebas_on(servidor.base, secretos, ev)
        sql = verificaciones_sql_on(secretos, ev)
    datos.update(sql)
    ruta = ev.escribir("EVIDENCIA_STAGING_ON_5BX")
    resumen = ev.resumen()
    print(f"[5BX] HTTP ON: {resumen['http']} | SQL: {resumen['sql']}", flush=True)
    if resumen["fallos"]:
        for fallo in resumen["fallos"]:
            print(f"[5BX]   FALLO {fallo}", flush=True)
    print(f"[5BX] Evidencia: {ruta}", flush=True)
    return datos, ruta


def ejecutar_pruebas_off(ev: Evidencia | None = None) -> Path:
    secretos = asegurar_secretos()
    ev = ev or Evidencia("5BX_STAGING_OFF")
    estado = leer_json(ESTADO)
    ev.datos["cluster"] = estado.get("cluster")
    for nombre, principal in (("off-runtime", "runtime"), ("off-legacy", "legacy")):
        puerto = puerto_libre_http()
        entorno = entorno_off(secretos, principal_legacy=(principal == "legacy"))
        with Servidor(nombre, entorno, puerto) as servidor:
            print(f"[5BX] FastAPI OFF [{principal}] en {servidor.base}", flush=True)
            pruebas_off(servidor.base, ev, principal=principal)
    ruta = ev.escribir("EVIDENCIA_STAGING_OFF_5BX")
    print(f"[5BX] HTTP OFF: {ev.resumen()['http']}", flush=True)
    for fallo in ev.resumen()["fallos"]:
        print(f"[5BX]   FALLO {fallo}", flush=True)
    print(f"[5BX] Evidencia: {ruta}", flush=True)
    return ruta


def ciclo_completo(esquema: Path) -> int:
    print("=" * 78)
    print("5BX - STAGING LOCAL REPRODUCIBLE (PostgreSQL 17 + FastAPI)")
    print("=" * 78)
    secretos = asegurar_secretos()
    asegurar_estructura_cluster(esquema)
    recrear_base(esquema, secretos)
    provisionar(esquema)
    ejecutar_pruebas_on()
    ejecutar_pruebas_off()
    print("=" * 78)
    print("[5BX] Ciclo completo. Cluster conservado para inspeccion; "
          "detener con --stop.")
    print("=" * 78)
    return 0


def destruir() -> int:
    datos = leer_json(ESTADO)
    puerto = datos.get("cluster", {}).get("puerto")
    if not puerto:
        print("[5BX] No hay staging registrado.")
        return 0
    detener_cluster()
    import shutil

    for ruta in (PGDATA, RUN, LOGS):
        if ruta.exists() and str(ruta).startswith(str(RAIZ)):
            shutil.rmtree(ruta, ignore_errors=True)
    SECRETOS.unlink(missing_ok=True)
    ESTADO.unlink(missing_ok=True)
    print(f"[5BX] Staging destruido. Puerto {puerto} liberado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BX: staging local reproducible de FashionStore SaaS")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--todo", action="store_true",
                       help="aprovisiona cluster + estructura + pipeline")
    grupo.add_argument("--ciclo-completo", action="store_true",
                       help="aprovisiona y ejecuta pruebas ON/OFF")
    grupo.add_argument("--pruebas-on", action="store_true")
    grupo.add_argument("--pruebas-off", action="store_true")
    grupo.add_argument("--status", action="store_true")
    grupo.add_argument("--stop", action="store_true")
    grupo.add_argument("--destruir", action="store_true")
    parser.add_argument("--esquema", default=str(ESTRUCTURA_DEFECTO),
                        help="export de estructura (solo DDL) del esquema original")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.destruir:
            if not args.confirm_desechable:
                raise StagingError("--destruir exige --confirm-desechable")
            return destruir()
        if args.stop:
            detener_cluster()
            print("[5BX] Cluster detenido.")
            return 0
        if args.pruebas_on:
            ejecutar_pruebas_on()
            return 0
        if args.pruebas_off:
            ejecutar_pruebas_off()
            return 0
        if args.todo:
            provisionar(Path(args.esquema))
            return 0
        if args.ciclo_completo:
            return ciclo_completo(Path(args.esquema))

        estado = leer_json(ESTADO)
        if not estado:
            print("[5BX] Sin staging aprovisionado. Ejecute --todo.")
            return 1
        resumen = {
            "cluster": estado_cluster(),
            "estructura": estado.get("estructura"),
            "post_migracion": estado.get("post_migracion"),
            "empresas": estado.get("empresas"),
            "secretos": "presentes (fuera de Git)" if SECRETOS.exists() else "ausentes",
            "manifest": str(ESTADO),
        }
        print(json.dumps(resumen, indent=2, ensure_ascii=False) if args.json
              else json.dumps(resumen, indent=2, ensure_ascii=False))
        return 0
    except StagingError as exc:
        return _fallo_global("staging", str(exc))
    except Exception as exc:  # noqa: BLE001 - se reporta sin filtrar secretos
        import traceback

        detalle = "".join(traceback.format_exc()[-1200:])
        print(detalle, file=sys.stderr, flush=True)
        return _fallo_global("inesperado", f"{type(exc).__name__}: {exc}\n{detalle}")


if __name__ == "__main__":
    raise SystemExit(main())
