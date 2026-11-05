"""5BS / Etapa J - Contratos OFFLINE de los artefactos de cierre.

Estas pruebas no abren conexiones, no usan PostgreSQL, no arrancan uvicorn y no
tocan Supabase. Protegen los invariantes que la Etapa J declara:

* la migracion 01200 solo revoca privilegios (no desactiva RLS, no borra);
* el validador 5BS se cierra antes de autovalidarse;
* el manifiesto 108 es determinista, incluye 01100 y 01200 y sus checksums son
  los reales;
* el manifiesto 85 (Etapa G) sigue validando porque 01200 esta clasificada;
* el catalogo de consumidores no usa red ni imprime valores;
* el runner de regresion solo usa puertos nuevos 55449-55451;
* el ensayo de respaldo exige confirmacion, cifra con AES-256 y verifica SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "supabase" / "migrations"
SAAS = ROOT / "database" / "saas"

MIGRACION_01200 = MIGRACIONES / "20261030001200_saas_acl_superficies_5bs.sql"
MIGRACION_01100 = MIGRACIONES / "20261030001100_saas_acl_plataforma_esquema_real_5br.sql"
MANIFIESTO_108 = SAAS / "108_manifiesto_migracion_esquema_real_5bs.json"
MANIFIESTO_85 = SAAS / "85_manifiesto_migracion_controlada_5bp.json"
CATALOGO = SAAS / "100_catalogo_consumidores_supabase_5bs.py"
RUNNER = SAAS / "101_runner_regresion_reproducible_5bs.py"
ENSAYO = SAAS / "102_ensayo_backup_restore_ficticio_5bs.py"
READINESS = ROOT / "tests_saas" / "_lab_readiness_5bs.py"


def _texto(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Migracion 01200
# ---------------------------------------------------------------------------


def test_01200_existe_y_es_incremental():
    assert MIGRACION_01200.exists()
    nombre = MIGRACION_01200.name
    assert nombre > MIGRACION_01100.name  # orden lexicografico == orden temporal
    texto = _texto(MIGRACION_01200)
    assert texto.strip().startswith("--")
    assert "BEGIN;" in texto and "COMMIT;" in texto


def test_01200_no_desactiva_rls_ni_borra_objetos():
    texto = _texto(MIGRACION_01200).upper()
    for prohibido in ("DISABLE ROW LEVEL SECURITY", "DROP TABLE", "DROP SCHEMA",
                      "DROP FUNCTION", "DROP TRIGGER", "TRUNCATE"):
        assert prohibido not in texto


def test_01200_revoca_superficies_no_publicas():
    texto = _texto(MIGRACION_01200)
    assert "REVOKE ALL ON SCHEMA" in texto
    assert "saas_admin" in texto and "saas_migracion" in texto
    assert "FROM PUBLIC" in texto
    assert "ALTER DEFAULT PRIVILEGES" in texto


def test_01200_cierra_el_validador_antes_de_autovalidarse():
    texto = _texto(MIGRACION_01200)
    pos_revoke = texto.index(
        "REVOKE ALL ON FUNCTION saas.validar_acl_superficies_5bs() FROM PUBLIC;")
    pos_validacion = texto.index("FROM saas.validar_acl_superficies_5bs();")
    assert pos_revoke < pos_validacion, (
        "El validador debe perder EXECUTE de PUBLIC antes de verificar; "
        "si no, se detecta a si mismo y la migracion falla cerrada.")


def test_01200_falla_cerrado():
    texto = _texto(MIGRACION_01200)
    assert "RAISE EXCEPTION '5BS ACL DE SUPERFICIES BLOQUEADO" in texto


# ---------------------------------------------------------------------------
# Manifiestos
# ---------------------------------------------------------------------------


def test_manifiesto_108_es_determinista_y_completo():
    manifiesto = json.loads(_texto(MANIFIESTO_108))
    fases = manifiesto["fases"]
    ordenes = [fase["orden"] for fase in fases]
    assert len(ordenes) == len(set(ordenes)), "orden duplicado"
    ids = [fase["id"] for fase in fases]
    assert len(ids) == len(set(ids))
    archivos = {
        archivo
        for fase in fases
        for archivo in fase.get("archivos", [])
    }
    assert "supabase/migrations/20261030001100_saas_acl_plataforma_esquema_real_5br.sql" in archivos
    assert "supabase/migrations/20261030001200_saas_acl_superficies_5bs.sql" in archivos
    assert "20261030000400_cuatro_tenants_demo_local.sql" not in str(archivos)
    # 01200 va despues de 01100 y antes de las fixtures
    por_id = {fase["id"]: fase["orden"] for fase in fases}
    assert por_id["F15-acl-plataforma-01100"] < por_id["F16-acl-superficies-01200"]
    assert por_id["F16-acl-superficies-01200"] < por_id["F14-fixtures-empresas"]


def test_manifiesto_108_checksums_reales():
    manifiesto = json.loads(_texto(MANIFIESTO_108))
    for relativa, esperado in manifiesto["checksums"].items():
        ruta = ROOT / relativa
        if not ruta.exists():
            continue
        real = hashlib.sha256(ruta.read_bytes()).hexdigest()
        assert real == esperado, f"checksum desactualizado: {relativa}"


def test_manifiesto_85_clasifica_01200_como_exclusion():
    manifiesto = json.loads(_texto(MANIFIESTO_85))
    nombres = {item["archivo"] for item in manifiesto["exclusiones"]}
    assert "supabase/migrations/20261030001200_saas_acl_superficies_5bs.sql" in nombres


# ---------------------------------------------------------------------------
# Catalogo de consumidores
# ---------------------------------------------------------------------------


def test_catalogo_no_abre_red_ni_conexiones():
    texto = _texto(CATALOGO)
    for modulo in ("import socket", "import requests", "import httpx",
                   "import psycopg", "urlopen("):
        assert modulo not in texto
    assert "no imprime valores" in texto.lower() or "no valores" in texto.lower()


def test_catalogo_clasifica_por_codigo_no_por_documentacion():
    texto = _texto(CATALOGO)
    assert "PATRONES_CLIENTE" in texto
    assert "_es_codigo_de_aplicacion" in texto
    assert "evidencia" in texto and "herramienta_interna" in texto


# ---------------------------------------------------------------------------
# Runner de regresion
# ---------------------------------------------------------------------------


def test_runner_usa_solo_puertos_nuevos():
    texto = _texto(RUNNER)
    for puerto in ("55449", "55450", "55451"):
        assert f'"puerto": {puerto}' in texto
    assert 'set(range(55432, 55449))' in texto
    assert "5432" in texto
    # Los laboratorios previos no se referencian como destino.
    assert '"fs_lab_5bm"' not in texto
    assert '"fs_lab_5br_pg17"' not in texto
    assert "fs_lab_5bs_repro_" in texto


def test_runner_exige_confirmacion_y_marcador():
    texto = _texto(RUNNER)
    assert "--confirm-desechable" in texto
    assert "DISECHABLE_5BS" in texto
    assert "if not (raiz / MARCADOR).exists()" in texto


def test_runner_corrige_deriva_01000_en_5bo():
    texto = _texto(RUNNER)
    assert "20261030001000_saas_invitaciones_rate_limit_5bp.sql" in texto
    assert 'if clave == "5bo"' in texto


# ---------------------------------------------------------------------------
# Ensayo de respaldo
# ---------------------------------------------------------------------------


def test_ensayo_respaldo_exige_confirmacion_y_no_es_schema_only():
    texto = _texto(ENSAYO)
    assert "--confirm-desechable" in texto
    assert "--schema-only" not in texto
    assert '"pg_dump", "-Fc"' in texto or '"-Fc", "-Z", "6"' in texto


def test_ensayo_respaldo_cifra_y_verifica_hash():
    texto = _texto(ENSAYO)
    assert '"-aes-256-cbc"' in texto and '"-pbkdf2"' in texto
    assert "sha256_plano" in texto and "roundtrip_descifrado_ok" in texto
    assert "passfile" in texto and '"file:{passfile}"' in texto


def test_ensayo_respaldo_declara_que_no_cierra_b2():
    texto = _texto(ENSAYO)
    assert "NO cierra B2" in texto
    assert "ficticios" in texto.lower()


# ---------------------------------------------------------------------------
# Readiness de laboratorios
# ---------------------------------------------------------------------------


def test_readiness_rechaza_puertos_ajenos_y_hosts_no_loopback():
    import importlib.util

    spec = importlib.util.spec_from_file_location("readiness_5bs", READINESS)
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)

    datos = {"cluster": {"host": "10.0.0.5", "puerto": 55449, "base": "fashionstore_lab_x"}}
    assert modulo.motivo_no_disponible(datos, 55449, "X", "cmd") is not None

    datos = {"cluster": {"host": "127.0.0.1", "puerto": 55432, "base": "fashionstore_lab_x"}}
    assert "contrato" in modulo.motivo_no_disponible(datos, 55449, "X", "cmd")

    datos = {"cluster": {"host": "127.0.0.1", "puerto": 55449, "base": "produccion"}}
    assert "descartable" in modulo.motivo_no_disponible(datos, 55449, "X", "cmd")

    assert 55449 in modulo.PUERTOS_5BS
    assert 55443 in modulo.PUERTOS_PROTEGIDOS


def test_los_tres_modulos_de_laboratorio_usan_la_guarda_explicita():
    for nombre in ("test_operativo_saas_5bm.py", "test_compras_devoluciones_5bn.py",
                   "test_nucleo_saas_5bo.py"):
        texto = _texto(ROOT / "tests_saas" / nombre)
        assert "motivo_no_disponible" in texto, nombre
        assert "pytest.skip(motivo)" in texto, nombre
        # La bateria sigue exigiendo el manifiesto: no se simula con SQLite.
        assert "MANIFIESTO.exists()" in texto, nombre
