"""Guardas de contrato CU15; no conectan a Supabase ni mutan datos reales."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = ROOT / "database" / "saas" / "CU15_MIGRACION_FINAL_REVISION.sql"
TEST_SQL = ROOT / "database" / "saas" / "CU15_PRUEBAS_TRANSACCIONALES.sql"


def test_cu15_no_placeholder_ni_notificacion_falsa():
    sql = SQL.read_text(encoding="utf-8")
    assert "falta autoridad CU08" not in sql
    assert "CU15_PRENDA_RECIBIDA" in sql
    assert "cu15_crear" in sql and "INSERT INTO cu15_outbox" not in sql[sql.index("cu15_crear"):sql.index("cu15_cotizar_diferencia")]


def test_cu15_seguridad_y_pago_verificado():
    sql = SQL.read_text(encoding="utf-8")
    for needle in (
        "c.usuario_id",
        "GESTIONAR_DEVOLUCIONES",
        "FORCE ROW LEVEL SECURITY",
        "estado='APROBADO'",
        "cobro no verificado por CU13/CU14",
        "ORDER BY LEAST(variante_original_id",
        "AJUSTE_SALIDA",
    ):
        assert needle in sql


def test_cu15_pruebas_son_reversibles_y_no_legacy():
    sql = SQL.read_text(encoding="utf-8")
    tests = TEST_SQL.read_text(encoding="utf-8")
    assert "sp_ajustar_inventario" not in sql
    assert "sp_registrar_devolucion" not in sql
    assert tests.count("ROLLBACK") >= 2
    assert "Supabase" in tests
    assert "CU15" in tests
