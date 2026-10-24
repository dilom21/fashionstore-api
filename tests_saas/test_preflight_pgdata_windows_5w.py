"""B3/5W: 14 tests ESTATICOS del preflight Windows READ ONLY.

No ejecuta PowerShell, PostgreSQL, SQL, Docker ni consulta servicios reales.
"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "database/saas/27_preflight_directorio_pg18_laboratorio_5w.ps1"
DOC = ROOT / "database/saas/39_b3_p5w_preflight_pgdata_y_servicios_windows.md"
MANIFEST = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"
CFG = ROOT / "app/core/config.py"
GATE = ROOT / "app/core/saas_route_gate.py"


def contenido():
    return PS.read_text(encoding="utf-8")


def instrucciones():
    return "\n".join(
        line for line in contenido().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_5w_powershell_readonly_es_explicito():
    assert "5W PREFLIGHT WIN READ ONLY - NO CREA PGDATA NI INICIA POSTGRES" in contenido()


def test_5w_base_de_ruta_candidata_es_solo_propuesta():
    s = contenido()
    assert r"C:\SI2_Parcial1\fashionstore-rls-lab-5v-data" in s
    assert "Test-Path -LiteralPath $rutaPropuesta" in s
    assert "5W PGDATA_PROPUESTO=EXISTE_BLOQUEAR" in s
    assert "5W PGDATA_PROPUESTO=NO_EXISTE_NO_CREADO" in s


def test_5w_padre_debe_existir_previamente():
    s = contenido()
    assert "Test-Path -LiteralPath $rutaPadre -PathType Container" in s
    assert "5W PADRE_PGDATA=NO_EXISTE_BLOQUEAR" in s
    assert "5W PADRE_PGDATA=EXISTE" in s


def test_5w_rechaza_junctions_en_ancestros():
    s = contenido()
    assert "Get-Item -LiteralPath $rutaPadre -Force" in s
    assert "[IO.FileAttributes]::ReparsePoint" in s
    assert "while ($null -ne $actual)" in s
    assert "5W ANCESTROS_REPARSE=DETECTADO_BLOQUEAR" in s
    assert "5W ANCESTROS_REPARSE=NINGUNO_OBSERVADO" in s


def test_5w_rechaza_solapamiento_de_rutas():
    s = contenido()
    assert "[IO.Path]::GetFullPath(" in s
    assert "[StringComparison]::OrdinalIgnoreCase" in s
    assert "5W RUTA_PROYECTO_BINARIOS=SOLAPAMIENTO_BLOQUEAR" in s
    assert "5W RUTA_PROYECTO_BINARIOS=SIN_SOLAPAMIENTO_TEXTUAL" in s


def test_5w_consulta_servicios_sin_arrancar():
    s = contenido()
    assert "Get-Command -Name 'Get-CimInstance'" in s
    assert "Get-CimInstance -ClassName Win32_Service -ErrorAction Stop" in s
    assert "5W SERVICIOS_POSTGRES=CONTADOS_" in s
    assert "5W SERVICIOS_POSTGRES=NO_CONSULTADOS" in s
    assert "5W SERVICIOS_POSTGRES=NO_VERIFICABLE" in s


def test_5w_no_muestra_rutas_de_servicios():
    s = instrucciones()
    assert "$_.PathName -match" in s
    assert not re.search(r"(?mi)^\s*Write-(?:Host|Output)[^\r\n]*PathName", s)
    assert "Get-Content" not in s


def test_5w_no_lee_pgdata_de_5432():
    s = contenido()
    assert "5W PGDATA_CLUSTER_5432=NO_VERIFICADO" in s
    assert "5W CLUSTER_NATIVO_INDEPENDIENTE=NO_CERTIFICADO" in s
    assert "fashionstore_restore_20261023_193554_594" not in instrucciones()


def test_5w_no_modifica_archivos_ni_servicios():
    s = instrucciones()
    for cmd in (
        "New-Item", "Remove-Item", "Set-Content", "Out-File",
        "Start-Process", "Start-Service", "Stop-Service", "Set-Service",
        "New-Service", "Invoke-Expression", "Invoke-Command", "Copy-Item",
        "Move-Item", "Set-Item", "Test-NetConnection",
    ):
        assert not re.search(r"(?mi)^\s*" + re.escape(cmd) + r"\b", s)


def test_5w_no_invoca_pg_docker_sql():
    s = instrucciones()
    assert not re.search(r"(?mi)^\s*&\s*", s)
    assert not re.search(
        r"(?mi)^\s*(?:initdb|pg_ctl|postgres|docker|psql|pg_dump)\s+", s
    )
    assert not re.search(r"(?mi)^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|CALL)\s+", s)


def test_5w_siempre_indica_no_certificado():
    s = contenido()
    for marker in (
        "5W DICTAMEN=BLOQUEADO_SIN_AUTORIZACION_EXPLICITA",
        "5W PGDATA_CLUSTER_5432=NO_VERIFICADO",
        "5W SYSTEM_IDENTIFIER_ORIGEN_DESTINO=NO_MEDIDOS",
        "5W CLUSTER_NATIVO_INDEPENDIENTE=NO_CERTIFICADO",
        "5W SIN CAMBIOS:",
    ):
        assert marker in s


def test_5w_manifiesto_5u_sigue_bloqueado():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["estado"] == "BORRADOR_NO_EJECUTAR"
    assert m["desplegable"] is False
    assert m["iniciar_laboratorio_autorizado"] is False
    assert m["evidencia"]["resultado_rls_a_b"] == "NO_EJECUTADO"
    assert m["baseline"]["bloqueantes_abiertos"] == 7


def test_5w_flag_saas_y_reservas_cerrados():
    assert "saas_auth_enabled: bool = False" in CFG.read_text(encoding="utf-8")
    assert '("POST", "/tenant/reservas")' not in GATE.read_text(encoding="utf-8")


def test_5w_documentacion_coherente():
    s = DOC.read_text(encoding="utf-8")
    for token in (
        "560 passed", "546 passed", "SAAS_AUTH_ENABLED=false",
        "7 bloqueantes", "Bloque 3 4/7", "autorización explícita",
        "system_identifier", "NO_EJECUTADO",
        "27_preflight_directorio_pg18_laboratorio_5w.ps1",
    ):
        assert token in s
