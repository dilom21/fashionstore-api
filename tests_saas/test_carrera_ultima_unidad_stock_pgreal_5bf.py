"""5BF: comprobaciones offline de carrera stock, nunca inicia PostgreSQL."""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/65_carrera_ultima_unidad_stock_pgreal_temporal_5bf.py")
S=P.read_text(encoding="utf-8")


def test_5bf_sintaxis_python():
    ast.parse(S)


def test_5bf_laboratorio_independiente_solo_55433():
    assert 'runpy.run_path(str(HERE/"63_pool_sql_individual_concurrencia_temporal_5bd.py"))' in S
    assert 'HOST,PORT=BD["HOST"],BD["PORT"]' in S
    assert 'require(port_free(),"55433 ocupado' in S
    assert 'validate_cluster(admin,data)' in S
    assert 'tempfile.mkdtemp(prefix="fashionstore-5bf-stock-")' in S
    assert "NUNCA 5432, 55432" in S
    assert "esquema comercial" in S
    assert "fashionstore_restore_" not in S
    assert "DATABASE_URL" not in S


def test_5bf_tres_sp_son_demo_y_no_comerciales():
    assert "No se prueban todavía los tres SP comerciales DEFINITIVOS" in S
    assert '"fs_5bf_crear_demo"' in S
    assert '"fs_5bf_cancelar_demo"' in S
    assert '"fs_5bf_finalizar_demo"' in S
    assert "CALL public.{proc}" in S
    assert "proc in {name for name,_ in PROCS}" in S
    assert "5BF PENDIENTE" in S


def test_5bf_ultima_unidad_usa_update_condicional_atomico():
    assert "UPDATE public.fs_5bf_stock" in S
    assert "SET reservado=reservado+1" in S
    assert "reservado<total" in S
    assert "GET DIAGNOSTICS afectadas=ROW_COUNT" in S
    assert "afectadas!=1" in S
    assert "ERRCODE='P0001'" in S
    assert "CHECK(reservado>=0 AND reservado<=total)" in S
    assert "total integer NOT NULL CHECK(total>=0)" in S


def test_5bf_login_scram_y_concurrencia_verdadera():
    assert "prepare_schema(admin,passw)" in S
    assert "with connect(role,passw[role]) as c:" in S
    assert "threading.Barrier(2)" in S
    assert "barrier.wait(timeout=12)" in S
    assert "ThreadPoolExecutor(max_workers=2)" in S
    assert "future.result" not in S or "f.result(timeout=28)" in S
    assert '["OK","SIN_STOCK"]' in S
    assert 'e.sqlstate=="P0001"' in S
    assert "5BF CARRERA PASS" in S


def test_5bf_guardia_session_user_no_guc():
    assert "public.fs_5bc_verificar(p_empresa,p_usuario)" in S
    assert "principal=session_user::name" in S
    assert "public.fs_5bf_empresa_actor()" in S
    assert "SET search_path=pg_catalog,pg_temp" in S
    assert "set_config('app.usuario_id',%s,true)" in S
    assert "GUC editable suplantó actor SQL" in S
    assert "SELECT session_user,current_user" in S


def test_5bf_rls_owners_nologin_y_public_exec_revocado():
    assert 'VERIFIER=BD["LAB"]["VERIFIER"]' in S
    assert 'WRITER=BD["LAB"]["WRITER"]' in S
    assert "SECURITY DEFINER" in S
    assert "OWNER TO {}" in S
    assert "REVOKE ALL ON PROCEDURE {} FROM PUBLIC" in S
    assert "REVOKE ALL ON FUNCTION" in S
    assert "REVOKE CREATE ON SCHEMA public FROM {}" in S
    assert "ENABLE ROW LEVEL SECURITY" in S
    assert "WITH CHECK (empresa_id=public.fs_5bf_empresa_actor())" in S
    assert "p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']" in S
    assert "can_login is False" in S
    assert "bypass is False" in S


def test_5bf_actores_sin_dml_directo_comprobado_en_catalogo():
    assert "has_table_privilege(" in S
    assert "'UPDATE'" in S
    assert "actor obtuvo UPDATE directo" in S
    assert "DML directo no negó privilegio" in S
    assert "5BF ACL/RLS PASS" in S


def test_5bf_cancelar_libera_stock_una_sola_vez():
    assert "SET estado='CANCELADA'" in S
    assert "estado='ACTIVA'" in S
    assert "SET reservado=reservado-1" in S
    assert "reservado>0" in S
    assert "Reserva no modificable" in S
    assert "doble cancelación" in S or "cancelación doble" in S
    assert "5BF CANCELAR PASS" in S


def test_5bf_finalizar_no_libera_dos_veces():
    assert 'DDL_FINALIZAR=DDL_CANCELAR.replace(' in S
    assert '"fs_5bf_finalizar_demo"' in S
    assert '"\'CANCELADA\'","\'ATENDIDA\'"' in S
    assert "doble finalización" in S
    assert "5BF FINALIZAR PASS" in S


def test_5bf_negativas_usuario_y_empresa_cruzadas():
    assert "ROLES[1] if winner==ROLES[0] else ROLES[0]" in S
    assert "ROLES[2],201,2" in S
    assert "actor ajeno canceló reserva" in S
    assert "suplantación SQL no dio 42501" in S
    assert "5BF BRECHA: UPDATE directo de stock" in S


def test_5bf_efectos_de_stock_y_estados_finales_comprobados():
    assert "[(1,1,1),(2,1,0)]" in S
    assert "len(reservas)==1" in S
    assert "[(1,1,0),(2,1,0)]" in S
    assert '[(1,"CANCELADA"),(2,"ATENDIDA")]' in S
    assert "stock=1" in S


def test_5bf_pg_ctl_sin_pipe_y_cleanup_especifico():
    assert '"--auth-host=scram-sha-256"' in S
    assert '"--auth-local=scram-sha-256"' in S
    assert "server_control=True,logfile=logfile" in S
    assert 'if started or pid.exists():' in S
    assert '"-m","fast","stop"' in S
    assert 'if not pid.exists() and port_free():' in S
    assert "shutil.rmtree(folder)" in S
    assert "5BF CLEANUP PASS" in S
    assert "5BF TEMP PASS" in S
    assert "5BF LIMPIEZA PENDIENTE" in S


def test_5bf_no_presume_cu16_cu18_definitivos():
    assert "FICTICIOS" in S
    assert "SP comerciales CU16–CU18 reales" in S
    assert "proveedor seguro escalable" in S
    assert "flags SaaS OFF" in S
