"""121 — Bateria de evidencia de la guardia fail-closed de arranque (5BZ).

Ejecuta, con datos 100% ficticios, la matriz adversarial de
`app.core.launch_guard.validar_arranque` y ademas comprueba el efecto real
sobre el arranque de la API:

  * un subproceso que hace `import app.main` con un `DATABASE_URL` remoto
    ficticio y `SAAS_LOCAL_ONLY=1` debe abortar con codigo != 0 y sin abrir
    sockets (es el incidente 5BY reproducido de forma segura);
  * el mismo import con el `.env` sintetico del staging 5BX debe cargar y
    resolver host/puerto/base locales, tambien con 0 sockets: importar no
    debe abrir conexiones.

Uso:
    python database/saas/121_guardia_arranque_5bz.py [--json]

Escribe la evidencia en
`database/saas/evidencias/EVIDENCIA_GUARDIA_ARRANQUE_5BZ_<sello>.json`.
Nunca escribe DSN completos ni contrasenas: solo host/puerto/base saneados y un
hash corto identificador. Sale con codigo 1 si algun caso falla.
"""

from __future__ import annotations

import importlib.util
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.launch_guard import (  # noqa: E402
    ArranqueInseguro,
    dsn_publico,
    validar_arranque,
)

EVIDENCIAS = Path(__file__).resolve().parent / "evidencias"
LANZADOR = Path(__file__).resolve().parent / "119_servidor_saas_local_5by.py"

PASS, FAIL = "PASS", "FAIL"

DSN_SINTETICO = (
    "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
    "127.0.0.1:55460/fashionstore_lab_5bx_staging"
)
DSN_SINTETICO_ADMIN = (
    "postgresql+psycopg://fs_saas_admin:clave_ficticia@"
    "127.0.0.1:55460/fashionstore_lab_5bx_staging"
)
DSN_REMOTO_DOMINIO = (
    "postgresql://usuario_ficticio:clave_ficticia@"
    "db.remoto-ficticio.invalid:5432/fashionstore_lab_5bx_staging"
)
DSN_REMOTO_IP = (
    "postgresql+psycopg://usuario_ficticio:clave_ficticia@"
    "10.255.255.1:5432/fashionstore_lab_5bx_staging"
)
DSN_SQLITE = "sqlite+pysqlite:///./saas_auth_unit_test.db"

CASOS = [
    {
        "nombre": "acepta_dsn_sintetico_estricto",
        "app_env": "staging-5bx-local",
        "database_url": DSN_SINTETICO,
        "admin": DSN_SINTETICO_ADMIN,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "ACEPTA",
    },
    {
        "nombre": "rechaza_dsn_remoto_dominio",
        "app_env": "development",
        "database_url": DSN_REMOTO_DOMINIO,
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_dsn_remoto_ip",
        "app_env": "development",
        "database_url": DSN_REMOTO_IP,
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_admin_remoto",
        "app_env": "development",
        "database_url": DSN_SINTETICO,
        "admin": DSN_REMOTO_DOMINIO,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_puerto_no_autorizado",
        "app_env": "development",
        "database_url": "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "127.0.0.1:5432/fashionstore_lab_5bx_staging",
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_base_fuera_de_prefijo",
        "app_env": "development",
        "database_url": "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "127.0.0.1:55460/base_ajena",
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_parametro_host_en_query",
        "app_env": "development",
        "database_url": DSN_SINTETICO + "?host=10.255.255.1",
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_localhost_literal",
        "app_env": "development",
        "database_url": "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "localhost:55460/fashionstore_lab_5bx_staging",
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_pghost_definido",
        "app_env": "development",
        "database_url": DSN_SINTETICO,
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1", "PGHOST": "10.255.255.1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "rechaza_sqlite_en_estricto",
        "app_env": "development",
        "database_url": DSN_SQLITE,
        "admin": None,
        "entorno": {"SAAS_LOCAL_ONLY": "1"},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "acepta_sqlite_en_local_implicito",
        "app_env": "development",
        "database_url": DSN_SQLITE,
        "admin": None,
        "entorno": {},
        "esperado": "ACEPTA",
    },
    {
        "nombre": "incidente_5by_sin_flag",
        "app_env": "development",
        "database_url": DSN_REMOTO_DOMINIO,
        "admin": None,
        "entorno": {},
        "esperado": "RECHAZO",
        "nota": "caso del incidente: DSN remoto heredado sin SAAS_LOCAL_ONLY",
    },
    {
        "nombre": "acepta_remoto_con_override",
        "app_env": "development",
        "database_url": DSN_REMOTO_IP,
        "admin": None,
        "entorno": {"SAAS_ALLOW_REMOTE_DB": "true"},
        "esperado": "ACEPTA",
    },
    {
        "nombre": "rechaza_desplegado_sin_allowlist",
        "app_env": "production",
        "database_url": DSN_REMOTO_DOMINIO,
        "admin": DSN_REMOTO_DOMINIO,
        "entorno": {},
        "esperado": "RECHAZO",
    },
    {
        "nombre": "acepta_staging_con_identidad_exacta",
        "app_env": "staging",
        "database_url": "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "db.staging-ficticio.invalid:5432/fashionstore_saas_staging",
        "admin": "postgresql+psycopg://saas_admin_runtime:clave_ficticia@"
        "db.staging-ficticio.invalid:5432/fashionstore_saas_staging",
        "entorno": {
            "SAAS_DEPLOY_ENV": "staging",
            "SAAS_DEPLOY_DB_HOST": "db.staging-ficticio.invalid",
            "SAAS_DEPLOY_DB_PORT": "5432",
            "SAAS_DEPLOY_DB_NAME": "fashionstore_saas_staging",
            "SAAS_DEPLOY_DB_USER": "fs_saas_runtime",
            "SAAS_DEPLOY_ADMIN_USER": "saas_admin_runtime",
            "SAAS_DEPLOY_DENY_HOSTS": "db.legacy-ficticio.invalid",
        },
        "esperado": "ACEPTA",
    },
]

# Wrapper del subproceso: instrumenta la red ANTES de importar la API. Si algo
# intenta conectar, el contador sube y la excepcion aparece como motivo.
WRAPPER = r"""
import json
import socket
import sys

contador = {"red": 0}
original = socket.socket


def _bloqueo(*args, **kwargs):
    contador["red"] += 1
    raise AssertionError("conexion de red")


class SocketBloqueado(original):
    def connect(self, *args, **kwargs):
        _bloqueo()

    def connect_ex(self, *args, **kwargs):
        _bloqueo()

    def sendto(self, *args, **kwargs):
        _bloqueo()


socket.socket = SocketBloqueado
socket.create_connection = _bloqueo
socket.getaddrinfo = _bloqueo

salida = {"cargado": False, "sockets": 0, "motivo": None}
try:
    import app.main  # noqa: F401
    from app.core.config import settings
    from urllib.parse import urlsplit

    partes = urlsplit(settings.database_url)
    salida["cargado"] = True
    salida["host"] = partes.hostname
    salida["puerto"] = partes.port
    salida["base"] = (partes.path or "").lstrip("/")
    salida["app_env"] = settings.app_env
except BaseException as exc:  # noqa: BLE001
    salida["motivo"] = f"{type(exc).__name__}: {exc}"
salida["sockets"] = contador["red"]
print(json.dumps(salida, ensure_ascii=False))
sys.exit(0 if salida["cargado"] and contador["red"] == 0 else 1)
"""


class RedInstrumentada:
    """Contexto que sustituye `socket.socket` y bloquea la resolucion DNS."""

    def __init__(self) -> None:
        self.sockets = 0
        self._original = socket.socket

    def __enter__(self) -> "RedInstrumentada":
        registro = self
        original = self._original

        class SocketBloqueado(original):  # type: ignore[misc, valid-type]
            def connect(self2, *args, **kwargs):
                registro.sockets += 1
                raise AssertionError("conexion de red")

            def connect_ex(self2, *args, **kwargs):
                registro.sockets += 1
                raise AssertionError("conexion de red")

            def sendto(self2, *args, **kwargs):
                registro.sockets += 1
                raise AssertionError("conexion de red")

        def bloqueo(*args, **kwargs):
            registro.sockets += 1
            raise AssertionError("conexion de red")

        socket.socket = SocketBloqueado  # type: ignore[assignment]
        socket.create_connection = bloqueo  # type: ignore[assignment]
        socket.getaddrinfo = bloqueo  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        socket.socket = self._original  # type: ignore[assignment]
        socket.create_connection = _CREATE_CONNECTION  # type: ignore[assignment]
        socket.getaddrinfo = _GETADDRINFO  # type: ignore[assignment]


_CREATE_CONNECTION = socket.create_connection
_GETADDRINFO = socket.getaddrinfo


def cargar_lanzador():
    spec = importlib.util.spec_from_file_location("lanzador_119_5by", LANZADOR)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


def hash_corto(dsn: str) -> str:
    return sha256((dsn or "").encode("utf-8")).hexdigest()[:12]


def sanear(dsn: str | None) -> str:
    if not dsn:
        return "(vacia)"
    return dsn_publico(dsn)


def sanear_texto(texto: str, maximo: int = 240) -> str:
    """Quita cualquier DSN embebido de un mensaje antes de persistirlo."""
    limpio = re.sub(r"[A-Za-z0-9+]+://[^\s\"']+", "<dsn_omitido>", texto or "")
    return limpio[:maximo]


def ejecutar_caso(caso: dict) -> dict:
    entrada = {
        "app_env": caso["app_env"],
        "database_url": sanear(caso["database_url"]),
        "database_url_hash": hash_corto(caso["database_url"]),
        "saas_admin_database_url": sanear(caso["admin"]),
        "entorno": {
            clave: valor
            for clave, valor in caso["entorno"].items()
            if clave in {"SAAS_LOCAL_ONLY", "SAAS_ALLOW_REMOTE_DB",
                         "SAAS_LOCAL_ALLOWED_PORTS", "SAAS_LOCAL_DB_PREFIX"}
            or clave.startswith("PG")
        },
    }
    obtenido = "?"
    detalle = ""
    with RedInstrumentada() as red:
        try:
            validar_arranque(
                app_env=caso["app_env"],
                database_url=caso["database_url"],
                saas_admin_database_url=caso["admin"],
                entorno=dict(caso["entorno"]),
            )
            obtenido = "ACEPTA"
        except ArranqueInseguro as exc:
            obtenido = "RECHAZO"
            detalle = sanear_texto(str(exc))
        except AssertionError as exc:
            obtenido = "ERROR_RED"
            detalle = sanear_texto(str(exc))
        except Exception as exc:  # noqa: BLE001
            obtenido = "ERROR"
            detalle = sanear_texto(f"{type(exc).__name__}: {exc}")

    fuga = any(
        secreto in detalle
        for secreto in ("clave_ficticia", "usuario_ficticio", "10.255.255.1")
    )
    ok = obtenido == caso["esperado"] and red.sockets == 0 and not fuga
    return {
        "nombre": caso["nombre"],
        "entrada_saneada": entrada,
        "esperado": caso["esperado"],
        "obtenido": obtenido,
        "sockets": red.sockets,
        "detalle": detalle,
        "filtra_credenciales": fuga,
        "nota": caso.get("nota", ""),
        "estado": PASS if ok else FAIL,
    }


def ejecutar_subproceso(nombre: str, entorno: dict[str, str],
                        esperado_carga: bool) -> dict:
    proceso = subprocess.run(
        [sys.executable, "-c", WRAPPER],
        cwd=str(ROOT),
        env=entorno,
        capture_output=True,
        text=True,
        timeout=180,
    )
    salida = None
    for linea in reversed((proceso.stdout or "").splitlines()):
        linea = linea.strip()
        if linea.startswith("{"):
            try:
                salida = json.loads(linea)
                break
            except json.JSONDecodeError:
                continue
    if salida is None:
        salida = {
            "cargado": False,
            "sockets": -1,
            "motivo": sanear_texto((proceso.stderr or "")[-240:]),
        }

    cargado = bool(salida.get("cargado"))
    sockets = salida.get("sockets")
    codigo_ok = proceso.returncode == 0 if esperado_carga else proceso.returncode != 0
    ok = cargado == esperado_carga and sockets == 0 and codigo_ok

    entrada = {
        "APP_ENV": entorno.get("APP_ENV", ""),
        "DATABASE_URL": sanear(entorno.get("DATABASE_URL")),
        "DATABASE_URL_hash": hash_corto(entorno.get("DATABASE_URL", "")),
        "SAAS_LOCAL_ONLY": entorno.get("SAAS_LOCAL_ONLY", ""),
        "variables_pg": sorted(
            clave for clave in entorno if clave.startswith("PG")
        ),
    }
    return {
        "nombre": nombre,
        "entrada_saneada": entrada,
        "esperado": "CARGA" if esperado_carga else "ABORTA SIN RED",
        "obtenido": {
            "cargado": cargado,
            "sockets": sockets,
            "codigo_salida": proceso.returncode,
            "motivo": sanear_texto(str(salida.get("motivo") or "")),
            "host": salida.get("host"),
            "puerto": salida.get("puerto"),
            "base": salida.get("base"),
        },
        "sockets": sockets,
        "estado": PASS if ok else FAIL,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    como_json = "--json" in args

    casos = [ejecutar_caso(caso) for caso in CASOS]

    lanzador = cargar_lanzador()
    ruta_env = lanzador.ENV_FILE
    subprocesos: list[dict] = []
    entorno_sintetico: dict[str, str] = {}

    if not ruta_env.exists():
        subprocesos.append(
            {
                "nombre": "entorno_sintetico_ausente",
                "entrada_saneada": {"ruta": str(ruta_env)},
                "esperado": "EXISTE",
                "obtenido": "FALTA",
                "sockets": 0,
                "estado": FAIL,
            }
        )
    else:
        sintetico = lanzador.leer_dotenv(ruta_env)
        entorno_sintetico, _informe = lanzador.construir_entorno(sintetico)

        entorno_negativo = dict(entorno_sintetico)
        entorno_negativo["DATABASE_URL"] = DSN_REMOTO_DOMINIO
        entorno_negativo["SAAS_LOCAL_ONLY"] = "1"
        subprocesos.append(
            ejecutar_subproceso(
                "import_app_main_dsn_remoto_estricto",
                entorno_negativo,
                esperado_carga=False,
            )
        )
        subprocesos.append(
            ejecutar_subproceso(
                "import_app_main_dotenv_sintetico",
                dict(entorno_sintetico),
                esperado_carga=True,
            )
        )

    total = len(casos) + len(subprocesos)
    fallos = sum(1 for c in casos if c["estado"] == FAIL) + sum(
        1 for s in subprocesos if s["estado"] == FAIL
    )
    resumen = {
        "total": total,
        "pass": total - fallos,
        "fail": fallos,
        "texto": f"{total - fallos} PASS / {fallos} FAIL",
        "sockets_abiertos": sum(c["sockets"] for c in casos)
        + sum(s["sockets"] for s in subprocesos),
    }

    datos = {
        "etapa": "5BZ",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datos_reales": False,
        "secretos_incluidos": False,
        "dsn_completos_incluidos": False,
        "entorno_sintetico": sanear(entorno_sintetico.get("DATABASE_URL", "")),
        "resumen": resumen,
        "casos": casos,
        "subprocesos": subprocesos,
    }

    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = EVIDENCIAS / f"EVIDENCIA_GUARDIA_ARRANQUE_5BZ_{sello}.json"
    destino.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if como_json:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        for caso in casos + subprocesos:
            obtenido = caso["obtenido"]
            if isinstance(obtenido, dict):
                obtenido = (
                    f"cargado={obtenido['cargado']} "
                    f"codigo={obtenido['codigo_salida']}"
                )
            print(
                f"[{caso['estado']:4}] {caso['nombre']:<38} "
                f"esperado={caso['esperado']:<18} "
                f"obtenido={obtenido} sockets={caso['sockets']}"
            )
            detalle = caso.get("detalle") or (
                caso["obtenido"].get("motivo")
                if isinstance(caso["obtenido"], dict)
                else ""
            )
            if detalle:
                print(f"       -> {detalle}")
        print()
        print(f"RESUMEN: {resumen['texto']} de {total} casos")
        print(f"Evidencia: {destino}")

    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
