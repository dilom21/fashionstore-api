"""125 - 5C0: preflight ESTATICO de configuracion de staging cloud.

Valida un archivo de entorno sintetico ANTES de crear recursos o desplegar.
No abre sockets, no resuelve DNS, no imprime secretos (solo la identidad
publica host:puerto/base y el resultado).

Reglas (delegadas en `app.core.launch_guard`):
  * APP_ENV debe coincidir con SAAS_DEPLOY_ENV.
  * host/puerto/base/usuario deben coincidir EXACTAMENTE con SAAS_DEPLOY_*.
  * se rechaza loopback, host denegado (legacy/produccion) y cualquier DSN con
    parametros de redireccion.
  * `SAAS_ALLOW_REMOTE_DB` no desbloquea un despliegue.

Uso:
    python database/saas/125_preflight_estatico_staging_5c0.py \
        --env database/saas/125_env_staging_5c0.example

Codigo de salida 0 si acepta, 1 si rechaza. No escribe archivos.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.core.launch_guard import (  # noqa: E402
    ArranqueInseguro,
    analizar_dsn,
    dsn_publico,
    modo_arranque,
    validar_arranque,
)

CLAVES = (
    "APP_ENV",
    "DATABASE_URL",
    "SAAS_ADMIN_DATABASE_URL",
    "SAAS_DEPLOY_ENV",
    "SAAS_DEPLOY_DB_HOST",
    "SAAS_DEPLOY_DB_PORT",
    "SAAS_DEPLOY_DB_NAME",
    "SAAS_DEPLOY_DB_USER",
    "SAAS_DEPLOY_ADMIN_USER",
    "SAAS_DEPLOY_DENY_HOSTS",
    "SAAS_ALLOW_REMOTE_DB",
    "SAAS_LOCAL_ONLY",
)


def leer_env(ruta: Path) -> dict[str, str]:
    if not ruta.exists():
        raise SystemExit(f"No existe el archivo: {ruta}")
    datos: dict[str, str] = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, valor = limpia.split("=", 1)
        clave = clave.strip()
        if clave:
            datos[clave] = valor.strip().strip('"').strip("'")
    return datos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env",
        default=str(Path(__file__).with_name("125_env_staging_5c0.example")),
    )
    args = parser.parse_args(argv)

    entorno = leer_env(Path(args.env))
    # Aisla el preflight del entorno real del proceso: solo el archivo manda.
    for clave in CLAVES:
        if clave in entorno:
            continue
        entorno.setdefault(clave, "")

    app_env = entorno.get("APP_ENV", "")
    print(f"Archivo : {args.env}")
    print(f"Modo    : {modo_arranque(app_env, entorno)} ({app_env or 'sin APP_ENV'})")
    for clave in (
        "SAAS_DEPLOY_DB_HOST",
        "SAAS_DEPLOY_DB_PORT",
        "SAAS_DEPLOY_DB_NAME",
        "SAAS_DEPLOY_DB_USER",
    ):
        print(f"{clave:<24}: {entorno.get(clave) or '<vacio>'}")

    for nombre in ("DATABASE_URL", "SAAS_ADMIN_DATABASE_URL"):
        dsn = entorno.get(nombre, "")
        if dsn:
            datos = analizar_dsn(dsn)
            print(f"{nombre:<24}: {dsn_publico(dsn)} "
                  f"usuario={'<definido>' if datos['usuario'] else '<vacio>'}")

    try:
        validar_arranque(
            app_env=app_env,
            database_url=entorno.get("DATABASE_URL", ""),
            saas_admin_database_url=entorno.get("SAAS_ADMIN_DATABASE_URL") or None,
            entorno=entorno,
        )
    except ArranqueInseguro as exc:
        print(f"RESULTADO: RECHAZO — {exc}")
        return 1

    print("RESULTADO: ACEPTA (identidad exacta verificada, 0 sockets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
