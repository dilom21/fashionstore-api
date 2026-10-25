"""5AH: regresión del bug de no ejecutar CALL y guardias estáticas.
Estos tests NO conectan con PostgreSQL.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "database" / "saas"
OLD = (ROOT / "41_pruebas_rls_temporales_llamadas_5ag.py").read_text(encoding="utf-8")
NEW = (ROOT / "42_verificar_call_legacy_real_5ah.py").read_text(encoding="utf-8")


def load_old():
    path = ROOT / "41_pruebas_rls_temporales_llamadas_5ag.py"
    spec = spec_from_file_location("fashionstore_5ag_regression", path)
    mod = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class DummyCursor:
    description = None

    def fetchall(self):
        raise AssertionError("CALL no debería hacer fetchall sin columnas")


class DummyConnection:
    def __init__(self):
        self.commands = []

    def execute(self, query, args=()):
        self.commands.append(query)
        return DummyCursor()


def test_5ah_regresion_call_si_se_ejecuta_sin_returning():
    con = DummyConnection()
    code, rows = load_old().aborted_sql(con, "CALL public.sp_cancelar_reserva(NULL)")
    assert code is None and rows is None
    assert con.commands == ["BEGIN", "CALL public.sp_cancelar_reserva(NULL)", "ROLLBACK"]


def test_5ah_no_hay_ternario_que_omita_call():
    assert 'cursor = conn.execute(statement, args)' in OLD
    assert 'conn.execute(statement, args).fetchall() if "RETURNING"' not in OLD
    assert "conn.execute(statement)" in NEW


def test_5ah_readonly_todos_los_principales():
    assert "default_transaction_read_only=on" in NEW
    assert 'conn.execute("BEGIN READ ONLY")' in NEW
    assert 'conn.execute("ROLLBACK")' in NEW
    assert 'HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"' in NEW


def test_5ah_obliga_identidad_dato_y_acl_por_oid():
    assert "data_directory" in NEW
    assert "35" in NEW and "46" in NEW and "48" in NEW
    assert "prokind='p'" in NEW
    assert "p.oid" in NEW
    assert "has_function_privilege(current_user,%s::oid,'EXECUTE')" in NEW
    assert "has_function_privilege(%s,%s::oid,'EXECUTE')" in NEW


def test_5ah_no_dml_ddl_grants():
    for forbidden in ("CREATE ROLE ", "GRANT EXECUTE ", "DROP POLICY ", "DROP DATABASE ",
                      "UPDATE public.", "INSERT INTO public.", "DELETE FROM public.",
                      "ALTER TABLE ", "REVOKE EXECUTE "):
        assert forbidden not in NEW


def test_5ah_comprueba_error_42501_luego_de_ocho_calls():
    assert 'check(code == "42501"' in NEW
    assert "count_a + count_b == 8" in NEW
    assert "5AH A10: PASS_CALL_REAL_DENEGADO" in NEW
    assert "A08/A11/A13: PENDIENTES" in NEW
