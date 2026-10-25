"""5AR: pruebas de seguridad estructural sin ejecutar DDL ni abrir PostgreSQL."""
import ast
from pathlib import Path

P=Path(__file__).resolve().parents[1]/"database/saas/53_rol_sql_por_usuario_guardia_reversible_5ar.py"
S=P.read_text(encoding="utf-8")


def test_5ar_python_valido():
    ast.parse(S)


def test_5ar_solo_lab_fijo_y_rollback_obligatorio():
    assert '49_ensayo_sp_tenant_reversible_5an.py' in S
    assert 'PREFLIGHT(c)' in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert 'finally:' in S
    assert 'c.execute("COMMIT")' not in S
    assert 'fashionstore_restore_' not in S
    assert 'DROP DATABASE' not in S
    assert 'DROP ROLE' not in S


def test_5ar_mapa_sql_protegido_y_rol_por_usuario():
    assert 'SQL_A1 = "fashionstore_5ar_a1"' in S
    assert 'SQL_A2 = "fashionstore_5ar_a2"' in S
    assert "PASSWORD NULL" in S
    assert "NOBYPASSRLS" in S and "NOSUPERUSER" in S
    assert "CREATE TABLE public.fs_lab_actor_sql_5ar" in S
    assert "REVOKE ALL ON TABLE public.fs_lab_actor_sql_5ar FROM PUBLIC" in S
    assert "has_table_privilege(" in S
    assert "rolpassword IS NULL" in S


def test_5ar_verificador_definer_encapsulado():
    assert "SECURITY DEFINER" in S
    assert "SET search_path = pg_catalog, pg_temp" in S
    assert "m.principal_sql=session_user::name" in S
    assert "m.empresa_id=p_empresa_id" in S
    assert "m.usuario_id=p_usuario_id" in S
    assert "ue.estado IS TRUE" in S
    assert "u.estado IS TRUE" in S
    assert "REVOKE ALL ON FUNCTION" in S
    assert "fs_lab_verificar_actor_5ar(bigint,bigint) FROM PUBLIC" in S
    assert "GRANT EXECUTE ON FUNCTION" in S
    assert "current_setting('app.usuario_id')" not in S


def test_5ar_guardia_cu16_no_permite_rol_tenant_anonimo():
    assert 'src.replace(punto,guardia,1)' in S
    assert "IF NOT public.fs_lab_verificar_actor_5ar(p_empresa_id,p_usuario_id)" in S
    assert "USING ERRCODE='42501'" in S
    assert "SAVEPOINT sp_5ar_guardado" in S
    assert "ROLLBACK TO SAVEPOINT sp_5ar_guardado" in S
    assert 'ENSURE(codigo=="42501"' in S
    assert "5AR CU16 BLOQUEADO PASS" in S


def test_5ar_no_confiar_en_guc_usuario_editable():
    assert "set_config('app.usuario_id',%s,true)" in S
    assert 'is False,"GUC app.usuario_id permitió suplantación"' in S
    assert "comprobar_por_usuario(c,SQL_A1" in S
    assert "comprobar_por_usuario(c,SQL_A2" in S
    assert "41 policies originales" in S
    assert "no activar flags SaaS" in S
