"""Paso 1/5 del laboratorio 5CA: crear el clúster desechable y dejarlo vivo.

Guarda en %TEMP%\\fashionstore-5ca-state.json el puerto y la contraseña aleatoria
del superusuario del clúster efímero para que los pasos siguientes se conecten.
Ese archivo vive sólo en el directorio temporal del usuario, no se versiona y lo
elimina 99_lab_destruir.py junto con el clúster completo.

No toca :5432, no toca bases existentes, no lee el .env de Supabase.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_common import DIR_PREFIX, Cluster, exe, correr  # noqa: E402

ESTADO = Path(tempfile.gettempdir()) / "fashionstore-5ca-state.json"
BASE = "fashionstore_lab_5ca"


def limpiar_restos() -> list[str]:
    """Elimina clústeres de ejecuciones anteriores que quedaron huérfanos."""
    borrados = []
    for directorio in Path(tempfile.gettempdir()).glob(DIR_PREFIX + "*"):
        if not directorio.is_dir():
            continue
        datos = directorio / "data"
        if (datos / "postmaster.pid").exists():
            try:
                correr([exe("pg_ctl"), "-D", str(datos), "-m", "immediate",
                        "-w", "-t", "30", "stop"], timeout=60, server_control=True)
            except Exception:  # noqa: BLE001 - si no se puede parar, se informa
                print("AVISO: no se pudo detener", directorio.name)
                continue
        if directorio.name.startswith(DIR_PREFIX):
            shutil.rmtree(directorio, ignore_errors=True)
            borrados.append(directorio.name)
    return borrados


def main() -> int:
    if ESTADO.exists():
        print("Ya existe un estado de laboratorio:", ESTADO)
        datos = json.loads(ESTADO.read_text(encoding="utf-8"))
        print("datadir:", datos["datadir"], "puerto:", datos["puerto"])
        print("Ejecuta 99_lab_destruir.py antes de crear otro clúster.")
        return 1
    print("restos eliminados:", limpiar_restos() or "ninguno")
    cluster = Cluster.crear(BASE)
    estado = {
        "datadir": str(cluster.datadir),
        "port": cluster.port,
        "passwd": cluster.passwd,
        "base": BASE,
        "logfile": str(cluster.logfile),
    }
    ESTADO.write_text(json.dumps(estado), encoding="utf-8")
    try:
        os.chmod(ESTADO, 0o600)
    except OSError:
        pass
    print("clúster listo en", cluster.datadir)
    print("puerto:", cluster.port, "| versión:", cluster.escalar("SHOW server_version"))
    print("base:", BASE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
