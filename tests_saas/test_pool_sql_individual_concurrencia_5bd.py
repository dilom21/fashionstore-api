"""5BD: 12 guardrails offline y selector puro; no PG ni sockets."""
import ast
from pathlib import Path

import pytest

P=(Path(__file__).resolve().parents[1]/
   "database/saas/63_pool_sql_individual_concurrencia_temporal_5bd.py")
S=P.read_text(encoding="utf-8")


def test_5bd_sintaxis():
    ast.parse(S)


def test_5bd_importa_5bc_sin_ejecutar_su_main():
    assert 'runpy.run_path(str(HERE/"62_cluster_temporal_login_scram_rls_5bc.py"))' in S
    assert 'if __name__=="__main__":' in S
    assert 'HOST,PORT=LAB["HOST"],LAB["PORT"]' in S
    assert 'validate_cluster=LAB["validate_cluster"]' in S
    assert 'prepare_schema=LAB["prepare_schema"]' in S


def test_5bd_aislamiento_entorno_y_limites():
    assert 'tempfile.mkdtemp(prefix="fashionstore-5bd-pool-")' in S
    assert "5432/55432 intactos" in S
    assert "require(port_free()" in S
    assert 'validate_cluster(admin,data)' in S
    assert 'SaaS flags OFF' in S
    assert "No usa PG 5432/55432" in S
    assert "DATABASE_URL" not in S
    assert "fashionstore_restore_" not in S


def test_5bd_roles_autenticos_con_url_estructurada():
    assert 'URL.create("postgresql+psycopg"' in S
    assert "password=passwords[role]" in S
    assert "username=role" in S
    assert "poolclass=QueuePool" in S
    assert "pool_size=1,max_overflow=0,pool_timeout=8" in S
    assert "pool_pre_ping=True" in S
    assert "sslmode" in S
    assert "set(passwords)==set(ROLES)" in S


def test_5bd_no_delega_a_un_pool_compartido_superusuario():
    assert "engines[role]=create_engine" in S
    assert "with engines[role].connect() as connection" in S
    assert "SELECT session_user,current_user" in S
    assert "tuple(who)==esperado" in S
    assert "pg_backend_pid()" in S
    assert "len(set(primeros.values()))==3" in S
    assert "pg_get_userbyid" not in S


def test_5bd_rls_y_guc_no_autorizan_por_si_solos():
    assert "row_security_active('public.fs_5bc_reserva'::regclass)" in S
    assert "5BD BRECHA: pool ve más de una reserva" in S
    assert "SELECT empresa_id,usuario_id" in S
    assert "current_setting(" in S
    assert "guc in (None,\"\")" in S
    assert "set_config(" in S
    assert "spoof is False" in S
    assert "connection.rollback()" in S


def test_5bd_concurrencia_de_tres_logins_verdaderos():
    assert "ThreadPoolExecutor(max_workers=3)" in S
    assert "threading.Barrier(3)" in S
    assert "espera.wait(timeout=12)" in S
    assert "pg_sleep(0.08)" in S
    assert "for vuelta in range(3)" in S
    assert "future.result(timeout=25)" in S
    assert "len({pid for _,pid in result})==3" in S
    assert "5BD CONCURRENCIA PASS" in S


def test_5bd_sin_update_directo_en_los_tres_pooles():
    assert "def auditar_acl_sin_update" in S
    assert "has_table_privilege(" in S
    assert "'public.fs_5bc_reserva','UPDATE'" in S
    assert "has is False" in S
    assert "5BD ACL PASS" in S


def test_5bd_no_pierde_control_del_directorio_temporal():
    assert 'pwfile.unlink(missing_ok=True)' in S
    assert 'if started or pid.exists():' in S
    assert 'server_control=True,logfile=logfile' in S
    assert '"-m","fast","stop"' in S
    assert 'if not pid.exists() and port_free():' in S
    assert "shutil.rmtree(directory)" in S
    assert "5BD LIMPIEZA PENDIENTE" in S
    assert "engine.dispose()" in S


def test_5bd_autenticacion_y_selector_no_equivale_a_jwt_real():
    assert "JWT real" in S
    assert "No es el pool de FastAPI" in S
    assert "membresía vigente" in S
    assert "selector de " in S
    assert "5BD PENDIENTE" in S


def _selector_sin_side_effects():
    tree=ast.parse(S)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
            and n.name=="resolver_principal_verificado")
    module=ast.Module(body=[fn],type_ignores=[])
    actors={
        (1,101):"fashionstore_5bc_a1",
        (1,102):"fashionstore_5bc_a2",
        (2,201):"fashionstore_5bc_b1",
    }
    roles=tuple(actors.values())
    def require_simple(ok,message):
        if not ok:
            raise RuntimeError(message)
    env={"ACTOR_MAP":actors,"ROLES":roles,"require":require_simple}
    exec(compile(module,str(P),"exec"),env)
    return env["resolver_principal_verificado"]


def test_5bd_selector_mismo_tenant_dos_principales_distintos():
    select=_selector_sin_side_effects()
    assert select(1,101)=="fashionstore_5bc_a1"
    assert select(1,102)=="fashionstore_5bc_a2"
    assert select(2,201)=="fashionstore_5bc_b1"


def test_5bd_selector_deniega_cruce_de_empresa_y_usuario():
    select=_selector_sin_side_effects()
    for contexto in ((1,201),(2,101),(2,102),(1,999),(0,101)):
        with pytest.raises(RuntimeError,match="fuera del mapeo"):
            select(*contexto)
