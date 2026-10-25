"""5AS: validación estática, sin conexión a PostgreSQL ni cambios de BD."""
import ast
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"database/saas/54_tres_sp_guardia_actor_sql_5as.py"
S=SCRIPT.read_text(encoding="utf-8")


def test_5as_python_compila():
    ast.parse(S)


def test_5as_solo_cluster_55432_y_rollback_obligatorio():
    assert "53_rol_sql_por_usuario_guardia_reversible_5ar.py" in S
    assert 'PREFLIGHT(conn)' in S
    assert 'conn.execute("BEGIN")' in S
    assert 'conn.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'conn.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "supabase" not in S.lower()
    assert 'to_regclass(' in S
    assert "0 roles individuales, 0 mapa, 0 SP finales" in S


def test_5as_tres_firmas_y_guardias_iniciales():
    m=runpy.run_path(str(SCRIPT))
    assert len(m["NOMBRES_SP"])==3
    original=m["SOURCES"]()
    punto="BEGIN\n    IF p_empresa_id IS NULL OR p_empresa_id<=0"
    for sp in ("sp_cancelar_reserva_tenant",
               "sp_finalizar_reserva_sin_compra_tenant"):
        assert original[sp].count(punto)==1
    assert "IF NOT public.fs_lab_verificar_actor_5ar(p_empresa_id,p_usuario_id)" in S
    assert "src.replace" not in S
    assert "sql.replace(punto,guardia,1)" in S
    assert "pg_get_functiondef" in S
    assert "NOT p.prosecdef" in S


def test_5as_mapa_privado_y_role_individual_b1():
    assert 'SQL_B1 = "fashionstore_5as_b1"' in S
    assert "NOBYPASSRLS PASSWORD NULL" in S
    assert "NOINHERIT NOSUPERUSER" in S
    assert "rolpassword IS NULL" in S
    assert "INSERT INTO public.fs_lab_actor_sql_5ar" in S
    assert "has_table_privilege(" in S
    assert "GRANT EXECUTE ON FUNCTION" in S
    assert "GRANT EXECUTE ON PROCEDURE" in S
    assert "NO otorgar INSERT/UPDATE/SELECT comercial" in S


def test_5as_call_negativo_verifica_42501_no_acl():
    assert 'ENSURE(codigo=="42501"' in S
    assert "has_function_privilege(" in S
    assert "SAVEPOINT sp_5as_denegado" in S
    assert "ROLLBACK TO SAVEPOINT sp_5as_denegado" in S
    assert "RELEASE SAVEPOINT sp_5as_denegado" in S
    assert "SQLSTATE" in S
    assert "5AS PELIGRO" in S
    assert "5AS GUARDIAS PASS" in S


def test_5as_suplantacion_mismo_tenant_y_compartidos():
    assert 'TEST_ACTOR(c,SQL_A1,a,a1,segundo)' in S
    assert 'TEST_ACTOR(c,SQL_A2,a,segundo,a1)' in S
    assert 'TEST_ACTOR(c,SQL_B1,b,b1,a1)' in S
    assert 'set_config(\'app.usuario_id\',%s,true)' in S
    assert '(TENANT_A,"sp_crear_reserva_tenant"' in S
    assert '(TENANT_A,"sp_cancelar_reserva_tenant"' in S
    assert '(TENANT_B,"sp_finalizar_reserva_sin_compra_tenant"' in S
    assert '(SQL_A1,"sp_cancelar_reserva_tenant"' in S
    assert '(SQL_A2,"sp_crear_reserva_tenant"' in S
    assert '(SQL_B1,"sp_finalizar_reserva_sin_compra_tenant"' in S
    assert "RLS/ACL por actor" in S
