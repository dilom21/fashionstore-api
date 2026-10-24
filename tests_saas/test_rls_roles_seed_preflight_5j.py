"""Bloque 3 / Paso 5J: protecciones ESTATICAS del preflight RLS local.

No conecta a PostgreSQL, no ejecuta DDL ni crea roles o usuarios.
Solo valida que scripts de AUDITORIA consulten el sandbox correcto.
"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1] / "database" / "saas"
SQL = BASE / "17_auditar_roles_rls_y_seed_sandbox_local.sql"
PS1 = BASE / "17_auditar_roles_rls_y_seed_sandbox_local.ps1"
DOC = BASE / "25_b3_p5j_preflight_rls_y_seed_ficticio.md"


def test_5j_sql_solo_read_only_rollback():
    s = SQL.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION READ ONLY;" in s
    assert re.search(r"(?m)^ROLLBACK;$", s)
    assert not re.search(
        r"(?im)^\s*(CREATE|CALL|GRANT|REVOKE|ALTER|DROP|"
        r"INSERT|UPDATE|DELETE|TRUNCATE|COMMIT)\b", s
    )


def test_5j_sql_solo_sandbox_local_exacto():
    s = SQL.read_text(encoding="utf-8")
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in s


def test_5j_guarda_46_tablas_48_fk_y_ningun_sp_nuevo():
    s = SQL.read_text(encoding="utf-8")
    assert "v_tablas <> 46" in s
    assert "v_fks <> 48" in s
    assert "v_sp <> 0" in s
    for nombre in (
        "sp_crear_reserva_tenant",
        "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ):
        assert nombre in s


def test_5j_guarda_sin_seed_previo():
    s = SQL.read_text(encoding="utf-8")
    assert "v_filas <> 0" in s
    for tabla in ("empresa", "usuario", "usuario_empresa", "cliente",
                  "carrito", "reserva", "inventario"):
        assert "public." + tabla in s


def test_5j_rol_superuser_y_bypass_rls_visibles():
    s = SQL.read_text(encoding="utf-8")
    assert "rolsuper" in s
    assert "rolbypassrls" in s
    assert "rolcreaterole" in s
    assert "rolcreatedb" in s
    assert "session_user" in s
    assert "current_user" in s


def test_5j_no_usa_rol_preexistente_en_cluster_sin_inspeccion():
    s = SQL.read_text(encoding="utf-8")
    assert "FROM pg_roles WHERE rolname LIKE 'fashionstore_5j_%'" in s
    assert "CREATE ROLE" in s  # solo comentario educativo, no DDL
    assert not re.search(r"(?im)^\s*CREATE\s+ROLE", s)


def test_5j_politicas_rls_en_tenant_tablas():
    s = SQL.read_text(encoding="utf-8")
    assert "FROM pg_policies" in s
    assert "LEFT JOIN pg_policies" in s
    assert "relrowsecurity" in s
    assert "relforcerowsecurity" in s
    assert "using_sql" in s
    assert "with_check_sql" in s


def test_5j_grants_public_se_inspeccionan_sin_otorgar():
    s = SQL.read_text(encoding="utf-8")
    assert "aclexplode" in s
    assert "acldefault" in s
    assert "acl.grantee=0" in s
    assert "<NINGUNO>" in s
    assert "5_PUBLIC_GRANTS" in s


def test_5j_columnas_y_constraints_para_seed():
    s = SQL.read_text(encoding="utf-8")
    assert "information_schema.columns" in s
    assert "jsonb_agg" in s
    assert "is_nullable='NO'" in s
    assert "column_default" in s
    assert "is_identity" in s
    assert "pg_get_constraintdef(co.oid)" in s


def test_5j_revisa_triggers_sin_ejecutar():
    s = SQL.read_text(encoding="utf-8")
    assert "FROM pg_trigger t" in s
    assert "NOT t.tgisinternal" in s
    assert "pg_get_triggerdef(t.oid)" in s


def test_5j_ps1_usando_local_y_output_fuera_de_git():
    s = PS1.read_text(encoding="utf-8")
    assert "$servidor = '127.0.0.1'" in s
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "Join-Path $env:TEMP" in s
    assert "Out-File -LiteralPath $salida" in s
    assert "Test-Path -LiteralPath $salida" in s
    assert "no subir a GitHub" in s


def test_5j_ps1_password_temporal_y_read_only_forzado():
    s = PS1.read_text(encoding="utf-8")
    assert "Read-Host" in s
    assert "-AsSecureString" in s
    assert "ZeroFreeBSTR" in s
    assert "default_transaction_read_only=on" in s
    assert "--no-password" in s
    assert "Remove-Item Env:PGPASSWORD" in s
    assert "Remove-Item Env:PGOPTIONS" in s


def test_5j_guia_advierte_rol_global_y_limita_ensayos():
    s = DOC.read_text(encoding="utf-8")
    assert "370 passed" in s
    assert "NOBYPASSRLS" in s
    assert "clúster" in s
    assert "NOLOGIN" in s
    assert "NO SEED" in s
    assert "SAAS_AUTH_ENABLED=false" in s
