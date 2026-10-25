"""5BD: pools SQLAlchemy separados por LOGIN PostgreSQL real y concurrencia.

Sólo PostgreSQL 18 NUEVO y desechable en 127.0.0.1:55433.
No usa PG 5432/55432, ni .env, ni tablas comerciales, ni JWT real.
Reutiliza bootstrap/validación/limpieza de 5BC, que ya fue aprobado.

Comprueba tres pooles pequeños QueuePool independientes, un login SCRAM
fijo por pool, RLS por usuario en consultas concurrentes, GUC LOCAL sin
fuga tras checkout/checkin, negación del actor indebido y sin UPDATE
directo. SQL SELECT session_user = rol real, NO SET ROLE ni superuser
en ninguno de los tres pooles.

No es el pool de FastAPI ni conecta todavía JWT a roles seguros. La
selección de actor a partir de JWT validado y membresía vigente es un
bloqueo pendiente; aquí sólo se usa un mapeo ficticio explícito.
Credenciales exclusivas de esta ejecución; 0 servidores persistentes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import runpy
import secrets
import shutil
import sys
import tempfile
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import QueuePool

HERE=Path(__file__).resolve().parent
LAB=runpy.run_path(str(HERE/"62_cluster_temporal_login_scram_rls_5bc.py"))
HOST,PORT=LAB["HOST"],LAB["PORT"]
ROLES,ACTORS=LAB["ROLES"],LAB["ACTORS"]
require=LAB["require"]
postgres_bin=LAB["postgres_bin"]
port_free=LAB["port_free"]
command=LAB["command"]
connect=LAB["connect"]
validate_cluster=LAB["validate_cluster"]
prepare_schema=LAB["prepare_schema"]

ACTOR_MAP={(empresa,uid):rol for rol,uid,empresa,_ in ACTORS}


def resolver_principal_verificado(empresa_id,usuario_id):
    """Selector de laboratorio, NO admite identidad suministrada por JSON.

    En la app real se derivará SOLO tras validar JWT y membresía, aún
    pendiente. Este mapeo fijo prueba el contrato de selección segura.
    """
    role=ACTOR_MAP.get((empresa_id,usuario_id))
    require(role in ROLES,
            "identidad fuera del mapeo ficticio: no obtener conexión SQL")
    return role


def crear_pooles(passwords):
    """Cada engine crea sesiones autenticadas en PostgreSQL, sin cambiar rol."""
    require(set(passwords)==set(ROLES),"credenciales incompletas o extra")
    engines={}
    try:
        for role in ROLES:
            url=URL.create("postgresql+psycopg",username=role,
                password=passwords[role],host=HOST,port=PORT,
                database="postgres")
            engines[role]=create_engine(url,poolclass=QueuePool,
                pool_size=1,max_overflow=0,pool_timeout=8,
                pool_pre_ping=True,
                connect_args={"sslmode":"disable","connect_timeout":6,
                    "options":"-c statement_timeout=15000 -c lock_timeout=3000"})
        return engines
    except BaseException:
        for engine in engines.values():
            engine.dispose()
        raise


def consulta_segura(engines,empresa_id,usuario_id,espera=None):
    """Autenticación real por pool; concurrencia sin mezclar sesiones.

    El manejo de identidades JWT será responsabilidad del futuro 5BE.
    GUC es información transaccional de auditoría, NUNCA autenticación.
    """
    role=resolver_principal_verificado(empresa_id,usuario_id)
    esperado=(role,role)
    with engines[role].connect() as connection:
        who=connection.execute(text("SELECT session_user,current_user")).one()
        require(tuple(who)==esperado,
                "5BD BRECHA: pool conectado como rol SQL equivocado")
        pid=connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        require(connection.execute(text("""SELECT
              row_security_active('public.fs_5bc_reserva'::regclass)
            """)).scalar_one() is True,
            "RLS no está activa para login del pool")
        count=connection.execute(text("""SELECT count(*) FROM
             public.fs_5bc_reserva""")).scalar_one()
        require(count==1,"5BD BRECHA: pool ve más de una reserva")
        actual=connection.execute(text("""SELECT empresa_id,usuario_id
             FROM public.fs_5bc_reserva""")).one()
        require(tuple(actual)==(empresa_id,usuario_id),
                "5BD BRECHA: consulta revela reserva ajena")
        # Ninguna sesión que regresa al pool debe traer GUC de la petición
        # anterior. Es NULL o cadena vacía, según el driver/PG.
        guc=connection.execute(text("""SELECT current_setting(
             'app.usuario_id',true)""")).scalar_one_or_none()
        require(guc in (None,""),"5BD BRECHA: fuga de GUC entre requests")
        intruso=102 if usuario_id==101 else 101
        connection.execute(text("""SELECT set_config(
            'app.usuario_id',:identidad,true)"""),
            {"identidad":str(intruso)})
        spoof=connection.execute(text("""SELECT public.fs_5bc_verificar(
            :empresa,:usuario)"""),
            {"empresa":empresa_id,"usuario":intruso}).scalar_one()
        require(spoof is False,
                "5BD BRECHA: GUC permite suplantar usuario SQL")
        # Para demostrar el solapamiento ENTRE pooles, la barrera se usa
        # ya con la conexión físicamente adquirida en cada hilo.
        if espera is not None:
            espera.wait(timeout=12)
            connection.execute(text("SELECT pg_sleep(0.08)"))
        # Check-in con rollback: GUC LOCAL no queda en la conexión física.
        connection.rollback()
        return role,pid


def auditar_acl_sin_update(engines):
    for role in ROLES:
        with engines[role].connect() as conn:
            has=conn.execute(text("""SELECT has_table_privilege(
                session_user,'public.fs_5bc_reserva','UPDATE')""")).scalar_one()
            require(has is False,"5BD BRECHA: rol tiene UPDATE directo")
            require(tuple(conn.execute(text(
                "SELECT session_user,current_user")).one())==(role,role),
                "5BD BRECHA: sesión usa credenciales DBA")
            conn.rollback()
    print("5BD ACL PASS: 3 pools SQL sin UPDATE directo ni identidad DBA")


def verificar_pooles_y_concurrencia(passwords):
    engines=crear_pooles(passwords)
    try:
        auditar_acl_sin_update(engines)
        # Mismo tenant A: JWT/actor A2 nunca debe pedir sesión como A1.
        require(resolver_principal_verificado(1,101)==ROLES[0],
                "selector no devuelve A1")
        require(resolver_principal_verificado(1,102)==ROLES[1],
                "selector no devuelve A2")
        require(resolver_principal_verificado(2,201)==ROLES[2],
                "selector no devuelve B1")
        for args in ((1,201),(2,101),(1,999)):
            try:
                resolver_principal_verificado(*args)
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "5BD BRECHA: selector acepta identidad inexistente")
        print("5BD SELECTOR PASS: usuarios misma empresa separados, "
              "contextos ajenos denegados antes de checkout")

        # Varias solicitudes comparten la MISMA conexión física cuando
        # vuelven al pool de un mismo actor; con rollback local entre ellas.
        primeros={}
        for role,uid,empresa,_ in ACTORS:
            r,pid=consulta_segura(engines,empresa,uid)
            require(r==role,"pool de rol incorrecto")
            primeros[role]=pid
        for role,uid,empresa,_ in ACTORS:
            r,pid=consulta_segura(engines,empresa,uid)
            require(r==role and pid==primeros[role],
                    "5BD: no se verificó reutilización del mismo backend")
        require(len(set(primeros.values()))==3,
                "5BD BRECHA: dos roles comparten backend físico")
        print("5BD REUSO PASS: 3 backends SQL únicos, reutilizados "
              "sin fuga de GUC ni permisos")

        # Tres roles concurren verdaderamente: barrera tras checkout.
        # Ejecutar 3 rondas, 9 consultas; cada una verifica login+RLS.
        for vuelta in range(3):
            barrier=threading.Barrier(3)
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures=[executor.submit(consulta_segura,engines,empresa,uid,
                                        barrier)
                         for _,uid,empresa,_ in ACTORS]
                result=[future.result(timeout=25) for future in futures]
            require({r for r,_ in result}==set(ROLES),
                    "faltó un actor SQL en concurrencia")
            require(len({pid for _,pid in result})==3,
                    "5BD BRECHA: concurrentes comparten conexión")
            require({r:pid for r,pid in result}==primeros,
                    "backends rotaron o se mezclaron tras checkin")
            print(f"5BD CONCURRENCIA PASS: ronda {vuelta+1}/3, "
                  "3 logins TCP simultáneos con RLS individual")
        print("5BD POOLS PASS: QueuePool autenticado por actor, "
              "sin SET ROLE, sin GUC persistente y con 3 rondas concurrentes")
    finally:
        for engine in engines.values():
            engine.dispose()
        print("5BD DISPOSE PASS: conexiones de los 3 pooles cerradas")


def run():
    require(os.name=="nt","5BD se ensaya exclusivamente en Windows")
    require(port_free(),"55433 ocupado: NO tocar proceso existente")
    initdb,pgctl=postgres_bin()
    admin_pass=secrets.token_urlsafe(36)
    passwords={role:secrets.token_urlsafe(36) for role in ROLES}
    # No reutilizar ningún dato ni directorio del primer parcial.
    directory=Path(tempfile.mkdtemp(prefix="fashionstore-5bd-pool-"))
    data=directory/"fresh_pg18_only_5bd"
    pwfile=directory/"bootstrap_pw.txt"
    logfile=directory/"postgres_5bd.log"
    started=False
    cleanup_ok=False
    try:
        pwfile.write_text(admin_pass+"\n",encoding="ascii")
        print("5BD INITDB: clúster TEMP aislado en 55433...",flush=True)
        command([initdb,"-D",data,"-U","postgres",
                 "--auth-host=scram-sha-256","--auth-local=scram-sha-256",
                 "--pwfile",pwfile,"--no-instructions"],timeout=90)
        pwfile.unlink(missing_ok=True)
        (data/"pg_hba.conf").write_text(
            "local all all scram-sha-256\n"
            "host all all 127.0.0.1/32 scram-sha-256\n"
            "host all all ::1/128 reject\n",encoding="ascii")
        print("5BD START: pg_ctl temporal, sin PIPE heredados...",flush=True)
        command([pgctl,"-D",data,"-l",logfile,"-w","-t","25",
                 "-o",f"-c listen_addresses={HOST} -p {PORT} -c ssl=off",
                 "start"],timeout=55,server_control=True,logfile=logfile)
        started=True
        with connect("postgres",admin_pass) as admin:
            validate_cluster(admin,data)
            print("5BD PREFLIGHT PASS: versión/puerto/IP/directorio "
                  "de PostgreSQL 18 TEMP verificados",flush=True)
            prepare_schema(admin,passwords)
            verificar_pooles_y_concurrencia(passwords)
    finally:
        pwfile.unlink(missing_ok=True)
        pid=data/"postmaster.pid"
        if started or pid.exists():
            print("5BD STOP: deteniendo exclusivamente el clúster TEMP...",
                  flush=True)
            try:
                command([pgctl,"-D",data,"-w","-t","25",
                         "-m","fast","stop"],timeout=50,
                        server_control=True,logfile=logfile)
                print("5BD CLEANUP PASS: instancia desechable detenida",
                      flush=True)
            except Exception as err:
                print("5BD STOP ALERTA: "+str(err),file=sys.stderr)
        if not pid.exists() and port_free():
            shutil.rmtree(directory)
            cleanup_ok=True
            print("5BD TEMP PASS: carpeta temporal eliminada",flush=True)
        else:
            print("5BD LIMPIEZA PENDIENTE: conservar exclusivamente "
                  +str(data),file=sys.stderr,flush=True)
    require(cleanup_ok,
            "instancia temporal aún activa; NO borrar datos ni repetir 5BD")
    print("5BD OK LAB: 3 pooles SQL independientes con SCRAM y concurrencia")
    print("5BD PENDIENTE: integrar JWT/membresía FastAPI en selector de "
          "credenciales, 3 SP comerciales CU16-18 y prueba de carrera "
          "sobre stock; SaaS flags OFF, 5432/55432 intactos")


if __name__=="__main__":
    try:
        run()
    except Exception as e:
        print(f"5BD ERROR: {type(e).__name__}: {e}",file=sys.stderr)
        sys.exit(2)
