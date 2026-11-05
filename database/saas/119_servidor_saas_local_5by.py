"""119 — Lanzador del servidor FastAPI SaaS ON aislado (etapas 5BY / 5BZ).

Motivo: el proceso del agente puede heredar variables de entorno (por ejemplo
`DATABASE_URL` de produccion) que pydantic-settings prioriza sobre el `.env`.
La version 5BY ya inyectaba el `.env` sintetico del staging en el hijo, pero
seguia copiando `os.environ` completo: toda variable no sobrescrita por el
`.env` (PGHOST, SAAS_*, ...) llegaba intacta al servidor.

5BZ: el entorno del hijo se construye por allowlist minima, se elimina toda
variable capaz de redirigir la conexion y el resultado se valida con
`app.core.launch_guard.validar_arranque` ANTES de crear el subproceso. Si la
validacion falla no se lanza uvicorn: el lanzador sale con codigo distinto de
cero y explica el motivo.

Uso:
    python database/saas/119_servidor_saas_local_5by.py --puerto 8010
    python database/saas/119_servidor_saas_local_5by.py --verificar

No imprime secretos: solo esquema/host/puerto/base y APP_ENV.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.launch_guard import (  # noqa: E402
    VARIABLES_PG,
    ArranqueInseguro,
    dsn_publico,
    validar_arranque,
)

RUN = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "opencode" / \
    "fs_staging_5bx" / "run" / "on"
ENV_FILE = RUN / ".env"

# Allowlist minima: solo lo necesario para que Python y el subproceso existan.
# Nada de esto puede redirigir la conexion a la base.
VARIABLES_BASE = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "USERPROFILE",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)

# Variables que JAMAS se heredan del proceso padre: fijan el destino SQL
# (libpq), o reconfiguran el modulo SaaS por fuera del `.env` sintetico.
VARIABLES_PROHIBIDAS = frozenset(
    {"DATABASE_URL", "SAAS_ADMIN_DATABASE_URL"} | set(VARIABLES_PG)
)


def leer_dotenv(ruta: Path) -> dict[str, str]:
    datos: dict[str, str] = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        datos[clave.strip()] = valor.strip()
    return datos


def construir_entorno(
    sintetico: dict[str, str],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Entorno del hijo por allowlist + `.env` sintetico.

    Devuelve `(entorno, informe)`: el diccionario listo para `Popen` y el
    detalle de las variables peligrosas del padre que no se heredaron, dividido
    en `sobrescritas` (el `.env` sintetico las define) y `descartadas` (no
    venian del `.env`). Solo nombres, nunca valores.
    """
    entorno: dict[str, str] = {}
    for clave in VARIABLES_BASE:
        valor = os.environ.get(clave)
        if valor:
            entorno[clave] = valor

    entorno["PYTHONPATH"] = str(ROOT)
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"

    # El `.env` sintetico es la unica fuente legitima de configuracion.
    for clave, valor in sintetico.items():
        entorno[clave] = valor
    entorno["SAAS_LOCAL_ONLY"] = "1"

    # Defensa en profundidad: aunque la allowlist ya lo impide, nada que no
    # venga del `.env` sintetico puede fijar el destino SQL ni el modo SaaS.
    for clave in list(entorno):
        if clave in sintetico or clave == "SAAS_LOCAL_ONLY":
            continue
        if clave in VARIABLES_PROHIBIDAS or clave.startswith("SAAS_"):
            entorno.pop(clave, None)

    neutralizadas = sorted(
        clave
        for clave in os.environ
        if (clave in VARIABLES_PROHIBIDAS or clave.startswith("SAAS_"))
        and clave not in sintetico
        and clave != "SAAS_LOCAL_ONLY"
    )
    informe = {
        "sobrescritas": sorted(
            clave
            for clave in os.environ
            if (clave in VARIABLES_PROHIBIDAS or clave.startswith("SAAS_"))
            and clave in sintetico
        ),
        "descartadas": neutralizadas,
    }
    return entorno, informe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--puerto", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--env", default=str(ENV_FILE))
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="valida el entorno aislado y sale 0 sin lanzar uvicorn",
    )
    args = parser.parse_args(argv)

    ruta_env = Path(args.env)
    if not ruta_env.exists():
        print(f"FALTA el entorno sintetico: {ruta_env}")
        return 2

    sintetico = leer_dotenv(ruta_env)
    if not sintetico.get("DATABASE_URL"):
        print("RECHAZADO: el .env sintetico no define DATABASE_URL")
        return 3

    entorno, informe = construir_entorno(sintetico)

    try:
        validar_arranque(
            app_env=entorno.get("APP_ENV", ""),
            database_url=entorno.get("DATABASE_URL", ""),
            saas_admin_database_url=entorno.get("SAAS_ADMIN_DATABASE_URL"),
            entorno=entorno,
        )
    except ArranqueInseguro as exc:
        print(f"RECHAZADO: {exc}")
        print("  uvicorn NO se lanza (guardia fail-closed 5BZ)")
        return 4

    print("Servidor SaaS ON (aislado)")
    print(f"  escucha      : http://{args.host}:{args.puerto}")
    print(f"  DATABASE_URL : {dsn_publico(entorno.get('DATABASE_URL', ''))}")
    print(
        "  ADMIN_DB     : "
        f"{dsn_publico(entorno.get('SAAS_ADMIN_DATABASE_URL', ''))}"
    )
    print(f"  APP_ENV      : {entorno.get('APP_ENV', '')}")
    print(f"  SAAS_LOCAL_ONLY: {entorno.get('SAAS_LOCAL_ONLY', '')}")
    print(
        "  heredadas sobrescritas por el .env: "
        f"{', '.join(informe['sobrescritas']) or 'ninguna'}"
    )
    print(
        "  heredadas descartadas             : "
        f"{', '.join(informe['descartadas']) or 'ninguna'}"
    )
    print("  entorno construido por allowlist minima (sin os.environ completo)")

    if args.verificar:
        print("  resultado    : entorno valido (no se lanza uvicorn)")
        return 0

    sys.stdout.flush()
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", args.host, "--port", str(args.puerto),
         "--log-level", "warning"],
        cwd=str(RUN), env=entorno,
    )
    try:
        return proceso.wait()
    except KeyboardInterrupt:
        proceso.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
