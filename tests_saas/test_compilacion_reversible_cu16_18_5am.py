"""5AM regression tests for reversible CU16-CU18 compiler.

No database connection; no stored passwords, no DDL execution.
"""
from pathlib import Path
import ast

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "database/saas/48_compilar_sp_tenant_transaccion_rollback_5am.py"
)
SRC = SCRIPT.read_text(encoding="utf-8")


def test_5am_sintaxis_python():
    ast.parse(SRC)


def test_5am_guardia_closter_55432_datos_inconfundibles():
    assert 'HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"' in SRC
    assert "fashionstore-rls-lab-5v-data" in SRC
    assert "ident == f" in SRC
    assert "pg_policies" in SRC
    assert "== 41" in SRC
    assert "count(*) FROM public.reserva" in SRC
    assert "== 3" in SRC


def test_5am_rollback_incondicional_y_no_instala_sp():
    assert 'conn.execute("BEGIN")' in SRC
    assert 'conn.execute("ROLLBACK")' in SRC
    assert "finally:" in SRC
    assert 'conn.execute(sql)' in SRC
    assert "REVOKE ALL ON PROCEDURE" in SRC
    assert "ningun CALL ejecutado" in SRC
    assert "ROLLBACK verificado" in SRC


def test_5am_no_toca_5432_ni_cinco_empresas():
    assert "fashionstore_restore_" not in SRC
    assert "supabase" not in SRC.lower()
    assert "DROP DATABASE" not in SRC
    assert "DROP ROLE" not in SRC
    assert "password=pw" in SRC
    assert "getpass(" in SRC


def test_5am_tres_drafts_seguridad_y_acls():
    assert '"sp_crear_reserva_tenant"' in SRC
    assert '"sp_cancelar_reserva_tenant"' in SRC
    assert '"sp_finalizar_reserva_sin_compra_tenant"' in SRC
    assert 'has_function_privilege(%s,%s::oid,\'EXECUTE\')' in SRC
    assert "not security_definer" in SRC
    assert 'len(rows) == 3' in SRC
