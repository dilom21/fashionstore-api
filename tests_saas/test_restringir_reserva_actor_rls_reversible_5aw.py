"""5AW: controles offline de RLS restrictivo, SIN conexión a PostgreSQL."""
import ast
from pathlib import Path

PATH=(Path(__file__).resolve().parents[1]
      /"database/saas/57_restringir_reserva_rls_actor_rollback_5aw.py")
S=PATH.read_text(encoding="utf-8")


def test_5aw_python_compila():
    ast.parse(S)


def test_5aw_cluster_lab_preflight_y_rollback_siempre():
    assert '56_servicio_actor_guard_real_rollback_5av.py' in S
    assert "PREFLIGHT(c)" in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'c.execute("COMMIT")' not in S
    assert 'fashionstore_restore_' not in S
    assert "Supabase" in S
    assert "41 policies" in S
    assert '([OWNER,*ROLES],)' in S


def test_5aw_ambas_policies_son_restrictivas():
    assert 'for accion in ("SELECT","UPDATE")' in S
    assert "AS RESTRICTIVE FOR {accion}" in S
    assert "USING ({OWN_OR_ADMIN})" in S
    assert '"WITH CHECK ("+OWN_OR_ADMIN+")"' in S
    assert "fs_lab_5aw_reserva_{sufijo}_individual" in S
    assert 'permisiva=="RESTRICTIVE"' in S
    assert 'total==107' in S


def test_5aw_solo_usuario_dueno_o_admin_autenticado():
    assert "cl.id=reserva.cliente_id" in S
    assert "cl.empresa_id=reserva.empresa_id" in S
    assert "cl.estado IS TRUE" in S
    assert "reserva.empresa_id,cl.usuario_id" in S
    assert "em.empresa_id=reserva.empresa_id" in S
    assert "rr.nombre='ADMINISTRADOR'" in S
    assert "ue.estado IS TRUE" in S
    assert "reserva.empresa_id,em.usuario_id" in S
    assert "fs_lab_empresa_actor_5at()" in S
    assert "fs_verificar_actor_reserva(" in S
    assert "current_setting('app.usuario_id')" not in S


def test_5aw_denegar_ajeno_aun_con_grant_update():
    assert "has_column_privilege(" in S
    assert 'observacion' in S
    assert 'SELECT count(*) FROM public.reserva' in S
    assert 'ENSURE(row is None and err is None' in S
    assert "5AW AJENO BLOQUEADO PASS" in S
    assert "5AW_A1_INTENTO_A2" in S


def test_5aw_regresion_cu16_17_18_positivo():
    assert 'M["verificar_5au_y_llamadas"](c,ids,a2,carro_a2)' in S
    assert 'M["INSTALAR_CU16"](c,src)' in S
    assert 'M["PROTEGER_CU17_18"](c,src)' in S
    assert 'M["ACL_RLS"](c)' in S
    assert 'M["usar_unico_verificador_en_tres_sp"](c)' in S
    assert "5AW OK PARCIAL" in S


def test_5aw_audita_permisos_dml_residuales():
    assert "SAVEPOINT sp_5aw_dml" in S
    assert "ROLLBACK TO SAVEPOINT sp_5aw_dml" in S
    assert "RELEASE SAVEPOINT sp_5aw_dml" in S
    assert "UPDATE public.reserva" in S
    assert "UPDATE public.inventario" in S
    assert "5AW ALERTA DML PROPIO" in S
    assert "5AW ALERTA INVENTARIO" in S
    assert "impedir DML comercial directo" in S


def test_5aw_mapa_roles_y_policies_no_persisten():
    assert 'to_regclass(\'public.fs_lab_actor_sql_5ar\') IS NULL' in S
    assert '==41,"políticas originales no restauradas"' in S
    assert '==0,' in S
    assert "0 roles, 0 mapa, 0 SP" in S
    assert "flags OFF" in S
