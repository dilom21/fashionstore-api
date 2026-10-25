"""5AQ: controles estáticos antes del ensayo SQL reversible de suplantación.

Sin importar el script ni crear conexiones a BD.
"""
import ast
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "database/saas/52_auditar_suplantacion_usuario_sql_5aq.py"
SRC = FILE.read_text(encoding="utf-8")


def test_5aq_sintaxis_python():
    ast.parse(SRC)


def test_5aq_solo_cluster_exacto_y_rollback_final():
    assert "49_ensayo_sp_tenant_reversible_5an.py" in SRC
    assert "preflight(c)" in SRC
    assert 'c.execute("BEGIN")' in SRC
    assert 'c.execute("ROLLBACK")' in SRC
    assert "finally:" in SRC
    assert 'conn.execute("COMMIT")' not in SRC
    assert 'c.execute("COMMIT")' not in SRC
    assert "fashionstore_restore_" not in SRC
    assert "Supabase" not in SRC


def test_5aq_control_legitimo_y_escenario_directo():
    assert 'usuario_a1 != usuario_a2' in SRC
    assert "SAVEPOINT sp_denegado" in SRC
    assert "ROLLBACK TO SAVEPOINT sp_denegado" in SRC
    assert 'code=="P0002"' in SRC
    assert "CALL_CREATE" in SRC
    assert "usuario_a1,fecha" in SRC
    assert "usuario_a2,fecha" in SRC
    assert 'session_user' in SRC
    assert "5AQ BRECHA DEMOSTRADA" in SRC


def test_5aq_no_altera_usuarios_productivos_ni_abre_saas():
    assert "demo.invalid" in SRC
    assert "'!SIN-LOGIN-PRUEBA-5AQ'" in SRC
    assert "55432" in SRC
    assert "SAAS_AUTH_ENABLED=true" not in SRC
    assert "SAAS_RESERVAS_ENABLED=true" not in SRC
    assert "CREATE ROLE" not in SRC
    assert "DROP ROLE" not in SRC


def test_5aq_exige_baseline_desde_el_laboratorio_original():
    assert "fixtures(c)" in SRC
    assert "grants(c)" in SRC
    assert '==52' in SRC
    assert "41 policies, 3 reservas originales, 4 carritos originales" in SRC
    assert "0 SP finales" in SRC
