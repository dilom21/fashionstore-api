"""123 â€” Runner E2E de navegador real (Playwright + Edge) de la etapa 5BZ.

Orquesta, en este orden y siempre sobre loopback:

1. Verifica que los puertos 8010, 4200 y 4201 esten libres. Si alguno esta
   ocupado, informa y aborta SIN matar procesos ajenos.
2. Arranca el backend SaaS ON con el lanzador seguro
   `119_servidor_saas_local_5by.py` (inyecta el `.env` sintetico del staging y
   rechaza cualquier destino no loopback) y espera `GET /health` 200.
3. Arranca el Angular SaaS (`npm run start:saas`) en el puerto 4200 y espera
   `GET /saas/login` 200.
4. Ejecuta `npx playwright test --project=saas` con las credenciales ficticias
   leidas de `secrets_5bx.json` (NUNCA se imprimen).
5. Detiene el Angular SaaS, arranca el legacy en 4201 y ejecuta
   `npx playwright test --project=legacy`.
6. Detiene SIEMPRE lo que arranco (`taskkill /PID <pid> /T /F` solo sobre sus
   propios PIDs) y comprueba que los puertos quedan libres.
7. Escribe `evidencias/EVIDENCIA_E2E_NAVEGADOR_5BZ_<sello>.json` con las cifras
   reales, las rutas visitadas y `datos_reales: false`.

No inventa resultados: si Playwright no arranca, la evidencia queda en estado
BLOQUEADO con el error real y el codigo de salida es 1.

Uso:
    python database/saas/123_e2e_navegador_5bz.py
    python database/saas/123_e2e_navegador_5bz.py --web C:\\ruta\\fashionstore-web
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANZADOR = Path(__file__).with_name("119_servidor_saas_local_5by.py")
EVIDENCIAS = Path(__file__).with_name("evidencias")
WEB_POR_DEFECTO = Path(r"C:\SI2_Parcial1\fashionstore-web")

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
SECRETOS = LOCALAPPDATA / "Temp" / "opencode" / "fs_staging_5bx" / "secrets_5bx.json"
TMP = LOCALAPPDATA / "Temp" / "opencode" / "5bz_e2e"

HOST = "127.0.0.1"
PUERTO_BACKEND = 8010
PUERTO_SAAS = 4200
PUERTO_LEGACY = 4201

URL_BACKEND = f"http://{HOST}:{PUERTO_BACKEND}"
URL_SAAS = f"http://{HOST}:{PUERTO_SAAS}"
URL_LEGACY = f"http://{HOST}:{PUERTO_LEGACY}"

# Correos ficticios del staging 5BX (no son secretos: estan en el brief).
CORREO_A = "admin@staging.invalid"
CORREO_B = "admin.b@staging.invalid"
CORREO_CAJERO_B = "cajero.b@staging.invalid"

TIMEOUT_BACKEND = 90
TIMEOUT_ANGULAR = 240
TIMEOUT_PLAYWRIGHT = 900

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sin_ansi(texto: str) -> str:
    """Quita las secuencias de color de la salida de Playwright/npm."""
    return ANSI.sub("", texto).strip()

# Correcciones de la ronda D1/D2/D3 y como se verifican en esta corrida.
CORRECCIONES = [
    {
        "id": "D1",
        "descripcion": (
            "El panel SaaS autenticado se renderiza en CLIENTE: en "
            "app.routes.server.ts `saas/login` y `saas/invitacion` siguen en "
            "RenderMode.Server, `saas/**` pasa a RenderMode.Client y el "
            "wildcard `**` sigue en Server. Los guards dependen de "
            "sessionStorage y ya no se ejecutan en el servidor."
        ),
        "archivos": [
            "fashionstore-web/src/app/app.routes.server.ts",
            "fashionstore-web/src/app/app.routes.server.spec.ts",
        ],
        "verificacion": (
            "Specs 02-guards: F5 mantiene la sesión y una URL profunda "
            "(/saas/catalogo) renderiza el panel sin rebote a /saas/login; "
            "sin sesión /saas/inicio sí redirige al login."
        ),
    },
    {
        "id": "D2",
        "descripcion": (
            "El selector de proveedor del alta de compras se deshabilita desde "
            "el FormControl (no con `[disabled]` en el template, que el estado "
            "reactivo sobrescribía) cuando el catálogo está vacío, cargando o "
            "sin permiso."
        ),
        "archivos": [
            "fashionstore-web/src/app/saas/pages/compras/saas-compras.page.ts",
            "fashionstore-web/src/app/saas/pages/compras/saas-compras.page.html",
            "fashionstore-web/src/app/saas/pages/compras/saas-compras.page.spec.ts",
        ],
        "verificacion": (
            "Spec de compras (3 casos de estado disabled) y E2E 07-compras B: "
            "el <select> queda deshabilitado con 0 proveedores."
        ),
    },
    {
        "id": "D3",
        "descripcion": (
            "Un 401 de POST /auth/tenant/login muestra «Correo, contraseña o "
            "empresa incorrectos.» en lugar del aviso genérico de sesión "
            "expirada. El interceptor sigue tratando los 401 de sesión igual."
        ),
        "archivos": [
            "fashionstore-web/src/app/saas/pages/login/saas-login.page.ts",
            "fashionstore-web/src/app/saas/pages/login/saas-login.page.spec.ts",
        ],
        "verificacion": (
            "Spec de login (401 exacto) y E2E 01-auth/06-errores: el aviso "
            "visible es el de credenciales y no menciona sesión expirada."
        ),
    },
]

# Notas que siguen siendo ciertas después de las correcciones.
OBSERVACIONES = [
    "El panel SaaS se sirve como aplicación de cliente: la primera carga de "
    "cada ruta privada descarga los chunks y luego el guard hidrata con "
    "GET /auth/tenant/me. /saas/login y /saas/invitacion siguen en SSR.",
    "Cada ejecución crea una venta de contado y una devolución reales en el "
    "staging local (datos ficticios). La devolución se aprueba y procesa para "
    "reponer el stock, así la suite se puede repetir sin agotar existencias.",
    "El límite de plan se comprueba con un intento real de alta en la empresa "
    "B: el backend responde 409 y no se crea la tercera sucursal.",
]


# --------------------------------------------------------------------------
# Utilidades de red y procesos
# --------------------------------------------------------------------------
def puerto_ocupado(puerto: int, host: str = HOST) -> bool:
    """`True` si algo acepta conexiones en el puerto indicado."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, puerto)) == 0


def esperar_puerto_libre(puerto: int, timeout: float = 30.0) -> bool:
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        if not puerto_ocupado(puerto):
            return True
        time.sleep(0.5)
    return not puerto_ocupado(puerto)


def esperar_http(url: str, timeout: float, log: Path | None = None) -> bool:
    fin = time.monotonic() + timeout
    ultimo = ""
    while time.monotonic() < fin:
        try:
            with urllib.request.urlopen(url, timeout=5) as respuesta:
                if respuesta.status == 200:
                    return True
                ultimo = f"HTTP {respuesta.status}"
        except urllib.error.HTTPError as exc:
            ultimo = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - red local en arranque
            ultimo = type(exc).__name__
        time.sleep(2)
    print(f"  timeout esperando {url} ({ultimo})")
    if log is not None and log.exists():
        lineas = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for linea in lineas[-15:]:
            print(f"  | {ANSI.sub('', linea)[:200]}")
    return False


def detener(proceso: subprocess.Popen | None, nombre: str) -> None:
    """Mata SOLO el arbol del proceso propio (shell=True crea cmd.exe)."""
    if proceso is None or proceso.poll() is not None:
        return
    pid = proceso.pid
    print(f"  deteniendo {nombre} (PID {pid})")
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        proceso.wait(timeout=20)
    except subprocess.TimeoutExpired:
        print(f"  aviso: {nombre} no termino en 20 s")


def abrir_log(nombre: str):
    TMP.mkdir(parents=True, exist_ok=True)
    return open(TMP / nombre, "wb")


def arrancar_backend() -> tuple[subprocess.Popen, Path]:
    log = TMP / "backend.log"
    salida = abrir_log("backend.log")
    print(f"  arrancando backend con {LANZADOR.name} --puerto {PUERTO_BACKEND}")
    proceso = subprocess.Popen(
        [sys.executable, str(LANZADOR), "--puerto", str(PUERTO_BACKEND)],
        cwd=str(ROOT),
        stdout=salida,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    salida.close()
    return proceso, log


def arrancar_web(web: Path, script: str, puerto: int) -> tuple[subprocess.Popen, Path]:
    log = TMP / f"web_{puerto}.log"
    salida = abrir_log(f"web_{puerto}.log")
    entorno = os.environ.copy()
    entorno["NG_CLI_ANALYTICS"] = "false"
    # `ng serve` escucha en ::1 por defecto; el CORS del backend y la baseURL de
    # Playwright usan 127.0.0.1, asi que se fija el host explicitamente.
    orden = (
        f"npm {script} -- --port {puerto} --host {HOST}"
    )
    print(f"  arrancando Angular: {orden}")
    proceso = subprocess.Popen(
        orden,
        cwd=str(web),
        shell=True,
        env=entorno,
        stdout=salida,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    salida.close()
    return proceso, log


# --------------------------------------------------------------------------
# Versiones y lectura de resultados
# --------------------------------------------------------------------------
def version_de(comando: list[str], cwd: Path | None = None) -> str | None:
    try:
        salida = subprocess.run(
            comando,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
            check=False,
        )
        texto = (salida.stdout or salida.stderr).strip()
        if salida.returncode != 0 or not texto:
            return None
        return texto.splitlines()[0].strip()
    except Exception:  # noqa: BLE001 - solo diagnostico
        return None


def version_playwright(web: Path) -> str | None:
    ruta = web / "node_modules" / "@playwright" / "test" / "package.json"
    try:
        return json.loads(ruta.read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001
        return None


def version_navegador(web: Path, node: str | None) -> dict:
    """Version real de Edge via Playwright (sin descargar navegadores)."""
    if node is None:
        return {"canal": "msedge", "version": None, "error": "node no disponible"}
    guion = (
        "const {chromium}=require('playwright');"
        "(async()=>{const b=await chromium.launch({channel:'msedge'});"
        "console.log(b.version());await b.close();})()"
        ".catch(e=>{console.error(String(e&&e.message||e));process.exit(1);});"
    )
    try:
        salida = subprocess.run(
            [node, "-e", guion],
            cwd=str(web),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if salida.returncode != 0:
            return {
                "canal": "msedge",
                "version": None,
                "error": (salida.stderr or salida.stdout).strip()[:300],
            }
        return {"canal": "msedge", "version": salida.stdout.strip(), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"canal": "msedge", "version": None, "error": str(exc)[:300]}


def recolectar_specs(suites: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for suite in suites or []:
        archivo = suite.get("file") or ""
        for spec in suite.get("specs") or []:
            duracion = 0
            mensaje = None
            for test in spec.get("tests") or []:
                for resultado in test.get("results") or []:
                    duracion += int(resultado.get("duration") or 0)
                    error = resultado.get("error") or {}
                    if mensaje is None and error.get("message"):
                        mensaje = sin_ansi(error["message"])
            specs.append(
                {
                    "archivo": archivo,
                    "titulo": spec.get("title") or "",
                    "estado": "passed" if spec.get("ok") else "failed",
                    "duracion_ms": duracion,
                    "error": (mensaje or "")[:800] or None,
                }
            )
        specs.extend(recolectar_specs(suite.get("suites") or []))
    return specs


def leer_reporte(ruta: Path) -> dict:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"no se pudo leer el reporte JSON: {exc}"}

    stats = datos.get("stats") or {}
    specs = recolectar_specs(datos.get("suites") or [])
    fallos = [
        {"archivo": s["archivo"], "titulo": s["titulo"], "error": s["error"]}
        for s in specs
        if s["estado"] == "failed"
    ]
    return {
        "passed": int(stats.get("expected") or 0),
        "failed": int(stats.get("unexpected") or 0),
        "skipped": int(stats.get("skipped") or 0),
        "flaky": int(stats.get("flaky") or 0),
        "duracion_ms": int(stats.get("duration") or 0),
        "specs": specs,
        "fallos": fallos,
    }

def leer_rutas(ruta: Path, proyecto: str) -> list[str]:
    if not ruta.exists():
        return []
    vistas: list[str] = []
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if registro.get("proyecto") != proyecto:
            continue
        ruta_vista = registro.get("ruta")
        if isinstance(ruta_vista, str) and ruta_vista not in vistas:
            vistas.append(ruta_vista)
    return vistas


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------
def entorno_playwright(web: Path, password: str, proyecto: str) -> dict:
    entorno = os.environ.copy()
    entorno.update(
        {
            "E2E_CORREO_A": CORREO_A,
            "E2E_CORREO_B": CORREO_B,
            "E2E_CORREO_CAJERO_B": CORREO_CAJERO_B,
            "E2E_PASSWORD": password,
            "E2E_SAAS_BASE_URL": URL_SAAS,
            "E2E_LEGACY_BASE_URL": URL_LEGACY,
            "E2E_JSON": str(TMP / f"resultados_{proyecto}.json"),
            "E2E_RUTAS_FILE": str(TMP / f"rutas_{proyecto}.jsonl"),
            "E2E_STATE": str(web / "test-results" / "estado-e2e-5bz.json"),
            "NG_CLI_ANALYTICS": "false",
        }
    )
    return entorno


def ejecutar_proyecto(
    web: Path,
    proyecto: str,
    password: str,
    node: str | None,
) -> dict:
    """Lanza `npx playwright test --project=<proyecto>` y devuelve cifras reales."""
    entorno = entorno_playwright(web, password, proyecto)
    reporte = Path(entorno["E2E_JSON"])
    rutas = Path(entorno["E2E_RUTAS_FILE"])
    for archivo in (reporte, rutas):
        if archivo.exists():
            archivo.unlink()

    cli = web / "node_modules" / "@playwright" / "test" / "cli.js"
    if node is not None and cli.exists():
        orden = [node, str(cli), "test", f"--project={proyecto}"]
        usa_shell = False
    else:
        orden = f"npx playwright test --project={proyecto}"
        usa_shell = True

    print(f"  ejecutando npx playwright test --project={proyecto}")
    inicio = time.monotonic()
    try:
        salida = subprocess.run(
            orden,
            cwd=str(web),
            env=entorno,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PLAYWRIGHT,
            shell=usa_shell,
            check=False,
        )
        codigo = salida.returncode
        texto = sin_ansi(f"{salida.stdout}\n{salida.stderr}")
        error_ejecucion = None
    except subprocess.TimeoutExpired as exc:
        codigo = 1
        texto = sin_ansi(str(exc))
        error_ejecucion = f"timeout de Playwright tras {TIMEOUT_PLAYWRIGHT} s"
    except Exception as exc:  # noqa: BLE001
        codigo = 1
        texto = ""
        error_ejecucion = f"no se pudo ejecutar Playwright: {exc}"

    duracion = int((time.monotonic() - inicio) * 1000)
    cifras = leer_reporte(reporte)
    if cifras.get("error") and error_ejecucion is None:
        error_ejecucion = cifras["error"]
    return {
        "proyecto": proyecto,
        "comando": orden if isinstance(orden, str) else " ".join(orden),
        "codigo_salida": codigo,
        "duracion_ms": cifras.get("duracion_ms") or duracion,
        "passed": cifras.get("passed"),
        "failed": cifras.get("failed"),
        "skipped": cifras.get("skipped"),
        "flaky": cifras.get("flaky"),
        "specs": cifras.get("specs") or [],
        "fallos": cifras.get("fallos") or [],
        "rutas_visitadas": leer_rutas(rutas, proyecto),
        "error_ejecucion": error_ejecucion,
        "reporte_json": str(reporte),
        "salida_resumen": texto.strip().splitlines()[-40:] if texto else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner E2E de navegador 5BZ")
    parser.add_argument("--web", default=str(WEB_POR_DEFECTO))
    args = parser.parse_args(argv)

    web = Path(args.web)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    ruta_evidencia = EVIDENCIAS / f"EVIDENCIA_E2E_NAVEGADOR_5BZ_{sello}.json"

    node = shutil.which("node")
    evidencia: dict = {
        "etapa": "5BZ",
        "tarea": "T04",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datos_reales": False,
        "secretos_expuestos": False,
        "estado": "BLOQUEADO",
        "entorno": {
            "backend": f"{URL_BACKEND} (119_servidor_saas_local_5by.py)",
            "frontend_saas": URL_SAAS,
            "frontend_legacy": URL_LEGACY,
            "base_datos": "PostgreSQL 17 local 127.0.0.1:55460 "
            "fashionstore_lab_5bx_staging (datos ficticios)",
            "navegador_canal": "msedge",
        },
        "versiones": {
            "python": sys.version.split()[0],
            "node": version_de([node, "--version"]) if node else None,
            "npm": version_de(["cmd", "/c", "npm --version"]),
            "playwright": version_playwright(web),
            "navegador": None,
        },
        "proyectos": {},
        "correcciones": CORRECCIONES,
        "observaciones": OBSERVACIONES,
        "errores": [],
        "puertos": {
            "verificados_al_inicio": [PUERTO_BACKEND, PUERTO_SAAS, PUERTO_LEGACY],
            "libres_al_inicio": None,
            "liberados_al_terminar": None,
        },
    }

    if not web.exists():
        evidencia["errores"].append(f"No existe el repo del frontend: {web}")
        ruta_evidencia.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BLOQUEADO: {web} no existe")
        print(f"Evidencia: {ruta_evidencia}")
        return 1

    if not SECRETOS.exists():
        evidencia["errores"].append(f"Falta {SECRETOS}")
        ruta_evidencia.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BLOQUEADO: falta el archivo de secretos del staging: {SECRETOS}")
        print(f"Evidencia: {ruta_evidencia}")
        return 1

    password = json.loads(SECRETOS.read_text(encoding="utf-8")).get("password_usuarios")
    if not password:
        evidencia["errores"].append("secrets_5bx.json no define password_usuarios")
        ruta_evidencia.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BLOQUEADO: secrets_5bx.json no define password_usuarios")
        print(f"Evidencia: {ruta_evidencia}")
        return 1

    procesos: dict[str, subprocess.Popen | None] = {"backend": None, "web": None}
    codigo = 1

    try:
        # 1) Puertos libres (no se toca nada ajeno).
        ocupados = [
            puerto
            for puerto in (PUERTO_BACKEND, PUERTO_SAAS, PUERTO_LEGACY)
            if puerto_ocupado(puerto)
        ]
        evidencia["puertos"]["libres_al_inicio"] = not ocupados
        if ocupados:
            mensaje = (
                "puertos ocupados por procesos ajenos: "
                + ", ".join(str(p) for p in ocupados)
                + ". No se mata ningun proceso; cierra esas aplicaciones y "
                "reintenta."
            )
            evidencia["errores"].append(mensaje)
            print(f"ABORTADO: {mensaje}")
            return 1

        print("Versiones:")
        evidencia["versiones"]["navegador"] = version_navegador(web, node)
        for clave, valor in evidencia["versiones"].items():
            print(f"  {clave}: {valor}")

        # 2) Backend seguro.
        procesos["backend"], log_backend = arrancar_backend()
        if not esperar_http(f"{URL_BACKEND}/health", TIMEOUT_BACKEND, log_backend):
            raise RuntimeError(
                f"el backend no respondio /health en {TIMEOUT_BACKEND} s"
            )
        print(f"  backend listo: {URL_BACKEND}/health")

        # 3) Angular SaaS.
        procesos["web"], log_web = arrancar_web(web, "run start:saas", PUERTO_SAAS)
        if not esperar_http(f"{URL_SAAS}/saas/login", TIMEOUT_ANGULAR, log_web):
            raise RuntimeError(
                f"el Angular SaaS no respondio /saas/login en {TIMEOUT_ANGULAR} s"
            )
        print(f"  Angular SaaS listo: {URL_SAAS}/saas/login")

        # 4) Proyecto saas.
        resultado_saas = ejecutar_proyecto(web, "saas", password, node)
        evidencia["proyectos"]["saas"] = resultado_saas
        print(
            "  saas: "
            f"{resultado_saas['passed']} passed / {resultado_saas['failed']} failed / "
            f"{resultado_saas['skipped']} skipped "
            f"({resultado_saas['duracion_ms']} ms)"
        )

        # 5) Angular legacy en su propio puerto.
        detener(procesos["web"], "Angular SaaS")
        procesos["web"] = None
        if not esperar_puerto_libre(PUERTO_SAAS, 40):
            raise RuntimeError(
                f"el puerto {PUERTO_SAAS} sigue ocupado tras detener el SaaS"
            )

        procesos["web"], log_legacy = arrancar_web(web, "start", PUERTO_LEGACY)
        if not esperar_http(f"{URL_LEGACY}/login", TIMEOUT_ANGULAR, log_legacy):
            raise RuntimeError(
                f"el Angular legacy no respondio /login en {TIMEOUT_ANGULAR} s"
            )
        print(f"  Angular legacy listo: {URL_LEGACY}/login")

        resultado_legacy = ejecutar_proyecto(web, "legacy", password, node)
        evidencia["proyectos"]["legacy"] = resultado_legacy
        print(
            "  legacy: "
            f"{resultado_legacy['passed']} passed / {resultado_legacy['failed']} failed / "
            f"{resultado_legacy['skipped']} skipped "
            f"({resultado_legacy['duracion_ms']} ms)"
        )

        hubo_fallo = any(
            (proyecto.get("failed") or 0) > 0
            or proyecto.get("error_ejecucion")
            for proyecto in evidencia["proyectos"].values()
        )
        evidencia["estado"] = "FALLIDO" if hubo_fallo else "OK"
        codigo = 1 if hubo_fallo else 0

    except Exception as exc:  # noqa: BLE001 - se reporta tal cual en la evidencia
        evidencia["estado"] = "BLOQUEADO"
        evidencia["errores"].append(f"{type(exc).__name__}: {exc}")
        print(f"BLOQUEADO: {type(exc).__name__}: {exc}")
        codigo = 1

    finally:
        # 6) Limpieza garantizada: solo procesos propios.
        detener(procesos["web"], "Angular")
        detener(procesos["backend"], "backend")
        liberados = {
            str(puerto): esperar_puerto_libre(puerto, 40)
            for puerto in (PUERTO_BACKEND, PUERTO_SAAS, PUERTO_LEGACY)
        }
        evidencia["puertos"]["liberados_al_terminar"] = liberados
        if not all(liberados.values()):
            evidencia["errores"].append(
                f"puertos que siguen ocupados al terminar: {liberados}"
            )
            if codigo == 0:
                codigo = 1

        # 7) Resumen agregado (cifras reales, nunca inventadas).
        totales = {"passed": 0, "failed": 0, "skipped": 0}
        for proyecto in evidencia["proyectos"].values():
            for clave in totales:
                totales[clave] += int(proyecto.get(clave) or 0)
        evidencia["totales"] = totales
        evidencia["codigo_salida"] = codigo

        ruta_evidencia.write_text(
            json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("")
        print(f"ESTADO   : {evidencia['estado']}")
        print(
            f"TOTAL    : {totales['passed']} passed / {totales['failed']} failed / "
            f"{totales['skipped']} skipped"
        )
        print(f"PUERTOS  : {liberados}")
        print(f"EVIDENCIA: {ruta_evidencia}")
        if evidencia["errores"]:
            print("ERRORES  :")
            for mensaje in evidencia["errores"]:
                print(f"  - {mensaje}")

    return codigo


if __name__ == "__main__":
    sys.exit(main())

