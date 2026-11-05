"""5BP / Etapa G - B2: ensayo reproducible de respaldo, restauracion y cotejo.

Que demuestra (y que NO demuestra)
----------------------------------
Demuestra que un respaldo REAL en formato custom (``pg_dump -Fc``) puede
restaurarse en una base NUEVA y DESECHABLE del mismo cluster de laboratorio, y
que el resultado conserva objetos, propietarios, politicas, triggers, rutinas,
extensiones y el numero exacto de filas de cada tabla.

NO demuestra una restauracion productiva: si el origen es sintetico, el ensayo es
``VALIDACION DE HERRAMIENTA``. B2 real solo se cierra restaurando una copia
autorizada y despersonalizada del origen real.

Reglas de seguridad
-------------------
* Solo loopback y solo puertos permitidos (por defecto 55446). Los puertos de
  E1/E2/F y de las bases historicas estan prohibidos.
* El respaldo se escribe FUERA del repositorio, bajo el temporal del usuario.
* El destino de restauracion debe llamarse ``fashionstore_restore_5bp_*`` y se
  crea vacio; nunca se restaura encima de una base existente.
* Nunca se imprime la contrasena ni el DSN completo.
* Un ``pg_dump`` sin ``pg_restore`` verificado NO se considera exito.

Uso
---
    $env:FASHIONSTORE_LAB_5BP_DB_PASSWORD = '...'
    python database/saas/88_ensayo_backup_restore_5bp.py --ensayo `
        --puerto 55446 --base fashionstore_lab_pg17_5bp --confirm-desechable

    python database/saas/88_ensayo_backup_restore_5bp.py --limpiar `
        --puerto 55446 --confirm-desechable
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PUERTOS_PROHIBIDOS = {5432, 55432, 55433, 55440, 55441, 55442, 55443, 55444, 55445}
PUERTO_POR_DEFECTO = 55446
HOST = "127.0.0.1"
MARCADOR_ORIGEN = re.compile(r"(lab|desechable|staging_tmp)")
RAIZ_RESPALDOS = Path(
    os.environ.get("FASHIONSTORE_LAB_5BP_BACKUPS")
    or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Temp" / "opencode"
    / "fs_lab_5bp_backups"
)
RUTA_PG17_POR_DEFECTO = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Temp" / "opencode"
    / "fs_pg17_5bp" / "pg17" / "pgsql" / "bin"
)
VARIABLE_PASSWORD = "FASHIONSTORE_LAB_5BP_DB_PASSWORD"


class RespaldoError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def bin_pg(nombre: str) -> Path:
    candidatos = []
    if os.environ.get("FASHIONSTORE_PG17_BIN"):
        candidatos.append(Path(os.environ["FASHIONSTORE_PG17_BIN"]) / f"{nombre}.exe")
    candidatos.append(RUTA_PG17_POR_DEFECTO / f"{nombre}.exe")
    for ruta in candidatos:
        if ruta.exists():
            return ruta
    encontrado = shutil.which(nombre)
    if encontrado:
        return Path(encontrado)
    raise RespaldoError(f"No se encontro el binario {nombre} de PostgreSQL 17")


def version_binario(nombre: str = "postgres") -> str:
    salida = subprocess.run([str(bin_pg(nombre)), "--version"], capture_output=True,
                            text=True, check=True)
    return salida.stdout.strip()


def validar_destino(puerto: int, base: str) -> None:
    if puerto in PUERTOS_PROHIBIDOS:
        raise RespaldoError(
            f"Puerto {puerto} prohibido: laboratorio o base protegida.")
    if not MARCADOR_ORIGEN.search(base):
        raise RespaldoError(
            f"La base origen '{base}' no lleva marcador de descarte.")
    if base.lower() in {"postgres", "template0", "template1"}:
        raise RespaldoError("Base de sistema prohibida como origen")


def _password() -> str:
    valor = os.environ.get(VARIABLE_PASSWORD, "")
    if not valor:
        raise RespaldoError(
            f"Falta la contrasena del laboratorio. Definir {VARIABLE_PASSWORD}.")
    return valor


def conectar(base: str, puerto: int):
    import psycopg

    password = _password()
    dsn = (f"postgresql://postgres:{password}@{HOST}:{puerto}/{base}")
    return psycopg.connect(dsn)


def _ejecutar(args: list[str], *, base: str) -> subprocess.CompletedProcess:
    entorno = dict(os.environ)
    entorno["PGPASSWORD"] = _password()
    salida = subprocess.run(args, capture_output=True, text=True, env=entorno)
    return salida


def inventario(base: str, puerto: int) -> dict:
    """Inventario deterministico: estructura + propietarios + filas exactas."""
    with conectar(base, puerto) as conn:
        tablas = conn.execute(
            """
            SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner)
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'r' AND n.nspname IN ('public','saas','saas_admin')
             ORDER BY 1, 2
            """
        ).fetchall()
        triggers = conn.execute(
            """
            SELECT n.nspname || '.' || c.relname || '.' || t.tgname
              FROM pg_trigger t
              JOIN pg_class c ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE NOT t.tgisinternal AND n.nspname IN ('public','saas','saas_admin')
             ORDER BY 1
            """
        ).fetchall()
        rutinas = conn.execute(
            """
            SELECT n.nspname || '.' || p.proname || '(' ||
                   pg_get_function_identity_arguments(p.oid) || ')',
                   p.prosecdef, pg_get_userbyid(p.proowner),
                   COALESCE(array_to_string(p.proconfig, ','), '<sin>')
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('public','saas','saas_admin')
             ORDER BY 1
            """
        ).fetchall()
        politicas = conn.execute(
            """
            SELECT schemaname || '.' || tablename || '.' || policyname
              FROM pg_policies
             WHERE schemaname IN ('public','saas','saas_admin')
             ORDER BY 1
            """
        ).fetchall()
        secuencias = conn.execute(
            """
            SELECT n.nspname || '.' || c.relname
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'S' AND n.nspname IN ('public','saas','saas_admin')
             ORDER BY 1
            """
        ).fetchall()
        extensiones = conn.execute(
            "SELECT extname || ':' || extversion FROM pg_extension ORDER BY 1"
        ).fetchall()

        filas: dict[str, int] = {}
        for schema, tabla, _duenio in tablas:
            # Solo se cuentan filas: nunca se extrae contenido.
            cantidad = conn.execute(
                f'SELECT count(*) FROM "{schema}"."{tabla}"').fetchone()[0]
            filas[f"{schema}.{tabla}"] = int(cantidad)

        version = conn.execute("SHOW server_version").fetchone()[0]

    return {
        "version_servidor": version,
        "tablas": [f"{s}.{t}" for s, t, _ in tablas],
        "propietarios_tablas": {f"{s}.{t}": d for s, t, d in tablas},
        "triggers": [f[0] for f in triggers],
        "rutinas": [
            {"firma": f[0], "security_definer": bool(f[1]), "propietario": f[2],
             "search_path": f[3]}
            for f in rutinas
        ],
        "politicas": [f[0] for f in politicas],
        "secuencias": [f[0] for f in secuencias],
        "extensiones": [f[0] for f in extensiones],
        "filas": filas,
        "totales": {
            "tablas": len(tablas), "triggers": len(triggers),
            "rutinas": len(rutinas), "politicas": len(politicas),
            "secuencias": len(secuencias), "extensiones": len(extensiones),
            "filas": sum(filas.values()),
        },
    }


def huella_inventario(datos: dict) -> str:
    comparable = {
        "tablas": datos["tablas"],
        "triggers": datos["triggers"],
        "rutinas": datos["rutinas"],
        "politicas": datos["politicas"],
        "secuencias": datos["secuencias"],
        "extensiones": datos["extensiones"],
        "filas": datos["filas"],
    }
    texto = json.dumps(comparable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Ensayo
# ---------------------------------------------------------------------------


def _sello() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _crear_base(base: str, puerto: int) -> None:
    with conectar("postgres", puerto) as conn:
        conn.autocommit = True
        existe = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone()
        if existe:
            raise RespaldoError(
                f"La base destino {base} ya existe: no se restaura encima.")
        conn.execute(f'CREATE DATABASE "{base}"')


def _eliminar_base(base: str, puerto: int) -> None:
    with conectar("postgres", puerto) as conn:
        conn.autocommit = True
        conn.execute(
            f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')


def ensayo(base_origen: str, puerto: int) -> dict:
    """Respalda, restaura en base nueva y coteja. Devuelve evidencia."""
    validar_destino(puerto, base_origen)
    version_17 = version_binario("postgres")
    if not version_17.startswith("postgres (PostgreSQL) 17"):
        raise RespaldoError(
            f"El binario pg_dump/pg_restore no es PostgreSQL 17: {version_17}")

    RAIZ_RESPALDOS.mkdir(parents=True, exist_ok=True)
    sello = _sello()
    archivo = RAIZ_RESPALDOS / f"fs_lab_5bp_{base_origen}_{sello}.dump"
    base_restore = f"fashionstore_restore_5bp_{sello}"

    pasos: list[dict] = []
    inicio_total = time.monotonic()

    # 1) Inventario de origen (antes del respaldo).
    t0 = time.monotonic()
    antes = inventario(base_origen, puerto)
    pasos.append({"paso": "inventario_origen",
                  "ms": int((time.monotonic() - t0) * 1000),
                  "totales": antes["totales"]})

    # 2) pg_dump -Fc
    t0 = time.monotonic()
    resultado = _ejecutar([
        str(bin_pg("pg_dump")), "-Fc", "-h", HOST, "-p", str(puerto),
        "-U", "postgres", "-d", base_origen, "-f", str(archivo),
    ], base=base_origen)
    if resultado.returncode != 0:
        raise RespaldoError(
            f"pg_dump fallo: {resultado.stderr.strip()[:400]}")
    sha = hashlib.sha256(archivo.read_bytes()).hexdigest()
    pasos.append({"paso": "pg_dump", "ms": int((time.monotonic() - t0) * 1000),
                  "archivo": str(archivo), "bytes": archivo.stat().st_size,
                  "sha256": sha})

    # 3) Lista del archivo (prueba de que el dump es legible).
    t0 = time.monotonic()
    lista = _ejecutar([str(bin_pg("pg_restore")), "-l", str(archivo)],
                      base=base_origen)
    if lista.returncode != 0:
        raise RespaldoError(
            f"pg_restore -l fallo: {lista.stderr.strip()[:400]}")
    entradas = [linea for linea in lista.stdout.splitlines()
                if linea and not linea.startswith(";")]
    pasos.append({"paso": "pg_restore_list", "ms": int((time.monotonic() - t0) * 1000),
                  "entradas": len(entradas)})

    # 4) Restauracion en base NUEVA (CON propietarios y ACL: son evidencia).
    _crear_base(base_restore, puerto)
    t0 = time.monotonic()
    restaurado = _ejecutar([
        str(bin_pg("pg_restore")), "--exit-on-error",
        "-h", HOST, "-p", str(puerto), "-U", "postgres", "-d", base_restore,
        str(archivo),
    ], base=base_restore)
    ms_restore = int((time.monotonic() - t0) * 1000)
    if restaurado.returncode != 0:
        _eliminar_base(base_restore, puerto)
        raise RespaldoError(
            f"pg_restore fallo: {restaurado.stderr.strip()[:500]}")
    pasos.append({"paso": "pg_restore", "ms": ms_restore, "base": base_restore})

    # 5) Cotejo estructural y de filas.
    t0 = time.monotonic()
    despues = inventario(base_restore, puerto)
    diferencias = _diferencias(antes, despues)
    pasos.append({"paso": "cotejo", "ms": int((time.monotonic() - t0) * 1000),
                  "diferencias": len(diferencias)})

    # 6) Limpieza del destino desechable (el dump se conserva).
    _eliminar_base(base_restore, puerto)
    pasos.append({"paso": "limpieza", "base_eliminada": base_restore})

    total_ms = int((time.monotonic() - inicio_total) * 1000)
    return {
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clase": "VALIDACION DE HERRAMIENTA (datos sinteticos de laboratorio)",
        "origen": {"base": base_origen, "host": HOST, "puerto": puerto,
                   "version_servidor": antes["version_servidor"]},
        "binarios": version_17,
        "respaldo": {"archivo": str(archivo), "bytes": archivo.stat().st_size,
                     "sha256": sha, "entradas_pg_restore": len(entradas)},
        "restauracion": {"base_temporal": base_restore,
                         "ms": ms_restore, "total_ms": total_ms},
        "inventario_origen": antes["totales"],
        "inventario_restaurado": despues["totales"],
        "huella_inventario_origen": huella_inventario(antes),
        "huella_inventario_restaurado": huella_inventario(despues),
        "diferencias": diferencias,
        "ok": not diferencias,
        "pasos": pasos,
        "nota_produccion": (
            "Este ensayo NO cierra B2 real: el origen es sintetico. Cierra la "
            "VALIDACION DE LA HERRAMIENTA de respaldo/restauracion."
        ),
    }


def _diferencias(antes: dict, despues: dict) -> list[dict]:
    diferencias: list[dict] = []
    for clave in ("tablas", "triggers", "politicas", "secuencias", "extensiones"):
        faltan = sorted(set(antes[clave]) - set(despues[clave]))
        sobran = sorted(set(despues[clave]) - set(antes[clave]))
        if faltan:
            diferencias.append({"conjunto": clave, "tipo": "faltan",
                                "elementos": faltan})
        if sobran:
            diferencias.append({"conjunto": clave, "tipo": "sobran",
                                "elementos": sobran})

    firmas_antes = {r["firma"]: r for r in antes["rutinas"]}
    firmas_despues = {r["firma"]: r for r in despues["rutinas"]}
    for firma in sorted(set(firmas_antes) - set(firmas_despues)):
        diferencias.append({"conjunto": "rutinas", "tipo": "faltan",
                            "elementos": [firma]})
    for firma in sorted(set(firmas_despues) - set(firmas_antes)):
        diferencias.append({"conjunto": "rutinas", "tipo": "sobran",
                            "elementos": [firma]})
    for firma in sorted(set(firmas_antes) & set(firmas_despues)):
        a, b = firmas_antes[firma], firmas_despues[firma]
        for atributo in ("security_definer", "propietario", "search_path"):
            if a[atributo] != b[atributo]:
                diferencias.append({
                    "conjunto": "rutinas", "tipo": "atributo_distinto",
                    "elementos": [firma], "atributo": atributo,
                    "origen": a[atributo], "restaurado": b[atributo]})

    for tabla, cantidad in antes["filas"].items():
        if despues["filas"].get(tabla) != cantidad:
            diferencias.append({
                "conjunto": "filas", "tipo": "conteo_distinto",
                "tabla": tabla, "origen": cantidad,
                "restaurado": despues["filas"].get(tabla)})

    for tabla, duenio in antes["propietarios_tablas"].items():
        if despues["propietarios_tablas"].get(tabla) != duenio:
            diferencias.append({
                "conjunto": "propietarios", "tipo": "propietario_distinto",
                "tabla": tabla, "origen": duenio,
                "restaurado": despues["propietarios_tablas"].get(tabla)})
    return diferencias


def limpiar_restauraciones(puerto: int) -> list[str]:
    if puerto in PUERTOS_PROHIBIDOS:
        raise RespaldoError(f"Puerto {puerto} prohibido")
    eliminadas: list[str] = []
    with conectar("postgres", puerto) as conn:
        conn.autocommit = True
        filas = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'fashionstore_restore_5bp_%'"
        ).fetchall()
        for (nombre,) in filas:
            if not re.fullmatch(r"fashionstore_restore_5bp_\d{8}_\d{6}", nombre):
                continue
            conn.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
            eliminadas.append(nombre)
    return eliminadas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5BP: ensayo de respaldo y restauracion en laboratorio desechable")
    parser.add_argument("--ensayo", action="store_true")
    parser.add_argument("--limpiar", action="store_true")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--base", default="fashionstore_lab_pg17_5bp")
    parser.add_argument("--confirm-desechable", action="store_true")
    parser.add_argument("--salida", default=None,
                        help="ruta del JSON de evidencia")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not args.confirm_desechable:
            raise RespaldoError(
                "Falta --confirm-desechable: el ensayo crea y elimina bases.")
        if args.limpiar:
            eliminadas = limpiar_restauraciones(args.puerto)
            print(json.dumps({"eliminadas": eliminadas}, ensure_ascii=False))
            return 0
        if not args.ensayo:
            parser.print_help()
            return 2

        evidencia = ensayo(args.base, args.puerto)
        texto = json.dumps(evidencia, indent=2, ensure_ascii=False)
        if args.salida:
            ruta = Path(args.salida)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(texto + "\n", encoding="utf-8")
            print(f"Evidencia escrita: {ruta}")
        if args.json:
            print(texto)
        else:
            print(f"Servidor         : PostgreSQL {evidencia['origen']['version_servidor']}")
            print(f"Respaldo         : {evidencia['respaldo']['bytes']} bytes")
            print(f"SHA-256          : {evidencia['respaldo']['sha256']}")
            print(f"Entradas restore : {evidencia['respaldo']['entradas_pg_restore']}")
            print(f"Restauracion     : {evidencia['restauracion']['ms']} ms en base temporal")
            print(f"Inventario origen: {evidencia['inventario_origen']}")
            print(f"Inventario rest. : {evidencia['inventario_restaurado']}")
            print(f"Diferencias      : {len(evidencia['diferencias'])}")
            print(f"Resultado        : {'OK' if evidencia['ok'] else 'FALLIDO'}")
        return 0 if evidencia["ok"] else 1

    except RespaldoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
