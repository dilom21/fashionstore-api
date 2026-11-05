"""5BS / Etapa J - Simulacro de la puerta de staging (sin desplegar nada).

Que hace
--------
Evalua, de forma determinista y OFFLINE, si el repositorio esta listo para que un
operador humano ejecute el runbook de staging. No conecta a Supabase, Render,
Vercel ni a ninguna base: solo lee archivos del repositorio y las evidencias de la
Etapa J.

Que NO hace
-----------
* No despliega, no aplica migraciones y no habilita flags.
* No valida el comportamiento real de PostgREST ni de `service_role`.
* No puede sustituir la prueba de staging real ni el respaldo con datos reales.

Salida
------
JSON + resumen por consola con `bloqueos`, `advertencias` y dictamen
`staging` / `produccion`. Un dictamen GO de staging exige cero bloqueos.

Uso
---
    python database/saas/106_simulacro_staging_5bs.py
    python database/saas/106_simulacro_staging_5bs.py --json salida.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAAS = ROOT / "database" / "saas"
EVIDENCIAS = SAAS / "evidencias"

MANIFIESTO_108 = SAAS / "108_manifiesto_migracion_esquema_real_5bs.json"
MIGRACION_01200 = ROOT / "supabase" / "migrations" / \
    "20261030001200_saas_acl_superficies_5bs.sql"
CONFIG = ROOT / "app" / "core" / "config.py"
ENV = ROOT / ".env"

DOCS_REQUERIDOS = {
    "runbook_backup": SAAS / "103_runbook_backup_restore_retorno_5bs.md",
    "solicitud_b2": SAAS / "104_solicitud_autorizacion_datos_5bs.md",
    "runbook_staging": SAAS / "105_runbook_staging_activacion_5bs.md",
    "guia_secretos": SAAS / "91_guia_secretos_staging_5bp.md",
    "activacion_controlada": SAAS / "ACTIVACION_CONTROLADA.md",
}

FLAGS_ESPERADOS_OFF = (
    "saas_auth_enabled",
    "saas_superadmin_enabled",
    "saas_reservas_enabled",
    "saas_ventas_enabled",
    "saas_compras_enabled",
    "saas_devoluciones_enabled",
)


def _texto(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8", errors="replace")


def _checksums(manifiesto: dict) -> list[str]:
    problemas = []
    for relativa, esperado in manifiesto.get("checksums", {}).items():
        ruta = ROOT / relativa
        if not ruta.exists():
            problemas.append(f"checksum de archivo ausente: {relativa}")
            continue
        if hashlib.sha256(ruta.read_bytes()).hexdigest() != esperado:
            problemas.append(f"checksum desactualizado: {relativa}")
    return problemas


def _flags_env() -> dict:
    """Solo NOMBRES y si estan definidos; nunca valores."""
    encontrados: dict[str, bool] = {}
    if not ENV.exists():
        return encontrados
    for linea in _texto(ENV).splitlines():
        m = re.match(r"^\s*(SAAS_[A-Z0-9_]+)\s*=", linea)
        if m:
            encontrados[m.group(1)] = True
    return encontrados


def _defaults_config() -> dict:
    texto = _texto(CONFIG)
    salida = {}
    for flag in FLAGS_ESPERADOS_OFF:
        m = re.search(rf"^\s*{flag}:\s*bool\s*=\s*(True|False)", texto, re.M)
        salida[flag] = None if not m else (m.group(1) == "True")
    return salida


def _evidencias_5bs() -> dict:
    return {
        "consumidores": sorted(p.name for p in EVIDENCIAS.glob("EVIDENCIA_CONSUMIDORES_5BS_*.json")),
        "backup_ficticio": sorted(p.name for p in EVIDENCIAS.glob("EVIDENCIA_BACKUP_RESTORE_5BS_*.json")),
        "clasificacion": sorted(p.name for p in EVIDENCIAS.glob("EVIDENCIA_CLASIFICACION_REGRESION_5BS_*.json")),
    }


def evaluar() -> dict:
    bloqueos: list[str] = []
    advertencias: list[str] = []

    # 1. Manifiesto
    manifiesto = {}
    if not MANIFIESTO_108.exists():
        bloqueos.append("Falta el manifiesto 108 de la Etapa J")
    else:
        manifiesto = json.loads(_texto(MANIFIESTO_108))
        ordenes = [f["orden"] for f in manifiesto["fases"]]
        if len(ordenes) != len(set(ordenes)):
            bloqueos.append("El manifiesto 108 tiene ordenes duplicados")
        bloqueos += _checksums(manifiesto)
        ids = [f["id"] for f in manifiesto["fases"]]
        if "F16-acl-superficies-01200" not in ids:
            bloqueos.append("El manifiesto 108 no incluye la migracion 01200")
        else:
            pos_01100 = ids.index("F15-acl-plataforma-01100")
            pos_01200 = ids.index("F16-acl-superficies-01200")
            if pos_01200 < pos_01100:
                bloqueos.append("01200 se aplicaria antes de 01100")
        texto_fases = json.dumps(manifiesto, ensure_ascii=False)
        if "20261030000400_cuatro_tenants_demo_local.sql" in texto_fases:
            bloqueos.append("El manifiesto 108 incluye la semilla demo de cuatro tenants")

    # 2. Migracion incremental
    if not MIGRACION_01200.exists():
        bloqueos.append("Falta 20261030001200_saas_acl_superficies_5bs.sql")

    # 3. Flags por defecto y en .env
    defaults = _defaults_config()
    for flag, valor in defaults.items():
        if valor is not False:
            bloqueos.append(f"{flag} no esta en False por defecto en config.py")
    env_flags = _flags_env()
    activos = [nombre for nombre in env_flags if nombre.endswith("_ENABLED")]
    if activos:
        bloqueos.append(
            "El .env local declara flags SAAS_*_ENABLED: " + ", ".join(sorted(activos)))

    # 4. Documentos operativos
    docs = {}
    for clave, ruta in DOCS_REQUERIDOS.items():
        docs[clave] = ruta.exists()
        if not ruta.exists():
            bloqueos.append(f"Falta el documento requerido: {ruta.name}")

    # 5. Evidencias de la etapa
    evidencias = _evidencias_5bs()
    if not evidencias["consumidores"]:
        bloqueos.append("No existe catalogo de consumidores Supabase/PostgREST")
    if not evidencias["backup_ficticio"]:
        bloqueos.append("No existe ensayo de backup/restore con datos ficticios")

    # 6. Dependencias humanas (no automatizables)
    pendientes_humanos = [
        "B2: autorizacion explicita para respaldar DATOS REALES del proyecto Supabase "
        "(sin ella, staging solo puede usar datos sinteticos).",
        "Contrato de consumidores: confirmar por escrito que no existe ningun cliente "
        "externo (Edge Function, integracion, BI) que use la Data API con "
        "anon/authenticated/service_role.",
        "Secretos de staging: aprovisionar SAAS_TENANT_ASSERTION_KEY, "
        "SAAS_SUPERADMIN_KEY_SHA256 y SAAS_ADMIN_DATABASE_URL en un gestor externo.",
        "Proyecto de staging separado de produccion (PostgreSQL 17) y ventana de "
        "mantenimiento aprobada.",
    ]
    for pendiente in pendientes_humanos:
        advertencias.append("PENDIENTE HUMANO: " + pendiente)

    # Un GO de staging exige cero bloqueos tecnicos Y cero dependencias humanas
    # abiertas; hoy las dependencias humanas siguen abiertas por definicion.
    staging_go = False
    return {
        "etapa": "5BS / Etapa J - simulacro de puerta staging",
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alcance": "OFFLINE: no despliega, no conecta y no habilita flags",
        "manifiesto": {
            "archivo": str(MANIFIESTO_108),
            "version": manifiesto.get("version"),
            "fases": len(manifiesto.get("fases", [])),
        },
        "defaults_config": defaults,
        "flags_en_env": sorted(env_flags),
        "documentos": docs,
        "evidencias": evidencias,
        "bloqueos": bloqueos,
        "advertencias": advertencias,
        "dictamen": {
            "staging": "GO" if staging_go else "NO-GO",
            "staging_motivo": (
                "sin bloqueos tecnicos, pero con dependencias humanas abiertas"
                if not bloqueos else "existen bloqueos tecnicos"),
            "produccion": "NO-GO",
            "produccion_motivo": (
                "requiere staging real aprobado, B2 con datos reales y "
                "autorizacion de despliegue"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    datos = evaluar()
    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    ruta = Path(args.json) if args.json else EVIDENCIAS / f"EVIDENCIA_SIMULACRO_STAGING_5BS_{sello}.json"
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(json.dumps({
        "bloqueos": datos["bloqueos"],
        "advertencias": len(datos["advertencias"]),
        "dictamen": datos["dictamen"],
        "manifiesto": datos["manifiesto"],
        "evidencia": str(ruta),
    }, indent=2, ensure_ascii=False))
    return 0 if not datos["bloqueos"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
