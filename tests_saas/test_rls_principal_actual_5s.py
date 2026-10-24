"""B3/P5S: contratos estaticos de auditoria RLS REAL, solo en sandbox.

No importa FastAPI, no crea Session, no conecta PostgreSQL y no ejecuta SQL.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "database/saas/24_auditar_rls_efectivo_sandbox_5s.sql"
PS = ROOT / "database/saas/24_auditar_rls_efectivo_sandbox_5s.ps1"
DOC = ROOT / "database/saas/35_b3_p5s_fotografia_rls_efectivo_y_plan_roles.md"
GATE = ROOT / "app/core/saas_route_gate.py"
CFG = ROOT / "app/core/config.py"


def codigo_sql():
    return SQL.read_text(encoding="utf-8")


def test_5s_solo_transaccion_lectura_y_rollback():
    s = codigo_sql()
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert "SET LOCAL row_security = on;" in s
    assert "SET LOCAL statement_timeout" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)


def test_5s_no_crea_roles_datos_policies_ni_call():
    s = codigo_sql()
    assert not re.search(
        r"(?im)^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|CALL|"
        r"INSERT|UPDATE|DELETE|TRUNCATE|COMMIT|SET\s+ROLE)\b", s
    )


def test_5s_solo_sandbox_local_con_host_preciso():
    s = codigo_sql()
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s
    assert "fashionstore_restore_20261023_193554_594" not in s


def test_5s_no_hace_set_role_y_verifica_usuario_actual():
    s = codigo_sql()
    assert "session_user <> 'postgres'" in s
    assert "current_user <> 'postgres'" in s
    assert "session_user AS sesion" in s
    assert "current_user AS principal_sql" in s
    assert "SET ROLE postgres" not in s


def test_5s_conserva_46_tablas_vacias():
    s = codigo_sql()
    assert "v_tablas <> 46" in s
    assert "FOR v_rel IN" in s
    assert "SELECT EXISTS(SELECT 1 FROM %I.%I LIMIT 1)" in s
    assert "IF v_hay_filas THEN" in s


def test_5s_fk_compuestas_validadas():
    s = codigo_sql()
    assert "v_fks <> 48" in s
    assert "co.convalidated" in s
    assert "co.conname LIKE 'fk_tenant_%'" in s


def test_5s_exige_ausencia_de_sp_nuevos_y_policies_criticas():
    s = codigo_sql()
    assert "v_sp <> 0" in s
    assert "v_policies <> 0" in s
    assert "FROM pg_policies" in s
    for n in ("sp_crear_reserva_tenant", "sp_cancelar_reserva_tenant",
              "sp_finalizar_reserva_sin_compra_tenant"):
        assert n in s


def test_5s_distingue_flag_rls_de_aplicacion_real():
    s = codigo_sql()
    assert "2_RLS_EFECTIVO_PRINCIPAL_ACTUAL" in s
    assert "row_security_active(c.oid)" in s
    assert "c.relrowsecurity" in s
    assert "c.relforcerowsecurity" in s
    assert "numero_policies" in s


def test_5s_registra_atributos_de_principal_y_dictamen_no_certificado():
    s = codigo_sql()
    assert "1_SQL_PRINCIPAL_ACTUAL" in s
    assert "3_DICTAMEN_ALCANCE" in s
    assert "r.rolsuper" in s and "r.rolbypassrls" in s
    assert "NO_CERTIFICADO: cuenta actual con SUPERUSER/BYPASSRLS" in s
    assert "row_security_active('public.usuario'::regclass)" in s


def test_5s_powershell_usa_pg_local_y_archivo_temp_sin_sobrescribir():
    p = PS.read_text(encoding="utf-8")
    assert "$servidor = '127.0.0.1'" in p
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in p
    assert "default_transaction_read_only=on" in p
    assert "Join-Path $env:TEMP" in p
    assert "Test-Path -LiteralPath $salida" in p
    assert "Read-Host" in p and "-AsSecureString" in p
    assert "ZeroFreeBSTR" in p
    assert "Remove-Item Env:PGPASSWORD" in p


def test_5s_powershell_verifica_secciones_y_rollback():
    p = PS.read_text(encoding="utf-8")
    for marker in ("1_SQL_PRINCIPAL_ACTUAL", "2_RLS_EFECTIVO_PRINCIPAL_ACTUAL",
                   "3_DICTAMEN_ALCANCE", "ROLLBACK"):
        assert marker in p
    assert "--no-psqlrc" in p
    assert "--set=ON_ERROR_STOP=1" in p
    assert "AUDITORIA 5S SANDBOX READ ONLY COMPLETADA" in p


def test_5s_documenta_limites_y_gate_sigue_cerrado():
    d = DOC.read_text(encoding="utf-8")
    for frase in ("498 passed", "SAAS_AUTH_ENABLED=false", "BYPASSRLS",
                  "row_security_active()", "no acredita aislamiento",
                  "NO crear roles", "Bloque 3 4/7"):
        assert frase in d
    assert "saas_auth_enabled: bool = False" in CFG.read_text(encoding="utf-8")
    assert '("POST", "/tenant/reservas")' not in GATE.read_text(encoding="utf-8")
