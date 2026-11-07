"""Paso 5/5 del laboratorio 5CA: apagar y destruir el clúster desechable."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_common import ESTADO, cargar_cluster  # noqa: E402


def main() -> int:
    if not ESTADO.is_file():
        print("No hay clúster de laboratorio que destruir.")
        return 0
    cluster = cargar_cluster()
    objetivo = cluster.datadir.parent
    cluster.destruir()
    estado_ok = not (cluster.datadir / "postmaster.pid").exists()
    ESTADO.unlink(missing_ok=True)
    print("clúster detenido:", estado_ok)
    print("directorio eliminado:", not objetivo.exists(), objetivo)
    return 0 if estado_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
