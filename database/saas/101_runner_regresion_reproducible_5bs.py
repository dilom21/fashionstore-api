"""5BS / Etapa J - Runner de regresion reproducible (sin laboratorios apagados).

Que hace
--------
1. `--clasificar`: clasifica cada modulo de `tests_saas` en
   OFFLINE / INTEGRACION_REPRODUCIBLE / INTEGRACION_PENDIENTE segun su
   dependencia real (PostgreSQL, uvicorn, manifiesto de laboratorio).
2. `--offline`: ejecuta la suite OFFLINE y reporta cifras exactas.
3. `--lab 5bm|5bn|5bo`: aprovisiona un laboratorio NUEVO (directorio nuevo,
   puerto nuevo 55449/55450/55451) reutilizando los runners historicos sin
   editarlos, apunta el modulo de pruebas a su manifiesto y ejecuta la bateria
   completa. Al terminar detiene el cluster (los datos quedan para reejecutar).
4. `--todo`: clasifica, corre OFFLINE y luego los tres laboratorios.
5. `--destruir`: elimina SOLO los laboratorios 5BS de este runner.

Reglas de seguridad
-------------------
* Nunca usa 5432 ni los puertos 55432-55448 de etapas previas.
* Los laboratorios previos (`fs_lab_5bm/5bn/5bo/5br...`) no se leen ni se tocan:
  cada laboratorio 5BS usa `fs_lab_5bs_repro_*` y un marcador `DISECHABLE_5BS`.
* `--lab` exige `--confirm-desechable`.
* No imprime DSN, contrasenas ni claves: reenvia los manifiestos por variable de
  entorno a pytest y reporta solo conteos y rutas.

Uso
---
    python database/saas/101_runner_regresion_reproducible_5bs.py --clasificar
    python database/saas/101_runner_regresion_reproducible_5bs.py --offline
    python database/saas/101_runner_regresion_reproducible_5bs.py --lab 5bm --confirm-desechable
    python database/saas/101_runner_regresion_reproducible_5bs.py --todo --confirm-desechable
    python database/saas/101_runner_regresion_reproducible_5bs.py --destruir --confirm-desechable
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCIAS = ROOT / "database" / "saas" / "evidencias"
TESTS = ROOT / "tests_saas"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
OPENCODE_TMP = LOCAL / "Temp" / "opencode"

LABS = {
    "5bm": {
        "runner": "database/saas/76_laboratorio_operativo_5bm.py",
        "puerto": 55449,
        "base": "fashionstore_lab_5bs_5bm",
        "raiz": OPENCODE_TMP / "fs_lab_5bs_repro_5bm",
        "modulo_test": "tests_saas/test_operativo_saas_5bm.py",
        "env_manifiesto": "FASHIONSTORE_LAB_5BM_MANIFEST",
        "manifiesto": "lab_5bm_manifest.json",
        "canonico": 55443,
    },
    "5bn": {
        "runner": "database/saas/77_laboratorio_operativo_5bn.py",
        "puerto": 55450,
        "base": "fashionstore_lab_5bs_5bn",
        "raiz": OPENCODE_TMP / "fs_lab_5bs_repro_5bn",
        "modulo_test": "tests_saas/test_compras_devoluciones_5bn.py",
        "env_manifiesto": "FASHIONSTORE_LAB_5BN_MANIFEST",
        "manifiesto": "lab_5bn_manifest.json",
        "canonico": 55444,
    },
    "5bo": {
        "runner": "database/saas/78_laboratorio_operativo_5bo.py",
        "puerto": 55451,
        "base": "fashionstore_lab_5bs_5bo",
        "raiz": OPENCODE_TMP / "fs_lab_5bs_repro_5bo",
        "modulo_test": "tests_saas/test_nucleo_saas_5bo.py",
        "env_manifiesto": "FASHIONSTORE_LAB_5BO_MANIFEST",
        "manifiesto": "lab_5bo_manifest.json",
        "canonico": 55445,
    },
}

MARCADOR = "DISECHABLE_5BS"
PUERTOS_RESERVADOS = set(range(55432, 55449)) | {5432}


class RunnerError(RuntimeError):
    pass


def _sello() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _cargar(relativa: str, alias: str):
    ruta = ROOT / relativa
    spec = importlib.util.spec_from_file_location(alias, ruta)
    if spec is None or spec.loader is None:
        raise RunnerError(f"No se pudo cargar {relativa}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _validar_destino_nuevo(host: str, puerto: int, base: str, pgdata: Path,
                           puerto_esperado: int, raiz: Path) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise RunnerError("Host no loopback rechazado")
    if puerto != puerto_esperado:
        raise RunnerError(f"Puerto {puerto} != {puerto_esperado} del runner 5BS")
    if puerto in PUERTOS_RESERVADOS:
        raise RunnerError("El runner 5BS nunca usa puertos de etapas previas")
    if "lab" not in base.lower() or "5bs" not in base.lower():
        raise RunnerError("El nombre de base debe identificar el laboratorio 5BS")
    resuelto = pgdata.resolve()
    if raiz.resolve() not in resuelto.parents and resuelto != raiz.resolve():
        raise RunnerError("El pgdata debe vivir dentro del directorio 5BS")


def _reapuntar(clave: str):
    cfg = LABS[clave]
    modulo = _cargar(cfg["runner"], f"lab_5bs_{clave}")
    raiz = cfg["raiz"]
    puerto = cfg["puerto"]
    base = cfg["base"]

    modulo.PUERTO_LAB = puerto
    modulo.BASE_LAB = base
    modulo.HOST_LAB = "127.0.0.1"
    modulo.raiz_lab = lambda: raiz
    modulo.ruta_pgdata = lambda: raiz / "pgdata"
    modulo.ruta_manifiesto = lambda: raiz / cfg["manifiesto"]
    modulo._dsn = lambda rol, password, b=None, _b=base, _p=puerto: (
        f"postgresql://{rol}:{password}@127.0.0.1:{_p}/{b or _b}")

    def _validar(host=None, p=None, b=None, pgdata=None):
        return _validar_destino_nuevo(
            host or "127.0.0.1", p or puerto, b or base,
            Path(pgdata) if pgdata else raiz / "pgdata", puerto, raiz)

    modulo._validar_destino = _validar
    return modulo, cfg


def _puerto_libre(puerto: int) -> bool:
    import socket
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", puerto)) != 0


def _password_admin(raiz: Path) -> str:
    archivo = raiz / ".admin_pw"
    if archivo.exists():
        return archivo.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(24)
    archivo.write_text(password, encoding="utf-8")
    return password


def _aplicar_migracion_psql(cfg: dict, password_admin: str, archivo: str) -> None:
    """Aplica una migracion adicional al laboratorio 5BS con psql (sin DSN en args)."""
    ruta = ROOT / "supabase" / "migrations" / archivo
    if not ruta.exists():
        raise RunnerError(f"No existe la migracion {archivo}")
    pg_bin = Path(r"C:\Program Files\PostgreSQL\18\bin\psql.exe")
    resultado = subprocess.run(
        [str(pg_bin), "-v", "ON_ERROR_STOP=1", "-X", "-q", "-f", str(ruta)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PGHOST": "127.0.0.1",
             "PGPORT": str(cfg["puerto"]), "PGUSER": "postgres",
             "PGDATABASE": cfg["base"], "PGPASSWORD": password_admin,
             "PGSSLMODE": "disable"},
    )
    if resultado.returncode != 0:
        errores = [l for l in ((resultado.stdout or "") + (resultado.stderr or "")).splitlines()
                   if "ERROR" in l][:3]
        raise RunnerError(f"No se pudo aplicar {archivo}: {' | '.join(errores)}")


def aprovisionar(clave: str) -> dict:
    cfg = LABS[clave]
    raiz: Path = cfg["raiz"]
    puerto: int = cfg["puerto"]

    if puerto in PUERTOS_RESERVADOS:
        raise RunnerError("Puerto reservado por una etapa previa")
    ruta_manifiesto = raiz / cfg["manifiesto"]
    reutilizar = ruta_manifiesto.exists()
    if not reutilizar and not _puerto_libre(puerto):
        raise RunnerError(f"El puerto {puerto} ya esta en uso")

    if raiz.exists() and any(raiz.iterdir()):
        if not (raiz / MARCADOR).exists():
            raise RunnerError(
                f"{raiz} existe sin marcador {MARCADOR}: no se toca")
    else:
        raiz.mkdir(parents=True, exist_ok=True)
        (raiz / MARCADOR).write_text(
            f"Laboratorio desechable 5BS ({clave}). Puertos previos intactos.\n",
            encoding="utf-8")

    modulo, cfg = _reapuntar(clave)
    lab = modulo.modulo_5bm() if hasattr(modulo, "modulo_5bm") else modulo
    pgdata = raiz / "pgdata"
    password_admin = _password_admin(raiz)
    password_runtime = secrets.token_urlsafe(24)
    password_admin_runtime = secrets.token_urlsafe(24)

    # Reutilizacion idempotente: si el laboratorio ya fue aprovisionado, se
    # arranca (si hace falta) y se reutiliza su manifiesto. Reaprovisionar sobre
    # una base con datos duplicaria filas de semilla; para eso existe --destruir.
    if ruta_manifiesto.exists():
        if lab.estado_cluster(pgdata) != "activo":
            lab.arrancar_cluster(pgdata)
        manifiesto = json.loads(ruta_manifiesto.read_text(encoding="utf-8"))
    else:
        if not pgdata.exists() or not any(pgdata.iterdir()):
            lab.iniciar_cluster(pgdata, password_admin)
            lab._crear_base_si_falta(password_admin)
        elif lab.estado_cluster(pgdata) != "activo":
            lab.arrancar_cluster(pgdata)

        manifiesto = modulo.provisionar(password_admin, password_runtime,
                                        password_admin_runtime)
    # Deriva del laboratorio 5BO: su runner (Etapa F) antecede a la migracion
    # 01000 (B9/5BP), que el endpoint de aceptacion de invitaciones exige. Sin
    # ella la app responde 503 fail-closed. Se aplica aqui, de forma explicita y
    # sin editar el runner historico, para que la regresion sea fiel al conjunto
    # de migraciones vigente.
    if clave == "5bo":
        extra = "20261030001000_saas_invitaciones_rate_limit_5bp.sql"
        _aplicar_migracion_psql(cfg, password_admin, extra)
        lista = list(manifiesto.get("migraciones_5bo") or [])
        if extra not in lista:
            lista.append(extra)
        manifiesto["migraciones_5bo"] = lista
        manifiesto["migracion_5bp_invitaciones"] = extra
        (raiz / cfg["manifiesto"]).write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8")
    ruta_manifiesto = raiz / cfg["manifiesto"]
    if not ruta_manifiesto.exists():
        raise RunnerError("El runner no escribio el manifiesto del laboratorio")
    return {
        "clave": clave,
        "puerto": puerto,
        "base": cfg["base"],
        "manifiesto": str(ruta_manifiesto),
        "cluster": manifiesto.get("cluster"),
        "raiz": str(raiz),
    }


def _parsear_pytest(salida: str) -> dict:
    resumen = {}
    for clave, patron in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("errors", r"(\d+) errors?"),
        ("skipped", r"(\d+) skipped"),
        ("deselected", r"(\d+) deselected"),
        ("xfailed", r"(\d+) xfailed"),
        ("xpassed", r"(\d+) xpassed"),
    ):
        m = re.search(patron, salida)
        resumen[clave] = int(m.group(1)) if m else 0
    return resumen


def ejecutar_pytest(objetivos: list[str], env_extra: dict | None = None,
                    marca: str = "run") -> dict:
    env = {**os.environ, **(env_extra or {})}
    junit = EVIDENCIAS / f"EVIDENCIA_JUNIT_5BS_{marca}_{_sello()}.xml"
    comando = [sys.executable, "-m", "pytest", *objetivos, "-q", "--no-header",
               "-p", "no:cacheprovider", "--tb=short", "-rf",
               f"--junitxml={junit}"]
    resultado = subprocess.run(comando, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env,
                               cwd=str(ROOT))
    salida = (resultado.stdout or "") + (resultado.stderr or "")
    cifras = _parsear_pytest(salida)
    return {
        "objetivos": objetivos,
        "returncode": resultado.returncode,
        "cifras": cifras,
        "junit": str(junit),
        "salida_tail": "\n".join(salida.strip().splitlines()[-25:]),
    }


# ---------------------------------------------------------------------------
# Clasificacion
# ---------------------------------------------------------------------------


def clasificar() -> dict:
    categorias: dict[str, list[str]] = {
        "OFFLINE": [], "INTEGRACION_REPRODUCIBLE": [], "INTEGRACION_PENDIENTE": [],
    }
    detalles: dict[str, dict] = {}
    for archivo in sorted(TESTS.glob("test_*.py")):
        texto = archivo.read_text(encoding="utf-8", errors="replace")
        senales = {
            "psycopg": "psycopg.connect(" in texto or "psycopg_connect" in texto,
            "uvicorn": "uvicorn" in texto,
            "manifiesto_env": bool(re.search(r"FASHIONSTORE_LAB_[A-Z0-9]+_MANIFEST", texto)),
            "puerto_fijo": bool(re.search(r":?55(4[2-8]\d)\b", texto)),
            "skip_sin_lab": "pytest.skip" in texto and "laboratorio" in texto.lower(),
            "sqlite": "sqlite" in texto.lower(),
        }
        if senales["manifiesto_env"]:
            categoria = "INTEGRACION_REPRODUCIBLE"
        elif senales["psycopg"] or senales["uvicorn"] or senales["puerto_fijo"]:
            categoria = "INTEGRACION_PENDIENTE"
        else:
            categoria = "OFFLINE"
        categorias[categoria].append(archivo.name)
        detalles[archivo.name] = senales
    return {
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_modulos": sum(len(v) for v in categorias.values()),
        "conteo": {k: len(v) for k, v in categorias.items()},
        "categorias": categorias,
        "senales": detalles,
        "laboratorios_5bs": {
            clave: {"puerto": cfg["puerto"], "base": cfg["base"],
                    "raiz": str(cfg["raiz"]),
                    "puerto_libre": _puerto_libre(cfg["puerto"])}
            for clave, cfg in LABS.items()
        },
    }


def destruir() -> dict:
    borrados = []
    for clave, cfg in LABS.items():
        raiz: Path = cfg["raiz"]
        if not raiz.exists():
            continue
        if not (raiz / MARCADOR).exists():
            continue
        try:
            modulo, _ = _reapuntar(clave)
            lab = modulo.modulo_5bm() if hasattr(modulo, "modulo_5bm") else modulo
            lab.detener_cluster(raiz / "pgdata")
        except Exception:  # noqa: BLE001 - la limpieza no debe bloquear
            pass
        shutil.rmtree(raiz, ignore_errors=True)
        borrados.append(str(raiz))
    return {"borrados": borrados}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clasificar", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--lab", choices=sorted(LABS))
    parser.add_argument("--todo", action="store_true")
    parser.add_argument("--destruir", action="store_true")
    parser.add_argument("--confirm-desechable", action="store_true")
    args = parser.parse_args(argv)
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - consola no reconfigurable
            pass

    if args.destruir:
        if not args.confirm_desechable:
            print("Falta --confirm-desechable", file=sys.stderr)
            return 2
        print(json.dumps(destruir(), indent=2, ensure_ascii=False))
        return 0

    salida: dict = {}
    if args.clasificar or args.todo:
        datos = clasificar()
        ruta = EVIDENCIAS / f"EVIDENCIA_CLASIFICACION_REGRESION_5BS_{_sello()}.json"
        ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        datos["evidencia"] = str(ruta)
        salida["clasificacion"] = {
            "conteo": datos["conteo"], "evidencia": str(ruta),
            "laboratorios_5bs": datos["laboratorios_5bs"],
        }

    if args.offline or args.todo:
        clasificacion = clasificar()
        offline = [f"tests_saas/{n}" for n in clasificacion["categorias"]["OFFLINE"]]
        salida["offline"] = ejecutar_pytest(offline, marca="offline")

    if args.todo or args.lab:
        claves = sorted(LABS) if args.todo else [args.lab]
        for clave in claves:
            if not args.confirm_desechable:
                print("Falta --confirm-desechable: --lab crea un cluster completo",
                      file=sys.stderr)
                return 2
            try:
                lab = aprovisionar(clave)
            except Exception as exc:  # noqa: BLE001
                salida[f"lab_{clave}"] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            cfg = LABS[clave]
            resultado = ejecutar_pytest(
                [cfg["modulo_test"]],
                env_extra={cfg["env_manifiesto"]: lab["manifiesto"]},
                marca=f"lab_{clave}")
            try:
                modulo, _ = _reapuntar(clave)
                labm = modulo.modulo_5bm() if hasattr(modulo, "modulo_5bm") else modulo
                labm.detener_cluster(cfg["raiz"] / "pgdata")
            except Exception:  # noqa: BLE001
                pass
            salida[f"lab_{clave}"] = {
                "cluster": lab["cluster"],
                "manifiesto": lab["manifiesto"],
                **resultado,
            }

    print(json.dumps(salida, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
