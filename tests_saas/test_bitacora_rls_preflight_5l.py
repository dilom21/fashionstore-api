"""B3/P5L: contrato ESTATICO para auditoria de bitacora y RLS.

Ningun caso conecta a PostgreSQL, ejecuta triggers o instala roles/politicas.
Pytest aprobado NO equivale a aislamiento multitenant probado.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "database" / "saas" / "19_auditar_bitacora_y_rls_sandbox.sql"
PS1 = ROOT / "database" / "saas" / "19_auditar_bitacora_y_rls_sandbox.ps1"
DOC = ROOT / "database" / "saas" / "27_b3_p5l_bitacora_rls_permisos_readonly.md"
GATE = ROOT / "app" / "core" / "saas_route_gate.py"


def sql():
    return SQL.read_text(encoding="utf-8")


def test_5l_transaccion_solo_lectura_con_rollback():
    s = sql()
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert "SET LOCAL statement_timeout" in s
    assert "SET LOCAL lock_timeout" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)


def test_5l_sql_sin_sentencias_que_escriben():
    s = sql()
    assert not re.search(
        r"(?im)^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|CALL|INSERT|"
        r"UPDATE|DELETE|TRUNCATE|COMMIT)\b", s
    )


def test_5l_restringido_a_sandbox_local_exacto():
    s = sql()
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s
    assert "fashionstore_restore_20261023_193554_594" not in s


def test_5l_sigue_exigiendo_46_tablas_y_48_fk_validadas():
    s = sql()
    assert "v_tablas <> 46" in s
    assert "v_fks <> 48" in s
    assert "co.convalidated" in s
    assert "co.conname LIKE 'fk_tenant_%'" in s


def test_5l_abort_si_instalaron_sp_tenant():
    s = sql()
    assert "v_sp <> 0" in s
    for n in (
        "sp_crear_reserva_tenant", "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ):
        assert n in s


def test_5l_no_permite_politicas_comerciales_nuevas_sin_revision():
    s = sql()
    assert "v_policies <> 0" in s
    assert "FROM pg_policies" in s
    assert "'detalle_reserva'" in s
    assert "'movimiento_inventario'" in s


def test_5l_verifica_todas_las_tablas_vacias_incluida_bitacora():
    s = sql()
    assert "to_regclass('public.bitacora')" in s
    assert "FOR v_rel IN" in s
    assert "c.relkind='r'" in s
    assert "SELECT count(*) FROM %I.%I" in s
    assert "IF v_filas <> 0" in s


def test_5l_describe_columnas_y_obligatoriedad_bitacora():
    s = sql()
    assert "1_BITACORA_COLUMNS" in s
    assert "information_schema.columns" in s
    for n in ("column_name", "is_nullable", "column_default", "is_identity"):
        assert n in s


def test_5l_lee_constraints_e_indices_sin_modificar():
    s = sql()
    assert "2_BITACORA_CONSTRAINTS" in s
    assert "3_BITACORA_INDEXES" in s
    assert "pg_get_constraintdef(con.oid)" in s
    assert "pg_get_indexdef(i.oid)" in s


def test_5l_bitacora_rls_y_policies_independientes_de_5j():
    s = sql()
    assert "4_AUDIT_RLS_STATUS" in s
    assert "5_AUDIT_POLICIES" in s
    assert "c.relrowsecurity" in s
    assert "c.relforcerowsecurity" in s
    assert "pol.policyname" in s
    assert "'bitacora'" in s
    assert "with_check AS with_check_sql" in s


def test_5l_privilegios_de_bitacora_incluyen_public():
    s = sql()
    assert "6_BITACORA_ACL" in s
    assert "aclexplode" in s
    assert "acldefault('r',c.relowner)" in s
    assert "a.grantee=0" in s
    assert "a.privilege_type" in s


def test_5l_audita_trigger_de_bitacora_sin_invocarlo():
    s = sql()
    assert "7_BITACORA_TRIGGERS" in s
    assert "FROM pg_trigger t" in s
    assert "NOT t.tgisinternal" in s
    assert "pg_get_triggerdef(t.oid)" in s
    assert "p.prosecdef" in s


def test_5l_inspecciona_cuerpos_de_registro_y_completar_ip():
    s = sql()
    assert "8_AUDIT_FUNCTION_BODIES" in s
    assert "pg_get_functiondef(p.oid)" in s
    assert "'fn_completar_ip_bitacora'" in s
    assert "'fn_registrar_bitacora'" in s
    assert "p.proconfig" in s


def test_5l_permisos_execute_y_roles_efectivos():
    s = sql()
    assert "9_AUDIT_FUNCTION_EXECUTE_PUBLIC" in s
    assert "acldefault('f',p.proowner)" in s
    assert "a.privilege_type='EXECUTE'" in s
    assert "10_ROLE_EFFECTIVE_PRIVILEGES" in s
    for n in ("has_database_privilege", "has_schema_privilege",
              "has_table_privilege"):
        assert n in s
    assert "r.rolbypassrls" in s


def test_5l_secuencias_y_protecciones_powershell():
    s = sql()
    p = PS1.read_text(encoding="utf-8")
    assert "11_BITACORA_SEQUENCES" in s
    assert "c.relkind='S'" in s
    assert "$servidor = '127.0.0.1'" in p
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in p
    assert "default_transaction_read_only=on" in p
    assert "Join-Path $env:TEMP" in p
    assert "Test-Path -LiteralPath $salida" in p
    assert "Read-Host" in p and "-AsSecureString" in p
    assert "ZeroFreeBSTR" in p
    assert "Remove-Item Env:PGPASSWORD" in p
    assert "8_AUDIT_FUNCTION_BODIES" in p
    assert "ROLLBACK" in p


def test_5l_documentacion_mantiene_los_bloqueos_saas():
    d = DOC.read_text(encoding="utf-8")
    g = GATE.read_text(encoding="utf-8")
    for n in ("400 passed", "NOBYPASSRLS", "WITH CHECK",
              "SAAS_AUTH_ENABLED=false", "NO crear roles"):
        assert n in d
    assert '("POST", "/tenant/reservas/prevalidar")' in g
    assert '("POST", "/tenant/reservas")' not in g
