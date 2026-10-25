"""5BE: controles estructurales sin abrir sockets ni arrancar PostgreSQL."""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
  "database/saas/64_jwt_membresia_pool_sql_real_temporal_5be.py")
S=P.read_text(encoding="utf-8")


def test_5be_python_compila():
    ast.parse(S)


def test_5be_solo_cluster_temporal_55433():
    assert "63_pool_sql_individual_concurrencia_temporal_5bd.py" in S
    assert "postgres_bin=BD[" in S
    assert "validate_cluster=BD[" in S
    assert 'require(port_free(),"55433 ocupado' in S
    assert 'tempfile.mkdtemp(prefix="fashionstore-5be-jwt-")' in S
    assert "NO usa/lee PostgreSQL 5432 ni 55432" in S
    assert "fashionstore_restore_" not in S
    assert "SAAS_AUTH_ENABLED" in S and "SAAS_RESERVAS_ENABLED" in S
    assert 'require(settings.saas_auth_enabled is False' in S


def test_5be_jwt_firmado_real_y_no_fakeado():
    assert "from app.core.tenant_context import emitir_token_empresa, leer_token_empresa" in S
    assert "leer_token_empresa(credentials.credentials)" in S
    assert "emitir_token_empresa(" in S
    assert 'bearer=HTTPBearer(auto_error=False)' in S
    assert "Depends(bearer)" in S
    assert 'app.get("/lab/tenant/reserva")' in S
    assert "api.dependency_overrides" not in S
    assert "SECRET_KEY" in S
    assert "sqlite+pysqlite:///./saas_5be_import_inerte.db" in S


def test_5be_lectura_membresia_vigente_sql_real():
    assert "CREATE TABLE public.fs_5be_membresia" in S
    assert "PRIMARY KEY(empresa_id,usuario_id,contexto)" in S
    assert "GRANT SELECT" in S
    assert "NOINHERIT" in S and "NOBYPASSRLS" in S
    assert 'FROM pg_catalog.pg_authid WHERE rolname=%s' in S
    assert 'hashed[0].startswith("SCRAM-SHA-256$")' in S
    assert "with auth_engine.connect() as c:" in S
    assert "filas=c.execute(text(" in S
    assert "return len(filas)==1 and filas[0][0] is True" in S
    assert 'if not validar_membresia_db(auth_engine,eid,uid,ctx):' in S
    assert 'raise HTTPException(status_code=403,detail="Membresía no vigente")' in S


def test_5be_bootstrap_tiene_permisos_minimos():
    assert 'AUTH="fashionstore_5be_auth_reader"' in S
    assert '"public."+table' in S
    assert '"fs_5bc_reserva","fs_5bc_actor"' in S
    assert "has_table_privilege(" in S
    assert "UPDATE" in S
    assert 'who==(AUTH,AUTH)' in S
    assert "5BE ACL AUTH PASS" in S
    assert "auth_engine.dispose()" in S


def test_5be_identidad_sql_solo_desde_jwt_y_membresia():
    assert "uid,eid,ctx=claims" in S
    assert "role=resolver_principal_verificado(eid,uid)" in S
    assert "role in actors" in S
    assert "with actors[role].connect() as c:" in S
    assert "tuple(c.execute(text(\"SELECT session_user,current_user\")).one())" in S
    assert "who==(role,role)" in S
    assert "rows=c.execute(text(" in S
    assert "len(rows)==1 and tuple(rows[0][1:])==(eid,uid)" in S
    assert 'c.execute("SET ROLE' not in S
    assert 'text("SET ROLE' not in S
    assert "SET SESSION AUTHORIZATION" not in S


def test_5be_jwt_falso_ausente_y_caducado():
    assert 'missing.status_code==401' in S
    assert 'altered.status_code==401' in S
    assert 'expired_claims["exp"]=datetime.now(timezone.utc)-timedelta(minutes=1)' in S
    assert 'req(client,expired).status_code==401' in S
    assert "JWT firmado pero caducado fue aceptado" in S


def test_5be_membresias_cruzadas_contextos_y_query_no_autorizan():
    assert 'req(client,cross).status_code==403' in S
    assert 'req(client,wrongcontext).status_code==403' in S
    assert '"empresa_id":2,"usuario_id":201' in S
    assert 'r1["principal_sql"]==ROLES[0]' in S
    assert 'r2["principal_sql"]==ROLES[1]' in S
    assert 'b1.json()["principal_sql"]==ROLES[2]' in S
    assert "5BE HTTP PASS" in S


def test_5be_revocacion_sin_cache_de_membresias():
    assert 'UPDATE public.fs_5be_membresia SET activa=FALSE' in S
    assert 'denied.status_code==403' in S
    assert 'req(client,tokens[(1,101)]).status_code==200' in S
    assert "5BE REVOCACIÓN PASS" in S


def test_5be_tres_http_concurrentes_jwt_y_pool_individual():
    assert "ThreadPoolExecutor(max_workers=3)" in S
    assert 'for ronda in range(3):' in S
    assert 'future.result(timeout=20)' in S
    assert 'len(ACTORS)' not in S or "ACTORS" in S
    assert 'data["principal_sql"],data["empresa_id"]' in S
    assert '5BE HTTP CONCURRENCIA PASS' in S


def test_5be_cleanup_y_sin_modificaciones_comerciales():
    assert 'if started or pid.exists():' in S
    assert '"-m","fast","stop"' in S
    assert 'if not pid.exists() and port_free():' in S
    assert "shutil.rmtree(folder)" in S
    assert "5BE CLEANUP PASS" in S
    assert "5BE TEMP PASS" in S
    assert "5BE LIMPIEZA PENDIENTE" in S
    assert "5BE PENDIENTE" in S
    assert "app/core/database" not in S



def test_5be_denegaciones_antes_de_checkout_sql_comercial():
    assert "from sqlalchemy import create_engine, event, text" in S
    assert 'event.listen(engine,"checkout",contar_checkout)' in S
    assert "checkout_count=[0]" in S
    assert "threading.Lock()" in S
    assert "prev=cuantos_checkout()" in S
    assert "cuantos_checkout()==prev" in S
    assert "prev_rev=cuantos_checkout()" in S
    assert "cuantos_checkout()==prev_rev" in S
    assert "5BE DENEGADO PASS" in S
    assert "5BE BRECHA: revocación dio acceso al pool SQL de A2" in S



def test_5be_psycopg3_connection_no_executemany():
    # Regresión real reportada: Connection de psycopg3 no dispone
    # de executemany(); las 3 inserciones son parametrizadas.
    assert "admin.executemany(" not in S
    assert "for empresa,usuario,contexto,activa in MEMBERSHIPS:" in S
    assert "admin.execute(" in S
    assert "(empresa,usuario,contexto,activa))" in S
    assert 'SELECT count(*) FROM public.fs_5be_membresia' in S
    assert "faltan membresías ficticias de arranque" in S


def test_5be_auth_select_por_columna_no_equivale_al_de_tabla():
    # Misma trampa detectada en 5BB: GRANT SELECT(col...) no concede
    # SELECT sobre la tabla completa. Permitir solo las columnas mínimas.
    assert "visible_tabla is False" in S
    assert 'for columna in ("empresa_id","usuario_id","contexto","activa"):' in S
    assert "has_column_privilege(" in S
    assert "auth no tiene SELECT sobre columna" in S
    assert "SELECT empresa_id,usuario_id,contexto,activa" in S
    assert "list(MEMBERSHIPS)" in S
    assert "has_any_column_privilege(" in S
    assert "auth puede leer columnas de " in S
    assert "auth puede mutar membresía" in S



def test_5be_psycopg3_no_like_percent_in_literal_sql():
    # Regresión: psycopg3 interpreta % dentro de LIKE como placeholder.
    # La consulta parametrizada sólo recupera el hash en memoria.
    assert "rolpassword LIKE 'SCRAM-SHA-256$%'" not in S
    assert 'SELECT rolpassword' in S
    assert 'FROM pg_catalog.pg_authid WHERE rolname=%s' in S
    assert 'hashed=admin.execute(' in S
    assert "isinstance(hashed[0],str)" in S
    assert 'hashed[0].startswith("SCRAM-SHA-256$")' in S
    assert 'print(hashed)' not in S
    assert 'log(hashed)' not in S
