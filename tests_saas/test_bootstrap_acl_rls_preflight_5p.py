"""B3/P5P: 14 pruebas ESTATICAS preflight bootstrap/RLS/ACL.

NO se conecta a PostgreSQL, no lee hashes ni usuarios, no instala roles,
no ejecuta rutinas comerciales ni importa la API.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "database" / "saas" / "21_auditar_bootstrap_rls_permisos_sandbox.sql"
PS1 = ROOT / "database" / "saas" / "21_auditar_bootstrap_rls_permisos_sandbox.ps1"
DOC = ROOT / "database" / "saas" / "31_b3_p5p_bootstrap_acl_rls_diseno_minimo.md"
CONFIG = ROOT / "app" / "core" / "config.py"
GATE = ROOT / "app" / "core" / "saas_route_gate.py"
AUTH = ROOT / "app" / "modules" / "autenticacion_seguridad" / "services" / "service.py"
AUTH_REPO = ROOT / "app" / "modules" / "autenticacion_seguridad" / "repositories" / "repository.py"


def sql():
    return SQL.read_text(encoding="utf-8")


def test_5p_readonly_inicio_fin_y_timeout():
    s = sql()
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert "SET LOCAL statement_timeout" in s
    assert "SET LOCAL lock_timeout" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)


def test_5p_sin_mutaciones_ni_invocacion_de_funciones_comerciales():
    s = sql()
    assert not re.search(
        r"(?im)^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|CALL|INSERT|"
        r"UPDATE|DELETE|TRUNCATE|COMMIT)\b", s
    )
    assert "pg_get_functiondef" not in s


def test_5p_solo_sandbox_local_no_restaurada():
    s = sql()
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s
    assert "fashionstore_restore_20261023_193554_594" not in s


def test_5p_guard_46_tablas_vacias_sin_mostrar_datos():
    s = sql()
    assert "v_tablas<>46" in s
    assert "FOR v_rel IN" in s
    assert "SELECT EXISTS(SELECT 1 FROM %I.%I LIMIT 1)" in s
    assert "IF v_hay_filas THEN" in s


def test_5p_guard_48_fk_tenant_validadas():
    s = sql()
    assert "v_fks<>48" in s
    assert "co.convalidated" in s
    assert "co.conname LIKE 'fk_tenant_%'" in s


def test_5p_guard_no_sp_tenant_instalados():
    s = sql()
    assert "v_sp<>0" in s
    for proc in (
        "sp_crear_reserva_tenant",
        "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ):
        assert proc in s


def test_5p_guard_no_policies_en_tablas_criticas():
    s = sql()
    assert "v_policies<>0" in s
    assert "FROM pg_policies" in s
    for tabla in ("'usuario'", "'usuario_empresa'", "'empresa'", "'bitacora'"):
        assert tabla in s


def test_5p_roles_sql_flags_no_passwords():
    s = sql()
    assert "1_SQL_ROLE_FLAGS" in s
    for atributo in (
        "r.rolcanlogin", "r.rolsuper", "r.rolbypassrls",
        "r.rolcreaterole", "r.rolcreatedb", "r.rolinherit",
    ):
        assert atributo in s
    assert "pg_authid" not in s


def test_5p_bootstrap_rls_propietarios_y_policies():
    s = sql()
    assert "2_BOOTSTRAP_RLS" in s
    assert "c.relrowsecurity" in s
    assert "c.relforcerowsecurity" in s
    assert "pg_get_userbyid(c.relowner)" in s
    assert "count(pol.policyname)" in s


def test_5p_grants_de_tablas_sin_asumir_grants_efectivos():
    s = sql()
    assert "3_AUTH_TABLE_GRANTS" in s
    assert "aclexplode(" in s
    assert "acldefault('r',c.relowner)" in s
    assert "acl.grantee=0" in s
    assert "acl.privilege_type" in s


def test_5p_revisa_privilegios_hash_sin_leer_su_contenido():
    s = sql()
    assert "4_BOOTSTRAP_COLUMN_RIGHTS" in s
    assert "5_EXPLICIT_COLUMN_ACL" in s
    assert "has_column_privilege" in s
    assert "a.attacl::text" in s
    assert "'password_hash'" in s
    assert "'correo'" in s
    assert not re.search(r"(?im)^\s*SELECT\s+.*password_hash\s+FROM\s+public\.usuario", s)


def test_5p_conectividad_rol_schema_y_rutinas_legacy():
    s = sql()
    assert "6_ROLE_CONNECT_SCHEMA" in s
    for token in ("has_database_privilege", "has_schema_privilege",
                  "has_table_privilege", "7_LEGACY_RESERVA_EXECUTE"):
        assert token in s
    assert "acldefault('f',p.proowner)" in s


def test_5p_role_membership_y_reporte_local_seguro():
    s = sql()
    p = PS1.read_text(encoding="utf-8")
    assert "8_PG_ROLE_MEMBERSHIP" in s
    assert "FROM pg_auth_members" in s
    assert "$servidor = '127.0.0.1'" in p
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in p
    assert "default_transaction_read_only=on" in p
    assert "Read-Host" in p and "-AsSecureString" in p
    assert "ZeroFreeBSTR" in p
    assert "Join-Path $env:TEMP" in p
    assert "Test-Path -LiteralPath $salida" in p
    for marker in ("1_SQL_ROLE_FLAGS", "2_BOOTSTRAP_RLS",
                   "4_BOOTSTRAP_COLUMN_RIGHTS", "ROLLBACK"):
        assert marker in p


def test_5p_documenta_modelo_de_amenaza_y_ambiente_bloqueado():
    d = DOC.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    gate = GATE.read_text(encoding="utf-8")
    auth = AUTH.read_text(encoding="utf-8")
    repo = AUTH_REPO.read_text(encoding="utf-8")
    for phrase in (
        "454 passed", "SAAS_AUTH_ENABLED=false", "NOBYPASSRLS",
        "password_hash", "SQL injection", "GUC", "CREATE ROLE",
    ):
        assert phrase in d
    assert "saas_auth_enabled: bool = False" in config
    assert '("POST", "/tenant/reservas")' not in gate
    assert "verify_password(password, usuario.password_hash)" in auth
    assert "joinedload(Usuario.rol)" in repo
