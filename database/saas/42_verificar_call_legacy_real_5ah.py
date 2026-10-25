"""FashionStore 5AH: cuatro CALL heredados x dos sesiones SQL autenticas.

SOLO READ-ONLY en laboratorio PostgreSQL 18 127.0.0.1:55432.
No DDL, DML, GRANT, REVOKE, CREATE POLICY ni invocacion como postgres.
Se verifica por OID que ningun rol tiene EXECUTE antes de intentar CALL.
Una llamada que no lance 42501 es fallo y nunca se declara PASS.
"""
from __future__ import annotations

from getpass import getpass
import sys

import psycopg

HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"
PGDATA = "c:/si2_parcial1/fashionstore-rls-lab-5v-data"
A = "fashionstore_5ab_a"
B = "fashionstore_5ab_b"
ROUTINES = (
    "sp_cancelar_reserva",
    "sp_crear_reserva",
    "sp_crear_reserva_desde_carrito",
    "sp_finalizar_reserva_sin_compra",
)


def check(condition, description):
    if not condition:
        raise RuntimeError("5AH BLOQUEADO: " + description)


def connection(user, password):
    return psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user=user, password=password,
        sslmode="disable", connect_timeout=8, autocommit=True,
        options=(
            "-c default_transaction_read_only=on "
            "-c statement_timeout=12000 -c lock_timeout=4000"
        ),
    )


def one(conn, sql, values=None):
    # psycopg intenta analizar cualquier '%' como placeholder cuando recibe
    # incluso una tupla vacia. LIKE 'fk_tenant_%' no tiene parametros.
    cursor = conn.execute(sql) if values is None else conn.execute(sql, values)
    return cursor.fetchone()[0]


def inspect(admin):
    result = one(admin, """
        SELECT current_database() || '|' || host(inet_server_addr()) || '|' ||
               inet_server_port()::text || '|' ||
               lower(rtrim(replace(current_setting('data_directory'),chr(92),'/'),'/')) ||
               '|' || (current_setting('server_version_num')::int / 10000)::text
    """)
    check(result == f"{DB}|{HOST}|{PORT}|{PGDATA}|18", "identidad lab distinta")
    check(one(admin, "SELECT session_user") == "postgres", "principal DBA incorrecto")
    check(one(admin, """
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
    """) == 46, "tablas cambiadas")
    check(one(admin, """
        SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND co.contype='f'
          AND co.convalidated AND co.conname LIKE 'fk_tenant_%'
    """) == 48, "FK cambiadas")
    check(one(admin, "SELECT count(*) FROM pg_policies WHERE schemaname='public'") == 35,
          "politicas temporales no retiradas")
    check(one(admin, """
        SELECT count(*) FROM pg_roles
        WHERE rolname IN ('fashionstore_5ab_a','fashionstore_5ab_b')
          AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
          AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolinherit
    """) == 2, "cuentas no restringidas")
    check(one(admin, """
        SELECT count(*) FROM public.empresa
        WHERE slug IN ('empresa-lab-a','empresa-lab-b')
    """) == 2, "empresas ficticias ausentes")
    check(one(admin, "SELECT count(*) FROM public.reserva") == 2, "reservas cambiadas")
    check(one(admin, """
        SELECT count(*) FROM public.inventario
        WHERE stock_actual=5 AND stock_reservado=0
    """) == 2, "inventario cambiado")
    # Obtener OID + NUMERO REAL DE ARGUMENTOS de las cuatro procedures;
    # no inventar una firma ni usar CALL como superusuario.
    rows = admin.execute("""
        SELECT p.proname, p.oid,
               coalesce(cardinality(p.proallargtypes),p.pronargs)::int
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind='p'
          AND p.proname = ANY(%s)
        ORDER BY p.proname
    """, (list(ROUTINES),)).fetchall()
    check(len(rows) == 4 and {r[0] for r in rows} == set(ROUTINES),
          "procedimientos originales ausentes/sobrecargados")
    for name, oid, nargs in rows:
        check(isinstance(nargs, int) and 0 <= nargs <= 20, "firma fuera del contrato")
        for user in (A, B):
            allowed = one(admin, "SELECT has_function_privilege(%s,%s::oid,'EXECUTE')",
                          (user, oid))
            check(allowed is False, f"{user} YA tiene EXECUTE en {name}")
    return rows


def verify_calls(conn, user, rows):
    check(one(conn, "SELECT session_user") == user, "sesion SQL suplantada")
    check(one(conn, """
        SELECT row_security_active('public.reserva'::regclass)
           AND row_security_active('public.carrito'::regclass)
    """) is True, "RLS no efectiva")
    count=0
    for name, oid, nargs in rows:
        # Comprobar el privilegio desde el LOGIN real y contra la firma OID.
        check(one(conn,
                  "SELECT has_function_privilege(current_user,%s::oid,'EXECUTE')",
                  (oid,)) is False, "EXECUTE permitido antes de CALL")
        statement = f"CALL public.{name}({','.join(['NULL']*nargs)})"
        conn.execute("BEGIN READ ONLY")
        code = None
        try:
            try:
                # Ejecucion OBLIGATORIA: nunca condicionar execute a RETURNING.
                conn.execute(statement)
            except psycopg.Error as exc:
                code = exc.sqlstate
        finally:
            conn.execute("ROLLBACK")
        check(code == "42501",
              f"{user} CALL {name} devolvio SQLSTATE={code!r}; se esperaba 42501")
        print(f"5AH {user} CALL {name}: DENEGADO_42501")
        count+=1
    check(count == 4, "no se intentaron cuatro CALL")
    return count


def main():
    print("5AH: CALL HEREDADOS CON LOGIN SQL REAL, SOLO LECTURA")
    super_pw = getpass("Contrasena postgres LAB 55432: ")
    a_pw = getpass("Contrasena LOGIN A: ")
    b_pw = getpass("Contrasena LOGIN B: ")
    with connection("postgres", super_pw) as admin:
        routines = inspect(admin)
        print("5AH PREFLIGHT OK: 35 policies; 2 tenants; 4 procedimientos sin EXECUTE")
        with connection(A, a_pw) as a, connection(B, b_pw) as b:
            count_a = verify_calls(a, A, routines)
            count_b = verify_calls(b, B, routines)
        check(count_a + count_b == 8, "no se intentaron ocho CALL")
        check(one(admin, "SELECT count(*) FROM public.reserva") == 2,
              "reservas modificadas")
        check(one(admin, """
            SELECT count(*) FROM public.inventario
            WHERE stock_actual=5 AND stock_reservado=0
        """) == 2, "stock modificado")
        check(one(admin,"SELECT count(*) FROM pg_policies WHERE schemaname='public'") == 35,
              "policies modificadas")
    print("5AH A10: PASS_CALL_REAL_DENEGADO en 4 SP x 2 roles, sin DDL/DML")
    print("5AH A08/A11/A13: PENDIENTES, requieren servicios/procedimientos tenant")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"5AH ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
