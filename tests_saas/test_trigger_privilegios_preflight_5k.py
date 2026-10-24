"""B3/P5K: verificaciones ESTATICAS de la auditoria de triggers/roles RLS.

No usa PostgreSQL, no ejecuta las funciones; un resultado aprobado
NO significa que SECURITY DEFINER o RLS sean seguros en ejecucion real.
"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1]
SQL = BASE / "database" / "saas" / "18_auditar_cuerpos_triggers_y_privilegios_sandbox.sql"
PS1 = BASE / "database" / "saas" / "18_auditar_cuerpos_triggers_y_privilegios_sandbox.ps1"
DOC = BASE / "database" / "saas" / "26_b3_p5k_cuerpos_triggers_diseno_rls_privilegios.md"
GATE = BASE / "app" / "core" / "saas_route_gate.py"


def codigo_sql():
    return SQL.read_text(encoding="utf-8")


def test_5k_sql_comienza_readonly_y_termina_rollback():
    s = codigo_sql()
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)
    assert "SET LOCAL lock_timeout" in s
    assert "SET LOCAL statement_timeout" in s


def test_5k_sql_no_contiene_sentencias_mutacion():
    s = codigo_sql()
    assert not re.search(
        r"(?im)^\s*(?:CALL|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|"
        r"GRANT|REVOKE|TRUNCATE|COMMIT)\b", s
    )


def test_5k_sql_solo_sandbox_local():
    s = codigo_sql()
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s
    assert "fashionstore_restore_20261023_193554_594" not in s


def test_5k_preflight_46_tablas_y_48_fk():
    s = codigo_sql()
    assert "v_tablas<>46" in s
    assert "v_fks<>48" in s
    assert "co.convalidated" in s
    assert "co.conname LIKE 'fk_tenant_%'" in s


def test_5k_preflight_sin_politicas_ni_sp_tenant():
    s = codigo_sql()
    assert "v_policies<>0" in s
    assert "v_procedimientos<>0" in s
    for n in ("sp_crear_reserva_tenant", "sp_cancelar_reserva_tenant",
              "sp_finalizar_reserva_sin_compra_tenant"):
        assert n in s


def test_5k_preflight_sin_seed_central():
    s = codigo_sql()
    assert "v_filas<>0" in s
    for tabla in ("empresa","usuario","usuario_empresa","cliente",
                  "carrito","reserva","inventario"):
        assert "public." + tabla in s


def test_5k_extrae_cuerpos_reales_de_trigger_sin_ejecutar():
    s = codigo_sql()
    assert "pg_get_functiondef(p.oid)" in s
    assert "FROM pg_proc p" in s
    assert "SELECT DISTINCT t.tgfoid" in s
    assert "NOT t.tgisinternal" in s
    assert "2_FUNCTION_BODIES" in s


def test_5k_audita_security_definer_y_searchpath():
    s = codigo_sql()
    assert "p.prosecdef" in s
    assert "pg_get_userbyid(p.proowner)" in s
    assert "p.proconfig" in s
    assert "1_TRIGGER_FUNCTIONS" in s


def test_5k_audita_execute_public():
    s = codigo_sql()
    assert "3_ROUTINE_EXECUTE_PUBLIC" in s
    assert "aclexplode" in s
    assert "acldefault('f',p.proowner)" in s
    assert "acl.grantee=0" in s
    assert "acl.privilege_type='EXECUTE'" in s


def test_5k_verifica_grants_efectivos_de_roles_existentes():
    s = codigo_sql()
    assert "4_ROLES_WITH_ACCESS" in s
    for permiso in ("has_database_privilege","has_schema_privilege",
                    "has_table_privilege"):
        assert permiso in s
    assert "r.rolsuper" in s and "r.rolbypassrls" in s


def test_5k_audita_secuencias_y_funciones_auxiliares():
    s = codigo_sql()
    assert "5_ID_SEQUENCES" in s
    assert "6_SECURITY_FUNCTIONS" in s
    assert "c.relkind='S'" in s
    assert "p.proname LIKE '%bitacora%'" in s


def test_5k_ps1_solo_readonly_y_temporal_local():
    s = PS1.read_text(encoding="utf-8")
    assert "$servidor = '127.0.0.1'" in s
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "default_transaction_read_only=on" in s
    assert "Join-Path $env:TEMP" in s
    assert "Out-File -LiteralPath $salida" in s
    assert "Test-Path -LiteralPath $salida" in s


def test_5k_ps1_protege_clave_y_resultado_completo():
    s = PS1.read_text(encoding="utf-8")
    assert "Read-Host" in s and "-AsSecureString" in s
    assert "ZeroFreeBSTR" in s
    assert "Remove-Item Env:PGPASSWORD" in s
    assert "Remove-Item Env:PGOPTIONS" in s
    for marca in ("1_TRIGGER_FUNCTIONS","2_FUNCTION_BODIES",
                  "3_ROUTINE_EXECUTE_PUBLIC","ROLLBACK"):
        assert marca in s


def test_5k_guia_y_gate_conservan_reservas_bloqueadas():
    d = DOC.read_text(encoding="utf-8")
    g = GATE.read_text(encoding="utf-8")
    for clave in ("384 passed","NOBYPASSRLS","WITH CHECK",
                  "SECURITY INVOKER","SAAS_AUTH_ENABLED=false"):
        assert clave in d
    assert '("POST", "/tenant/reservas/prevalidar")' in g
    assert '("POST", "/tenant/reservas")' not in g
    assert "NO crear roles" in d
