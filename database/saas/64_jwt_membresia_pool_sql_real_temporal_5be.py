"""5BE: JWT firmado + membresía SQL REAL → pool SCRAM del actor correcto.

SÓLO LAB NUEVO PostgreSQL18 127.0.0.1:55433, carpeta TEMP.
NO usa/lee PostgreSQL 5432 ni 55432, ni datos de negocios ni .env.
Prueba una API FastAPI TESTCLIENT aislada (NO modifica routers reales)
que decodifica JWT real, consulta membresía activa en PostgreSQL usando
un cuarto principal SQL de SOLO LECTURA, y SÓLO ENTONCES selecciona
un pool PostgreSQL individual A1/A2/B1 del ensayo 5BD.

Negativas: JWT ausente/falso/caducado; membresía cruzada, revocada,
contexto equivocado, empresa inexistente; GET con empresa_id/usuario_id
maliciosos no cambia la identidad; SQL A1 != SQL A2 dentro de empresa A.
0 SET ROLE, 0 password por JWT, 0 superuser en los pooles HTTP.
No habilita SAAS_AUTH_ENABLED ni SAAS_RESERVAS_ENABLED.
Los tres SP comerciales CU16-18 y pool productivo SIGUEN pendientes.
"""
from __future__ import annotations

import os
# Nunca inicializar app.core.database con la DATABASE_URL REAL del .env.
os.environ["DATABASE_URL"]="sqlite+pysqlite:///./saas_5be_import_inerte.db"
os.environ["SECRET_KEY"]="5be-jwt-lab-firmado-no-productivo-clave-aislada-2026"
os.environ["SAAS_AUTH_ENABLED"]="false"
os.environ["SAAS_RESERVAS_ENABLED"]="false"

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import runpy
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import jwt

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import QueuePool
from psycopg import sql

ROOT=Path(__file__).resolve().parents[2]
if not (ROOT/"app"/"core"/"tenant_context.py").is_file():
    raise RuntimeError("5BE: no es la raíz del proyecto conocido")
sys.path.insert(0,str(ROOT))
from app.core.tenant_context import emitir_token_empresa, leer_token_empresa
from app.core.config import settings

HERE=Path(__file__).resolve().parent
BD=runpy.run_path(str(HERE/"63_pool_sql_individual_concurrencia_temporal_5bd.py"))
HOST,PORT=BD["HOST"],BD["PORT"]
ROLES,ACTORS=BD["ROLES"],BD["ACTORS"]
require=BD["require"]
postgres_bin=BD["postgres_bin"]
port_free=BD["port_free"]
command=BD["command"]
connect=BD["connect"]
validate_cluster=BD["validate_cluster"]
prepare_schema=BD["prepare_schema"]
crear_pooles=BD["crear_pooles"]
resolver_principal_verificado=BD["resolver_principal_verificado"]
AUTH="fashionstore_5be_auth_reader"
MEMBERSHIPS=(
    (1,101,"cliente",True),
    (1,102,"cliente",True),
    (2,201,"personal",True),
)
bearer=HTTPBearer(auto_error=False)


def preparar_membresias(admin,passwd):
    # Esta tabla ficticia NO representa todos los checks reales de suscripción
    # del repositorio SaaS. Solamente prueba la separación de identidad.
    admin.execute("""CREATE TABLE public.fs_5be_membresia(
      empresa_id integer NOT NULL,usuario_id integer NOT NULL,
      contexto text NOT NULL,activa boolean NOT NULL,
      PRIMARY KEY(empresa_id,usuario_id,contexto)
    )""")
    # psycopg3.Connection no implementa .executemany; para tres
    # membresías ficticias usar execute parametrizado por registro.
    for empresa,usuario,contexto,activa in MEMBERSHIPS:
        admin.execute("""INSERT INTO public.fs_5be_membresia
          (empresa_id,usuario_id,contexto,activa) VALUES (%s,%s,%s,%s)""",
          (empresa,usuario,contexto,activa))
    require(admin.execute("SELECT count(*) FROM public.fs_5be_membresia"
        ).fetchone()[0]==3,"faltan membresías ficticias de arranque")
    admin.execute("REVOKE ALL ON public.fs_5be_membresia FROM PUBLIC")
    admin.execute("SET password_encryption='scram-sha-256'")
    admin.execute(sql.SQL("""CREATE ROLE {} LOGIN NOINHERIT
      NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD {}""").format(
      sql.Identifier(AUTH),sql.Literal(passwd)))
    admin.execute(sql.SQL("""GRANT SELECT
      (empresa_id,usuario_id,contexto,activa)
      ON public.fs_5be_membresia TO {}""").format(sql.Identifier(AUTH)))
    admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
      sql.Identifier(AUTH)))
    # psycopg3 interpreta '%' como introductor de placeholder aun dentro
    # de un literal SQL LIKE. Leer SOLO en memoria y validar prefijo
    # evita el parser '%'' y jamás imprime la credencial ni su hash.
    hashed=admin.execute("""SELECT rolpassword
       FROM pg_catalog.pg_authid WHERE rolname=%s""",(AUTH,)).fetchone()
    require(hashed is not None and isinstance(hashed[0],str)
            and hashed[0].startswith("SCRAM-SHA-256$"),
            "el login de lectura no usa SCRAM")
    print("5BE MEMBRESÍA PASS: cuenta auth SQL separada, "
          "SELECT únicamente de membresías ficticias")


def pool_lectura_auth(password):
    url=URL.create("postgresql+psycopg",username=AUTH,
        password=password,host=HOST,port=PORT,database="postgres")
    return create_engine(url,poolclass=QueuePool,pool_size=2,
        max_overflow=0,pool_timeout=8,pool_pre_ping=True,
        connect_args={"sslmode":"disable","connect_timeout":6,
          "options":"-c statement_timeout=15000 -c lock_timeout=3000"})


def comprobar_acls_auth(auth_engine):
    with auth_engine.connect() as c:
        who=tuple(c.execute(text("SELECT session_user,current_user")).one())
        require(who==(AUTH,AUTH),"cuenta de membresías usa identidad DBA")
        # 5BB: SELECT(columnas) NO equivale a SELECT sobre la tabla.
        # La lectura de la tabla completa debe seguir denegada.
        visible_tabla=c.execute(text("""SELECT has_table_privilege(
          session_user,'public.fs_5be_membresia','SELECT')""")).scalar_one()
        require(visible_tabla is False,
                "auth tiene SELECT de tabla completa; excede ACL mínima")
        for columna in ("empresa_id","usuario_id","contexto","activa"):
            allowed=c.execute(text("""SELECT has_column_privilege(
               session_user,'public.fs_5be_membresia',:columna,'SELECT')"""),
               {"columna":columna}).scalar_one()
            require(allowed is True,
                    "auth no tiene SELECT sobre columna "+columna)
        # No basta con ACL de catálogo: leer mediante login restringido.
        filas=c.execute(text("""SELECT empresa_id,usuario_id,contexto,activa
          FROM public.fs_5be_membresia ORDER BY empresa_id,usuario_id""")).all()
        require([tuple(fila) for fila in filas]==list(MEMBERSHIPS),
                "auth no recupera las tres membresías ficticias esperadas")
        for table in ("fs_5bc_reserva","fs_5bc_actor"):
            permitido=c.execute(text("""SELECT has_any_column_privilege(
              session_user,:tabla,'SELECT')"""),
              {"tabla":"public."+table}).scalar_one()
            require(permitido is False,
                    "auth puede leer columnas de "+table)
        dml=c.execute(text("""SELECT has_any_column_privilege(
          session_user,'public.fs_5be_membresia','UPDATE')""")).scalar_one()
        require(dml is False,"auth puede mutar membresía")
        c.rollback()
    print("5BE ACL AUTH PASS: verificador de membresía sin acceso "
          "a reservas, mapa privado o UPDATE")


def validar_membresia_db(auth_engine,empresa,usuario,contexto):
    with auth_engine.connect() as c:
        who=c.execute(text("SELECT session_user")).scalar_one()
        require(who==AUTH,"5BE BRECHA: membresía consultada como DBA")
        filas=c.execute(text("""SELECT activa FROM public.fs_5be_membresia
          WHERE empresa_id=:empresa AND usuario_id=:usuario
            AND contexto=:contexto"""),
          {"empresa":empresa,"usuario":usuario,"contexto":contexto}).all()
        c.rollback()
    return len(filas)==1 and filas[0][0] is True


def construir_api_aislada(auth_engine,actors):
    """Nunca reemplazar dependencias del backend de entrega."""
    app=FastAPI()
    def autorizacion(
      credentials: HTTPAuthorizationCredentials | None=Depends(bearer)
    ):
        if credentials is None:
            raise HTTPException(status_code=401,detail="JWT requerido")
        claims=leer_token_empresa(credentials.credentials)
        if claims is None:
            raise HTTPException(status_code=401,detail="JWT inválido")
        uid,eid,ctx=claims
        if not validar_membresia_db(auth_engine,eid,uid,ctx):
            raise HTTPException(status_code=403,detail="Membresía no vigente")
        # El par uid/eid ES el JWT firmado y confirmado en base, nunca
        # viene de query, body, headers personalizables ni GUC.
        try:
            role=resolver_principal_verificado(eid,uid)
        except RuntimeError:
            raise HTTPException(status_code=403,
                                detail="Sin principal SQL autorizado") from None
        require(role in actors,"actor carece de engine autorizado")
        return role,eid,uid

    @app.get("/lab/tenant/reserva")
    def mi_reserva(actor=Depends(autorizacion)):
        role,eid,uid=actor
        with actors[role].connect() as c:
            who=tuple(c.execute(text("SELECT session_user,current_user")).one())
            require(who==(role,role),"5BE BRECHA: SQL login distinto al JWT")
            require(c.execute(text("""SELECT
                row_security_active('public.fs_5bc_reserva'::regclass)
              """)).scalar_one() is True,"RLS inactiva en actor")
            rows=c.execute(text("""SELECT id,empresa_id,usuario_id
                  FROM public.fs_5bc_reserva""")).all()
            c.rollback()
        require(len(rows)==1 and tuple(rows[0][1:])==(eid,uid),
                "5BE BRECHA: JWT válido consultó filas RLS ajenas")
        return {"reserva_id":rows[0][0],"empresa_id":eid,
                "usuario_id":uid,"principal_sql":role}
    return app


def probar_http_jwt_y_sql(admin,passwords,auth_pass):
    actors=crear_pooles(passwords)
    auth_engine=pool_lectura_auth(auth_pass)
    # Registrar checkout ÚNICAMENTE de los tres pooles comerciales.
    # El lector de membresía es independiente y nunca puede leer reservas.
    lock=threading.Lock()
    checkout_count=[0]
    def contar_checkout(dbapi_con,connection_record,connection_proxy):
        with lock:
            checkout_count[0]+=1
    for engine in actors.values():
        event.listen(engine,"checkout",contar_checkout)
    def cuantos_checkout():
        with lock:
            return checkout_count[0]
    try:
        comprobar_acls_auth(auth_engine)
        api=construir_api_aislada(auth_engine,actors)
        tokens={(eid,uid):emitir_token_empresa(
            usuario_id=uid,empresa_id=eid,
            contexto=("personal" if eid==2 else "cliente"))
            for _,uid,eid,_ in ACTORS}
        def req(client,token,params=None):
            return client.get("/lab/tenant/reserva",
                headers={"Authorization":"Bearer "+token},params=params)
        with TestClient(api) as client:
            prev=cuantos_checkout()
            missing=client.get("/lab/tenant/reserva")
            require(missing.status_code==401,"JWT ausente aceptado")
            altered=req(client,tokens[(1,101)]+"x")
            require(altered.status_code==401,"JWT adulterado aceptado")
            expired_claims=jwt.decode(
                tokens[(1,101)],settings.secret_key,
                algorithms=[settings.jwt_algorithm])
            expired_claims["exp"]=datetime.now(timezone.utc)-timedelta(minutes=1)
            expired=jwt.encode(expired_claims,settings.secret_key,
                               algorithm=settings.jwt_algorithm)
            require(req(client,expired).status_code==401,
                    "JWT firmado pero caducado fue aceptado")
            # Token bien firmado pero empresa distinta a la del usuario.
            cross=emitir_token_empresa(
                usuario_id=101,empresa_id=2,contexto="cliente")
            require(req(client,cross).status_code==403,
                    "membresía interempresa inexistente aceptada")
            wrongcontext=emitir_token_empresa(
                usuario_id=101,empresa_id=1,contexto="personal")
            require(req(client,wrongcontext).status_code==403,
                    "contexto de rol incorrecto aceptado")
            require(cuantos_checkout()==prev,
                    "5BE BRECHA: solicitud 401/403 obtuvo conexión "
                    "del pool comercial antes de autorizarse")
            print("5BE DENEGADO PASS: JWT inválido/cruzado/expirado "
                  "no retiró conexiones de A1/A2/B1")
            # Firma válida nunca permite elegir empresa/usuario vía query.
            a1=req(client,tokens[(1,101)],
                {"empresa_id":2,"usuario_id":201})
            a2=req(client,tokens[(1,102)],
                {"empresa_id":2,"usuario_id":201})
            require(a1.status_code==200 and a2.status_code==200,
                    "miembros reales A1/A2 rechazados")
            r1,r2=a1.json(),a2.json()
            require(r1["principal_sql"]==ROLES[0] and
                    r1["reserva_id"]==1 and r1["usuario_id"]==101 and
                    r1["empresa_id"]==1,
                    "A1 pudo elegir otro SQL actor/tenant por query")
            require(r2["principal_sql"]==ROLES[1] and
                    r2["reserva_id"]==2 and r2["usuario_id"]==102,
                    "A2 comparte identidad de A1")
            b1=req(client,tokens[(2,201)])
            require(b1.status_code==200 and
                    b1.json()["principal_sql"]==ROLES[2] and
                    b1.json()["reserva_id"]==3,
                    "personal B1 no accede a su reserva")
            print("5BE HTTP PASS: JWT reales, membresías SQL y selección "
                  "A1/A2/B1 del pool SCRAM sin cross-tenant")

            # Retirar membresía en la base, el MISMO JWT firmado debe dejar
            # de servir de inmediato; sin caché de membresía.
            admin.execute("""UPDATE public.fs_5be_membresia SET activa=FALSE
                  WHERE empresa_id=1 AND usuario_id=102 AND contexto='cliente'""")
            prev_rev=cuantos_checkout()
            denied=req(client,tokens[(1,102)])
            require(denied.status_code==403,
                    "JWT de A2 siguió autorizado tras revocar membresía")
            require(cuantos_checkout()==prev_rev,
                    "5BE BRECHA: revocación dio acceso al pool SQL de A2")
            require(req(client,tokens[(1,101)]).status_code==200,
                    "revocación de A2 afectó A1")
            print("5BE REVOCACIÓN PASS: JWT A2 vigente rechazado por "
                  "membresía revocada sin checkout comercial")

            # Concurrencia HTTP; cada token debe abrir sólo su propio pool
            # aun al compartir tenant A y con suscripción/revocation actual.
            admin.execute("""UPDATE public.fs_5be_membresia SET activa=TRUE
                WHERE empresa_id=1 AND usuario_id=102 AND contexto='cliente'""")
            for ronda in range(3):
                with ThreadPoolExecutor(max_workers=3) as pool:
                    calls=[pool.submit(req,client,tokens[(eid,uid)])
                           for _,uid,eid,_ in ACTORS]
                    responses=[future.result(timeout=20) for future in calls]
                require(all(x.status_code==200 for x in responses),
                        "respuesta concurrente JWT falló")
                for expected,res in zip(ACTORS,responses):
                    role,uid,eid,rid=expected
                    data=res.json()
                    require((data["principal_sql"],data["empresa_id"],
                             data["usuario_id"],data["reserva_id"])
                            ==(role,eid,uid,rid),
                            "5BE BRECHA: concurrencia HTTP mezcló identidad")
                print(f"5BE HTTP CONCURRENCIA PASS: {ronda+1}/3, "
                      "3 tokens y tres roles SQL independientes")
        print("5BE AUTH→SQL PASS: jamás confiar en empresa/usuario externo "
              "sin JWT firmado y membresía vigente")
    finally:
        auth_engine.dispose()
        for pool in actors.values():
            pool.dispose()
        print("5BE DISPOSE PASS: 4 pooles temporales cerrados")


def run():
    require(os.name=="nt","5BE únicamente en Windows LAB")
    require(port_free(),"55433 ocupado: no conectar a procesos ajenos")
    require(settings.saas_auth_enabled is False
            and settings.saas_reservas_enabled is False,
            "las dos flags SaaS deben continuar apagadas")
    initdb,pgctl=postgres_bin()
    pw=secrets.token_urlsafe(36)
    passwords={role:secrets.token_urlsafe(36) for role in ROLES}
    auth_pw=secrets.token_urlsafe(36)
    folder=Path(tempfile.mkdtemp(prefix="fashionstore-5be-jwt-"))
    data=folder/"fresh_pg18_only_5be"
    pwfile=folder/"bootstrap_pw.txt"
    logfile=folder/"postgres_5be.log"
    started=False
    cleaned=False
    try:
        pwfile.write_text(pw+"\n",encoding="ascii")
        print("5BE INITDB: clúster temporal aislado para JWT→SQL...",flush=True)
        command([initdb,"-D",data,"-U","postgres",
                 "--auth-host=scram-sha-256","--auth-local=scram-sha-256",
                 "--pwfile",pwfile,"--no-instructions"],timeout=90)
        pwfile.unlink(missing_ok=True)
        (data/"pg_hba.conf").write_text(
            "local all all scram-sha-256\n"
            "host all all 127.0.0.1/32 scram-sha-256\n"
            "host all all ::1/128 reject\n",encoding="ascii")
        print("5BE START: PostgreSQL nuevo en 127.0.0.1:55433...",flush=True)
        command([pgctl,"-D",data,"-l",logfile,"-w","-t","25",
                 "-o",f"-c listen_addresses={HOST} -p {PORT} -c ssl=off",
                 "start"],timeout=55,server_control=True,logfile=logfile)
        started=True
        with connect("postgres",pw) as admin:
            validate_cluster(admin,data)
            print("5BE PREFLIGHT PASS: PG18/55433 y data_directory temporales")
            prepare_schema(admin,passwords)
            preparar_membresias(admin,auth_pw)
            probar_http_jwt_y_sql(admin,passwords,auth_pw)
    finally:
        pwfile.unlink(missing_ok=True)
        pid=data/"postmaster.pid"
        if started or pid.exists():
            print("5BE STOP: deteniendo exclusivamente PG temporal...",flush=True)
            try:
                command([pgctl,"-D",data,"-w","-t","25",
                         "-m","fast","stop"],timeout=50,
                        server_control=True,logfile=logfile)
                print("5BE CLEANUP PASS: instancia temporal detenida",
                      flush=True)
            except Exception as err:
                print("5BE STOP ALERTA: "+str(err),file=sys.stderr)
        if not pid.exists() and port_free():
            shutil.rmtree(folder)
            cleaned=True
            print("5BE TEMP PASS: carpeta temporal eliminada",flush=True)
        else:
            print("5BE LIMPIEZA PENDIENTE: "+str(data),
                  file=sys.stderr,flush=True)
    require(cleaned,
            "no se detuvo el clúster TEMP; NO borrar ni repetir sin revisar")
    print("5BE OK LAB: firma JWT + membresía real SQL → pool SCRAM correcto")
    print("5BE PENDIENTE: aplicación comercial real, secretos administrados, "
          "conexiones multiusuario escalables, CU16–CU18 y carrera de stock; "
          "flags SaaS OFF; 5432/55432 intactos")


if __name__=="__main__":
    try:
        run()
    except Exception as err:
        print(f"5BE ERROR: {type(err).__name__}: {err}",file=sys.stderr)
        sys.exit(2)
