"""5BA: auditoría del perímetro SQL tras 5AZ, SÓLO PG18 LAB.

Comprobar en catálogo real (no mocks): todas las tablas del esquema public,
GRANT efectivo por tabla/columna, funciones/procedimientos SECURITY DEFINER,
acceso público EXECUTE, membresía a propietarios, roles individuales
sin password y ausencia de privilegios CREATE.

Absolutamente todas las escrituras de fixtures/policies/roles/SP son
temporales: BEGIN ... ROLLBACK obligatorio, preflight antes y después.
Exclusivo 127.0.0.1:55432/fashionstore_rls_lab_5u, 2 empresas ficticias.
Nunca usar en 5432 ni Supabase; flags SaaS siguen false.

Hallazgos FUERA de las ocho tablas de reserva se reportan con "ALERTA":
no significa aprobado para producción. Los roles A1/A2/B1 creados con
PASSWORD NULL dentro de una transacción aún NO prueban login real.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

HERE=Path(__file__).resolve().parent
AZ=runpy.run_path(str(HERE/"59_http_jwt_sp_definer_rollback_5az.py"))
X=AZ["X"]
ONE,ENSURE=AZ["ONE"],AZ["ENSURE"]
HOST,PORT,DB=AZ["HOST"],AZ["PORT"],AZ["DB"]
PREFLIGHT,FIXTURES,SOURCES=AZ["PREFLIGHT"],AZ["FIXTURES"],AZ["SOURCES"]
ROLES=X["ROLES"]
SHARED=X["SHARED"]
ACTORS=ROLES+SHARED
WRITER=X["WRITER"]
VERIFIER_OWNER=X["VERIFIER_OWNER"]
TABLES=X["TABLES"]
PROCEDURES=X["PROCEDURES"]

AUDITED_ROUTINES=(
    *PROCEDURES.keys(),
    "fs_verificar_actor_reserva",
    "fs_lab_empresa_actor_5at",
)
WRITABLE=("INSERT","UPDATE","DELETE","TRUNCATE","TRIGGER")


def configurar_lab(c,fuentes):
    ids=FIXTURES(c)
    a2=X["M"]["CREAR_A2"](c,ids["a_empresa"])
    X["M"]["MAPEAR_A"](c,ids["a_empresa"],ids["a_usuario"],a2)
    X["M"]["MAPEAR_B"](c,ids["b_empresa"],ids["b_usuario"])
    X["M"]["INSTALAR_CU16"](c,fuentes)
    X["M"]["PROTEGER_CU17_18"](c,fuentes)
    X["M"]["ACL_RLS"](c)
    X["M"]["instalar_verificador_5av"](c)
    X["M"]["usar_unico_verificador_en_tres_sp"](c)
    X["X"]["preparar_restriccion_individual"](c)
    X["preparar_writer"](c)
    X["copiar_rls_a_writer"](c)
    X["grant_lecturas_y_escrituras_writer"](c)
    X["retirar_dml_directo_de_actores"](c)
    X["instalar_sp_como_definer"](c)
    ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
           ==161,"perímetro RLS 5AX no coincidió")
    return ids,a2


def auditar_tablas(c):
    rows=c.execute("""SELECT cl.oid,cl.relname
       FROM pg_catalog.pg_class cl
       JOIN pg_catalog.pg_namespace ns ON ns.oid=cl.relnamespace
       WHERE ns.nspname='public' AND cl.relkind='r'
       ORDER BY cl.relname""").fetchall()
    ENSURE(len(rows)>=46,"faltan tablas del laboratorio original")
    ENSURE(any(name=="fs_lab_actor_sql_5ar" for _,name in rows),
           "mapa temporal no instalado")
    riesgos=[]
    comprobados=0
    for oid,name in rows:
        for role in ACTORS:
            for priv in WRITABLE:
                allowed=ONE(c,"SELECT has_table_privilege(%s,%s::oid,%s)",
                            (role,oid,priv))
                ENSURE(type(allowed) is bool,"privilegio tabla no comprobable")
                comprobados+=1
                if allowed:
                    riesgos.append((role,name,priv,"tabla"))
            for priv in ("INSERT","UPDATE","REFERENCES"):
                allowed=ONE(c,"SELECT has_any_column_privilege(%s,%s::oid,%s)",
                            (role,oid,priv))
                ENSURE(type(allowed) is bool,"privilegio columna no comprobable")
                comprobados+=1
                if allowed:
                    riesgos.append((role,name,priv,"columna"))
    # La protección 5AX excluye DML también por COLUMNA; asegurar que
    # esa premisa nunca quede oculta por alertas de tablas no revisadas.
    esenciales=[x for x in riesgos if x[1] in TABLES]
    ENSURE(not esenciales,
           "5BA BRECHA: los ocho recursos críticos aún admiten DML directo: "
           +str(esenciales[:10]))
    sensibles=[(role,name,priv,alcance) for role,name,priv,alcance in riesgos
               if name in ("usuario","cliente","empleado","empresa",
                           "usuario_empresa","fs_lab_actor_sql_5ar")]
    ENSURE(not sensibles,
           "5BA BRECHA: DML directo sobre identidad/tenant/mapa: "
           +str(sensibles[:10]))
    print(f"5BA CATALOGO PASS: {len(rows)} tablas public, "
          f"{len(ACTORS)} principales, {comprobados} privilegios efectivos")
    print("5BA CRITICOS PASS: ocho tablas de reserva/stock sin "
          "INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER para actores SQL")
    if riesgos:
        # AUDITORÍA no destructiva: detallar SOLO identidades y nombres de
        # tablas públicas del laboratorio, nunca contraseñas, datos ni hashes.
        print(f"5BA ALERTA ALCANCE: {len(riesgos)} otros privilegios "
              "efectivos fuera de las ocho tablas protegidas")
        for role,name,priv,alcance in riesgos[:20]:
            print(f"5BA ALERTA: {role} {name} {priv} ({alcance})")
    else:
        print("5BA ALCANCE PASS: no se encontró DML directo "
              "en ninguna tabla public para los cinco principales")
    return riesgos


def auditar_rutinas(c):
    datos=c.execute("""SELECT p.proname,p.prokind,p.prosecdef,
         pg_get_userbyid(p.proowner),
         (p.proacl IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM aclexplode(p.proacl) acl
            WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
         )) AS publico_revocado,
         (r.rolcanlogin IS FALSE AND r.rolsuper IS FALSE
          AND r.rolbypassrls IS FALSE) AS dueno_limitado,
         (p.proconfig @>
           ARRAY['search_path=pg_catalog, pg_temp']::text[]) AS path_seguro
       FROM pg_catalog.pg_proc p
       JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
       JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
       WHERE n.nspname='public' AND p.proname=ANY(%s)
       ORDER BY p.proname""",(list(AUDITED_ROUTINES),)).fetchall()
    ENSURE(len(datos)==len(AUDITED_ROUTINES),
           "no se encontraron todas las cinco rutinas protegidas")
    for name,kind,definer,owner,public_revoked,limited,path_ok in datos:
        expected=WRITER if name in PROCEDURES else (
            VERIFIER_OWNER if name=="fs_verificar_actor_reserva" else
            "postgres"
        )
        ENSURE(kind==("p" if name in PROCEDURES else "f"),
               "tipo de rutina inesperado: "+name)
        ENSURE(owner==expected,"owner incorrecto de "+name)
        ENSURE(bool(public_revoked),"PUBLIC EXECUTE permitido: "+name)
        ENSURE(bool(path_ok),"search_path inseguro: "+name)
        if name!="fs_lab_empresa_actor_5at":
            ENSURE(bool(definer) and bool(limited),
                   "definer inseguro: "+name)
        else:
            # Hallazgo conocido: función auxiliar 5AT creada por postgres.
            # No debe promoverse como artefacto final sin dueño NOLOGIN.
            ENSURE(bool(definer),"función contexto no es definer")
            print("5BA PENDIENTE: fs_lab_empresa_actor_5at "
                  "es SECURITY DEFINER con dueño DBA del ensayo, "
                  "NO apta para instalación final")
    for role in ACTORS:
        for owner in (WRITER,VERIFIER_OWNER):
            member=ONE(c,"SELECT pg_has_role(%s::name,%s::name,'MEMBER')",
                       (role,owner))
            ENSURE(member is False,
                   f"5BA BRECHA: {role} pertenece al owner {owner}")
    print("5BA FUNCIONES PASS: SP y verificador sin EXECUTE PUBLIC, "
          "owners comprobados, membresía a owners bloqueada")


def auditar_login(c):
    actors=c.execute("""SELECT rolname,rolcanlogin,rolpassword IS NULL,
           rolsuper,rolbypassrls,rolcreaterole,rolcreatedb
       FROM pg_catalog.pg_authid
       WHERE rolname=ANY(%s) ORDER BY rolname""",
       (list(ROLES),)).fetchall()
    ENSURE(len(actors)==3,"no están A1/A2/B1")
    for role,canlogin,password_null,superuser,bypass,create_role,create_db in actors:
        ENSURE(canlogin and password_null and not any(
            (superuser,bypass,create_role,create_db)),
            "actor de laboratorio con password o privilegios inesperados")
        ENSURE(ONE(c,"SELECT has_schema_privilege(%s,'public','CREATE')",
                   (role,)) is False,"actor puede crear objetos SQL")
    print("5BA LOGIN PENDIENTE: tres roles individuales sin password "
          "(PASSWORD NULL), creados sin COMMIT; ninguna conexión nueva "
          "puede acreditarse aún como A1/A2/B1")
    return False


def main():
    print("5BA: AUDITORÍA CATALOGO Y LOGIN PG18, 0 COMMIT, ROLLBACK")
    fuentes=SOURCES()
    pw=getpass("Contraseña postgres SOLO laboratorio 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=pw,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=65000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5BA PREFLIGHT OK: laboratorio dos tenants ficticios")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='65s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            configurar_lab(c,fuentes)
            riscos=auditar_tablas(c)
            auditar_rutinas(c)
            auditar_login(c)
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                       ([WRITER,VERIFIER_OWNER,*ROLES],))==0,
                   "rol temporal quedó persistente")
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==41,"policies no regresaron a su baseline")
            ENSURE(ONE(c,"SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                   is True,"mapa privado persistió")
            print("5BA ROLLBACK VERIFICADO: 0 roles/owners, 0 mapa, "
                  "0 SP comerciales, 41 policies originales")
        if riscos:
            print("5BA BLOQUEO DE DESPLIEGUE: revisar permisos adicionales")
        else:
            print("5BA ACL GENERAL PASS: ninguna tabla public permite "
                  "DML directo a los cinco principales del laboratorio")
        print("5BA BLOQUEO ACTUAL: sigue faltando login PG individual "
              "autenticado y conexiones HTTP sin DBA; flags OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        print(f"5BA ERROR: {type(error).__name__}: {error}",file=sys.stderr)
        sys.exit(2)
