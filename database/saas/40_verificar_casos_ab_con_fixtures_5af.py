"""5AF: pruebas con dos LOGIN SQL autenticos sobre fixtures 5AE.
No crea/elimina nada. A05 y A06 prueban solo denegacion ACL dentro de ROLLBACK.
No declara A01-A14 terminadas: A11/A13 requieren SP/servicio integrado.
"""
from getpass import getpass
import sys
import psycopg

HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"
DATA = "c:/si2_parcial1/fashionstore-rls-lab-5v-data"
A, B = "fashionstore_5ab_a", "fashionstore_5ab_b"


def require(condition, label):
    if not condition:
        raise RuntimeError("5AF BLOQUEADO: " + label)


def connect(user, password, readonly=True):
    args = dict(host=HOST, port=PORT, dbname=DB, user=user,
                password=password, connect_timeout=8, sslmode="disable",
                autocommit=True)
    if readonly:
        args["options"] = "-c default_transaction_read_only=on"
    return psycopg.connect(**args)


def scalar(conn, sql, args=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchone()[0]


def denied_dml(conn, sql, args):
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL statement_timeout = '5s'")
            try:
                cur.execute(sql, args)
            except psycopg.Error as exc:
                if exc.sqlstate == "42501":
                    return True
                raise RuntimeError("SQLSTATE distinto a 42501") from exc
            return False
    finally:
        conn.execute("ROLLBACK")


def main():
    print("5AF ENSAYOS CON DATOS FICTICIOS; NO CERTIFICA A01-A14 COMPLETAS")
    admin_password = getpass("Clave postgres del LABORATORIO 55432: ")
    a_password = getpass("Clave SQL ficticia Empresa A: ")
    b_password = getpass("Clave SQL ficticia Empresa B: ")
    states = {}

    def result(case, ok, status="PASS"):
        require(ok, "fallo " + case)
        states[case] = status
        print(f"5AF {case}: {status}")

    with connect("postgres", admin_password) as admin, \
         connect(A, a_password) as a, connect(B, b_password) as b:
        identity = scalar(admin, """
            SELECT current_database()||'|'||host(inet_server_addr())||'|'||
                   inet_server_port()::text||'|'||
                   lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/'))
        """)
        require(identity == f"{DB}|{HOST}|{PORT}|{DATA}", "identidad clúster")
        require(scalar(admin, "SELECT count(*) FROM pg_policies WHERE schemaname='public'")==35,
                "35 policies esperadas")
        ids = dict(admin.execute("""
            SELECT slug,id FROM public.empresa
            WHERE slug IN ('empresa-lab-a','empresa-lab-b')
        """).fetchall())
        require(set(ids)=={"empresa-lab-a","empresa-lab-b"}, "dos empresas ficticias")
        id_a,id_b = ids["empresa-lab-a"],ids["empresa-lab-b"]

        fixtures = {}
        for k, tid in (("a",id_a),("b",id_b)):
            fixtures[k] = {}
            for table in ("carrito","inventario","reserva"):
                rows = admin.execute(f"SELECT id FROM public.{table} WHERE empresa_id=%s",
                                     (tid,)).fetchall()
                require(len(rows)==1, f"fixture {table} {k} ausente o duplicado")
                fixtures[k][table] = rows[0][0]
        for tid in (id_a,id_b):
            require(scalar(admin,"SELECT count(*) FROM public.bitacora WHERE empresa_id=%s",
                           (tid,))>0,"bitacora ficticia inexistente")

        require(scalar(a,"SELECT session_user")==A and scalar(b,"SELECT session_user")==B,
                "login SQL incorrecto")
        require(scalar(a,"SELECT row_security_active('public.carrito'::regclass)") is True
                and scalar(b,"SELECT row_security_active('public.reserva'::regclass)") is True,
                "RLS inactiva")

        result("A01",scalar(a,"SELECT count(*) FROM public.carrito WHERE id=%s",
                            (fixtures["a"]["carrito"],))==1)
        result("A02",scalar(a,"SELECT count(*) FROM public.carrito WHERE id=%s",
                            (fixtures["b"]["carrito"],))==0)
        result("A03",scalar(b,"SELECT count(*) FROM public.carrito WHERE id=%s",
                            (fixtures["a"]["carrito"],))==0)
        result("A04",scalar(b,"SELECT count(*) FROM public.carrito WHERE id=%s",
                            (fixtures["b"]["carrito"],))==1)

        # Roles 5AB carecen de permiso INSERT/UPDATE.
        # No se sustituye esa denegacion por un falso certificado WITH CHECK.
        with connect(A,a_password,readonly=False) as aw:
            denied=denied_dml(aw, """
                INSERT INTO public.reserva(empresa_id,cliente_id,sucursal_id,fecha_atencion)
                SELECT %s,c.id,s.id,now()+interval '1 day'
                FROM public.cliente c CROSS JOIN public.sucursal s
                WHERE c.empresa_id=%s AND s.empresa_id=%s LIMIT 1
            """,(id_b,id_b,id_b))
            result("A05",denied,"PARCIAL_ACL_NO_WITH_CHECK")
        with connect(B,b_password,readonly=False) as bw:
            denied=denied_dml(bw, """
                UPDATE public.inventario
                SET stock_reservado=stock_reservado+1 WHERE id=%s
            """,(fixtures["a"]["inventario"],))
            result("A06",denied,"PARCIAL_ACL_NO_USING_UPDATE")

        a.execute("SELECT set_config('app.empresa_id',%s,false)",(str(id_b),))
        result("A07",scalar(a,"SELECT count(*) FROM public.carrito WHERE empresa_id=%s",
                            (id_b,))==0)

        b.execute("SELECT set_config('app.usuario_id','999999',false)")
        result("A08",scalar(b,"SELECT session_user")==B,
               "PARCIAL_SIN_SP_TENANT_INSTALADO")

        try:
            a.execute("SELECT password_hash FROM public.usuario LIMIT 1")
            raise RuntimeError("5AF: A09 puede leer hash")
        except psycopg.Error as exc:
            result("A09",exc.sqlstate=="42501")

        legacy = """
            SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname IN
            ('sp_cancelar_reserva','sp_crear_reserva','sp_crear_reserva_desde_carrito',
             'sp_finalizar_reserva_sin_compra')
            AND has_function_privilege(session_user,p.oid,'EXECUTE')
        """
        result("A10",scalar(a,legacy)==0 and scalar(b,legacy)==0,
               "PARCIAL_ACL_SIN_CALL_REAL")
        states["A11"]="PENDIENTE_TRANSACCION_SP"
        print("5AF A11: PENDIENTE_TRANSACCION_SP")
        result("A12",scalar(b,"SELECT count(*) FROM public.bitacora WHERE empresa_id=%s",
                            (id_a,))==0 and
               scalar(b,"SELECT count(*) FROM public.bitacora WHERE empresa_id=%s",
                      (id_b,))>0)
        states["A13"]="PENDIENTE_CONCURRENCIA_SP"
        print("5AF A13: PENDIENTE_CONCURRENCIA_SP")
        result("A14",scalar(a,"SELECT count(*) FROM public.inventario WHERE id=%s AND stock_actual=5",
                            (fixtures["a"]["inventario"],))==1 and
               scalar(a,"SELECT count(*) FROM public.inventario WHERE empresa_id=%s",
                      (id_b,))==0)

        require(scalar(admin,"SELECT count(*) FROM public.reserva")==2,
                "cantidad reserva alterada")
        require(scalar(admin,"SELECT count(*) FROM public.inventario WHERE stock_actual<>5 OR stock_reservado<>0")==0,
                "stock alterado")

    passed=sum(x=="PASS" for x in states.values())
    partial=sum(x.startswith("PARCIAL") for x in states.values())
    pending=sum(x.startswith("PENDIENTE") for x in states.values())
    print(f"5AF RESULTADO: PASS={passed}; PARCIAL={partial}; PENDIENTE={pending}")
    print("5AF NO CERTIFICA A01-A14 COMPLETAS: faltan WITH CHECK, reserva tenant, rollback y concurrencia")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AF ERROR: {exc.__class__.__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
