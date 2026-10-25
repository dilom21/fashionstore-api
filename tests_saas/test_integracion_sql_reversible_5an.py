"""5AN: pruebas estáticas del ensayo SQL reversible, sin usar PostgreSQL."""
import ast
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"database/saas/49_ensayo_sp_tenant_reversible_5an.py"
S=SCRIPT.read_text(encoding="utf-8")


def test_script_5an_sintaxis_python():
    ast.parse(S)


def test_5an_reutiliza_preflight_exactamente_5am():
    assert "48_compilar_sp_tenant_transaccion_rollback_5am.py" in S
    assert "preflight(c)" in S
    assert "preflight, sources, catalog" in S


def test_5an_persiste_nada_y_no_instala_en_servidor_original():
    assert "conn.execute" not in S or "COMMIT" not in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'conn.execute("COMMIT")' not in S
    assert 'c.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "supabase db push" not in S
    assert "DROP DATABASE" not in S
    assert "DROP ROLE" not in S


def test_5an_actores_5an_solo_sinteticos_y_sin_password_hash_real():
    assert "!FICTIONAL-NONLOGIN-5AN" in S
    assert "lab-a-cliente-5an@demo.invalid" in S
    assert "lab-b-admin-5an@demo.invalid" in S
    assert 'GRANT SELECT (id,estado) ON public.usuario' in S
    assert 'GRANT SELECT ON public.usuario' not in S
    assert "getpass(" in S


def test_5an_principal_autorizado_no_simulado_con_set_role():
    assert "SET LOCAL SESSION AUTHORIZATION" in S
    assert "SET LOCAL SESSION AUTHORIZATION DEFAULT" in S
    assert "SELECT session_user" in S
    assert "row_security_active('public.reserva'::regclass)" in S
    assert "row_security_active('public.inventario'::regclass)" in S
    assert "SET ROLE" not in S


def test_5an_tres_sp_y_rollback_completo():
    assert "sp_crear_reserva_tenant" in S
    assert "sp_cancelar_reserva_tenant" in S
    assert "sp_finalizar_reserva_sin_compra_tenant" in S
    assert "CALL_CREATE" in S
    assert "CALL_CHANGE" in S
    assert "CU16 PASS" in S and "CU17 PASS" in S and "CU18 PASS" in S
    assert "preflight(c)" in S.split('c.execute("ROLLBACK")')[-1]
    assert "5 empresas finales intactas" in S


def test_5an_rls_policy_temporal_y_planes_activos():
    assert "fs_lab_5an_plan_select" in S
    assert "fs_lab_5an_{t}_update" in S
    assert "permite_reservas,estado" in S
    assert "usuario_empresa" in S
    assert "fecha_vencimiento" in S
    assert "GRANT EXECUTE ON PROCEDURE" in S
