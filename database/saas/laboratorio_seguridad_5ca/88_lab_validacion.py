"""Ejecución completa del laboratorio 5CA (una sola pasada, sin dobles).

Orden: clúster desechable -> baseline -> migraciones 010-040 -> fixtures ->
050a-c (copias sin guarda) -> correcciones -> SP -> logins -> pruebas ->
evidencia -> destrucción del clúster.
"""
from __future__ import annotations

import json
import secrets
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# El código original de FastAPI (app/) vive en la raíz del repositorio: se
# importa tal cual, sin modificarlo, para probar el parche de identidad.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import lab_install  # noqa: E402
import lab_tests  # noqa: E402
from lab_common import Cluster, Evidencia, claims_tenant, jwt_hs256, paso  # noqa: E402

RESULTADOS = Path(__file__).resolve().parent / "resultados"


def _tokens(secreto: str) -> dict[str, str]:
    def t(usuario: int, empresa: int, contexto: str) -> str:
        return jwt_hs256(secreto, claims_tenant(usuario, empresa, contexto))

    return {
        "a1": t(101, 1, "personal"),
        "a2": t(102, 1, "cliente"),
        "a3": t(103, 1, "cliente"),
        "b1": t(201, 2, "cliente"),
        "b2": t(202, 2, "personal"),
    }


def _marcdown(datos: dict) -> str:
    lineas = [
        "# Evidencia del laboratorio 5CA",
        "",
        f"- Fecha: {datos['fecha']}",
        f"- PostgreSQL: {datos['version']}",
        f"- Clúster desechable en: {datos['datadir']}",
        f"- Base: {datos['base']}",
        f"- Copias 050a-c con la guarda retirada: "
        f"{', '.join(c['copia'] for c in datos['instalacion']['copias_050'])}",
        "",
        "## Resumen estructural",
        "",
        "| Métrica | Valor |",
        "|---|---|",
    ]
    for clave, valor in datos["resumen"].items():
        lineas.append(f"| {clave} | {valor} |")
    lineas += ["", "## Pruebas", "", "| Código | Resultado | Prueba | Detalle |",
               "|---|---|---|---|"]
    for prueba in datos["pruebas"]:
        detalle = str(prueba["detalle"]).replace("|", "\\|")[:160]
        lineas.append(f"| {prueba['codigo']} | {prueba['resultado']} | "
                      f"{prueba['titulo']} | {detalle} |")
    lineas += ["", f"**Total: {datos['total']} pruebas, {datos['pass']} PASS, "
                   f"{datos['fail']} FAIL.**", ""]
    return "\n".join(lineas)


def main() -> int:
    ev = Evidencia()
    cluster = None
    datos: dict[str, object] = {}
    try:
        cluster = Cluster.crear()
        secreto = secrets.token_urlsafe(48)
        paso("instalando esquema y propuestas 050a-c")
        instalacion = lab_install.instalar(cluster, secreto)
        password = secrets.token_urlsafe(18)
        lab_install.fijar_hashes_argon2(cluster, password)
        logins = lab_install.provisionar_logins(cluster)
        resumen = lab_install.resumen_estructural(cluster)
        datos.update({
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": cluster.escalar("SHOW server_version"),
            "datadir": str(cluster.datadir.parent), "base": cluster.detalles["base"],
            "instalacion": instalacion, "resumen": resumen,
            "logins": [v["rol"] for v in logins.values()],
        })
        ctx = lab_tests.Ctx(
            dsn={"admin": cluster.dsn("postgres", cluster.passwd),
                 "api": logins["api"]["dsn"],
                 "bootstrap": logins["bootstrap"]["dsn"],
                 "platform": logins["platform"]["dsn"]},
            secreto=secreto,
            tokens=_tokens(secreto),
        )
        lab_tests.pruebas_estructura(ctx, ev, resumen)
        lab_tests.pruebas_acl(ctx, ev)
        lab_tests.pruebas_identidad(ctx, ev)
        lab_tests.pruebas_aislamiento(ctx, ev)
        lab_tests.pruebas_pool(ctx, ev)
        lab_tests.pruebas_sp(ctx, ev)
        paso("pruebas SQL/pool/SP terminadas; iniciando pruebas HTTP de FastAPI")
        try:
            import lab_tests_fastapi

            lab_tests_fastapi.pruebas_fastapi(ctx, ev, password)
        except Exception as exc:  # noqa: BLE001
            ev.registrar("FAPI-00", "Bloque FastAPI ejecutado", False,
                         f"{type(exc).__name__}: {exc}"[:400])
            traceback.print_exc()
    except Exception as exc:  # noqa: BLE001
        ev.registrar("LAB-00", "Ejecución completa del laboratorio", False,
                     f"{type(exc).__name__}: {exc}"[:400])
        traceback.print_exc()
    finally:
        resumen_final = ev.resumen()
        datos.update(resumen_final)
        RESULTADOS.mkdir(parents=True, exist_ok=True)
        (RESULTADOS / "5ca_evidencia.json").write_text(
            json.dumps(datos, indent=1, ensure_ascii=False, default=str),
            encoding="utf-8")
        if datos.get("resumen"):
            (RESULTADOS / "5ca_resultados.md").write_text(
                _marcdown(datos), encoding="utf-8")
        print(f"5CA RESULTADO: {resumen_final['pass']} PASS / "
              f"{resumen_final['fail']} FAIL de {resumen_final['total']}")
        if cluster is not None:
            cluster.destruir()
            print("clúster destruido:", not (cluster.datadir / "postmaster.pid").exists())
    return 0 if not ev.fallos else 2


if __name__ == "__main__":
    raise SystemExit(main())
