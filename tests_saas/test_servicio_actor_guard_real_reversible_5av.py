"""5AV: 8 controles sin conexiones, credenciales ni cambios PostgreSQL.

Los ensayos PostgreSQL se ejecutan SOLAMENTE en el script 56 y 55432.
"""
import ast
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"database/saas/56_servicio_actor_guard_real_rollback_5av.py"
CODE=SCRIPT.read_text(encoding="utf-8")


def test_5av_sintaxis_python():
    ast.parse(CODE)


def test_5av_solo_laboratorio_y_rollback_final_obligatorio():
    assert "55_sp_actor_individual_dml_rollback_5at.py" in CODE
    assert "PREFLIGHT(c)" in CODE
    assert 'c.execute("BEGIN")' in CODE
    assert 'c.execute("ROLLBACK")' in CODE
    assert "finally:" in CODE
    assert 'c.execute("COMMIT")' not in CODE
    assert "fashionstore_restore_" not in CODE
    assert 'HOST,PORT,DB=LAB["HOST"],LAB["PORT"],LAB["DB"]' in CODE
    assert "saas_5av_import_inerte.db" in CODE
    assert '"SAAS_AUTH_ENABLED"]="false"' in CODE
    assert '"SAAS_RESERVAS_ENABLED"]="false"' in CODE


def test_5av_owner_nologin_y_mapa_inaccesible():
    assert "OWNER=\"fashionstore_5av_guard_owner\"" in CODE
    assert "NOLOGIN NOINHERIT" in CODE
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in CODE
    assert "NOBYPASSRLS" in CODE
    assert "GRANT SELECT" in CODE
    assert "TO {OWNER}" in CODE
    assert "CREATE POLICY fs_lab_5av_map_owner_select" in CODE
    assert "ALTER FUNCTION public.fs_verificar_actor_reserva" in CODE
    assert "REVOKE CREATE ON SCHEMA public FROM {OWNER}" in CODE
    assert "has_table_privilege(" in CODE
    assert '"SELECT count(*) FROM pg_policies' in CODE
    assert "==105" in CODE


def test_5av_verificador_auditado_separado_de_guc():
    assert "SECURITY DEFINER" in CODE
    assert "SET search_path = pg_catalog, pg_temp" in CODE
    assert "REVOKE ALL ON FUNCTION" in CODE
    assert "FROM PUBLIC" in CODE
    assert "m.principal_sql=session_user::name" in CODE
    assert "m.empresa_id=p_empresa_id" in CODE
    assert "m.usuario_id=p_usuario_id" in CODE
    assert "current_setting('app.usuario_id')" not in CODE
    assert 'public.fs_verificar_actor_reserva(bigint,bigint)' in CODE


def test_5av_cu16_cu17_cu18_exigen_misma_guardia_sql():
    assert "usar_unico_verificador_en_tres_sp(c)" in CODE
    assert "sp_crear_reserva_tenant" in CODE
    assert "sp_cancelar_reserva_tenant" in CODE
    assert "sp_finalizar_reserva_sin_compra_tenant" in CODE
    assert "pg_get_functiondef" in CODE
    assert "ddl.count(original)==1" in CODE
    assert "nuevo.count(reemplazo)==1" in CODE
    assert "fs_lab_verificar_actor_5ar" in CODE
    assert "fs_verificar_actor_reserva(p_empresa_id,p_usuario_id)" in CODE


def test_5av_adaptador_real_no_comitea_y_rechaza_actor_ajeno():
    assert "service.verificar_principal_reserva(db,actor2)" in CODE
    assert "except service.ReservaTenantDeshabilitada:" in CODE
    assert "session_user" in CODE
    assert "set_config('app.usuario_id',%s,true)" in CODE
    assert "servicio 5AU" in CODE
    assert "self.commits_interceptados+=1" in CODE
    assert "ENSURE(db.commits_interceptados==5" in CODE
    assert "settings.saas_actor_sql_owner=owner_anterior" in CODE


def test_5av_operaciones_legitimas_roles_individuales():
    assert "CREAR_A2(c,ids" in CODE
    assert "MAPEAR_A(c,ids" in CODE
    assert "MAPEAR_B(c,ids" in CODE
    assert "CARRO_A2(c,ids,a2)" in CODE
    assert "service.crear_reserva_transaccional_sin_exposicion_http(" in CODE
    assert "service.cancelar_reserva_transaccional_tenant(" in CODE
    assert "service.finalizar_reserva_sin_compra_transaccional_tenant(" in CODE
    assert "CU16 PASS" in CODE and "CU17 PASS" in CODE and "CU18 PASS" in CODE
    assert "54_tres_sp_guardia_actor_sql_5as" not in CODE


def test_5av_auditoria_dml_directo_es_negativa_y_reversible():
    assert "auditar_dml_directo(c,ids,reserva2)" in CODE
    assert "SAVEPOINT sp_audit_5av" in CODE
    assert "ROLLBACK TO SAVEPOINT sp_audit_5av" in CODE
    assert "RELEASE SAVEPOINT sp_audit_5av" in CODE
    assert "UPDATE public.reserva" in CODE
    assert "RETURNING id" in CODE
    assert "5AV ALERTA DML" in CODE
    assert "5AV BLOQUEO DE DESPLIEGUE" in CODE
    assert "NO valida login no-superuser real" in CODE
