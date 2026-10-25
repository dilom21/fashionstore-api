"""5BC — autenticación PostgreSQL REAL por usuario, en clúster DESECHABLE.

Crea un NUEVO clúster PostgreSQL 18 en un TemporaryDirectory aleatorio,
127.0.0.1:55433 exclusivamente. NO usa PostgreSQL :5432 ni :55432,
ni lee .env, ni copia datos reales. No hay COMMIT en bases existentes.
Detiene la instancia y borra el directorio temporal al terminar.

Usa SCRAM-SHA-256 y tres logins SQL independientes con contraseñas
aleatorias, distintas y exclusivas de este proceso (NO imprimirlas).
Un owner NOLOGIN verifica session_user usando un mapa privado con RLS.
Otro owner NOLOGIN ejecuta un procedimiento protegido SECURITY DEFINER.
Se prueban conexiones TCP separadas de A1/A2/B1, RLS individual,
inmutabilidad del actor ante GUC manipuladas, ACL sin UPDATE y un CALL
legítimo/uno ajeno con SQLSTATE 42501. No es el esquema comercial.

Esta prueba NO valida todavía el pool FastAPI/JWT, los 3 SP comerciales
ni condiciones de concurrencia: esos son pasos posteriores.

Requisitos: PostgreSQL 18 local con initdb.exe y pg_ctl.exe; Windows.
NO ejecutar como administrador del sistema ni desde un servicio de PG.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

import psycopg
from psycopg import sql

HOST = "127.0.0.1"
PORT = 55433
ROLES = ("fashionstore_5bc_a1", "fashionstore_5bc_a2", "fashionstore_5bc_b1")
VERIFIER = "fashionstore_5bc_guard_owner"
WRITER = "fashionstore_5bc_writer_owner"
ACTORS = (
    (ROLES[0], 101, 1, 1),
    (ROLES[1], 102, 1, 2),
    (ROLES[2], 201, 2, 3),
)


def require(ok, message):
    if not ok:
        raise RuntimeError("5BC BLOQUEADO: " + message)


def postgres_bin():
    """Solo descubrir ejecutables del servidor; nunca servicios existentes."""
    choices = []
    if os.name == "nt":
        program_files = [os.environ.get("ProgramFiles", r"C:\Program Files")]
        program_files.append(os.environ.get("ProgramW6432", r"C:\Program Files"))
        for base in program_files:
            choices.append(Path(base) / "PostgreSQL" / "18" / "bin")
    for cmd in ("initdb", "pg_ctl"):
        located = shutil.which(cmd)
        if located:
            choices.append(Path(located).resolve().parent)
    for folder in choices:
        initdb = folder / ("initdb.exe" if os.name=="nt" else "initdb")
        pg_ctl = folder / ("pg_ctl.exe" if os.name=="nt" else "pg_ctl")
        if initdb.is_file() and pg_ctl.is_file():
            ver = subprocess.run([str(initdb), "--version"],
                capture_output=True,text=True,timeout=8,errors="replace")
            require(ver.returncode==0 and re.search(
                    r"PostgreSQL\)?\s+18(?:[.\s]|$)", ver.stdout
                ) is not None,
                "solamente se acepta initdb PostgreSQL 18")
            return initdb, pg_ctl
    raise RuntimeError("5BC: no se encontraron initdb/pg_ctl PostgreSQL 18. "
                       "Instala el paquete servidor PG18 o añade su bin a PATH.")


def port_free():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((HOST,PORT)) != 0


def command(parts, timeout=90):
    p = subprocess.run([str(x) for x in parts], text=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=timeout,errors="replace")
    if p.returncode != 0:
        # Ningún argumento de los comandos contiene contraseña.
        detail=(p.stderr.strip() or p.stdout.strip())[-1100:]
        raise RuntimeError("5BC: falló proceso local PostgreSQL: "+detail)
    return p


def connect(user,password,db="postgres"):
    return psycopg.connect(host=HOST,port=PORT,dbname=db,
        user=user,password=password,sslmode="disable",
        connect_timeout=6,autocommit=True,
        options="-c statement_timeout=15000 -c lock_timeout=3000")


def one(conn,stmt,args=None):
    return conn.execute(stmt,args).fetchone()[0]


def validate_cluster(conn,datadir):
    row=conn.execute("""SELECT current_database(),session_user,
       inet_server_addr()::text,inet_server_port(),
       current_setting('server_version_num')::integer,
       current_setting('data_directory')""").fetchone()
    db,login,addr,port,version,actual_dir=row
    require(db=="postgres" and login=="postgres" and addr==HOST
            and port==PORT and version//10000==18,
            "el puerto no lleva a la instancia temporal PostgreSQL 18")
    require(Path(actual_dir).resolve()==datadir.resolve(),
            "data_directory no coincide con el creado por 5BC")
    require(one(conn,"SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                (list(ROLES)+[VERIFIER,WRITER],))==0,
            "roles de 5BC ya existen: instalación no fresca")


def prepare_schema(admin,passwords):
    """Todo el DDL afecta ÚNICAMENTE la base NUEVA en :55433."""
    # No depender del default de una instalación PostgreSQL diferente.
    admin.execute("SET password_encryption='scram-sha-256'")
    admin.execute("""CREATE TABLE public.fs_5bc_actor(
        principal name PRIMARY KEY, empresa_id integer NOT NULL,
        usuario_id integer NOT NULL, UNIQUE(empresa_id,usuario_id))""")
    admin.execute("REVOKE ALL ON public.fs_5bc_actor FROM PUBLIC")
    admin.execute("ALTER TABLE public.fs_5bc_actor ENABLE ROW LEVEL SECURITY")
    for role,uid,enterprise,_ in ACTORS:
        admin.execute(sql.SQL("""CREATE ROLE {} LOGIN NOINHERIT
            NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
            PASSWORD {}""").format(
            sql.Identifier(role),sql.Literal(passwords[role])
        ))
        admin.execute("INSERT INTO public.fs_5bc_actor VALUES (%s,%s,%s)",
                      (role,enterprise,uid))
    scram=admin.execute("""SELECT rolname,rolpassword
      FROM pg_catalog.pg_authid
      WHERE rolname=ANY(%s) ORDER BY rolname""",
      (list(ROLES),)).fetchall()
    require(len(scram)==3 and all(
        hashed is not None and hashed.startswith("SCRAM-SHA-256$")
        for _,hashed in scram
    ),"los tres roles no tienen credenciales SCRAM")
    for owner in (VERIFIER,WRITER):
        admin.execute(sql.SQL("""CREATE ROLE {} NOLOGIN NOINHERIT
            NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB""").format(
            sql.Identifier(owner)))
    admin.execute(sql.SQL("""GRANT SELECT(principal,empresa_id,usuario_id)
        ON public.fs_5bc_actor TO {}""").format(sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("""CREATE POLICY fs_5bc_guard_map
        ON public.fs_5bc_actor FOR SELECT TO {} USING(TRUE)""").format(
            sql.Identifier(VERIFIER)))
    admin.execute("""CREATE FUNCTION public.fs_5bc_verificar(
        p_empresa_id integer,p_usuario_id integer)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $verify$ SELECT EXISTS(
          SELECT 1 FROM public.fs_5bc_actor m
          WHERE m.principal=session_user::name
            AND m.empresa_id=p_empresa_id
            AND m.usuario_id=p_usuario_id
        ) $verify$""")
    admin.execute("REVOKE ALL ON FUNCTION public.fs_5bc_verificar(integer,integer) FROM PUBLIC")
    # CREATE sólo durante transferencia de titularidad y luego revocación.
    admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
        sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("""ALTER FUNCTION public.fs_5bc_verificar(integer,integer)
        OWNER TO {}""").format(sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
        sql.Identifier(VERIFIER)))
    admin.execute(sql.SQL("""GRANT EXECUTE ON FUNCTION
        public.fs_5bc_verificar(integer,integer) TO {},{},{},{}""").format(
            *(sql.Identifier(x) for x in (*ROLES,WRITER))
        ))
    admin.execute("""CREATE TABLE public.fs_5bc_reserva(
       id integer PRIMARY KEY, empresa_id integer NOT NULL,
       usuario_id integer NOT NULL, estado text NOT NULL)""")
    admin.execute("""INSERT INTO public.fs_5bc_reserva
       VALUES(1,1,101,'PENDIENTE'),(2,1,102,'PENDIENTE'),
             (3,2,201,'PENDIENTE')""")
    admin.execute("ALTER TABLE public.fs_5bc_reserva ENABLE ROW LEVEL SECURITY")
    roles=sql.SQL(",").join(sql.Identifier(x) for x in ROLES)
    admin.execute(sql.SQL("""GRANT SELECT ON public.fs_5bc_reserva TO {}""").format(
        roles))
    admin.execute(sql.SQL("""CREATE POLICY fs_5bc_own_select ON
        public.fs_5bc_reserva FOR SELECT TO {} USING
        (public.fs_5bc_verificar(empresa_id,usuario_id))""").format(roles))
    admin.execute(sql.SQL("""GRANT SELECT ON public.fs_5bc_reserva
        TO {}""").format(sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""GRANT UPDATE(estado) ON public.fs_5bc_reserva
        TO {}""").format(sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""CREATE POLICY fs_5bc_writer_select ON
        public.fs_5bc_reserva FOR SELECT TO {} USING
        (public.fs_5bc_verificar(empresa_id,usuario_id))""").format(
            sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""CREATE POLICY fs_5bc_writer_update ON
        public.fs_5bc_reserva FOR UPDATE TO {} USING
        (public.fs_5bc_verificar(empresa_id,usuario_id))
        WITH CHECK (public.fs_5bc_verificar(empresa_id,usuario_id))""").format(
            sql.Identifier(WRITER)))
    admin.execute("""CREATE PROCEDURE public.fs_5bc_confirmar(
      p_reserva integer,p_empresa integer,p_usuario integer)
      LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp
      AS $sp$
      DECLARE n integer;
      BEGIN
        IF NOT public.fs_5bc_verificar(p_empresa,p_usuario) THEN
          RAISE EXCEPTION 'Actor SQL no acreditado' USING ERRCODE='42501';
        END IF;
        UPDATE public.fs_5bc_reserva
          SET estado='CONFIRMADA'
          WHERE id=p_reserva AND empresa_id=p_empresa
            AND usuario_id=p_usuario AND estado='PENDIENTE';
        GET DIAGNOSTICS n=ROW_COUNT;
        IF n!=1 THEN
          RAISE EXCEPTION 'Reserva no autorizada' USING ERRCODE='42501';
        END IF;
      END
      $sp$""")
    admin.execute("""REVOKE ALL ON PROCEDURE
        public.fs_5bc_confirmar(integer,integer,integer) FROM PUBLIC""")
    admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
        sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""ALTER PROCEDURE
        public.fs_5bc_confirmar(integer,integer,integer) OWNER TO {}""").format(
            sql.Identifier(WRITER)))
    admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
        sql.Identifier(WRITER)))
    admin.execute(sql.SQL("""GRANT EXECUTE ON PROCEDURE
        public.fs_5bc_confirmar(integer,integer,integer)
        TO {}""").format(roles))
    # Un nuevo PG18 suele otorgar USAGE a PUBLIC, pero nunca asumirlo.
    admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
        sql.SQL(",").join(sql.Identifier(x) for x in (*ROLES,VERIFIER,WRITER))))
    print("5BC BOOTSTRAP PASS: SCRAM para A1/A2/B1, dos owners NOLOGIN, "
          "RLS actor/empresa y SP de demostración")


def audit_live(admin,passwords):
    # Si dos roles compartieran credenciales habría una brecha de aislamiento.
    try:
        with connect(ROLES[1],passwords[ROLES[0]]):
            raise RuntimeError("BRECHA: contraseña A1 autentica como A2")
    except psycopg.OperationalError:
        print("5BC PASSWORD PASS: contraseña A1 rechazada para login A2")
    for role,uid,empresa,row_id in ACTORS:
        with connect(role,passwords[role]) as c:
            who=c.execute("SELECT session_user,current_user").fetchone()
            require(who==(role,role),
                    "login TCP no autentica la identidad individual: "+role)
            require(one(c,"SELECT row_security_active('public.fs_5bc_reserva'::regclass)")
                    is True,"RLS inactiva: "+role)
            own=c.execute("SELECT id,empresa_id,usuario_id FROM "
                          "public.fs_5bc_reserva ORDER BY id").fetchall()
            require(own==[(row_id,empresa,uid)],
                    "rol puede leer filas ajenas: "+role)
            require(one(c,"SELECT public.fs_5bc_verificar(%s,%s)",
                        (empresa,uid)) is True,"verificador rechaza rol legítimo")
            other_uid=102 if uid==101 else 101
            require(one(c,"SELECT public.fs_5bc_verificar(%s,%s)",
                        (empresa,other_uid)) is False,
                    "verificador aceptó actor distinto")
            c.execute("SELECT set_config('app.usuario_id',%s,true)",
                      (str(other_uid),))
            require(one(c,"SELECT public.fs_5bc_verificar(%s,%s)",
                        (empresa,other_uid)) is False,
                    "GUC editable permitió suplantación")
            require(one(c,"SELECT has_table_privilege("
                        "session_user,'public.fs_5bc_actor','SELECT')") is False,
                    "mapa sensible abierto a actor")
            try:
                c.execute("UPDATE public.fs_5bc_reserva SET estado='FRAUDE' "
                          "WHERE id=%s",(row_id,))
            except psycopg.Error as e:
                require(e.sqlstate=="42501",
                        "rechazo DML directo sin SQLSTATE 42501")
            else:
                raise RuntimeError("BRECHA: actor pudo UPDATE directo")
            print(f"5BC LOGIN+RLS PASS: {role} se autenticó realmente, "
                  "leyó sólo su fila y no tuvo DML directo")

    with connect(ROLES[0],passwords[ROLES[0]]) as a1:
        # El actor A1 puede modificar SOLO su fila vía SECURITY DEFINER.
        a1.execute("CALL public.fs_5bc_confirmar(%s,%s,%s)",(1,1,101))
        try:
            a1.execute("CALL public.fs_5bc_confirmar(%s,%s,%s)",(2,1,102))
        except psycopg.Error as e:
            require(e.sqlstate=="42501","se esperaba denegación por actor A2")
        else:
            raise RuntimeError("BRECHA: A1 realizó CALL como usuario A2")
    with connect(ROLES[2],passwords[ROLES[2]]) as b1:
        try:
            b1.execute("CALL public.fs_5bc_confirmar(%s,%s,%s)",(1,1,101))
        except psycopg.Error as e:
            require(e.sqlstate=="42501","se esperaba denegación entre empresas")
        else:
            raise RuntimeError("BRECHA: B1 utilizó CALL para empresa A")
    rows=admin.execute("SELECT id,estado FROM public.fs_5bc_reserva ORDER BY id").fetchall()
    require(rows==[(1,"CONFIRMADA"),(2,"PENDIENTE"),(3,"PENDIENTE")],
            "efectos de SP no cumplen aislamiento entre usuarios y empresas")
    print("5BC CALL PASS: SP NOLOGIN/SECURITY DEFINER permitió A1 propio, "
          "rechazó A2 y B1, sin GRANT UPDATE directo")


def run():
    require(os.name=="nt","5BC preparado para Windows, abortado en otro SO")
    require(os.geteuid()!=0 if hasattr(os,"geteuid") else True,
            "5BC no admite ejecución como root")
    require(port_free(),"puerto 55433 ocupado; nunca conectar a un PG ajeno")
    initdb,pgctl=postgres_bin()
    # Contraseñas únicas por ejecución, no impresas, ni .env, ni repo.
    admin_password=secrets.token_urlsafe(36)
    role_passwords={r:secrets.token_urlsafe(36) for r in ROLES}
    with tempfile.TemporaryDirectory(prefix="fashionstore-5bc-auth-") as root:
        directory=Path(root)
        datadir=directory/"fresh_pg18_only_5bc"
        pwfile=directory/"bootstrap_pw.txt"
        logfile=directory/"postgres_5bc.log"
        with open(pwfile,"x",encoding="ascii") as stream:
            stream.write(admin_password+"\n")
        started=False
        try:
            command([initdb,"-D",datadir,"-U","postgres",
                    "--auth-host=scram-sha-256",
                    "--auth-local=scram-sha-256",
                    "--pwfile",pwfile,"--no-instructions"],timeout=90)
            pwfile.unlink(missing_ok=True)
            # Solo TCP 127.0.0.1 y autenticación SCRAM.
            (datadir/"pg_hba.conf").write_text(
                "local all all scram-sha-256\n"
                "host all all 127.0.0.1/32 scram-sha-256\n"
                "host all all ::1/128 reject\n",
                encoding="ascii")
            command([pgctl,"-D",datadir,"-l",logfile,"-w","-t","25",
                     "-o",f"-c listen_addresses={HOST} -p {PORT} -c ssl=off",
                     "start"],timeout=55)
            started=True
            with connect("postgres",admin_password) as admin:
                validate_cluster(admin,datadir)
                print("5BC CLUSTER PASS: PG18 nuevo, 127.0.0.1:55433, "
                      "directorio TEMP único verificado")
                prepare_schema(admin,role_passwords)
                audit_live(admin,role_passwords)
        finally:
            pwfile.unlink(missing_ok=True)
            if started or (datadir/"postmaster.pid").exists():
                command([pgctl,"-D",datadir,"-w","-t","25",
                         "-m","immediate","stop"],timeout=50)
                print("5BC CLEANUP PASS: instancia desechable detenida")
            # TemporaryDirectory destruye el directorio completo,
            # incluido catálogo, SCRAM hashes y logs, al salir de with.
    require(port_free(),"55433 todavía escucha después de cleanup")
    print("5BC OK: 3 conexiones SQL REALES SCRAM, RLS y SP protegido")
    print("5BC FINAL: directorio temporal eliminado; 5432/55432 no tocados")
    print("5BC PENDIENTE: esquema empresarial CU16–CU18 en instancia "
          "desechable, pool FastAPI con credenciales por actor y concurrencia; "
          "flags SaaS OFF")


if __name__=="__main__":
    try:
        run()
    except Exception as exc:
        print(f"5BC ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
