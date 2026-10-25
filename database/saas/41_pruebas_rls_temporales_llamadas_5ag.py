"""FashionStore 5AG: pruebas SQL de WITH CHECK / USING y CALL legacy.
Ejecuta DDL temporal SOLO EN LAB PostgreSQL 18 y revierte test DML.
Se rehusa a trabajar si el entorno difiere o hay policies/privil. de prueba previos.
NO INSTALA NI EJECUTA procedimientos tenant; A08/A11/A13 siguen pendientes.
"""
from __future__ import annotations

from getpass import getpass
import sys

import psycopg


HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"
DATA = "c:/si2_parcial1/fashionstore-rls-lab-5v-data"
A, B = "fashionstore_5ab_a", "fashionstore_5ab_b"
NAMES = (
    "sp_cancelar_reserva", "sp_crear_reserva",
    "sp_crear_reserva_desde_carrito", "sp_finalizar_reserva_sin_compra"
)
POLICIES = (
    ("reserva", "fs_lab_5ag_reserva_insert"),
    ("inventario", "fs_lab_5ag_inventario_update"),
    ("bitacora", "fs_lab_5ag_bitacora_insert"),
)


def ensure(value: bool, reason: str):
    if not value:
        raise RuntimeError("5AG BLOQUEADO: " + reason)


def connect(user: str, password: str):
    return psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user=user, password=password,
        connect_timeout=8, sslmode="disable", autocommit=True,
        options="-c statement_timeout=15000 -c lock_timeout=5000"
    )


def one(conn, statement, parameters=None):
    return conn.execute(statement, parameters).fetchone()[0]


def inspect_admin(admin):
    identity = one(admin, """
       SELECT current_database() || '|' || host(inet_server_addr()) || '|' ||
       inet_server_port()::text || '|' ||
       lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/')) ||
       '|' || (current_setting('server_version_num')::int / 10000)::text
    """)
    ensure(identity == f"{DB}|{HOST}|{PORT}|{DATA}|18", "servidor lab incorrecto")
    ensure(one(admin, "SELECT session_user") == "postgres", "principal DBA incorrecto")
    ensure(one(admin, """SELECT count(*) FROM pg_class c JOIN pg_namespace n
       ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'""") == 46,
       "numero tablas incorrecto")
    ensure(one(admin, """SELECT count(*) FROM pg_constraint co
       JOIN pg_class c ON c.oid=co.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND co.contype='f'
         AND co.convalidated AND co.conname LIKE 'fk_tenant_%'""") == 48,
       "48 FK tenant incorrectas")
    ensure(one(admin, "SELECT count(*) FROM pg_policies WHERE schemaname='public'") == 35,
           "policies previas desconocidas")
    ensure(one(admin, """SELECT count(*) FROM pg_roles WHERE rolname IN
      ('fashionstore_5ab_a','fashionstore_5ab_b')
      AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolinherit""") == 2,
      "cuentas restringidas ausentes")
    for table, name in POLICIES:
        ensure(one(admin, """
          SELECT count(*) FROM pg_policies
          WHERE schemaname='public' AND tablename=%s AND policyname=%s
        """, (table, name)) == 0, "politica temporal ya existente")
    ensure(one(admin, "SELECT has_table_privilege(%s,'public.reserva','INSERT')", (A,)) is False,
           "rol A ya tiene INSERT reserva")
    ensure(one(admin, "SELECT has_table_privilege(%s,'public.inventario','UPDATE')", (B,)) is False,
           "rol B ya tiene UPDATE inventario")
    for role in (A, B):
        ensure(one(admin,"SELECT has_table_privilege(%s,'public.bitacora','INSERT')",(role,)) is False,
               "rol ya tiene INSERT bitacora")
    ids = dict(admin.execute("""
       SELECT slug,id FROM public.empresa
       WHERE slug IN ('empresa-lab-a','empresa-lab-b')
    """).fetchall())
    ensure(set(ids)=={"empresa-lab-a","empresa-lab-b"}, "empresas ficticias")
    fixture = {}
    for slug in ids:
        tenant = ids[slug]
        fixture[slug] = {"id":tenant}
        for table in ("cliente","sucursal","carrito","inventario","reserva"):
            rows=admin.execute(f"SELECT id FROM public.{table} WHERE empresa_id=%s",
                               (tenant,)).fetchall()
            ensure(len(rows)==1, "fixture "+table+" "+slug)
            fixture[slug][table]=rows[0][0]
    ensure(one(admin,"SELECT count(*) FROM public.reserva")==2,"reservas iniciales")
    ensure(one(admin,"SELECT count(*) FROM public.inventario WHERE stock_actual=5 AND stock_reservado=0")==2,
           "stock inicial")
    return fixture


INSTALL = """
BEGIN;
CREATE POLICY fs_lab_5ag_reserva_insert ON public.reserva
  FOR INSERT TO fashionstore_5ab_a
  WITH CHECK (empresa_id = (SELECT id FROM public.empresa
                            WHERE slug='empresa-lab-a'));
CREATE POLICY fs_lab_5ag_inventario_update ON public.inventario
  FOR UPDATE TO fashionstore_5ab_b
  USING (empresa_id = (SELECT id FROM public.empresa
                       WHERE slug='empresa-lab-b'))
  WITH CHECK (empresa_id = (SELECT id FROM public.empresa
                            WHERE slug='empresa-lab-b'));
CREATE POLICY fs_lab_5ag_bitacora_insert ON public.bitacora
  FOR INSERT TO fashionstore_5ab_a, fashionstore_5ab_b
  WITH CHECK (
   (session_user='fashionstore_5ab_a' AND empresa_id =
      (SELECT id FROM public.empresa WHERE slug='empresa-lab-a'))
   OR
   (session_user='fashionstore_5ab_b' AND empresa_id =
      (SELECT id FROM public.empresa WHERE slug='empresa-lab-b'))
  );
GRANT INSERT (empresa_id,cliente_id,sucursal_id,fecha_atencion)
  ON public.reserva TO fashionstore_5ab_a;
GRANT UPDATE (stock_reservado) ON public.inventario TO fashionstore_5ab_b;
GRANT INSERT ON public.bitacora TO fashionstore_5ab_a, fashionstore_5ab_b;
COMMIT;
"""

CLEANUP = """
BEGIN;
DROP POLICY fs_lab_5ag_reserva_insert ON public.reserva;
DROP POLICY fs_lab_5ag_inventario_update ON public.inventario;
DROP POLICY fs_lab_5ag_bitacora_insert ON public.bitacora;
REVOKE INSERT (empresa_id,cliente_id,sucursal_id,fecha_atencion)
  ON public.reserva FROM fashionstore_5ab_a;
REVOKE UPDATE (stock_reservado) ON public.inventario FROM fashionstore_5ab_b;
REVOKE INSERT ON public.bitacora FROM fashionstore_5ab_a, fashionstore_5ab_b;
COMMIT;
"""


def aborted_sql(conn, statement, args=()):
    """Ejecuta tentativa DML o CALL dentro de transacción siempre revertida."""
    conn.execute("BEGIN")
    try:
        try:
            rows = conn.execute(statement, args).fetchall() if "RETURNING" in statement.upper() else None
        except psycopg.Error as exc:
            return exc.sqlstate, None
        return None, rows
    finally:
        conn.execute("ROLLBACK")


def run(admin, a, b, fixture):
    print("5AG [2/4] Probando permisos y policies temporalmente concedidos")
    ensure(one(admin, "SELECT count(*) FROM pg_policies WHERE schemaname='public'")==38,
           "policies temporales ausentes")
    ensure(one(a, "SELECT has_column_privilege(current_user,'public.reserva','empresa_id','INSERT')") is True,
           "A sin privilegio INSERT empresarial")
    ensure(one(b, "SELECT has_column_privilege(current_user,'public.inventario','stock_reservado','UPDATE')") is True,
           "B sin privilegio UPDATE de stock")
    ensure(one(a,"SELECT row_security_active('public.reserva'::regclass)") is True,
           "RLS no efectiva en reserva")
    ensure(one(b,"SELECT row_security_active('public.inventario'::regclass)") is True,
           "RLS no efectiva en inventario")

    fa=fixture['empresa-lab-a']
    fb=fixture['empresa-lab-b']
    # Prueba positiva: A INSERTA su propia reserva y todo se revierte.
    code,rows=aborted_sql(a, """
      INSERT INTO public.reserva (empresa_id,cliente_id,sucursal_id,fecha_atencion)
      VALUES (%s,%s,%s,now()+interval '2 days') RETURNING id
    """, (fa['id'],fa['cliente'],fa['sucursal']))
    ensure(code is None and rows is not None and len(rows)==1,
           f"A05: INSERT propio falló, SQLSTATE={code}")

    # Prueba adversarial: INSERT con columnas y FK validas de B desde A.
    code,_=aborted_sql(a, """
      INSERT INTO public.reserva (empresa_id,cliente_id,sucursal_id,fecha_atencion)
      VALUES (%s,%s,%s,now()+interval '2 days') RETURNING id
    """, (fb['id'],fb['cliente'],fb['sucursal']))
    ensure(code=="42501",f"A05 no rechazado por politica RLS (SQLSTATE={code})")
    print("5AG A05: PASS_WITH_CHECK - propio permitido y ajeno rechazado por RLS")

    # Prueba positiva: B modifica su fila; ROLLBACK (bitacora via trigger).
    code,rows=aborted_sql(b, """
      UPDATE public.inventario SET stock_reservado=stock_reservado+1
      WHERE id=%s RETURNING id
    """,(fb['inventario'],))
    ensure(code is None and rows==[(fb['inventario'],)],
           f"A06: UPDATE propio falló, SQLSTATE={code}")
    code,rows=aborted_sql(b, """
      UPDATE public.inventario SET stock_reservado=stock_reservado+1
      WHERE id=%s RETURNING id
    """,(fa['inventario'],))
    ensure(code is None and rows==[],
           f"A06: UPDATE cruzado no fue filtrado (SQLSTATE={code},filas={rows})")
    print("5AG A06: PASS_USING_UPDATE - B modifica propio con rollback, ajeno invisible")

    # CALL REAL, cuatro SP originales. Debe fallar por SQLSTATE 42501 ANTES de entrar al SP.
    procs=admin.execute("""
       SELECT p.proname,coalesce(cardinality(p.proallargtypes),p.pronargs)::int
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='public' AND p.prokind='p'
         AND p.proname=ANY(%s)
       ORDER BY p.proname
    """,(list(NAMES),)).fetchall()
    ensure(len(procs)==4 and {x[0] for x in procs}==set(NAMES),"SP legacy distintos o ausentes")
    for name,nargs in procs:
        ensure(0<=nargs<=20,"firma SP legacy no segura")
        sql=f"CALL public.{name}({','.join('NULL' for _ in range(nargs))})"
        code,_=aborted_sql(a,sql)
        ensure(code=="42501",f"A10 CALL {name} SQLSTATE={code} en vez de 42501")
    print("5AG A10: PASS_CALL_DENEGADO - 4 CALL SQL reales rechazados por ACL")
    ensure(one(admin,"SELECT count(*) FROM public.reserva")==2,"cambio persistente en reserva")
    ensure(one(admin,"SELECT count(*) FROM public.inventario WHERE stock_actual=5 AND stock_reservado=0")==2,
           "cambio persistente en stock")


def main():
    print("5AG LAB: permisos RLS temporales con limpieza comprobada; NO procedimientos tenant")
    admin_pw=getpass("Clave postgres del LABORATORIO 55432: ")
    a_pw=getpass("Clave SQL Empresa A ficticia: ")
    b_pw=getpass("Clave SQL Empresa B ficticia: ")
    with connect("postgres",admin_pw) as admin:
        fixture=inspect_admin(admin)
        installed=False
        outcome=None
        try:
            admin.execute(INSTALL)
            installed=True
            print("5AG [1/4] Policies y GRANT temporales instalados SOLO en lab")
            with connect(A,a_pw) as a, connect(B,b_pw) as b:
                run(admin,a,b,fixture)
        except Exception as exc:
            outcome=exc
        finally:
            if installed:
                print("5AG [3/4] Quitando 3 policies y permisos temporales del laboratorio")
                admin.execute(CLEANUP)
                ensure(one(admin,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==35,
                       "cleanup: policies no restauradas")
                for column in ("empresa_id","cliente_id","sucursal_id","fecha_atencion"):
                    ensure(one(admin,"SELECT has_column_privilege(%s,'public.reserva',%s,'INSERT')",
                               (A,column)) is False, "cleanup: A conserva INSERT columna "+column)
                ensure(one(admin,"SELECT has_column_privilege(%s,'public.inventario','stock_reservado','UPDATE')",
                           (B,)) is False, "cleanup: B conserva UPDATE stock_reservado")
                for role in (A,B):
                    ensure(one(admin,"SELECT has_table_privilege(%s,'public.bitacora','INSERT')",(role,)) is False,
                           "cleanup: role conserva bitacora INSERT")
                print("5AG [4/4] OK: 35 policies originales, privilegios temporales retirados")
        if outcome:
            raise outcome
    print("5AG RESULTADO: A05, A06 y A10 ensayados con SQL auténtico.")
    print("5AG PENDIENTES: A08 llamada autenticada; A11 rollback de SP; A13 concurrencia de SP.")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AG ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
