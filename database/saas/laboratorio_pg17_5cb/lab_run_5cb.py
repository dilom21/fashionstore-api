"""Ejecución completa del laboratorio 5CB en UNA sola pasada (sin dobles).

Orden: clúster desechable PG17 -> baseline -> 010-040 (040 instala
trg_bitacora_categoria y se verifica su idempotencia) -> fixtures -> Vault
sustituto -> 050a-050c SIN 060 -> SP de reservas -> logins de servicio ->
pruebas SQL/identidad/ACL/RLS/bitácora/SP/pool -> sonda del código real de
la aplicación -> evidencia en ``resultados/`` -> destrucción del clúster.

Ni el puerto 5432 ni la base de Supabase se usan en ningún momento.
"""
from __future__ import annotations

import json
import secrets
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# El código original de FastAPI (app/) vive en la raíz del repositorio: la sonda
# lo importa tal cual, sin modificarlo, para probar bitacora_trigger.py real.
sys.path.insert(0, str(HERE.parents[2]))

import lab_instalar  # noqa: E402
import lab_pruebas  # noqa: E402
from lab_comun import BASE, Cluster, Evidencia, claims_tenant, jwt_hs256, paso  # noqa: E402

RESULTADOS = HERE / "resultados"

PROPUESTAS = [
    "050a (bloqueante para el login por slug): `GRANT SELECT(id) ON public.empresa` "
    "no habilita las columnas que usa el predicado de bootstrap_empresa_id() "
    "(slug, estado) y la función SECURITY DEFINER aborta con 42501. Corrección "
    "mínima probada en BST-08: `GRANT SELECT(id, slug, estado) ON public.empresa "
    "TO fashionstore_bootstrap_owner`.",
    "Bitácora: 050c concede SELECT a fashionstore_api a propósito; la lectura "
    "queda limitada por las policies de 050b a personal del propio tenant "
    "(verificado en AIS-10). No es una fuga, pero conviene documentarlo en el "
    "contrato del endpoint.",
]


def limpiar_restos() -> list[str]:
    """Elimina clústeres de ejecuciones anteriores que quedaron huérfanos.

    Sólo borra directorios con el prefijo del laboratorio 5CB dentro de %TEMP%;
    nunca toca el clúster de otro laboratorio ni una instalación real.
    """
    import shutil
    import tempfile

    from lab_comun import DIR_PREFIX, exe, correr

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
                print("AVISO: no se pudo detener", directorio.name, flush=True)
                continue
        shutil.rmtree(directorio, ignore_errors=True)
        borrados.append(directorio.name)
    return borrados


def tokens_de(secreto: str) -> dict[str, str]:
    """Los cinco actores del laboratorio, con la forma de claims de FastAPI."""

    def token(usuario: int, empresa: int, contexto: str) -> str:
        return jwt_hs256(secreto, claims_tenant(usuario, empresa, contexto))

    return {
        "a1": token(101, 1, "personal"),   # administrador de la empresa A
        "a2": token(102, 1, "cliente"),    # cliente de A con carrito abierto
        "a3": token(103, 1, "cliente"),    # segundo cliente de A
        "b1": token(201, 2, "cliente"),    # cliente de la empresa B
        "b2": token(202, 2, "personal"),   # administrador de la empresa B
    }


def _markdown(datos: dict) -> str:
    lineas = [
        "# Evidencia del laboratorio 5CB",
        "",
        f"- Fecha: {datos['fecha']}",
        f"- PostgreSQL: {datos['version']}",
        f"- Base: {datos['base']} (clúster desechable en %TEMP%)",
        "- 060_lab_correcciones.sql: **NO aplicado** (050a-c se ensayan tal cual)",
        "- Copias 050a-c con la guarda retirada: "
        f"{', '.join(c['copia'] for c in datos['instalacion']['copias_050'])}",
        "- trg_bitacora_categoria: "
        f"antes={datos['instalacion']['trigger_categoria_antes']} "
        f"despues={datos['instalacion']['trigger_categoria_despues']} "
        f"idempotente={datos['instalacion']['trigger_categoria_idempotente']}",
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
        detalle = str(prueba["detalle"]).replace("|", "\\|")[:200]
        lineas.append(f"| {prueba['codigo']} | {prueba['resultado']} | "
                      f"{prueba['titulo']} | {detalle} |")
    lineas += ["", f"**Total: {datos['total']} pruebas, {datos['pass']} PASS, "
                   f"{datos['fail']} FAIL.**", ""]
    lineas += ["## Hallazgos y correcciones propuestas", ""]
    for fallo in datos.get("fallos", []):
        detalle = str(fallo["detalle"]).replace("\n", " ")[:300]
        lineas.append(f"- FAIL `{fallo['codigo']}` — {fallo['titulo']}: {detalle}")
    for propuesta in datos.get("propuestas", []):
        lineas.append(f"- Propuesta: {propuesta}")
    lineas.append("")
    return "\n".join(lineas)


def main() -> int:
    ev = Evidencia()
    cluster = None
    datos: dict[str, object] = {}
    try:
        print("restos de 5CB eliminados:", limpiar_restos() or "ninguno", flush=True)
        cluster = Cluster.crear(BASE)
        secreto = secrets.token_urlsafe(48)
        paso("instalando baseline, 010-040, fixtures y 050a-c (sin 060)")
        instalacion = lab_instalar.instalar(cluster, secreto)
        password = secrets.token_urlsafe(18)
        lab_instalar.fijar_hashes_argon2(cluster, password)
        logins = lab_instalar.provisionar_logins(cluster)
        resumen = lab_instalar.resumen_estructural(cluster)
        datos.update({
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": cluster.escalar("SHOW server_version"),
            "base": str(cluster.detalles["base"]),
            "instalacion": instalacion,
            "resumen": resumen,
            "logins": [v["rol"] for v in logins.values()],
        })
        ctx = lab_pruebas.Ctx(
            dsn={"admin": cluster.dsn("postgres", cluster.passwd),
                 "api": logins["api"]["dsn"],
                 "bootstrap": logins["bootstrap"]["dsn"],
                 "platform": logins["platform"]["dsn"]},
            secreto=secreto,
            tokens=tokens_de(secreto),
        )
        lab_pruebas.pruebas_estructura(ctx, ev, resumen, instalacion)
        lab_pruebas.pruebas_identidad(ctx, ev)
        lab_pruebas.pruebas_acl(ctx, ev)
        lab_pruebas.pruebas_bootstrap(ctx, ev)
        lab_pruebas.pruebas_aislamiento(ctx, ev)
        lab_pruebas.pruebas_bitacora(ctx, ev)
        lab_pruebas.pruebas_sp(ctx, ev)
        lab_pruebas.pruebas_pool(ctx, ev)
        paso("pruebas SQL terminadas; ejecutando la sonda del código real")
        lab_pruebas.pruebas_sonda(ctx, ev)
        paso("sonda terminada; ejecutando las pruebas HTTP con FastAPI real")
        import lab_http
        lab_http.pruebas_http(ctx, ev, password)
    except Exception as exc:  # noqa: BLE001 - el laboratorio registra el fallo
        ev.registrar("LAB-00", "Ejecución completa del laboratorio", False,
                     f"{type(exc).__name__}: {exc}"[:400])
        traceback.print_exc()
    finally:
        datos.update(ev.resumen())
        datos["fallos"] = ev.fallos
        datos["propuestas"] = PROPUESTAS
        RESULTADOS.mkdir(parents=True, exist_ok=True)
        (RESULTADOS / "5cb_evidencia.json").write_text(
            json.dumps(datos, indent=1, ensure_ascii=False, default=str),
            encoding="utf-8")
        if datos.get("resumen"):
            (RESULTADOS / "5cb_resultados.md").write_text(
                _markdown(datos), encoding="utf-8")
        print(f"5CB RESULTADO: {datos['pass']} PASS / {datos['fail']} FAIL "
              f"de {datos['total']}", flush=True)
        for fallo in ev.fallos:
            print(f"  FAIL {fallo['codigo']}: {fallo['detalle']}", flush=True)
        if cluster is not None:
            cluster.destruir()
            print("clúster destruido:",
                  not (cluster.datadir / "postmaster.pid").exists(), flush=True)
    return 0 if not ev.fallos else 2


if __name__ == "__main__":
    raise SystemExit(main())