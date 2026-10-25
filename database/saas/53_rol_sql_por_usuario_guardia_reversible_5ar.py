"""5AR - primer control REAL contra suplantacion SQL intraempresa.

Crea, dentro de un unico BEGIN...ROLLBACK en LAB 55432:
* Dos principales SQL INDIVIDUALES A1/A2, sin password ni privilegios elevados.
* Un mapa SQL principal -> (empresa_id, usuario_id) solo legible por postgres.
* Funcion SECURITY DEFINER de SOLO comprobacion de identidad, con
  search_path seguro y EXECUTE limitado/revocado a PUBLIC.
* Una version temporal del SP CU16 que EXIGE el mapa, ademas de RLS.

Demostracion: mismo tenant pero usuario equivocado => FALSE; intentar cambiar
app.usuario_id no tiene efecto; principal empresarial compartido => FALSE y
CALL CU16 denegado. Usuarios individuales validan solo SU PROPIO actor.

NO prepara un login SQL para usuarios reales, ni autoriza activar SaaS.
NO modifica la BD original 5432 ni Supabase. 0 COMMIT.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

ROOT = Path(__file__).resolve().parent
M = runpy.run_path(str(ROOT / "49_ensayo_sp_tenant_reversible_5an.py"))
PREFLIGHT = M["preflight"]
FIXTURES = M["fixtures"]
SOURCES = M["sources"]
ACL_LAB = M["grant_minimal_lab"]
AS_TENANT = M["as_role"]
ONE = M["one"]
ENSURE = M["ensure"]
CALL_CREATE = M["CALL_CREATE"]
TENANT_ROLE = M["A"]
HOST, PORT, DB = M["HOST"], M["PORT"], M["DB"]

SQL_A1 = "fashionstore_5ar_a1"
SQL_A2 = "fashionstore_5ar_a2"
GUARD_FN = "public.fs_lab_verificar_actor_5ar"


def crear_segundo_usuario_a(c, a_empresa):
    rol = ONE(c, "SELECT id FROM public.rol WHERE nombre='CLIENTE'")
    uid = ONE(c, """INSERT INTO public.usuario
        (rol_id, correo, password_hash, estado)
        VALUES(%s,'cliente-a2-5ar@demo.invalid',
               '!HASH-INUTILIZABLE-SOLO-5AR',TRUE) RETURNING id""", (rol,))
    c.execute("""INSERT INTO public.usuario_empresa
        (usuario_id, empresa_id, rol_id, estado)
        VALUES(%s,%s,%s,TRUE)""", (uid,a_empresa,rol))
    c.execute("""INSERT INTO public.cliente
        (empresa_id,usuario_id,nombre,apellido,estado)
        VALUES(%s,%s,'Cliente','A2 de 5AR',TRUE)""", (a_empresa,uid))
    return uid


def instalar_mapeo_transitorio(c, a_empresa, a1, a2):
    for principal in (SQL_A1, SQL_A2):
        # Solo uso en un ensayo: roles sin contraseña que no pueden
        # establecer conexiones externas directas; DBA simula el principal.
        c.execute(f"""CREATE ROLE {principal}
            WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS PASSWORD NULL""")
    ENSURE(ONE(c, """SELECT count(*) FROM pg_catalog.pg_authid WHERE rolname
        IN (%s,%s) AND rolpassword IS NULL AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb
        AND NOT rolinherit""", (SQL_A1,SQL_A2)) == 2,
        "roles individuales no son de minimo privilegio")

    # Mapa protegido, nunca se expone ni se modifica por usuarios del SaaS.
    c.execute("""CREATE TABLE public.fs_lab_actor_sql_5ar (
        principal_sql name PRIMARY KEY,
        empresa_id bigint NOT NULL REFERENCES public.empresa(id),
        usuario_id bigint NOT NULL REFERENCES public.usuario(id),
        UNIQUE (empresa_id,usuario_id)
    )""")
    c.execute("ALTER TABLE public.fs_lab_actor_sql_5ar ENABLE ROW LEVEL SECURITY")
    c.execute("REVOKE ALL ON TABLE public.fs_lab_actor_sql_5ar FROM PUBLIC")
    c.execute("""INSERT INTO public.fs_lab_actor_sql_5ar
        (principal_sql,empresa_id,usuario_id) VALUES
        (%s,%s,%s),(%s,%s,%s)""",
        (SQL_A1,a_empresa,a1,SQL_A2,a_empresa,a2))

    c.execute(r"""
        CREATE FUNCTION public.fs_lab_verificar_actor_5ar(
            p_empresa_id bigint, p_usuario_id bigint
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn_5ar$
            SELECT EXISTS (
              SELECT 1 FROM public.fs_lab_actor_sql_5ar m
              JOIN public.usuario u ON u.id=m.usuario_id AND u.estado IS TRUE
              JOIN public.usuario_empresa ue
                   ON ue.usuario_id=m.usuario_id
                  AND ue.empresa_id=m.empresa_id
                  AND ue.estado IS TRUE
              JOIN public.empresa e
                   ON e.id=m.empresa_id AND e.estado IS TRUE
              WHERE m.principal_sql=session_user::name
                AND m.empresa_id=p_empresa_id
                AND m.usuario_id=p_usuario_id
            )
        $fn_5ar$
    """)
    # PostgreSQL otorga EXECUTE a PUBLIC por defecto; revocar en la MISMA tx.
    c.execute("""REVOKE ALL ON FUNCTION
        public.fs_lab_verificar_actor_5ar(bigint,bigint) FROM PUBLIC""")
    c.execute(f"GRANT USAGE ON SCHEMA public TO {SQL_A1},{SQL_A2}")
    c.execute(f"""GRANT EXECUTE ON FUNCTION
        public.fs_lab_verificar_actor_5ar(bigint,bigint)
        TO {SQL_A1},{SQL_A2},{TENANT_ROLE}""")
    ENSURE(ONE(c, """SELECT count(*) FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname='fs_lab_verificar_actor_5ar'
          AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']::text[]""")
           == 1, "funcion firmada/parametros de seguridad no verificados")
    for principal in (SQL_A1, SQL_A2):
        ENSURE(ONE(c,
            "SELECT has_table_privilege(%s,'public.fs_lab_actor_sql_5ar','SELECT')",
            (principal,)) is False, "mapa interno expuesto")


def instalar_cu16_guardado_solo_lab(c, sqls):
    for sql in sqls.values():
        c.execute(sql)

    src = sqls["sp_crear_reserva_tenant"]
    punto = """BEGIN
    IF p_empresa_id IS NULL OR p_empresa_id <= 0"""
    guardia = """BEGIN
    IF NOT public.fs_lab_verificar_actor_5ar(p_empresa_id,p_usuario_id) THEN
        RAISE EXCEPTION 'Actor SQL no coincide con usuario de la empresa'
            USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id <= 0"""
    ENSURE(src.count(punto)==1,"ancla CU16 original modificada; abortar")
    c.execute(src.replace(punto,guardia,1))
    ACL_LAB(c) # ACL/RLS anteriores sólo en laboratorio, también reversibles.


def comprobar_por_usuario(c, rol, empresa, propio, ajeno):
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {rol}")
    ENSURE(ONE(c,"SELECT session_user")==rol,"principal individual no activo")
    ENSURE(ONE(c,
        "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",(empresa,propio))
        is True, "no se reconoce su propio usuario")
    ENSURE(ONE(c,
        "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",(empresa,ajeno))
        is False,"se permite usuario ajeno dentro del tenant")
    # Nunca confiar en GUC editable por una sesión sin privilegios.
    c.execute("SELECT set_config('app.usuario_id',%s,true)",(str(ajeno),))
    ENSURE(ONE(c,
        "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",(empresa,ajeno))
        is False,"GUC app.usuario_id permitió suplantación")
    ENSURE(ONE(c,"SELECT has_table_privilege(" +
               "'public.fs_lab_actor_sql_5ar','SELECT')") is False,
           "el usuario puede leer el mapa")
    print(f"5AR ACTOR PASS: {rol} solo acredita su identidad, "
          "GUC manipulada no habilita A1/A2 ajeno")
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(c,"SELECT session_user")=="postgres","no volvió DBA del lab")


def comprobar_sp_compartido_bloqueado(c, empresa, carrito, uid):
    AS_TENANT(c,TENANT_ROLE)
    ENSURE(ONE(c,"SELECT session_user")==TENANT_ROLE,"rol SQL A inesperado")
    ENSURE(ONE(c,
        "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",(empresa,uid))
        is False, "el rol SQL compartido pudo acreditar a A1")
    from datetime import datetime, timedelta, timezone
    fecha=datetime.now(timezone.utc)+timedelta(days=2)
    c.execute("SAVEPOINT sp_5ar_guardado")
    try:
        c.execute(CALL_CREATE,
                  (empresa,carrito,uid,fecha,"5AR actor SQL",None))
    except psycopg.Error as err:
        codigo=err.sqlstate
        c.execute("ROLLBACK TO SAVEPOINT sp_5ar_guardado")
        ENSURE(codigo=="42501",
               "el SP protegido falló por error distinto a identidad")
        print("5AR CU16 BLOQUEADO PASS: SQL compartido A no puede "
              "reservar como A1/A2; check 42501")
    else:
        c.execute("ROLLBACK TO SAVEPOINT sp_5ar_guardado")
        raise RuntimeError("5AR PELIGRO: CU16 aceptó identidad bajo rol compartido")
    c.execute("RELEASE SAVEPOINT sp_5ar_guardado")
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")


def main():
    print("5AR: actor SQL individual / guardia CU16 / ROLLBACK TOTAL")
    sqls=SOURCES()
    pw=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=pw,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=25000 -c lock_timeout=4000",
    ) as c:
        PREFLIGHT(c)
        print("5AR PREFLIGHT OK: base A/B, 41 policies")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='25s'")
            c.execute("SET LOCAL lock_timeout='4s'")
            ids=FIXTURES(c)
            a2=crear_segundo_usuario_a(c,ids["a_empresa"])
            instalar_mapeo_transitorio(c,ids["a_empresa"],
                                      ids["a_usuario"],a2)
            instalar_cu16_guardado_solo_lab(c,sqls)
            ENSURE(ONE(c,
                "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                == 52,"policies temporales cambiaron inesperadamente")
            comprobar_por_usuario(c,SQL_A1,ids["a_empresa"],
                                  ids["a_usuario"],a2)
            comprobar_por_usuario(c,SQL_A2,ids["a_empresa"],
                                  a2,ids["a_usuario"])
            comprobar_sp_compartido_bloqueado(
                c,ids["a_empresa"],ids["a_carrito"],ids["a_usuario"])
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            print("5AR ROLLBACK VERIFICADO: mapa/roles/SP sin persistencia, "
                  "41 policies originales")
        print("5AR OK: controles SQL individuales viables en laboratorio")
        print("5AR PENDIENTE: RLS DML con roles individuales, "
              "CU17/CU18, conexion API segura y pruebas end-to-end; "
              "no activar flags SaaS")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AR ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
