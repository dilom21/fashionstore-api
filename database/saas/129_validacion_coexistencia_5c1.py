"""129 - Validacion de coexistencia legacy + SaaS (5C1).

Arranca UN proceso FastAPI con las dos superficies activas sobre infraestructura
LOCAL sintetica y comprueba el contrato esencial:

  * La superficie legacy atiende login y catalogo desde su propia base.
  * La superficie tenant atiende login y catalogo desde la base multiempresa.
  * Ninguna superficie ve los datos de la otra.
  * Un token tenant no autoriza una ruta legacy y viceversa.
  * El diagnostico `health/superficies` confirma la separacion de destinos.

Reglas de seguridad del runner:
  * Solo loopback y solo bases con el prefijo desechable `fashionstore_lab_`.
  * Nunca imprime DSN, usuario ni contrasena; los hechos se reportan en JSON.
  * No crea bases ni aplica migraciones: usa el staging ya existente.
  * Siempre detiene el proceso hijo en `finally`.

Uso:
    python database/saas/129_validacion_coexistencia_5c1.py \
        --secretos <ruta secrets_5bx.json> \
        --clave-legacy <ruta legacy_login.json> \
        --puerto 8011
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PREFIJO_BASE = "fashionstore_lab_"
HOST = "127.0.0.1"
BASE_SAAS = "fashionstore_lab_5bx_staging"
BASE_LEGACY = "fashionstore_lab_final_legacy"


class FallaValidacion(RuntimeError):
    pass


def _dsn(usuario: str, clave: str, base: str, puerto: int) -> str:
    return (
        f"postgresql+psycopg://{usuario}:{clave}@"
        f"{HOST}:{puerto}/{base}"
    )


def _entorno_limpio() -> dict[str, str]:
    """Entorno sin DSN ni credenciales heredadas del proceso padre."""
    heredado = {
        clave: valor
        for clave, valor in os.environ.items()
        if not clave.upper().startswith("PG")
        and clave.upper()
        not in {
            "DATABASE_URL",
            "LEGACY_DATABASE_URL",
            "SAAS_ADMIN_DATABASE_URL",
        }
        and not clave.upper().startswith("SAAS_")
    }
    return heredado


def _peticion(
    puerto: int,
    metodo: str,
    ruta: str,
    *,
    cuerpo: dict | None = None,
    token: str | None = None,
) -> tuple[int, object]:
    datos = None
    cabeceras = {"Accept": "application/json"}
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(
        f"http://{HOST}:{puerto}{ruta}",
        data=datos,
        headers=cabeceras,
        method=metodo,
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            crudo = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(crudo) if crudo else None
    except urllib.error.HTTPError as exc:
        crudo = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(crudo) if crudo else None
        except json.JSONDecodeError:
            return exc.code, crudo


def _esperar(puerto: int, segundos: int = 60) -> None:
    limite = time.time() + segundos
    ultimo: str = "sin respuesta"
    while time.time() < limite:
        try:
            estado, _ = _peticion(puerto, "GET", "/health")
            if estado == 200:
                return
            ultimo = f"HTTP {estado}"
        except Exception as exc:  # noqa: BLE001 - diagnostico de arranque
            ultimo = type(exc).__name__
        time.sleep(1)
    raise FallaValidacion(f"el servidor no respondio /health en {segundos}s ({ultimo})")


def _ids(lista: object) -> set[int]:
    if not isinstance(lista, list):
        raise FallaValidacion("se esperaba una lista JSON")
    return {
        int(elemento["id"])
        for elemento in lista
        if isinstance(elemento, dict) and "id" in elemento
    }


def _nombres(lista: object) -> set[str]:
    """Nombres normalizados; sirve para comparar sin depender de los ids."""
    if not isinstance(lista, list):
        raise FallaValidacion("se esperaba una lista JSON")
    return {
        str(elemento.get("nombre", "")).strip().lower()
        for elemento in lista
        if isinstance(elemento, dict) and elemento.get("nombre")
    }


def _sembrar_marcadores(
    secretos: dict, pg_puerto: int
) -> None:
    """Inserta un producto marcador por superficie (idempotente).

    Sirve para probar aislamiento con un discriminador que no dependa de ids ni
    de nombres repetidos por la semilla sintetica.
    """
    import psycopg

    def _conectar(base: str, usuario: str, clave: str):
        return psycopg.connect(
            host=HOST,
            port=pg_puerto,
            dbname=base,
            user=usuario,
            password=clave,
            autocommit=True,
        )

    sentencias = (
        (
            BASE_LEGACY,
            "postgres",
            secretos["admin_password"],
            "Marcador solo legacy 5C1",
            1,
        ),
        (
            BASE_SAAS,
            "postgres",
            secretos["admin_password"],
            "Marcador tenant B 5C1",
            2,
        ),
    )
    for base, usuario, clave, nombre, empresa_id in sentencias:
        with _conectar(base, usuario, clave) as conexion:
            with conexion.cursor() as cursor:
                # La semilla sintetica inserta ids explicitos: sincronizar la
                # secuencia antes de un INSERT sin id.
                cursor.execute(
                    "SELECT setval("
                    "pg_get_serial_sequence('public.producto','id'), "
                    "GREATEST(COALESCE(MAX(id), 1), 1)) FROM public.producto"
                )
                cursor.execute(
                    """
                    INSERT INTO public.producto
                        (empresa_id, categoria_id, nombre, precio)
                    SELECT %s, p.categoria_id, %s, 1
                      FROM public.producto p
                     WHERE p.empresa_id = %s
                       AND NOT EXISTS (
                           SELECT 1 FROM public.producto x WHERE x.nombre = %s
                     )
                     LIMIT 1
                    """,
                    (empresa_id, nombre, empresa_id, nombre),
                )


def ejecutar(args: argparse.Namespace) -> dict:
    secretos = json.loads(Path(args.secretos).read_text(encoding="utf-8"))
    legacy_login = json.loads(Path(args.clave_legacy).read_text(encoding="utf-8"))

    for base in (BASE_SAAS, BASE_LEGACY):
        if not base.startswith(PREFIJO_BASE):
            raise FallaValidacion("base fuera del prefijo desechable")

    _sembrar_marcadores(secretos, args.pg_puerto)

    entorno = _entorno_limpio()
    entorno.update(
        {
            "APP_ENV": "development",
            "APP_DEBUG": "true",
            "DATABASE_URL": _dsn(
                "fs_saas_runtime", secretos["runtime_password"], BASE_SAAS, args.pg_puerto
            ),
            "LEGACY_DATABASE_URL": _dsn(
                "postgres", secretos["admin_password"], BASE_LEGACY, args.pg_puerto
            ),
            "SECRET_KEY": secretos["secret_key"],
            "SAAS_AUTH_ENABLED": "true",
            "LEGACY_SURFACE_ENABLED": "true",
            "SAAS_TENANT_ASSERTION_KEY": secretos["assertion_key"],
            "SAAS_VENTAS_ENABLED": "true",
            "SAAS_COMPRAS_ENABLED": "true",
            "SAAS_DEVOLUCIONES_ENABLED": "true",
            "SAAS_RESERVAS_ENABLED": "true",
            "PYTHONPATH": str(RAIZ),
        }
    )

    registro = Path(args.log)
    casos: list[dict] = []
    proceso = None
    try:
        with registro.open("w", encoding="utf-8") as salida:
            proceso = subprocess.Popen(  # noqa: S603 - comando fijo local
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    HOST,
                    "--port",
                    str(args.puerto),
                    "--log-level",
                    "warning",
                ],
                cwd=str(RAIZ),
                env=entorno,
                stdout=salida,
                stderr=subprocess.STDOUT,
            )
        _esperar(args.puerto)

        def caso(nombre: str, obtenido, esperado, ok: bool) -> None:
            casos.append(
                {
                    "caso": nombre,
                    "obtenido": obtenido,
                    "esperado": esperado,
                    "ok": bool(ok),
                }
            )

        # 1. Diagnostico de superficies.
        estado, salud = _peticion(args.puerto, "GET", "/health/superficies")
        caso(
            "health/superficies responde 200",
            estado,
            200,
            estado == 200 and isinstance(salud, dict),
        )
        superficies = (salud or {}).get("superficies", {})
        caso(
            "las dos superficies declaradas",
            superficies,
            {"legacy": True, "tenant": True},
            superficies == {"legacy": True, "tenant": True},
        )
        caso(
            "health/superficies confirma separacion de datos",
            (salud or {}).get("separacion_datos"),
            True,
            (salud or {}).get("separacion_datos") is True,
        )
        datos = (salud or {}).get("datos", {})
        caso(
            "ambos destinos responden SELECT 1",
            datos,
            {"tenant_ok": True, "legacy_ok": True},
            datos == {"tenant_ok": True, "legacy_ok": True},
        )

        # 2. Superficie legacy: login y catalogo desde su base.
        estado, cuerpo = _peticion(
            args.puerto,
            "POST",
            "/auth/personal/login",
            cuerpo={
                "correo": "admin@staging.invalid",
                "password": legacy_login["password"],
            },
        )
        token_legacy = (cuerpo or {}).get("access_token") if isinstance(cuerpo, dict) else None
        caso("login legacy 200", estado, 200, estado == 200 and bool(token_legacy))

        estado, productos_legacy = _peticion(
            args.puerto, "GET", "/productos", token=token_legacy
        )
        ids_legacy = _ids(productos_legacy) if estado == 200 else set()
        nombres_legacy = _nombres(productos_legacy) if estado == 200 else set()
        caso(
            "catalogo legacy responde con su propia base",
            {"status": estado, "total": len(ids_legacy)},
            "200 con al menos 1 producto",
            estado == 200 and len(ids_legacy) >= 1,
        )

        # 3. Superficie tenant: dos empresas, cada una con su token.
        estado, cuerpo_a = _peticion(
            args.puerto,
            "POST",
            "/auth/tenant/login",
            cuerpo={
                "correo": "admin@staging.invalid",
                "password": secretos["password_usuarios"],
                "empresa_slug": "empresa-a",
                "contexto": "personal",
            },
        )
        token_a = (cuerpo_a or {}).get("access_token") if isinstance(cuerpo_a, dict) else None
        caso("login tenant empresa-a 200", estado, 200, estado == 200 and bool(token_a))

        estado, productos_a = _peticion(
            args.puerto, "GET", "/tenant/catalogo/productos", token=token_a
        )
        ids_a = _ids(productos_a) if estado == 200 else set()
        nombres_a = _nombres(productos_a) if estado == 200 else set()
        caso(
            "catalogo tenant empresa-a responde",
            {"status": estado, "total": len(ids_a)},
            "200",
            estado == 200,
        )

        estado, cuerpo_b = _peticion(
            args.puerto,
            "POST",
            "/auth/tenant/login",
            cuerpo={
                "correo": "admin.b@staging.invalid",
                "password": secretos["password_usuarios"],
                "empresa_slug": "empresa-b",
                "contexto": "personal",
            },
        )
        token_b = (cuerpo_b or {}).get("access_token") if isinstance(cuerpo_b, dict) else None
        caso("login tenant empresa-b 200", estado, 200, estado == 200 and bool(token_b))

        estado, productos_b = _peticion(
            args.puerto, "GET", "/tenant/catalogo/productos", token=token_b
        )
        ids_b = _ids(productos_b) if estado == 200 else set()
        nombres_b = _nombres(productos_b) if estado == 200 else set()
        caso(
            "catalogo tenant empresa-b responde",
            {"status": estado, "total": len(ids_b)},
            "200",
            estado == 200,
        )

        # 4. Aislamiento entre las tres superficies, con marcadores sinteticos.
        caso(
            "empresa-a y empresa-b no comparten identificadores",
            {"a": sorted(ids_a), "b": sorted(ids_b)},
            "interseccion vacia",
            bool(ids_a) and bool(ids_b) and not (ids_a & ids_b),
        )
        marcador_legacy = "marcador solo legacy 5c1"
        marcador_tenant = "marcador tenant b 5c1"
        caso(
            "la base legacy contiene su marcador",
            marcador_legacy in nombres_legacy,
            True,
            marcador_legacy in nombres_legacy,
        )
        caso(
            "la empresa-b contiene su marcador",
            marcador_tenant in nombres_b,
            True,
            marcador_tenant in nombres_b,
        )
        caso(
            "el catalogo tenant no ve los datos legacy",
            {"marcador_legacy_visible": marcador_legacy in (nombres_a | nombres_b)},
            {"marcador_legacy_visible": False},
            marcador_legacy not in (nombres_a | nombres_b),
        )
        caso(
            "el catalogo legacy no ve los datos tenant",
            {"marcador_tenant_visible": marcador_tenant in nombres_legacy},
            {"marcador_tenant_visible": False},
            marcador_tenant not in nombres_legacy,
        )

        # 5. Ningun token cruza de superficie. Se usa una ruta legacy PROTEGIDA:
        # `/productos` es publica por contrato (CU09) y no evalua el token.
        estado, _ = _peticion(
            args.puerto, "GET", "/productos/admin", token=token_a
        )
        caso("token tenant rechazado en ruta legacy", estado, 401, estado == 401)

        estado, _ = _peticion(
            args.puerto,
            "GET",
            "/tenant/catalogo/productos",
            token=token_legacy,
        )
        caso("token legacy rechazado en ruta tenant", estado, 401, estado == 401)

        # 6. Diagnostico heredado sigue cerrado en modo SaaS.
        estado, _ = _peticion(args.puerto, "GET", "/health/db")
        caso("health/db cerrado en modo SaaS", estado, 503, estado == 503)

        return {
            "ok": all(caso["ok"] for caso in casos),
            "casos": casos,
            "proceso_pid": proceso.pid if proceso else None,
            "log": str(registro),
        }
    finally:
        if proceso is not None and proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait(timeout=10)


def ejecutar_legacy_puro(args: argparse.Namespace) -> dict:
    """Modo del despliegue ACTUAL: solo legacy, SaaS apagado.

    Comprueba que actualizar el codigo no cambia el contrato existente: el
    engine unico es DATABASE_URL y no se exige ningun DSN adicional.
    """
    secretos = json.loads(Path(args.secretos).read_text(encoding="utf-8"))
    legacy_login = json.loads(Path(args.clave_legacy).read_text(encoding="utf-8"))

    if not BASE_LEGACY.startswith(PREFIJO_BASE):
        raise FallaValidacion("base fuera del prefijo desechable")

    entorno = _entorno_limpio()
    entorno.update(
        {
            "APP_ENV": "development",
            "APP_DEBUG": "true",
            "DATABASE_URL": _dsn(
                "postgres", secretos["admin_password"], BASE_LEGACY, args.pg_puerto
            ),
            "SECRET_KEY": secretos["secret_key"],
            "SAAS_AUTH_ENABLED": "false",
            "LEGACY_SURFACE_ENABLED": "true",
            "PYTHONPATH": str(RAIZ),
        }
    )

    proceso = None
    casos: list[dict] = []
    try:
        with Path(args.log).open("w", encoding="utf-8") as salida:
            proceso = subprocess.Popen(  # noqa: S603 - comando fijo local
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    HOST,
                    "--port",
                    str(args.puerto),
                    "--log-level",
                    "warning",
                ],
                cwd=str(RAIZ),
                env=entorno,
                stdout=salida,
                stderr=subprocess.STDOUT,
            )
        _esperar(args.puerto)

        def caso(nombre: str, obtenido, esperado, ok: bool) -> None:
            casos.append(
                {
                    "caso": nombre,
                    "obtenido": obtenido,
                    "esperado": esperado,
                    "ok": bool(ok),
                }
            )

        estado, salud = _peticion(args.puerto, "GET", "/health/superficies")
        caso(
            "superficie unica declarada",
            (salud or {}).get("superficies"),
            {"legacy": True, "tenant": False},
            (salud or {}).get("superficies") == {"legacy": True, "tenant": False},
        )
        caso(
            "sin separacion de datos en modo legacy",
            (salud or {}).get("separacion_datos"),
            False,
            (salud or {}).get("separacion_datos") is False,
        )

        estado, cuerpo = _peticion(
            args.puerto,
            "POST",
            "/auth/personal/login",
            cuerpo={
                "correo": "admin@staging.invalid",
                "password": legacy_login["password"],
            },
        )
        token = (cuerpo or {}).get("access_token") if isinstance(cuerpo, dict) else None
        caso("login legacy 200", estado, 200, estado == 200 and bool(token))

        estado, productos = _peticion(
            args.puerto, "GET", "/productos", token=token
        )
        caso(
            "catalogo publico 200",
            {"status": estado, "total": len(_ids(productos)) if estado == 200 else 0},
            "200",
            estado == 200,
        )

        estado, _ = _peticion(
            args.puerto, "GET", "/auth/me", token=token
        )
        caso("ruta legacy protegida 200 con su JWT", estado, 200, estado == 200)

        estado, _ = _peticion(
            args.puerto,
            "POST",
            "/auth/tenant/login",
            cuerpo={
                "correo": "admin@staging.invalid",
                "password": legacy_login["password"],
                "empresa_slug": "fashionstore-legacy",
                "contexto": "personal",
            },
        )
        caso("superficie tenant cerrada con SaaS off", estado, 503, estado == 503)

        return {
            "ok": all(caso["ok"] for caso in casos),
            "casos": casos,
            "proceso_pid": proceso.pid if proceso else None,
            "log": str(Path(args.log)),
        }
    finally:
        if proceso is not None and proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait(timeout=10)


def main() -> int:
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--secretos", required=True)
    analizador.add_argument("--clave-legacy", required=True)
    analizador.add_argument("--puerto", type=int, default=8011)
    analizador.add_argument("--pg-puerto", type=int, default=55460)
    analizador.add_argument(
        "--modo",
        choices=("coexistencia", "legacy-puro"),
        default="coexistencia",
        help="coexistencia = SaaS + legacy con dos destinos; "
        "legacy-puro = despliegue actual (SaaS apagado)",
    )
    analizador.add_argument(
        "--log",
        default=str(Path(os.environ.get("TEMP", ".")) / "coexistencia_5c1_uvicorn.log"),
    )
    args = analizador.parse_args()

    if args.pg_puerto < 1 or args.puerto < 1:
        raise SystemExit("puertos invalidos")

    resultado = (
        ejecutar_legacy_puro(args)
        if args.modo == "legacy-puro"
        else ejecutar(args)
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
