"""5AS: extender el control de actor SQL a CU16, CU17 y CU18.

Reutiliza el 5AR ya probado: mapa PRIVADO y verificador de session_user.
Instala versiones temporales de los TRES SP con guardia al comienzo
dentro de UNA transaccion reversible del LAB PostgreSQL 18 :55432.

Pruebas negativas: principal empresarial compartido A/B y un usuario SQL
individual que declara otro usuario (misma empresa o distinta) => 42501,
INCLUSO si modifican app.usuario_id. Pruebas positivas del verificador:
A1/A2/B1 coinciden SOLO con sus propias identidades.

NO se prueban operaciones DML positivas con principal individual: requieren
ACL y RLS de cada tabla adaptadas a ese principal. NO desplegar SaaS.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

ROOT = Path(__file__).resolve().parent
LAB = runpy.run_path(str(ROOT / "53_rol_sql_por_usuario_guardia_reversible_5ar.py"))
PREFLIGHT = LAB["PREFLIGHT"]
FIXTURES = LAB["FIXTURES"]
SOURCES = LAB["SOURCES"]
ONE = LAB["ONE"]
ENSURE = LAB["ENSURE"]
CREAR_A2 = LAB["crear_segundo_usuario_a"]
MAPEAR_A = LAB["instalar_mapeo_transitorio"]
INSTALAR_CU16 = LAB["instalar_cu16_guardado_solo_lab"]
TEST_ACTOR = LAB["comprobar_por_usuario"]
SQL_A1, SQL_A2 = LAB["SQL_A1"], LAB["SQL_A2"]
TENANT_A = LAB["TENANT_ROLE"]
TENANT_B = LAB["M"]["B"]
HOST, PORT, DB = LAB["HOST"], LAB["PORT"], LAB["DB"]
SQL_B1 = "fashionstore_5as_b1"
NOMBRES_SP = {
    "sp_crear_reserva_tenant": (
        "bigint,bigint,bigint,timestamptz,text,bigint", "CU16"
    ),
    "sp_cancelar_reserva_tenant": (
        "bigint,bigint,bigint,text", "CU17"
    ),
    "sp_finalizar_reserva_sin_compra_tenant": (
        "bigint,bigint,bigint,text", "CU18"
    ),
}


def ampliar_mapa_para_b(c, tenant_b, usuario_b):
    """Identidad SQL individual B1, sin clave de acceso ni ACL comercial."""
    c.execute(f"""CREATE ROLE {SQL_B1}
        WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS PASSWORD NULL""")
    ENSURE(ONE(c, """SELECT count(*) FROM pg_catalog.pg_authid
        WHERE rolname=%s AND rolpassword IS NULL
          AND NOT rolsuper AND NOT rolbypassrls
          AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolinherit
        """, (SQL_B1,)) == 1, "B1 tiene atributos SQL indebidos")
    c.execute("""INSERT INTO public.fs_lab_actor_sql_5ar
        (principal_sql,empresa_id,usuario_id)
        VALUES(%s,%s,%s)""", (SQL_B1,tenant_b,usuario_b))
    c.execute(f"GRANT USAGE ON SCHEMA public TO {SQL_B1}")
    c.execute(f"""GRANT EXECUTE ON FUNCTION
        public.fs_lab_verificar_actor_5ar(bigint,bigint) TO {SQL_B1}""")
    ENSURE(ONE(c, """SELECT has_table_privilege(
        %s,'public.fs_lab_actor_sql_5ar','SELECT')""", (SQL_B1,)) is False,
        "B1 puede consultar el mapa de identidades protegido")


def proteger_cu17_y_cu18(c, originales):
    """Fail-closed antes de leer/modificar reservas, usando la misma guardia."""
    punto = "BEGIN\n    IF p_empresa_id IS NULL OR p_empresa_id<=0"
    guardia = """BEGIN
    IF NOT public.fs_lab_verificar_actor_5ar(p_empresa_id,p_usuario_id) THEN
        RAISE EXCEPTION 'Actor SQL no coincide con usuario de la empresa'
            USING ERRCODE='42501';
    END IF;
    IF p_empresa_id IS NULL OR p_empresa_id<=0"""
    for nombre in (
        "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ):
        sql = originales[nombre]
        ENSURE(sql.count(punto)==1,
               f"ancla SQL {nombre} cambió; prohibido parchear a ciegas")
        c.execute(sql.replace(punto,guardia,1))
    for nombre in NOMBRES_SP:
        codigo = ONE(c, """SELECT pg_get_functiondef(p.oid)
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=%s AND p.prokind='p'
              AND NOT p.prosecdef""", (nombre,))
        ENSURE("IF NOT public.fs_lab_verificar_actor_5ar" in codigo,
               f"guardia no registrada en {nombre}")
    # Sólo son privilegios de EXECUTE transitorios para poder demostrar
    # que el SP falla por su check de identidad (y NO por falta de GRANT).
    # NO otorgar INSERT/UPDATE/SELECT comercial a los roles individuales.
    for nombre,(firma,_) in NOMBRES_SP.items():
        c.execute(f"""GRANT EXECUTE ON PROCEDURE
            public.{nombre}({firma}) TO {SQL_A1},{SQL_A2},{SQL_B1}""")


def intento_denegado(c, *, principal, nombre_sp, empresa,
                    reserva_o_carrito, actor_falso):
    """Ejecuta CALL dentro de savepoint y exige rechazo 42501 de la guardia."""
    ENSURE(principal in {SQL_A1,SQL_A2,SQL_B1,TENANT_A,TENANT_B},
           "principal no permitido en el laboratorio")
    ENSURE(nombre_sp in NOMBRES_SP, "procedimiento no permitido")
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {principal}")
    ENSURE(ONE(c,"SELECT session_user")==principal,
           "no se asignó el principal SQL del ensayo")
    # Confirmar que el fallo NO viene de la ACL EXECUTE.
    ENSURE(ONE(c,"""SELECT has_function_privilege(
        session_user,p.oid,'EXECUTE') FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname=%s AND p.prokind='p'
        """,(nombre_sp,)) is True,
        "usuario no tiene EXECUTE: prueba inconclusa")
    # La GUC es editable por este mismo principal; nunca debe permitir
    # que p_usuario_id o session_user sean suplantados.
    c.execute("SELECT set_config('app.usuario_id',%s,true)",
              (str(actor_falso),))
    c.execute("SAVEPOINT sp_5as_denegado")
    try:
        if nombre_sp == "sp_crear_reserva_tenant":
            fecha = datetime.now(timezone.utc)+timedelta(days=2)
            c.execute(LAB["CALL_CREATE"],
                (empresa,reserva_o_carrito,actor_falso,fecha,
                 "5AS denegación",None))
        else:
            llamada = LAB["M"]["CALL_CHANGE"].format(nombre_sp)
            c.execute(llamada,
                (empresa,reserva_o_carrito,actor_falso,"5AS denegación"))
    except psycopg.Error as err:
        codigo=err.sqlstate
        c.execute("ROLLBACK TO SAVEPOINT sp_5as_denegado")
        ENSURE(codigo=="42501",
               f"{principal}/{nombre_sp}: SQLSTATE {codigo} distinto de 42501")
        print(f"5AS {NOMBRES_SP[nombre_sp][1]} DENEGADO PASS: "
              f"{principal} no acredita p_usuario_id ajeno ({codigo})")
    else:
        c.execute("ROLLBACK TO SAVEPOINT sp_5as_denegado")
        raise RuntimeError(
            f"5AS PELIGRO: {nombre_sp} aceptó actor falso bajo {principal}"
        )
    finally:
        # Si un assert Python falla, el BEGIN exterior sigue protegido por
        # el ROLLBACK final de main. No liberar un SAVEPOINT abortado.
        pass
    c.execute("RELEASE SAVEPOINT sp_5as_denegado")
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(c,"SELECT session_user")=="postgres","no regresó DBA LAB")


def verificar_5as(c, ids, segundo):
    a, b = ids["a_empresa"],ids["b_empresa"]
    a1, b1 = ids["a_usuario"],ids["b_usuario"]
    # Verificar la identidad incluso ante manipulación de la GUC.
    TEST_ACTOR(c,SQL_A1,a,a1,segundo)
    TEST_ACTOR(c,SQL_A2,a,segundo,a1)
    TEST_ACTOR(c,SQL_B1,b,b1,a1)
    # Un mismo principal de empresa NO representa individuo.
    for principal, procedimiento, empresa, objeto, usuario in (
        (TENANT_A,"sp_crear_reserva_tenant",a,ids["a_carrito"],a1),
        (TENANT_A,"sp_cancelar_reserva_tenant",a,1,a1),
        (TENANT_B,"sp_finalizar_reserva_sin_compra_tenant",b,ids["b_reserva"],b1),
        (SQL_A1,"sp_crear_reserva_tenant",a,ids["a_carrito"],segundo),
        (SQL_A1,"sp_cancelar_reserva_tenant",a,1,segundo),
        (SQL_A2,"sp_crear_reserva_tenant",a,ids["a_carrito"],a1),
        (SQL_B1,"sp_finalizar_reserva_sin_compra_tenant",b,ids["b_reserva"],a1),
    ):
        intento_denegado(c,principal=principal,nombre_sp=procedimiento,
                        empresa=empresa,reserva_o_carrito=objeto,
                        actor_falso=usuario)
    print("5AS GUARDIAS PASS: CU16/CU17/CU18 verifican identidad SQL al inicio")


def main():
    print("5AS: proteger los tres SP por principal individual; rollback total")
    fuentes = SOURCES()
    pw = getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=pw,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=35000 -c lock_timeout=4000",
    ) as conn:
        PREFLIGHT(conn)
        print("5AS PREFLIGHT OK: PG18 laboratorio 55432")
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='35s'")
            conn.execute("SET LOCAL lock_timeout='4s'")
            ids=FIXTURES(conn)
            segundo=CREAR_A2(conn,ids["a_empresa"])
            MAPEAR_A(conn,ids["a_empresa"],ids["a_usuario"],segundo)
            ampliar_mapa_para_b(conn,ids["b_empresa"],ids["b_usuario"])
            INSTALAR_CU16(conn,fuentes)
            proteger_cu17_y_cu18(conn,fuentes)
            ENSURE(ONE(conn,
                "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                == 52,"políticas transitorias de laboratorio inesperadas")
            verificar_5as(conn,ids,segundo)
        finally:
            conn.execute("ROLLBACK")
            PREFLIGHT(conn)
            ENSURE(ONE(conn, """SELECT count(*) FROM pg_roles WHERE
                rolname IN (%s,%s,%s)""",(SQL_A1,SQL_A2,SQL_B1))==0,
                "algún principal individual de ensayo persistió")
            ENSURE(ONE(conn,
                "SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                is True,"el mapa de identidad quedó instalado")
            print("5AS ROLLBACK OK: 0 roles individuales, 0 mapa, 0 SP finales")
        print("5AS OK: los 3 procedimientos rechazaron al actor SQL falso")
        print("5AS PENDIENTE: validación positiva DML bajo roles individuales,"
              " RLS/ACL por actor, API conexión sin superuser; flags OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AS ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
