"""5BB: quitar propietario DBA de la funcion RLS de contexto individual.

SOLO LAB PostgreSQL 18, 127.0.0.1:55432 / fashionstore_rls_lab_5u.
Sin migraciones permanentes, sin rol con password, sin conexión a 5432
ni Supabase. Fixtures, DDL, ACL, cinco CALL positivos y negativas SQL
ocurren en una transaccion EXCLUSIVA BEGIN ... ROLLBACK.

5BA detectó public.fs_lab_empresa_actor_5at SECURITY DEFINER bajo
postgres. El propietario de VERIFICADOR 5AV ya es NOLOGIN, NOINHERIT,
NOSUPERUSER, NOBYPASSRLS, tiene GRANT SELECT limitado sobre el mapa
privado y una policy SELECT específica. Se transfieren las dos funciones
al MISMO propietario verificador, no al owner de los tres SP comerciales.
El mapa y RLS permanecen privados para A1/A2/B1/A/B.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

HERE=Path(__file__).resolve().parent
BA=runpy.run_path(str(HERE/"60_auditar_acl_todas_tablas_roles_login_rollback_5ba.py"))
X=BA["X"]
PREFLIGHT=BA["PREFLIGHT"]
SOURCES=BA["SOURCES"]
ONE=BA["ONE"]
ENSURE=BA["ENSURE"]
HOST,PORT,DB=BA["HOST"],BA["PORT"],BA["DB"]
VERIFIER_OWNER=BA["VERIFIER_OWNER"]
WRITER=BA["WRITER"]
ROLES=BA["ROLES"]
ALL_ACTORS=BA["ACTORS"]
EXPECTED_ROUTINES=(
    *X["PROCEDURES"].keys(),
    "fs_verificar_actor_reserva",
    "fs_lab_empresa_actor_5at",
)


def transferir_funcion_contexto(c):
    """El GRANT CREATE existe sólo dentro del ensayo y se revoca al mover."""
    ENSURE(ONE(c,"""SELECT pg_get_userbyid(p.proowner)
      FROM pg_catalog.pg_proc p
      WHERE p.oid=to_regprocedure('public.fs_lab_empresa_actor_5at()')
        AND p.prosecdef""")=="postgres",
      "se alteró el owner del punto de partida 5BA")
    ENSURE(ONE(c,"""SELECT count(*) FROM pg_catalog.pg_roles
      WHERE rolname=%s AND NOT rolcanlogin AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolinherit""",(VERIFIER_OWNER,))==1,
      "owner verificador no es NOLOGIN de minimo privilegio")
    # 5BB: 5AV deliberadamente otorgó SELECT por COLUMNA,
    # nunca SELECT de toda la tabla. has_table_privilege da False
    # correctamente aunque las tres columnas indispensables sean legibles.
    mapa="public.fs_lab_actor_sql_5ar"
    ENSURE(ONE(c,"SELECT has_table_privilege(%s,%s,'SELECT')",
               (VERIFIER_OWNER,mapa)) is False,
           "owner recibió SELECT de tabla completa en lugar del mínimo")
    for col in ("principal_sql","empresa_id","usuario_id"):
        ENSURE(ONE(c,"SELECT has_column_privilege(%s,%s,%s,'SELECT')",
                   (VERIFIER_OWNER,mapa,col)) is True,
               f"owner NOLOGIN no puede leer la columna {col} del mapa")
    ENSURE(ONE(c,"SELECT has_table_privilege(%s,%s,'UPDATE')",
               (VERIFIER_OWNER,mapa)) is False,
           "owner NOLOGIN no debería modificar el mapa")
    # Comprobar SELECT efectivo con el principal NOLOGIN simulado
    # exclusivamente por postgres en la transacción LAB; no es login.
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {VERIFIER_OWNER}")
    try:
        ENSURE(ONE(c,"SELECT session_user")==VERIFIER_OWNER,
               "principal dueño no quedó activo")
        ENSURE(ONE(c,
            "SELECT row_security_active('public.fs_lab_actor_sql_5ar'::regclass)"
        ) is True,"RLS del mapa no está activo para el owner")
        filas=c.execute("SELECT principal_sql,empresa_id,usuario_id "
                        "FROM public.fs_lab_actor_sql_5ar").fetchall()
        ENSURE(len(filas)==3
               and len(set(row[0] for row in filas))==3,
               "owner NOLOGIN no ve exactamente el mapa ficticio A1/A2/B1")
    finally:
        c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(c,"SELECT session_user")=="postgres",
           "no se restauró DBA exclusivo del laboratorio")
    print("5BB MAPA PASS: SELECT de 3 columnas, RLS activo, 3 actores "
          "legibles por dueño NOLOGIN; sin SELECT total ni UPDATE")
    # Requiere CREATE temporal sobre public para ALTER FUNCTION OWNER.
    c.execute(f"GRANT CREATE ON SCHEMA public TO {VERIFIER_OWNER}")
    c.execute(f"""ALTER FUNCTION public.fs_lab_empresa_actor_5at()
        OWNER TO {VERIFIER_OWNER}""")
    c.execute(f"REVOKE CREATE ON SCHEMA public FROM {VERIFIER_OWNER}")
    ENSURE(ONE(c,"SELECT has_schema_privilege(%s,'public','CREATE')",
               (VERIFIER_OWNER,)) is False,
           "owner retuvo CREATE sobre public")
    ENSURE(ONE(c,"SELECT has_database_privilege(%s,current_database(),'CREATE')",
               (VERIFIER_OWNER,)) is False,
           "owner retuvo CREATE sobre la base")
    meta=ONE(c,"""SELECT pg_get_userbyid(p.proowner)
      FROM pg_catalog.pg_proc p
      WHERE p.oid=to_regprocedure('public.fs_lab_empresa_actor_5at()')
        AND p.prosecdef
        AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]""")
    ENSURE(meta==VERIFIER_OWNER,"funcion contexto no es definer restringida")
    print("5BB FUNCION CONTEXTO PASS: SECURITY DEFINER NOLOGIN, "
          "sin DBA owner ni CREATE publico")


def neutralizar_verificador_5ar_obsoleto(c):
    """5AR contiene una función histórica DBA-SECDEF ya reemplazada.

    Los tres SP activos llaman fs_verificar_actor_reserva (5AV).
    Desactivar DEFINER y revocar EXECUTE a todos los actores evita que
    una función no auditada retenga poderes privilegiados.
    """
    old="public.fs_lab_verificar_actor_5ar(bigint,bigint)"
    ENSURE(ONE(c,"""SELECT p.prosecdef FROM pg_catalog.pg_proc p
      WHERE p.oid=to_regprocedure(%s)""",(old,)) is True,
      "función legacy 5AR no coincide con baseline")
    for proc in X["PROCEDURES"]:
        ddl=ONE(c,"""SELECT pg_get_functiondef(p.oid) FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname=%s AND p.prokind='p'""",
          (proc,))
        ENSURE("fs_lab_verificar_actor_5ar" not in ddl,
               "SP comercial aún depende del verificador obsoleto")
    c.execute(f"ALTER FUNCTION {old} SECURITY INVOKER")
    targets=",".join(ALL_ACTORS)
    c.execute(f"REVOKE ALL ON FUNCTION {old} FROM {targets}")
    for role in ALL_ACTORS:
        ENSURE(ONE(c,"SELECT has_function_privilege(%s,"
                   " 'public.fs_lab_verificar_actor_5ar(bigint,bigint)',"
                   " 'EXECUTE')",(role,)) is False,
               "actor puede ejecutar verificador legado de DBA")
    ENSURE(ONE(c,"""SELECT p.prosecdef FROM pg_catalog.pg_proc p
      WHERE p.oid=to_regprocedure(%s)""",(old,)) is False,
      "función 5AR continúa SECURITY DEFINER DBA")
    print("5BB LEGACY PASS: verificador 5AR es INVOKER y no ejecutable "
          "por A1/A2/B1/A/B")


def comprobar_rutinas(c):
    rows=c.execute("""SELECT p.proname,p.prokind,
        p.prosecdef,pg_get_userbyid(p.proowner),
        (p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]),
        (p.proacl IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM aclexplode(p.proacl) acl
           WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
        ))
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname=ANY(%s)
      ORDER BY p.proname""",(list(EXPECTED_ROUTINES),)).fetchall()
    ENSURE(len(rows)==5,"se esperaban 3 SP y 2 funciones de verificacion")
    for name,kind,definer,owner,path_safe,public_revoked in rows:
        expected_owner=WRITER if name in X["PROCEDURES"] else VERIFIER_OWNER
        expected_kind="p" if name in X["PROCEDURES"] else "f"
        ENSURE(kind==expected_kind and definer is True
               and owner==expected_owner and path_safe is True
               and public_revoked is True,
               f"rutina no cumple OWNER, SECDEF, PATH, ACL: {name}")
    for actor in ALL_ACTORS:
        for owner in (WRITER,VERIFIER_OWNER):
            ENSURE(ONE(c,"SELECT pg_has_role(%s::name,%s::name,'MEMBER')",
                       (actor,owner)) is False,
                   f"actor {actor} es miembro del owner {owner}")
        ENSURE(ONE(c,"SELECT has_table_privilege(%s,"
                   "'public.fs_lab_actor_sql_5ar','SELECT')",
                   (actor,)) is False,
               f"actor {actor} puede leer el mapa privado")
    print("5BB RUTINAS PASS: 5 routines SECDEF, owners NOLOGIN "
          "independientes, ACL PUBLIC revocada")


def probar_ruta_contexto(c,ids,a2):
    """session_user siempre de rol individual, aun con GUC manipulada."""
    testcases=(
        (X["A1"],ids["a_empresa"],ids["b_empresa"]),
        (X["A2"],ids["a_empresa"],ids["b_empresa"]),
        (X["B1"],ids["b_empresa"],ids["a_empresa"]),
    )
    for actor,empresa,otra in testcases:
        X["M"]["como_rol"](c,actor)
        try:
            c.execute("SELECT set_config('app.empresa_id',%s,true)",
                      (str(otra),))
            ENSURE(ONE(c,
                "SELECT public.fs_lab_empresa_actor_5at()")==empresa,
                "GUC ajena cambió empresa del principal SQL")
            ENSURE(ONE(c,"SELECT count(*) FROM public.empresa")==1,
                   "se filtró otra empresa por RLS")
        finally:
            X["M"]["como_dba"](c)
    print("5BB RLS PASS: A1/A2/B1 acreditan solo su empresa "
          "aunque modifiquen GUC")


def repetir_flujos(c,ids,a2):
    """Conserva los cinco CALL de 5AX tras transferir el owner auxiliar."""
    carrito_a2=X["M"]["CARRO_A2"](c,ids,a2)
    from app.core.config import settings
    old_sp=settings.saas_sp_sql_owner
    try:
        settings.saas_sp_sql_owner=WRITER
        reserva_a2=X["M"]["verificar_5au_y_llamadas"](c,ids,a2,carrito_a2)
        X["auditar_guardia_definer"](c,ids,a2,carrito_a2)
        X["auditar_reserva_ajena_sin_grant"](c,ids,reserva_a2)
        X["auditar_dml_sin_privilegios"](c,ids,reserva_a2)
    finally:
        settings.saas_sp_sql_owner=old_sp
    print("5BB SP PASS: 5 operaciones CU16-18 y guardias "
          "funcionaron sin funcion SECURITY DEFINER de owner DBA")


def main():
    print("5BB: eliminar owner DBA del contexto SQL sin cambiar PG original")
    sqls=SOURCES()
    pw=getpass("Contraseña postgres SOLO laboratorio 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=pw,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=65000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5BB PREFLIGHT OK: PG18 LAB, A/B ficticios, 41 policies")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='65s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids,a2=BA["configurar_lab"](c,sqls)
            transferir_funcion_contexto(c)
            neutralizar_verificador_5ar_obsoleto(c)
            comprobar_rutinas(c)
            probar_ruta_contexto(c,ids,a2)
            repetir_flujos(c,ids,a2)
            riesgos=BA["auditar_tablas"](c)
            ENSURE(not riesgos,
                   "ACL global contiene privilegios DML residuales")
            BA["auditar_login"](c)
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_catalog.pg_roles "
                       "WHERE rolname=ANY(%s)",
                       ([WRITER,VERIFIER_OWNER,*ROLES],))==0,
                   "no se eliminaron los roles del ensayo")
            ENSURE(ONE(c,"SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                   is True,"no se revirtió el mapa privado")
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==41,"las políticas originales no regresaron")
            print("5BB ROLLBACK VERIFICADO: 0 roles/owners/mapas/SP, "
                  "41 policies, 3 reservas, 4 carritos originales")
        print("5BB OK: función de identidad sin superusuario, "
              "RLS y operaciones comerciales válidas")
        print("5BB PENDIENTE: login real PG no-superuser en cluster "
              "desechable, pool API/JWT y concurrencia; flags SaaS OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print(f"5BB ERROR: {type(err).__name__}: {err}",file=sys.stderr)
        sys.exit(2)
