"""B3/5V: controles estáticos para diagnóstico de herramientas SOLO LECTURA.

No ejecuta PowerShell, docker, PostgreSQL, initdb ni conexiones de red.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "database/saas/26_inspeccionar_herramientas_laboratorio_5v.ps1"
DOC = ROOT / "database/saas/38_b3_p5v_entorno_win_laboratorio_independiente.md"
CONTRACT = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"
CFG = ROOT / "app/core/config.py"
GATE = ROOT / "app/core/saas_route_gate.py"


def fuente():
    return PS.read_text(encoding="utf-8")


def test_5v_encabezado_de_solo_lectura():
    s = fuente()
    assert "5V INSPECCION WINDOWS SOLO LECTURA - SIN SERVIDORES NI DDL" in s


def test_5v_docker_solo_se_localiza_no_se_invoca():
    s = fuente()
    assert "Get-Command -Name 'docker.exe' -CommandType Application" in s
    assert re.search(r"(?m)^\s*&\s*\$docker", s) is None
    assert not re.search(r"(?mi)^\s*(docker|docker\.exe)\s+(run|build|compose|start|create)\b", s)


def test_5v_postgres18_verifica_binarios_sin_ejecutarlos():
    s = fuente()
    assert "C:\\Program Files\\PostgreSQL\\18\\bin" in s
    for binario in ("initdb.exe", "pg_ctl.exe", "pg_config.exe"):
        assert f"'{binario}'" in s
    assert not re.search(r"(?m)^\s*&\s*\$(?:initdb|pgCtl|pgConfig)", s)


def test_5v_no_confunde_puerto_distinto_con_cluster_independiente():
    s = fuente()
    assert "$puertoPropuesto = 55432" in s
    assert "5V CLUSTER_INDEPENDIENTE=NO_VERIFICADO" in s
    assert "5V SYSTEM_IDENTIFIER=NO_MEDIDO" in s


def test_5v_no_ejecuta_comandos_que_muten_entorno():
    s = fuente()
    # Prohibiciones limitadas a código PS ejecutable, no comentarios.
    ejecutable = "\n".join(
        line for line in s.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    for accion in (
        "New-Item", "Remove-Item", "Set-Content", "Out-File",
        "Start-Process", "Invoke-Expression", "Invoke-Command",
        "Start-Service", "Stop-Service", "Set-ExecutionPolicy",
        "Set-Item", "Copy-Item", "Move-Item", "docker run",
        "docker compose", "initdb -", "pg_ctl start",
    ):
        assert accion not in ejecutable


def test_5v_inspeccion_sockets_sin_pedir_conexion():
    s = fuente()
    assert "Get-NetTCPConnection -State Listen -ErrorAction Stop" in s
    assert "Where-Object { $_.LocalPort -eq $puertoPropuesto }" in s
    assert "5V PUERTO_55432=" in s
    assert "Invoke-WebRequest" not in s
    assert "Test-NetConnection" not in s


def test_5v_ruta_de_datos_solo_testpath_no_creada():
    s = fuente()
    assert "C:\\SI2_Parcial1\\fashionstore-rls-lab-5v-data" in s
    assert "Test-Path -LiteralPath $rutaDatosPropuesta" in s
    assert "5V RUTA_DATOS_PROPUESTA=YA_EXISTE_NO_USAR" in s
    assert "5V RUTA_DATOS_PROPUESTA=NO_EXISTE_NO_CREADA" in s


def test_5v_no_lee_claves_ni_archivos_env():
    s = fuente()
    for peligro in ("PGPASSWORD", "DATABASE_URL", "SECRET_KEY",
                    "Get-Credential", "Read-Host", "Get-Content",
                    "ConvertFrom-SecureString", "Get-ChildItem Env:"):
        assert peligro not in "\n".join(
            line for line in s.splitlines()
            if not line.lstrip().startswith("#")
        )


def test_5v_marca_docker_como_herramienta_no_aprobacion():
    s = fuente()
    assert "DOCKER_CLI=ENCONTRADO_NO_EJECUTADO" in s
    assert "DOCKER_CLI=NO_ENCONTRADO" in s
    assert "DOCKER_CLI_REQUIERE_VERIFICACION" in s


def test_5v_marca_pg18_como_herramienta_no_instancia():
    s = fuente()
    assert "PG18_INSTANCIA_SEPARADA_REQUIERE_AUTORIZACION" in s
    assert "PG18_INITDB=ENCONTRADO_NO_EJECUTADO" in s
    assert "PG18_PG_CTL=ENCONTRADO_NO_EJECUTADO" in s


def test_5v_no_certifica_rls_ni_abre_comercio():
    s = fuente()
    assert "5V AISLAMIENTO_RLS_A_B=NO_CERTIFICADO" in s
    assert "5V SIN CAMBIOS" in s
    assert "saas_auth_enabled: bool = False" in CFG.read_text(encoding="utf-8")
    assert '("POST", "/tenant/reservas")' not in GATE.read_text(encoding="utf-8")


def test_5v_contrato_5u_continua_no_desplegable():
    import json
    m = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert m["desplegable"] is False
    assert m["iniciar_laboratorio_autorizado"] is False
    assert m["evidencia"]["resultado_rls_a_b"] == "NO_EJECUTADO"
    assert len(m["plan_casos"]) == 14


def test_5v_doc_indica_riesgos_y_autorizacion_separada():
    s = DOC.read_text(encoding="utf-8")
    for token in (
        "546 passed", "532 passed", "SAAS_AUTH_ENABLED=false",
        "NO se ha creado", "autorización explícita", "Bloque 3 4/7",
        "system_identifier", "7 bloqueantes",
    ):
        assert token in s


def test_5v_script_no_hace_sql_ni_conecta_sockets():
    s = fuente()
    code = "\n".join(
        line for line in s.splitlines()
        if not line.lstrip().startswith("#")
    )
    for palabra in (
        "psql.exe", "pg_dump", "pg_restore", "CREATE ROLE",
        "CREATE POLICY", "GRANT ", "REVOKE ", "CALL ", "New-Object Net.Sockets",
    ):
        assert palabra not in code
