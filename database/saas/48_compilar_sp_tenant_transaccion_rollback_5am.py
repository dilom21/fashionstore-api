"""5AM / CU16-CU18: compilar tres borradores de SP SOLO en transaccion revertida.

No instala ni concede privilegios persistentes. Nunca ejecuta CALL ni
modifica reservas, carritos, stock, roles o policies. Solo laboratorio PG18
127.0.0.1:55432 con cluster conocido y 2 empresas ficticias A/B.
Detecta fallos de sintaxis/DDL/ACL y reporta prerequisitos sin datos sensibles.

LIMITACION: PostgreSQL puede compilar PL/pgSQL sin resolver todas las
referencias DML internas; esto NO certifica funcionamiento transaccional.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import re
import sys

import psycopg

HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"
DATA = "c:/si2_parcial1/fashionstore-rls-lab-5v-data"
ROLES = ("fashionstore_5ab_a", "fashionstore_5ab_b")
SOURCE_DIR = Path(__file__).resolve().parent / "borradores"
PROCEDURES = {
    "sp_crear_reserva_tenant": ("12_sp_crear_reserva_tenant.sql.txt", 6),
    "sp_cancelar_reserva_tenant": ("13_sp_cancelar_reserva_tenant.sql.txt", 4),
    "sp_finalizar_reserva_sin_compra_tenant": (
        "14_sp_finalizar_reserva_sin_compra_tenant.sql.txt", 4
    ),
}


def require(ok, message):
    if not ok:
        raise RuntimeError("5AM BLOQUEADO: " + message)


def scalar(c, sql, args=None):
    cur = c.execute(sql) if args is None else c.execute(sql, args)
    return cur.fetchone()[0]


def preflight(c):
    ident = scalar(c, """
        SELECT current_database() || '|' || host(inet_server_addr()) || '|' ||
           inet_server_port()::text || '|' ||
           lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/')) ||
           '|' || (current_setting('server_version_num')::int/10000)::text
    """)
    require(ident == f"{DB}|{HOST}|{PORT}|{DATA}|18", "identidad PG18 distinta")
    require(scalar(c, "SELECT session_user") == "postgres", "usuario no DBA del LAB")
    require(scalar(c, """SELECT count(*) FROM pg_class t
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relkind='r'""") == 46,
        "cantidad de tablas distinta")
    require(scalar(c, """SELECT count(*) FROM pg_constraint co
        JOIN pg_class t ON t.oid=co.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND co.contype='f' AND co.convalidated
          AND co.conname LIKE 'fk_tenant_%'""") == 48, "48 FK distintas")
    require(scalar(c, "SELECT count(*) FROM pg_policies WHERE schemaname='public'") == 41,
            "cantidad de policies distinta; no reconfigurar")
    require(scalar(c, """SELECT count(*) FROM public.empresa WHERE
        slug IN ('empresa-lab-a','empresa-lab-b')""") == 2, "empresas lab ausentes")
    require(scalar(c, "SELECT count(*) FROM public.empresa") == 2,
            "empresas adicionales detectadas")
    require(scalar(c, "SELECT count(*) FROM public.reserva") == 3,
            "resultado 5AK esperado: tres reservas")
    require(scalar(c, "SELECT count(*) FROM public.carrito") == 4,
            "resultado 5AK esperado: cuatro carritos")
    require(scalar(c, """SELECT count(*) FROM public.inventario i
        JOIN public.empresa e ON e.id=i.empresa_id WHERE
        (e.slug='empresa-lab-a' AND i.stock_actual=5 AND i.stock_reservado=4)
        OR (e.slug='empresa-lab-b' AND i.stock_actual=5 AND i.stock_reservado=0)
    """) == 2, "stock 5AK modificado")
    require(scalar(c, """SELECT count(*) FROM pg_roles WHERE
       rolname IN ('fashionstore_5ab_a','fashionstore_5ab_b')
       AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
       AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolinherit""") == 2,
       "logins SQL A/B cambiados")
    require(scalar(c, """SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace WHERE
        n.nspname='public' AND p.proname='sp_lab_reservar_5aj'
        AND p.prokind='p'""") == 1, "SP 5AJ de control ausente")
    require(scalar(c, """SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace WHERE
        n.nspname='public' AND p.proname=ANY(%s)""",
        (list(PROCEDURES),)) == 0,
        "uno o varios SP definitivos ya instalados; NO reemplazar")


def load_sources():
    contents = {}
    for procedure, (name, _) in PROCEDURES.items():
        sql = (SOURCE_DIR / name).read_text(encoding="utf-8")
        statements = [
            line.strip() for line in sql.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        require(statements[0].startswith(
            f"CREATE OR REPLACE PROCEDURE public.{procedure}("
        ), f"{name}: el DDL original cambio")
        require(len(re.findall(
            r"(?im)^CREATE OR REPLACE PROCEDURE\b", sql
        )) == 1, f"{name}: multiples CREATE")
        require(len(re.findall(r"(?im)^REVOKE ALL ON PROCEDURE\b",sql)) == 1,
                f"{name}: falta REVOCAR EXECUTE PUBLIC")
        require(not re.search(
            r"(?im)^\s*(?:COMMIT|ROLLBACK|DROP|GRANT|TRUNCATE|ALTER ROLE|CREATE ROLE)\b",
            sql
        ), f"{name}: sentencia prohibida fuera del ensayo")
        contents[procedure] = sql
    return contents


def catalog_check(c):
    rows = c.execute("""SELECT p.proname, p.oid, p.pronargs,
               p.prosecdef, p.prokind
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname=ANY(%s)
        ORDER BY p.proname
    """, (list(PROCEDURES),)).fetchall()
    require(len(rows) == 3, "no se compilaron tres procedimientos")
    for name, oid, args, security_definer, kind in rows:
        require(args == PROCEDURES[name][1] and kind == "p"
                and not security_definer, f"firma o seguridad inesperada en {name}")
        for login in ROLES:
            require(scalar(c, "SELECT has_function_privilege(%s,%s::oid,'EXECUTE')",
                           (login,oid)) is False,
                    f"{login} tiene EXECUTE en {name}")
        print(f"5AM COMPILADO: {name}, SECURITY INVOKER, EXECUTE A/B denegado")


def main():
    print("5AM: COMPILAR CU16/CU17/CU18 Y ROLLBACK — SOLO LAB 55432")
    sources = load_sources()  # Nunca leer SQL arbitrario de parametros HTTP.
    pw = getpass("Contrasena postgres SOLO laboratorio 55432: ")
    with psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="postgres",
        password=pw, sslmode="disable", connect_timeout=8, autocommit=True,
        options="-c statement_timeout=20000 -c lock_timeout=4000",
    ) as conn:
        preflight(conn)
        print("5AM PREFLIGHT OK: laboratorio intacto tras 5AK")
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='20s'")
            conn.execute("SET LOCAL lock_timeout='4s'")
            for name, sql in sources.items():
                conn.execute(sql)  # CREATE + REVOKE en la MISMA transaccion.
            catalog_check(conn)
            print("5AM 3/3: DDL compilado, ningun CALL ejecutado")
        finally:
            # Se deshacen CREATE/REVOKE incluso ante error.
            conn.execute("ROLLBACK")
        require(scalar(conn, """SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=ANY(%s)""",
            (list(PROCEDURES),)) == 0,
            "procedimiento de ensayo persiste despues del ROLLBACK")
        require(scalar(conn,
            "SELECT count(*) FROM pg_policies WHERE schemaname='public'") == 41,
            "policies del lab cambiadas")
        require(scalar(conn,"SELECT count(*) FROM public.reserva") == 3,
            "reservas cambiadas")
        print("5AM OK: los tres SP son sintacticamente instalables; "
              "ROLLBACK verificado, 0 SP finales instalados")
        print("5AM PENDIENTE: ACL/RLS DML, membresias/planes y llamadas reales")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AM ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
