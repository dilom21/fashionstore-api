"""5AT: guardrails de operaciones SQL positivas por actor (sin BD real).

No importa el script 5AT, no abre PostgreSQL, no consulta .env.
El ensayo vivo debe ejecutarse manualmente en PG18 LAB 55432.
"""
import ast
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"database/saas/55_sp_actor_individual_dml_rollback_5at.py"
S=P.read_text(encoding="utf-8")


def test_5at_python_valido():
    ast.parse(S)


def test_5at_cluster_fijo_y_rollback_estricto():
    assert '54_tres_sp_guardia_actor_sql_5as.py' in S
    assert "PREFLIGHT(conn)" in S
    assert 'conn.execute("BEGIN")' in S
    assert 'conn.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'conn.execute("COMMIT")' not in S
    assert 'fashionstore_restore_' not in S
    assert "NO utilizar en PG 5432, Supabase" in S
    assert "supabase db push" not in S.lower()
    assert "41 políticas originales" in S
    assert '"SELECT to_regclass(' in S


def test_5at_mapeo_de_tres_principales_y_rls_sin_guc():
    assert 'SQL_A1,SQL_A2,SQL_B1=BASE[' in S
    assert "session_user::name" in S
    assert "SECURITY DEFINER" in S
    assert "SET search_path = pg_catalog, pg_temp" in S
    assert "REVOKE ALL ON FUNCTION" in S
    assert "GRANT EXECUTE ON FUNCTION" in S
    assert "fs_lab_empresa_actor_5at()" in S
    assert "current_setting('app.empresa_id')" not in S
    assert "current_setting('app.usuario_id')" not in S


def test_5at_exactamente_34_tablas_tenant_y_policies_dml():
    assert 'ENSURE(len(tablas)==34' in S
    assert "CREATE POLICY fs_lab_5at_tenant_select" in S
    assert "CREATE POLICY fs_lab_5at_empresa_select" in S
    assert "CREATE POLICY fs_lab_5at_usuario_select" in S or "fs_lab_5at_{tabla}_select" in S
    assert "CREATE POLICY fs_lab_5at_plan_select" in S
    assert "CREATE POLICY fs_lab_5at_rol_funcion_select" in S
    assert "CREATE POLICY fs_lab_5at_{tabla}_update" in S
    assert "CREATE POLICY fs_lab_5at_{tabla}_insert" in S
    assert '==104' in S
    assert "políticas transitorias adicionales" in S


def test_5at_minimos_permisos_y_mapas_privados():
    assert "GRANT SELECT (id,estado) ON public.usuario" in S
    assert "GRANT SELECT (id,nombre,estado) ON public.rol" in S
    assert "GRANT INSERT ON public.bitacora" in S
    assert "GRANT USAGE ON SEQUENCE" in S
    assert "GRANT UPDATE (estado) ON public.usuario_empresa" in S
    assert "GRANT UPDATE (estado) ON public.suscripcion" in S
    assert "GRANT UPDATE (estado,fecha_actualizacion)" in S
    assert "has_table_privilege(" in S
    assert "privado" in S.lower() or "identidades" in S.lower()
    assert "GRANT SELECT ON public.usuario" not in S
    assert "GRANT SELECT ON public.rol" not in S
    assert "GRANT SELECT ON public.fs_lab_actor_sql_5ar" not in S


def test_5at_dos_clientes_y_un_admin_en_operaciones_reales():
    assert "CREAR_A2(conn,ids" in S
    assert "crear_carrito_cliente_a2(conn,ids,a2)" in S
    assert 'CALL_CREATE' in S and 'CALL_CHANGE' in S
    assert '(SQL_A1,ids["a_usuario"],ids["a_carrito"])' in S
    assert '(SQL_A2,a2,carrito_a2)' in S
    assert "cambiar_rol(conn,SQL_B1)" in S
    assert "CU16 PASS" in S and "CU17 PASS" in S and "CU18 PASS" in S
    assert "stock_reservado" in S and "ATENDIDA" in S
    assert "row_security_active(" in S


def test_5at_no_instalacion_comercial_y_resultado_no_declarado():
    assert "NO utilizar en PG 5432" in S
    assert "0 SP" in S
    assert "no prueba login DB real" in S.lower() or "NO prueba login DB real" in S
    assert "credenciales sin postgres" in S or "sin postgres" in S
    assert "flags OFF" in S
