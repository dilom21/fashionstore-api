"""FashionStore B3/5AK: A08 A11 A13 con LOGINS SQL reales sobre SP de LAB.
Este NO ES el flujo de reservas de la aplicacion final. No autentica JWT.
SOLO en PG18 fisicamente independiente 127.0.0.1:55432.
Ejecutar UNA VEZ tras instalar 44_instalar_ensayo_reserva_sql_5aj_LAB.sql;
el caso de concurrencia COMMIT una reserva FICTICIA y cambia stock reservado.

No altera ni lee PostgreSQL 5432, Supabase, main o cinco empresas definitivas.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from getpass import getpass
from threading import Barrier
import sys

import psycopg

HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"
DATA = "c:/si2_parcial1/fashionstore-rls-lab-5v-data"
A, B = "fashionstore_5ab_a", "fashionstore_5ab_b"
SP = "public.sp_lab_reservar_5aj"
CALL_SQL = (
    "CALL public.sp_lab_reservar_5aj("
    "%s::bigint,%s::bigint,%s::bigint,%s::timestamptz,%s::boolean,%s::bigint)"
)


def require(ok, message):
    if not ok:
        raise RuntimeError("5AK BLOQUEADO: " + message)


def connect(login, password, readonly=False):
    mode = "-c default_transaction_read_only=on " if readonly else ""
    return psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user=login, password=password,
        sslmode="disable", connect_timeout=8, autocommit=True,
        options=mode+"-c statement_timeout=16000 -c lock_timeout=8000",
    )


def scalar(conn, sql, params=None):
    cursor = conn.execute(sql) if params is None else conn.execute(sql, params)
    return cursor.fetchone()[0]


def check_preflight(admin):
    identity = scalar(admin, """
        SELECT current_database() || '|' || host(inet_server_addr()) || '|' ||
          inet_server_port()::text || '|' ||
          lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/')) ||
          '|' || (current_setting('server_version_num')::int / 10000)::text
    """)
    require(identity == f"{DB}|{HOST}|{PORT}|{DATA}|18", "cluster / data directory incorrecto")
    require(scalar(admin,"SELECT session_user")=="postgres", "usuario admin distinto")
    require(scalar(admin,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==41,
            "5AJ aun no instalado o configuracion alterada")
    require(scalar(admin,"SELECT count(*) FROM public.empresa")==2, "empresas de lab")
    require(scalar(admin,"SELECT count(*) FROM public.carrito")==4, "carritos lab")
    require(scalar(admin,"SELECT count(*) FROM public.reserva")==2, "reservas iniciales")
    require(scalar(admin,"SELECT count(*) FROM public.movimiento_inventario")==0,
            "movimientos preexistentes")
    require(scalar(admin,"SELECT count(*) FROM public.inventario WHERE stock_actual=5 AND stock_reservado=0")==2,
            "stock inicial distinto")
    require(scalar(admin, """
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
        WHERE n.nspname='public' AND p.proname='sp_lab_reservar_5aj'
          AND p.prokind='p' AND NOT p.prosecdef
    """)==1,"SP lab invoker ausente")
    require(scalar(admin, """
        SELECT count(*) FROM pg_roles WHERE rolname IN
        ('fashionstore_5ab_a','fashionstore_5ab_b')
        AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
    """)==2,"roles no seguros")
    ids=dict(admin.execute("""
        SELECT slug,id FROM public.empresa
        WHERE slug IN ('empresa-lab-a','empresa-lab-b')
    """).fetchall())
    require(set(ids)=={"empresa-lab-a","empresa-lab-b"},"slugs del laboratorio")
    id_a,id_b=ids["empresa-lab-a"],ids["empresa-lab-b"]
    carts=admin.execute("""
       SELECT ca.id
       FROM public.carrito ca
       JOIN public.detalle_carrito dc ON dc.carrito_id=ca.id
         AND dc.empresa_id=ca.empresa_id
       WHERE ca.empresa_id=%s AND ca.estado='ACTIVO'
         AND dc.cantidad=4
         AND NOT EXISTS (SELECT 1 FROM public.reserva r WHERE r.carrito_id=ca.id)
       ORDER BY ca.id
    """,(id_a,)).fetchall()
    require(len(carts)==2,"se requieren dos carritos nuevos A de cantidad 4")
    return id_a,id_b,tuple(x[0] for x in carts)


def call(conn, tenant, cart, actor, fail=False):
    tomorrow = datetime.now(timezone.utc) + timedelta(days=2)
    return conn.execute(CALL_SQL,(tenant,cart,actor,tomorrow,fail,None)).fetchone()[0]


def expected_exception(conn, expected, run):
    conn.execute("BEGIN")
    try:
        try:
            run()
        except psycopg.Error as exc:
            require(exc.sqlstate == expected,
                    f"SQLSTATE={exc.sqlstate!r}; esperado={expected!r}")
        else:
            raise RuntimeError(f"Se esperaba error SQLSTATE={expected}, pero la llamada tuvo exito")
    finally:
        conn.execute("ROLLBACK")


def check_after_failed(admin,id_a):
    require(scalar(admin,"SELECT count(*) FROM public.reserva")==2,
            "ROLLBACK altero reservas")
    require(scalar(admin,"SELECT count(*) FROM public.movimiento_inventario")==0,
            "ROLLBACK dejo Kardex")
    require(scalar(admin,"SELECT count(*) FROM public.detalle_reserva")==2,
            "ROLLBACK dejo detalle reserva")
    require(scalar(admin,
                   "SELECT count(*) FROM public.inventario WHERE empresa_id=%s AND stock_actual=5 AND stock_reservado=0",
                   (id_a,))==1, "ROLLBACK dejo stock")


def concurrency_worker(password, tenant, cart, barrier):
    with connect(A,password) as worker:
        require(scalar(worker,"SELECT session_user")==A,"worker no es LOGIN A")
        worker.execute("BEGIN")
        try:
            barrier.wait(timeout=12)
            reservation=call(worker,tenant,cart,910001)
            worker.execute("COMMIT")
            return ("COMMIT",cart,reservation)
        except psycopg.Error as exc:
            worker.execute("ROLLBACK")
            return ("ROLLBACK",cart,exc.sqlstate)
        except Exception:
            worker.execute("ROLLBACK")
            raise


def main():
    print("5AK: ENSAYOS A08/A11/A13 SOBRE PROCEDIMIENTO EXCLUSIVO DEL LABORATORIO")
    admin_secret=getpass("Clave postgres SOLO LAB 55432: ")
    a_secret=getpass("Clave LOGIN Empresa A LAB: ")
    b_secret=getpass("Clave LOGIN Empresa B LAB: ")
    with connect("postgres",admin_secret,readonly=True) as admin:
        tenant_a,tenant_b,carts=check_preflight(admin)
        print("5AK PREFLIGHT OK: 41 policies lab, 2 carritos competidores, stock 5")
        with connect(A,a_secret) as a,connect(B,b_secret) as b:
            require(scalar(a,"SELECT session_user")==A,"autenticacion A")
            require(scalar(b,"SELECT session_user")==B,"autenticacion B")
            for con in (a,b):
                require(scalar(con,"SELECT row_security_active('public.inventario'::regclass)") is True,
                        "RLS inventario inactiva")
                require(scalar(con,"SELECT row_security_active('public.reserva'::regclass)") is True,
                        "RLS reserva inactiva")
            # A08: B intenta simular A usando GUCs mutables y todos los
            # argumentos reales de A. SESSION_USER debe impedir la suplantacion.
            b.execute("SELECT set_config('app.empresa_id',%s,false)",(str(tenant_a),))
            b.execute("SELECT set_config('app.usuario_id',%s,false)",("910001",))
            expected_exception(
                b,"42501",lambda:call(b,tenant_a,carts[0],910001)
            )
            check_after_failed(admin,tenant_a)
            print("5AK A08: PASS_PRINCIPAL_SQL - GUC ajena y actor A rechazados desde B")

            # A11: inserta reserva, actualiza stock, guarda detalle+movimiento
            # y falla intencionalmente ANTES de poder confirmar el commit.
            audit_before=scalar(admin,
                "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s",(tenant_a,))
            expected_exception(
                a,"23514",lambda:call(a,tenant_a,carts[0],910001,True)
            )
            check_after_failed(admin,tenant_a)
            audit_after=scalar(admin,
                "SELECT count(*) FROM public.bitacora WHERE empresa_id=%s",(tenant_a,))
            require(audit_before==audit_after,"ROLLBACK dejo evento de auditoria")
            print("5AK A11: PASS_ROLLBACK - reserva/stock/Kardex/bitacora intactos")

        # A13: DOS SESIONES SQL DISTINTAS del mismo tenant, carritos separados
        # y el MISMO inventario. Cantidad 4+4 con stock 5.
        # Solo una transaccion puede confirmar; la otra debe fracasar 23514.
        barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            fa=executor.submit(concurrency_worker,a_secret,tenant_a,carts[0],barrier)
            fb=executor.submit(concurrency_worker,a_secret,tenant_a,carts[1],barrier)
            results=(fa.result(timeout=26),fb.result(timeout=26))
        success=[r for r in results if r[0]=="COMMIT"]
        rejected=[r for r in results if r[0]=="ROLLBACK"]
        require(len(success)==1 and len(rejected)==1
                and rejected[0][2]=="23514",
                f"Concurrencia incorrecta {results}")
        require(scalar(admin,
            "SELECT count(*) FROM public.inventario WHERE empresa_id=%s AND stock_actual=5 AND stock_reservado=4",
            (tenant_a,))==1, "A13 stock reservado distinto de 4")
        require(scalar(admin,
            "SELECT count(*) FROM public.inventario WHERE empresa_id=%s AND stock_actual=5 AND stock_reservado=0",
            (tenant_b,))==1, "A13 altero empresa B")
        require(scalar(admin,"SELECT count(*) FROM public.reserva")==3,
                "A13 esperaba una unica reserva nueva")
        require(scalar(admin,"SELECT count(*) FROM public.movimiento_inventario")==1,
                "A13 esperaba un unico movimiento RESERVA")
        require(scalar(admin,"SELECT count(*) FROM public.detalle_reserva")==3,
                "A13 esperaba un unico detalle de reserva")
        require(scalar(admin,
            "SELECT count(*) FROM public.carrito WHERE id=ANY(%s) AND estado='CONVERTIDO'",
            (list(carts),))==1,"A13 se convirtieron demasiados carritos")
        require(scalar(admin,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==41,
                "A13 altero policies")
        print("5AK A13: PASS_LOCKS - 1 COMMIT, 1 ROLLBACK 23514, reservado=4/5")
        print("5AK OK: A08 A11 A13 verificados SOLO en procedure SQL de ensayo")
        print("5AK FALTA: SP CU16 definitivo, membresia real, planes y endpoints FastAPI")
        print("5AK MANTENER: cinco empresas originales sin cambios, SaaS AUTH false")


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print(f"5AK ERROR: {type(err).__name__}: {err}",file=sys.stderr)
        sys.exit(2)
