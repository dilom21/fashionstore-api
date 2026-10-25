"""5BF: carrera de la última unidad con 2 LOGINS SQL verdaderos.

PostgreSQL18 desechable en 127.0.0.1:55433; NUNCA 5432, 55432,
ni esquema comercial. Datos, roles, dos SP de estado y un SP de reserva
son FICTICIOS. Reutiliza helpers seguros de 5BC/5BD.
No se prueban todavía los tres SP comerciales DEFINITIVOS CU16–CU18.

A1 y A2 (MISMA empresa) ejecutan CALL simultáneo por 1 unidad restante.
Únicamente uno puede reservar. El perdedor recibe P0001 y no crea
reserva, ni reduce stock. A1/A2/B1 autentican por SCRAM individual,
SP SECURITY DEFINER de dueño NOLOGIN con RLS derivado de session_user.
Se comprueba rechazo de usuario ajeno, tenant ajeno y DML directo,
liberación por cancelar/finalizar e idempotencia (no liberar 2 veces).
Todo se crea y elimina en un cluster PG NUEVO, nunca datos empresariales.
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

import psycopg
from psycopg import sql

HERE=Path(__file__).resolve().parent
BD=runpy.run_path(str(HERE/"63_pool_sql_individual_concurrencia_temporal_5bd.py"))
HOST,PORT=BD["HOST"],BD["PORT"]
ROLES=BD["ROLES"]
ACTORS=BD["ACTORS"]
require=BD["require"]
postgres_bin=BD["postgres_bin"]
port_free=BD["port_free"]
command=BD["command"]
connect=BD["connect"]
validate_cluster=BD["validate_cluster"]
prepare_schema=BD["prepare_schema"]
VERIFIER=BD["LAB"]["VERIFIER"]
WRITER=BD["LAB"]["WRITER"]

DDL_FUNCTION=r"""
CREATE FUNCTION public.fs_5bf_empresa_actor()
RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
 SELECT empresa_id FROM public.fs_5bc_actor
 WHERE principal=session_user::name
$$
"""

DDL_CREAR=r"""
CREATE PROCEDURE public.fs_5bf_crear_demo(
 p_empresa integer,p_usuario integer,p_stock integer
) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE afectadas integer;
BEGIN
 IF NOT public.fs_5bc_verificar(p_empresa,p_usuario) THEN
   RAISE EXCEPTION 'Identidad SQL distinta' USING ERRCODE='42501';
 END IF;
 -- UPDATE condicional atomico: PostgreSQL bloquea esta fila y
 -- reevalúa el WHERE tras esperar a la otra transacción concurrente.
 UPDATE public.fs_5bf_stock
 SET reservado=reservado+1
 WHERE id=p_stock AND empresa_id=p_empresa AND reservado<total;
 GET DIAGNOSTICS afectadas=ROW_COUNT;
 IF afectadas!=1 THEN
   RAISE EXCEPTION 'Stock agotado' USING ERRCODE='P0001';
 END IF;
 INSERT INTO public.fs_5bf_reserva(empresa_id,usuario_id,stock_id,estado)
 VALUES(p_empresa,p_usuario,p_stock,'ACTIVA');
END;
$$
"""

DDL_CANCELAR=r"""
CREATE PROCEDURE public.fs_5bf_cancelar_demo(
 p_empresa integer,p_usuario integer,p_reserva integer
) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE inventario integer; afectadas integer;
BEGIN
 IF NOT public.fs_5bc_verificar(p_empresa,p_usuario) THEN
   RAISE EXCEPTION 'Identidad SQL distinta' USING ERRCODE='42501';
 END IF;
 UPDATE public.fs_5bf_reserva
 SET estado='CANCELADA'
 WHERE id=p_reserva AND empresa_id=p_empresa AND usuario_id=p_usuario
   AND estado='ACTIVA'
 RETURNING stock_id INTO inventario;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'Reserva no modificable' USING ERRCODE='42501';
 END IF;
 UPDATE public.fs_5bf_stock
 SET reservado=reservado-1
 WHERE id=inventario AND empresa_id=p_empresa AND reservado>0;
 GET DIAGNOSTICS afectadas=ROW_COUNT;
 IF afectadas!=1 THEN
   RAISE EXCEPTION 'Stock inconsistente' USING ERRCODE='P0001';
 END IF;
END;
$$
"""

DDL_FINALIZAR=DDL_CANCELAR.replace(
    "fs_5bf_cancelar_demo","fs_5bf_finalizar_demo"
).replace("'CANCELADA'","'ATENDIDA'")

PROCS=(
    ("fs_5bf_crear_demo",DDL_CREAR),
    ("fs_5bf_cancelar_demo",DDL_CANCELAR),
    ("fs_5bf_finalizar_demo",DDL_FINALIZAR),
)


def preparar_stock_temporal(admin):
    # PRECONDICION validada por validate_cluster en la base recién creada.
    admin.execute(DDL_FUNCTION)
    admin.execute("""REVOKE ALL ON FUNCTION
        public.fs_5bf_empresa_actor() FROM PUBLIC""")
    admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
        sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("""ALTER FUNCTION public.fs_5bf_empresa_actor()
        OWNER TO {}""").format(sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
        sql.Identifier(VERIFIER)))
    receivers=sql.SQL(",").join(sql.Identifier(x) for x in (*ROLES,WRITER))
    admin.execute(sql.SQL("""GRANT EXECUTE ON FUNCTION
        public.fs_5bf_empresa_actor() TO {}""").format(receivers))

    admin.execute("""CREATE TABLE public.fs_5bf_stock(
        id integer PRIMARY KEY,empresa_id integer NOT NULL,
        total integer NOT NULL CHECK(total>=0),
        reservado integer NOT NULL DEFAULT 0
        CHECK(reservado>=0 AND reservado<=total)
    )""")
    admin.execute("""CREATE TABLE public.fs_5bf_reserva(
        id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        empresa_id integer NOT NULL,usuario_id integer NOT NULL,
        stock_id integer NOT NULL REFERENCES public.fs_5bf_stock(id),
        estado text NOT NULL CHECK(estado IN
            ('ACTIVA','CANCELADA','ATENDIDA'))
    )""")
    admin.execute("INSERT INTO public.fs_5bf_stock VALUES (1,1,1,0),(2,2,1,0)")
    for table in ("fs_5bf_stock","fs_5bf_reserva"):
        admin.execute(sql.SQL("ALTER TABLE public.{} ENABLE ROW LEVEL SECURITY").format(
            sql.Identifier(table)))
        # RLS empresa para los 3 LOGIN y dueño escritor,
        # jamás GUC manipulables.
        all_roles=sql.SQL(",").join(sql.Identifier(x) for x in (*ROLES,WRITER))
        admin.execute(sql.SQL("""CREATE POLICY {} ON public.{}
          FOR SELECT TO {} USING
          (empresa_id=public.fs_5bf_empresa_actor())""").format(
            sql.Identifier(f"fs_5bf_{table}_select"),
            sql.Identifier(table),all_roles))
        admin.execute(sql.SQL("""GRANT SELECT ON public.{} TO {}""").format(
            sql.Identifier(table),all_roles))
        admin.execute(sql.SQL("""GRANT UPDATE ON public.{} TO {}""").format(
            sql.Identifier(table),sql.Identifier(WRITER)))
        admin.execute(sql.SQL("""CREATE POLICY {} ON public.{}
          FOR UPDATE TO {} USING
          (empresa_id=public.fs_5bf_empresa_actor())
          WITH CHECK (empresa_id=public.fs_5bf_empresa_actor())""").format(
            sql.Identifier(f"fs_5bf_{table}_update"),
            sql.Identifier(table),sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""GRANT INSERT
      (empresa_id,usuario_id,stock_id,estado)
      ON public.fs_5bf_reserva TO {}""").format(sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""CREATE POLICY fs_5bf_reserva_insert
      ON public.fs_5bf_reserva FOR INSERT TO {}
      WITH CHECK (empresa_id=public.fs_5bf_empresa_actor())""").format(
      sql.Identifier(WRITER)))

    for name,body in PROCS:
        admin.execute(body)
        signature=f"public.{name}(integer,integer,integer)"
        admin.execute(sql.SQL("REVOKE ALL ON PROCEDURE {} FROM PUBLIC").format(
            sql.SQL(signature)))
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
            sql.Identifier(WRITER)))
        admin.execute(sql.SQL("ALTER PROCEDURE {} OWNER TO {}").format(
            sql.SQL(signature),sql.Identifier(WRITER)))
        admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
            sql.Identifier(WRITER)))
        admin.execute(sql.SQL("GRANT EXECUTE ON PROCEDURE {} TO {}").format(
            sql.SQL(signature),sql.SQL(",").join(sql.Identifier(x) for x in ROLES)))

    expected={name for name,_ in PROCS}
    functions=admin.execute("""SELECT p.proname,p.prosecdef,
       r.rolname,r.rolcanlogin,r.rolsuper,r.rolbypassrls,
       p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
      WHERE n.nspname='public' AND p.proname=ANY(%s)""",
      (list(expected)+["fs_5bf_empresa_actor"],)).fetchall()
    require(len(functions)==4,"faltan tres SP y función de tenant")
    for name,definer,owner,can_login,superuser,bypass,path in functions:
        require(definer is True and can_login is False
                and superuser is False and bypass is False
                and path is True
                and owner==(VERIFIER if name=="fs_5bf_empresa_actor"
                           else WRITER),
                "5BF BRECHA: titularidad/SECDEF/search_path de "+name)
    for actor in ROLES:
        for table in ("fs_5bf_stock","fs_5bf_reserva"):
            require(admin.execute("""SELECT has_table_privilege(
              %s,%s,'UPDATE')""",(actor,"public."+table)
              ).fetchone()[0] is False,
              "5BF BRECHA: actor obtuvo UPDATE directo")
    print("5BF INSTALACIÓN PASS: 3 SP DEMO SECURITY DEFINER NOLOGIN, "
          "RLS por session_user y sin DML directo")


def uno(con,stmt,args=None):
    return con.execute(stmt,args).fetchone()[0]


def llamada(c,proc,empresa,usuario,dato):
    require(proc in {name for name,_ in PROCS},"SP ajeno a lista de ensayo")
    # SQL dinámico se limita a tres constantes internas, nunca inputs HTTP.
    c.execute(f"CALL public.{proc}(%s,%s,%s)",(empresa,usuario,dato))


def comprobar_actor(c,role,empresa,usuario):
    require(c.execute("SELECT session_user,current_user").fetchone()
            ==(role,role),"principal SQL no fue autenticado por SCRAM")
    require(uno(c,"SELECT row_security_active('public.fs_5bf_stock'::regclass)")
            is True,"RLS no activa")
    require(c.execute("""SELECT id,empresa_id FROM public.fs_5bf_stock
      ORDER BY id""").fetchall()==[(empresa,empresa)],
      "actor lee stock de empresa ajena")
    # Los valores 'app.*' provienen de la sesión y no alteran la
    # vinculación principal SQL verificada por la función guard.
    c.execute("SELECT set_config('app.usuario_id',%s,true)",(str(usuario+999),))
    require(uno(c,"SELECT public.fs_5bc_verificar(%s,%s)",
            (empresa,usuario+999)) is False,
            "GUC editable suplantó actor SQL")


def carrera_stock_unico(passw):
    barrier=threading.Barrier(2)
    def competidor(role,usuario):
        with connect(role,passw[role]) as c:
            comprobar_actor(c,role,1,usuario)
            barrier.wait(timeout=12)
            try:
                llamada(c,"fs_5bf_crear_demo",1,usuario,1)
                return role,usuario,"OK"
            except psycopg.Error as e:
                require(e.sqlstate=="P0001",
                        "contención SQL falló con estado distinto a sin stock")
                return role,usuario,"SIN_STOCK"
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures=[
            ex.submit(competidor,ROLES[0],101),
            ex.submit(competidor,ROLES[1],102),
        ]
        salida=[f.result(timeout=28) for f in futures]
    require(sorted(x[2] for x in salida)==["OK","SIN_STOCK"],
            "5BF BRECHA: stock 1 vendido dos veces o rechazado por completo")
    ganadora=next(x for x in salida if x[2]=="OK")
    print("5BF CARRERA PASS: A1/A2 autenticados, 1 ganador "
          "y 1 agotado con SQLSTATE P0001")
    return ganadora


def auditar_negativas(admin,passw,ganadora):
    winner,uid,_=ganadora
    loser=ROLES[1] if winner==ROLES[0] else ROLES[0]
    loser_uid=102 if uid==101 else 101
    stock=admin.execute("SELECT id,total,reservado FROM public.fs_5bf_stock ORDER BY id").fetchall()
    require(stock==[(1,1,1),(2,1,0)],
            "5BF BRECHA: stock tras carrera es incorrecto")
    reservas=admin.execute("""SELECT id,empresa_id,usuario_id,estado
      FROM public.fs_5bf_reserva""").fetchall()
    require(len(reservas)==1 and reservas[0][1:]==(1,uid,"ACTIVA"),
            "5BF BRECHA: se creó más de una reserva")
    reserva_id=reservas[0][0]
    for principal,user,empresa in ((loser,loser_uid,1),(ROLES[2],201,2)):
        with connect(principal,passw[principal]) as c:
            comprobar_actor(c,principal,empresa,user)
            try:
                llamada(c,"fs_5bf_cancelar_demo",1,uid,reserva_id)
            except psycopg.Error as e:
                require(e.sqlstate=="42501","suplantación SQL no dio 42501")
            else:
                raise RuntimeError("5BF BRECHA: actor ajeno canceló reserva")
            try:
                c.execute("""UPDATE public.fs_5bf_stock
                    SET reservado=0 WHERE id=%s""",(empresa,))
            except psycopg.Error as e:
                require(e.sqlstate=="42501",
                        "DML directo no negó privilegio")
            else:
                raise RuntimeError("5BF BRECHA: UPDATE directo de stock")
    with connect(winner,passw[winner]) as c:
        comprobar_actor(c,winner,1,uid)
        llamada(c,"fs_5bf_cancelar_demo",1,uid,reserva_id)
        try:
            llamada(c,"fs_5bf_cancelar_demo",1,uid,reserva_id)
        except psycopg.Error as e:
            require(e.sqlstate=="42501","cancelación repetida no negó")
        else:
            raise RuntimeError("5BF BRECHA: cancelación doble liberó stock")
    check=admin.execute("""SELECT reservado FROM public.fs_5bf_stock
       WHERE empresa_id=1""").fetchone()[0]
    require(check==0,"5BF BRECHA: cancelación no liberó exactamente 1")
    require(uno(admin,"SELECT count(*) FROM public.fs_5bf_reserva "
                "WHERE estado='CANCELADA'")==1,
            "no se confirmó cancelación")
    print("5BF CANCELAR PASS: ganador liberó 1 unidad; "
          "suplantación, DML directo y doble cancelación rechazados")

    with connect(ROLES[2],passw[ROLES[2]]) as b1:
        comprobar_actor(b1,ROLES[2],2,201)
        llamada(b1,"fs_5bf_crear_demo",2,201,2)
        own=b1.execute("""SELECT id FROM public.fs_5bf_reserva
            WHERE estado='ACTIVA'""").fetchall()
        require(len(own)==1,"B1 no ve su propia reserva")
        llamada(b1,"fs_5bf_finalizar_demo",2,201,own[0][0])
        try:
            llamada(b1,"fs_5bf_finalizar_demo",2,201,own[0][0])
        except psycopg.Error as e:
            require(e.sqlstate=="42501","doble finalización no negó")
        else:
            raise RuntimeError("5BF BRECHA: finalización doble")
    rows=admin.execute("""SELECT id,total,reservado
      FROM public.fs_5bf_stock ORDER BY id""").fetchall()
    require(rows==[(1,1,0),(2,1,0)],
            "5BF BRECHA: CU17/CU18 demo no devolvió stock a 0")
    status=admin.execute("""SELECT empresa_id,estado FROM public.fs_5bf_reserva
      ORDER BY id""").fetchall()
    require(status==[(1,"CANCELADA"),(2,"ATENDIDA")],
            "5BF BRECHA: transición de estado inesperada")
    print("5BF FINALIZAR PASS: B1 liberó 1 unidad una sola vez, "
          "sin cruzar empresa ni cambiar A")
    print("5BF ACL/RLS PASS: A1/A2/B1 sin escritura directa, "
          "sólo EXECUTE de SP de demostración")


def run():
    require(os.name=="nt","5BF diseñado solo para Windows")
    require(port_free(),"55433 ocupado; no conectar a otro PostgreSQL")
    initdb,pgctl=postgres_bin()
    admin_pass=secrets.token_urlsafe(36)
    passw={r:secrets.token_urlsafe(36) for r in ROLES}
    folder=Path(tempfile.mkdtemp(prefix="fashionstore-5bf-stock-"))
    data=folder/"fresh_pg18_only_5bf"
    pwfile=folder/"bootstrap_pw.txt"
    logfile=folder/"postgres_5bf.log"
    started=False
    cleaned=False
    try:
        pwfile.write_text(admin_pass+"\n",encoding="ascii")
        print("5BF INITDB: clúster TEMP PG18, sin acceso a empresas reales",flush=True)
        command([initdb,"-D",data,"-U","postgres",
                 "--auth-host=scram-sha-256","--auth-local=scram-sha-256",
                 "--pwfile",pwfile,"--no-instructions"],timeout=90)
        pwfile.unlink(missing_ok=True)
        (data/"pg_hba.conf").write_text(
            "local all all scram-sha-256\n"
            "host all all 127.0.0.1/32 scram-sha-256\n"
            "host all all ::1/128 reject\n",encoding="ascii")
        print("5BF START: PostgreSQL 18 exclusivamente 127.0.0.1:55433",flush=True)
        command([pgctl,"-D",data,"-l",logfile,"-w","-t","25",
                 "-o",f"-c listen_addresses={HOST} -p {PORT} -c ssl=off",
                 "start"],timeout=55,server_control=True,logfile=logfile)
        started=True
        with connect("postgres",admin_pass) as admin:
            validate_cluster(admin,data)
            print("5BF PREFLIGHT PASS: puerto/IP/major/data_directory TEMP")
            prepare_schema(admin,passw)
            preparar_stock_temporal(admin)
            ganador=carrera_stock_unico(passw)
            auditar_negativas(admin,passw,ganador)
    finally:
        pwfile.unlink(missing_ok=True)
        pid=data/"postmaster.pid"
        if started or pid.exists():
            print("5BF STOP: deteniendo solamente el clúster TEMP",flush=True)
            try:
                command([pgctl,"-D",data,"-w","-t","25",
                         "-m","fast","stop"],timeout=50,
                         server_control=True,logfile=logfile)
                print("5BF CLEANUP PASS: instancia detenida",flush=True)
            except Exception as error:
                print("5BF STOP ALERTA: "+str(error),file=sys.stderr)
        if not pid.exists() and port_free():
            shutil.rmtree(folder)
            cleaned=True
            print("5BF TEMP PASS: carpeta temporal eliminada",flush=True)
        else:
            print("5BF LIMPIEZA PENDIENTE: "+str(data),
                  file=sys.stderr,flush=True)
    require(cleaned,
            "instancia 5BF aún activa; no borrar ni repetir hasta diagnosticar")
    print("5BF OK LAB: stock=1, concurrencia A1/A2, liberación y ACL/RLS")
    print("5BF PENDIENTE: montar SP comerciales CU16–CU18 reales "
          "en esquema empresarial desechable, luego integrar FastAPI "
          "con proveedor seguro escalable; flags SaaS OFF")


if __name__=="__main__":
    try:
        run()
    except Exception as error:
        print(f"5BF ERROR: {type(error).__name__}: {error}",file=sys.stderr)
        sys.exit(2)
