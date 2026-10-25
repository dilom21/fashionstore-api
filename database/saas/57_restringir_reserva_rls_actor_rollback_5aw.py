"""5AW: cerrar RLS entre usuarios sobre RESERVA, sin crear una falsa
garantia de que UPDATE directo o INVENTARIO ya están asegurados.

SOLO PostgreSQL 18 LAB 127.0.0.1:55432 fashionstore_rls_lab_5u.
Preflight 5AM, BEGIN+ROLLBACK; jamás instalar en las 5 empresas ni Supabase.

5AV demostró que A1 podía UPDATE de reserva A2. Añadimos policy
AS RESTRICTIVE para SELECT y UPDATE sobre reserva: clientes SOLO sus
reservas; personal ADMINISTRADOR de su empresa sí puede gestionar.
La identidad se deriva del verificador SQL NOLOGIN 5AV, nunca de GUC.
Se reejecutan CU16/CU17 A1+A2 y CU18 B1 por servicio 5AU+SP real.
Se comprueba acceso RLS A1→A2 denegado SIN confundirlo con ausencia
de GRANT. Auditoría de UPDATE directo propio / inventario pendiente.

NO simula login real, no activa flags, no certifica producción.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

HERE = Path(__file__).resolve().parent
M = runpy.run_path(str(HERE / "56_servicio_actor_guard_real_rollback_5av.py"))
PREFLIGHT, FIXTURES, SOURCES = M["PREFLIGHT"], M["FIXTURES"], M["SOURCES"]
ONE, ENSURE = M["ONE"], M["ENSURE"]
ROLES = M["ROLES"]
SQL_A1, SQL_A2, SQL_B1 = M["SQL_A1"], M["SQL_A2"], M["SQL_B1"]
OWNER = M["OWNER"]
HOST, PORT, DB = M["HOST"], M["PORT"], M["DB"]

# Filas de cliente y administrador siempre permanecen dentro de la misma
# empresa. El check actor es SECURITY DEFINER propiedad del rol NOLOGIN.
OWN_OR_ADMIN = """
   empresa_id=public.fs_lab_empresa_actor_5at()
   AND (
      EXISTS (
        SELECT 1 FROM public.cliente cl
        WHERE cl.id=reserva.cliente_id
          AND cl.empresa_id=reserva.empresa_id
          AND cl.estado IS TRUE
          AND public.fs_verificar_actor_reserva(
              reserva.empresa_id,cl.usuario_id)
      )
      OR EXISTS (
        SELECT 1 FROM public.empleado em
        JOIN public.usuario_empresa ue
          ON ue.usuario_id=em.usuario_id
         AND ue.empresa_id=em.empresa_id AND ue.estado IS TRUE
        JOIN public.rol rr
          ON rr.id=ue.rol_id AND rr.estado IS TRUE
        WHERE em.empresa_id=reserva.empresa_id
          AND em.estado IS TRUE
          AND rr.nombre='ADMINISTRADOR'
          AND public.fs_verificar_actor_reserva(
              reserva.empresa_id,em.usuario_id)
      )
   )
"""


def preparar_restriccion_individual(c):
    """AND restrictivo sobre SELECT/UPDATE: policies permisivas 5AT quedan."""
    roles=", ".join(ROLES)
    for accion in ("SELECT","UPDATE"):
        sufijo=accion.lower()
        c.execute(f"""CREATE POLICY fs_lab_5aw_reserva_{sufijo}_individual
            ON public.reserva AS RESTRICTIVE FOR {accion} TO {roles}
            USING ({OWN_OR_ADMIN})
            {"WITH CHECK ("+OWN_OR_ADMIN+")" if accion=="UPDATE" else ""}
        """)
    total=ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
    ENSURE(total==107, f"se esperaban 107 policies temporales, hay {total}")
    policies=c.execute("""SELECT policyname,permissive,cmd,roles
        FROM pg_policies WHERE schemaname='public'
          AND tablename='reserva'
          AND policyname LIKE 'fs_lab_5aw_reserva_%'
        ORDER BY policyname""").fetchall()
    ENSURE(len(policies)==2,"faltan 2 policies restrictivas")
    for _,permisiva,accion,destinatarios in policies:
        ENSURE(permisiva=="RESTRICTIVE" and accion in ("SELECT","UPDATE")
               and set(ROLES).issubset(set(destinatarios)),
               "política RLS no restrictiva o sin roles adecuados")
    print("5AW RLS OK: SELECT y UPDATE de reserva restringidos "
          "a dueño individual / administrador de su empresa")


def _update_con_savepoint(c,query,params):
    c.execute("SAVEPOINT sp_5aw_dml")
    try:
        row=c.execute(query,params).fetchone()
    except psycopg.Error as err:
        codigo=err.sqlstate
        c.execute("ROLLBACK TO SAVEPOINT sp_5aw_dml")
        c.execute("RELEASE SAVEPOINT sp_5aw_dml")
        return None,codigo
    else:
        c.execute("ROLLBACK TO SAVEPOINT sp_5aw_dml")
        c.execute("RELEASE SAVEPOINT sp_5aw_dml")
        return row,None


def auditar_reserva_ajena(c, ids, reserva_a2):
    """A1 tiene GRANT UPDATE pero RLS le oculta la reserva A2."""
    M["como_rol"](c,SQL_A1)
    try:
        ENSURE(ONE(c,"SELECT session_user")==SQL_A1,"usuario SQL equivocado")
        ENSURE(ONE(c,"SELECT row_security_active('public.reserva'::regclass)")
               is True,"RLS reserva deshabilitado")
        ENSURE(ONE(c,"""SELECT has_column_privilege(
            session_user,'public.reserva','observacion','UPDATE')""") is True,
            "prueba inconclusa: A1 carece de GRANT UPDATE observacion")
        ENSURE(ONE(c,"""SELECT count(*) FROM public.reserva
            WHERE id=%s AND empresa_id=%s""",
            (reserva_a2,ids["a_empresa"]))==0,
            "BRECHA: A1 puede leer la reserva A2")
        row,err=_update_con_savepoint(c,"""UPDATE public.reserva
            SET observacion='5AW_A1_INTENTO_A2'
            WHERE id=%s AND empresa_id=%s RETURNING id""",
            (reserva_a2,ids["a_empresa"]))
        ENSURE(row is None and err is None,
               "BRECHA: reserva A2 modificable por A1 o RLS denegó con "
               "error SQL inesperado: "+str(err))
        print("5AW AJENO BLOQUEADO PASS: A1 ni lee ni modifica reserva de A2")
    finally:
        M["como_dba"](c)


def auditar_escritura_directa_residual(c, ids, reserva_a2):
    """Registro informativo: RLS de dueño NO sustituye las reglas del SP."""
    M["como_rol"](c,SQL_A2)
    try:
        row,err=_update_con_savepoint(c,"""UPDATE public.reserva
            SET observacion='5AW_UPDATE_DIRECTO_PROPIO'
            WHERE id=%s AND empresa_id=%s RETURNING id""",
            (reserva_a2,ids["a_empresa"]))
        if row is not None:
            print("5AW ALERTA DML PROPIO: A2 aún puede UPDATE de su "
                  "reserva fuera del procedimiento; reglas de negocio no garantizadas")
        else:
            print("5AW DML PROPIO: UPDATE denegado ("+str(err)+")")
    finally:
        M["como_dba"](c)

    M["como_rol"](c,SQL_A1)
    try:
        row,err=_update_con_savepoint(c,"""UPDATE public.inventario
            SET fecha_actualizacion=clock_timestamp()
            WHERE id=%s AND empresa_id=%s RETURNING id""",
            (ids["a_inventario"],ids["a_empresa"]))
        if row is not None:
            print("5AW ALERTA INVENTARIO: A1 aún puede UPDATE directo "
                  "sin SP; falta revisar DML comercial completo")
        else:
            print("5AW INVENTARIO: UPDATE directo denegado ("+str(err)+")")
    finally:
        M["como_dba"](c)


def main():
    print("5AW: cierre RLS reserva A1/A2 + SP CU16-18 reales + rollback")
    src=SOURCES()
    password=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=password,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=50000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5AW PREFLIGHT OK: dos tenants ficticios y 41 policies")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='50s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids=FIXTURES(c)
            a2=M["CREAR_A2"](c,ids["a_empresa"])
            M["MAPEAR_A"](c,ids["a_empresa"],ids["a_usuario"],a2)
            M["MAPEAR_B"](c,ids["b_empresa"],ids["b_usuario"])
            carro_a2=M["CARRO_A2"](c,ids,a2)
            M["INSTALAR_CU16"](c,src)
            M["PROTEGER_CU17_18"](c,src)
            M["ACL_RLS"](c)
            M["instalar_verificador_5av"](c)
            M["usar_unico_verificador_en_tres_sp"](c)
            preparar_restriccion_individual(c)
            reserva_a2=M["verificar_5au_y_llamadas"](c,ids,a2,carro_a2)
            auditar_reserva_ajena(c,ids,reserva_a2)
            auditar_escritura_directa_residual(c,ids,reserva_a2)
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                       ([OWNER,*ROLES],))==0,
                   "algún principal o dueño SQL persistió")
            ENSURE(ONE(c,"SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                   is True,"mapa privado quedó instalado")
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==41,"políticas originales no restauradas")
            print("5AW ROLLBACK VERIFICADO: 0 roles, 0 mapa, 0 SP, "
                  "41 policies; 3 reservas y 4 carritos originales")
        print("5AW OK PARCIAL: aislamiento de reservas entre A1/A2 probado")
        print("5AW PENDIENTE CRÍTICO: impedir DML comercial directo, "
              "rol SQL autenticado sin DBA, HTTP/JWT con conexión real; flags OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print(f"5AW ERROR: {type(err).__name__}: {err}",file=sys.stderr)
        sys.exit(2)
