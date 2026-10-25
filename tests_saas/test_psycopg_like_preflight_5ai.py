"""5AI: regresion del error psycopg al analizar LIKE con % sin parametros.

Pruebas unitarias sin conexion, sin credenciales y sin modificaciones SQL.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

PATH = (
    Path(__file__).resolve().parents[1]
    / "database/saas/42_verificar_call_legacy_real_5ah.py"
)


def module():
    spec = spec_from_file_location("fashionstore_5ah_5ai_regression", PATH)
    assert spec is not None and spec.loader is not None
    value = module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class FakeCursor:
    def fetchone(self):
        return (48,)


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, *values):
        self.calls.append((query, values))
        return FakeCursor()


def test_sin_parametros_no_se_pasa_tupla_vacia_a_psycopg():
    conn = FakeConnection()
    query = "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk_tenant_%'"
    assert module().one(conn, query) == 48
    assert conn.calls == [(query, ())]


def test_con_parametros_si_se_envia_binding():
    conn = FakeConnection()
    query = "SELECT %s::integer"
    assert module().one(conn, query, (48,)) == 48
    assert conn.calls == [(query, ((48,),))]


def test_cadena_like_del_preflight_mantiene_porcentaje_literal():
    src = PATH.read_text(encoding="utf-8")
    assert "conname LIKE 'fk_tenant_%'" in src
    assert "cursor = conn.execute(sql) if values is None else conn.execute(sql, values)" in src
    assert "def one(conn, sql, values=())" not in src
