"""5BC: controles offline del clúster desechable PG18.

No arranca PostgreSQL, no abre sockets ni solicita credenciales.
"""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/62_cluster_temporal_login_scram_rls_5bc.py")
S=P.read_text(encoding="utf-8")


def test_5bc_python_compila():
    ast.parse(S)


def test_5bc_no_reutiliza_5432_55432_ni_entorno_comercial():
    assert 'HOST = "127.0.0.1"' in S
    assert "PORT = 55433" in S
    assert "fashionstore-5bc-auth-" in S
    assert 'tempfile.mkdtemp(prefix="fashionstore-5bc-auth-")' in S
    assert "fashionstore_restore_" not in S
    assert "PGPASSFILE" not in S
    assert "DATABASE_URL" not in S
    assert "psql.exe" not in S
    assert "NO usa PostgreSQL :5432 ni :55432" in S
    assert "puerto 55433 ocupado" in S
    assert "data_directory no coincide" in S


def test_5bc_escoge_binarios_exactos_pg18_y_arranque_local():
    assert "initdb.exe" in S
    assert "pg_ctl.exe" in S
    assert "PostgreSQL" in S
    assert 'r"PostgreSQL\\)?\\s+18(?:[.\\s]|$)"' in S
    assert '"--auth-host=scram-sha-256"' in S
    assert '"--auth-local=scram-sha-256"' in S
    assert "host all all 127.0.0.1/32 scram-sha-256" in S
    assert "-c listen_addresses={HOST} -p {PORT} -c ssl=off" in S
    assert 'started or (datadir/"postmaster.pid").exists()' in S


def test_5bc_contrasenas_aleatorias_sin_almacenamiento_fijo():
    assert "secrets.token_urlsafe(36)" in S
    assert "role_passwords={r:secrets.token_urlsafe(36) for r in ROLES}" in S
    assert 'pwfile.unlink(missing_ok=True)' in S
    assert "PASSWORD {}" in S
    assert "sql.Literal(passwords[role])" in S
    assert "PASSWORD NULL" not in S
    assert "with open(pwfile,\"x\",encoding=\"ascii\")" in S


def test_5bc_roles_sql_real_no_superusuario_ni_impersonacion():
    assert '"fashionstore_5bc_a1"' in S
    assert '"fashionstore_5bc_a2"' in S
    assert '"fashionstore_5bc_b1"' in S
    assert "LOGIN NOINHERIT" in S
    assert "NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB" in S
    assert "session_user,current_user" in S
    assert "SET SESSION AUTHORIZATION" not in S
    assert "SET LOCAL SESSION AUTHORIZATION" not in S
    assert "SET ROLE" not in S


def test_5bc_map_privado_rls_y_verificador_por_session_user():
    assert "CREATE TABLE public.fs_5bc_actor" in S
    assert "REVOKE ALL ON public.fs_5bc_actor FROM PUBLIC" in S
    assert "ENABLE ROW LEVEL SECURITY" in S
    assert "GRANT SELECT(principal,empresa_id,usuario_id)" in S
    assert "SECURITY DEFINER" in S
    assert "SET search_path = pg_catalog, pg_temp" in S
    assert "m.principal=session_user::name" in S
    assert "fs_5bc_guard_map" in S
    assert "REVOKE ALL ON FUNCTION public.fs_5bc_verificar" in S
    assert "REVOKE CREATE ON SCHEMA public FROM {}" in S


def test_5bc_acl_directo_y_sp_protegido_owner_nologin():
    assert 'WRITER = "fashionstore_5bc_writer_owner"' in S
    assert "CREATE ROLE {} NOLOGIN NOINHERIT" in S
    assert "GRANT UPDATE(estado) ON public.fs_5bc_reserva" in S
    assert "fs_5bc_writer_update" in S
    assert "WITH CHECK (public.fs_5bc_verificar" in S
    assert "CREATE PROCEDURE public.fs_5bc_confirmar" in S
    assert "SET estado='CONFIRMADA'" in S
    assert "REVOKE ALL ON PROCEDURE" in S
    assert "sqlstate==\"42501\"" in S
    assert "BRECHA: actor pudo UPDATE directo" in S
    assert "BRECHA: A1 realizó CALL como usuario A2" in S


def test_5bc_tres_conexiones_independientes_y_tenant_cruzado():
    assert "for role,uid,empresa,row_id in ACTORS:" in S
    assert "with connect(role,passwords[role]) as c:" in S
    assert "with connect(ROLES[0],passwords[ROLES[0]])" in S
    assert "with connect(ROLES[2],passwords[ROLES[2]])" in S
    assert "own==[(row_id,empresa,uid)]" in S
    assert "set_config('app.usuario_id',%s,true)" in S
    assert "GUC editable permitió suplantación" in S
    assert "función" not in S or "fs_5bc_verificar" in S


def test_5bc_limpieza_automatica_revisada():
    assert 'command([pgctl,"-D",datadir,"-w","-t","25"' in S
    assert '"-m","fast","stop"' in S
    assert "5BC CLEANUP PASS" in S
    assert 'require(port_free(),"55433 todavía escucha después de cleanup")' in S
    assert "5BC FINAL: directorio temporal eliminado" in S
    assert "5432/55432 no tocados" in S


def test_5bc_no_es_ensayo_http_ni_comercial():
    assert "NO valida todavía el pool FastAPI/JWT" in S
    assert "ni condiciones de concurrencia" in S
    assert "No es el esquema comercial" in S
    assert "flags SaaS OFF" in S



def test_5bc_no_comparte_password_y_exige_hash_scram():
    assert "SET password_encryption='scram-sha-256'" in S
    assert "FROM pg_catalog.pg_authid" in S
    assert 'hashed.startswith("SCRAM-SHA-256$")' in S
    assert "with connect(ROLES[1],passwords[ROLES[0]])" in S
    assert "except psycopg.OperationalError:" in S
    assert "5BC PASSWORD PASS" in S
    assert "BRECHA: contraseña A1 autentica como A2" in S



def test_5bc_pgctl_no_pipes_heredables_ni_bloqueo_de_communicate():
    # regresión Windows: stdout/stderr=PIPE de pg_ctl es heredado por
    # postgres.exe y subprocess.communicate queda esperando EOF.
    assert "server_control=False" in S
    assert "subprocess.DEVNULL if server_control" in S
    assert "stdout=(subprocess.DEVNULL if server_control" in S
    assert "stderr=(subprocess.DEVNULL if server_control" in S
    assert "stdin=subprocess.DEVNULL" in S
    assert '"start"],timeout=55,server_control=True,logfile=logfile)' in S
    assert "server_control=True,logfile=logfile)" in S
    assert "stdout/stderr en Windows" in S
    assert "5BC START: iniciando pg_ctl" in S


def test_5bc_interrupcion_no_borra_cluster_vivo_y_diagnostica():
    assert "tempfile.mkdtemp" in S
    assert "if started or pid.exists():" in S
    assert 'command([pgctl,"-D",datadir,"-w","-t","25",' in S
    assert 'if not pid.exists() and port_free():' in S
    assert "shutil.rmtree(directory)" in S
    assert "5BC LIMPIEZA PENDIENTE" in S
    assert "5BC STOP ALERTA" in S
    assert "5BC TEMP PASS" in S
    assert "require(cleanup_ok," in S
    assert "taskkill genérico" in S
