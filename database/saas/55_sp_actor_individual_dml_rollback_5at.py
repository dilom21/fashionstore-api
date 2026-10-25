"""5AT: CU16-CU18 POSITIVOS con tres principales SQL individuales y RLS real.

Reutiliza los scripts 5AN/5AR/5AS, únicamente PostgreSQL 18 LAB
127.0.0.1:55432, BD fashionstore_rls_lab_5u. Un único BEGIN+ROLLBACK.
NO utilizar en PG 5432, Supabase, otras instalaciones ni producción.

Los tres SP se instrumentan temporalmente con la guardia 5AR; A1 y A2
hacen CU16→CU17 con sus propios carritos; B1 hace CU18. Cada CALL corre
bajo session_user INDIVIDUAL, y RLS/ACL de las tablas se conceden sólo
durante esta transacción. Se verifican stock, propiedad, denegación
intraempresa, GUC manipulable, no lectura de empresa B por A, RLS activo.

LIMITACIONES: PostgreSQL DBA simula SESSION AUTHORIZATION del principal
sin contraseña. NO prueba login DB real, API pool, certificados/JWT,
concurrencia, ni seguridad de una implementación comercial desplegada.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

HERE=Path(__file__).resolve().parent
BASE=runpy.run_path(str(HERE/"54_tres_sp_guardia_actor_sql_5as.py"))
PREFLIGHT=BASE["PREFLIGHT"]
FIXTURES=BASE["FIXTURES"]
SOURCES=BASE["SOURCES"]
MAPEAR_A=BASE["MAPEAR_A"]
CREAR_A2=BASE["CREAR_A2"]
MAPEAR_B=BASE["ampliar_mapa_para_b"]
INSTALAR_CU16=BASE["INSTALAR_CU16"]
PROTEGER_CU17_18=BASE["proteger_cu17_y_cu18"]
TEST_ACTOR=BASE["TEST_ACTOR"]
INTENTO_DENEGADO=BASE["intento_denegado"]
ONE=BASE["ONE"]
ENSURE=BASE["ENSURE"]
SQL_A1,SQL_A2,SQL_B1=BASE["SQL_A1"],BASE["SQL_A2"],BASE["SQL_B1"]
HOST,PORT,DB=BASE["HOST"],BASE["PORT"],BASE["DB"]
CALL_CREATE=BASE["LAB"]["CALL_CREATE"]
CALL_CHANGE=BASE["LAB"]["M"]["CALL_CHANGE"]
ROLES=(SQL_A1,SQL_A2,SQL_B1)


def asegurar_principales(conn):
    ENSURE(ONE(conn, """SELECT count(*) FROM pg_catalog.pg_authid
      WHERE rolname=ANY(%s) AND rolcanlogin AND rolpassword IS NULL
        AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolinherit""",
        (list(ROLES),))==3,"tres principales individuales no válidos")
    for role in ROLES:
        ENSURE(ONE(conn, """SELECT has_table_privilege(
            %s,'public.fs_lab_actor_sql_5ar','SELECT')""",(role,)) is False,
            "el mapa de identidades es consultable desde un actor")


def crear_politicas_y_permisos_individuales(conn):
    """Crear políticas nuevas, sin modificar ninguna de las 52 preexistentes.

    RLS por empresa derivada del mapa privado del principal SQL real;
    NO de app.empresa_id ni app.usuario_id editables por el actor.
    """
    aseg_roles=", ".join(ROLES)
    conn.execute(r"""
        CREATE FUNCTION public.fs_lab_empresa_actor_5at()
        RETURNS bigint
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $actor_empresa_5at$
          SELECT m.empresa_id
          FROM public.fs_lab_actor_sql_5ar m
          WHERE m.principal_sql=session_user::name
        $actor_empresa_5at$
    """)
    conn.execute("""REVOKE ALL ON FUNCTION
       public.fs_lab_empresa_actor_5at() FROM PUBLIC""")
    conn.execute(f"""GRANT EXECUTE ON FUNCTION
       public.fs_lab_empresa_actor_5at() TO {aseg_roles}""")
    filtro="empresa_id=public.fs_lab_empresa_actor_5at()"

    # Empresa + exactamente las 34 tablas tenant del clúster original.
    conn.execute(f"""CREATE POLICY fs_lab_5at_empresa_select
       ON public.empresa FOR SELECT TO {aseg_roles}
       USING (id=public.fs_lab_empresa_actor_5at() AND estado IS TRUE)""")
    tablas=[r[0] for r in conn.execute("""SELECT c.relname
       FROM pg_catalog.pg_class c
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
       WHERE n.nspname='public' AND c.relkind='r'
         AND a.attname='empresa_id' AND a.attnum>0 AND NOT a.attisdropped
         AND c.relname<>'fs_lab_actor_sql_5ar'
       ORDER BY c.relname""").fetchall()]
    ENSURE(len(tablas)==34,"estructura de 34 tablas tenant distinta")
    for tabla in tablas:
        ENSURE(tabla.isidentifier(),"nombre inesperado en el catálogo")
        conn.execute(f"""CREATE POLICY fs_lab_5at_tenant_select
           ON public.{tabla} FOR SELECT TO {aseg_roles}
           USING ({filtro})""")

    # Globales no asignadas directamente a tenant: solo identificadores
    # con membresía de la empresa y catálogos relacionados.
    conn.execute(f"""CREATE POLICY fs_lab_5at_plan_select
       ON public.plan_saas FOR SELECT TO {aseg_roles}
       USING (estado IS TRUE)""")
    for tabla,join_id in (("usuario","usuario_id"),("rol","rol_id")):
        conn.execute(f"""CREATE POLICY fs_lab_5at_{tabla}_select
          ON public.{tabla} FOR SELECT TO {aseg_roles}
          USING (EXISTS (SELECT 1 FROM public.usuario_empresa ue
            WHERE ue.{join_id}={tabla}.id AND ue.estado IS TRUE
              AND ue.empresa_id=public.fs_lab_empresa_actor_5at()))""")
    for tabla,columna in (("talla","talla_id"),("color","color_id")):
        conn.execute(f"""CREATE POLICY fs_lab_5at_{tabla}_select
          ON public.{tabla} FOR SELECT TO {aseg_roles}
          USING (EXISTS (SELECT 1 FROM public.variante_producto vp
            WHERE vp.{columna}={tabla}.id AND vp.estado IS TRUE
              AND vp.empresa_id=public.fs_lab_empresa_actor_5at()))""")
    conn.execute(f"""CREATE POLICY fs_lab_5at_rol_funcion_select
       ON public.rol_funcion FOR SELECT TO {aseg_roles}
       USING (EXISTS (SELECT 1 FROM public.usuario_empresa ue
         WHERE ue.rol_id=rol_funcion.rol_id AND ue.estado IS TRUE
           AND ue.empresa_id=public.fs_lab_empresa_actor_5at()))""")
    for tabla,columna in (("funcion","funcion_id"),("accion","accion_id")):
        conn.execute(f"""CREATE POLICY fs_lab_5at_{tabla}_select
         ON public.{tabla} FOR SELECT TO {aseg_roles}
         USING (EXISTS (
           SELECT 1 FROM public.rol_funcion rf
           JOIN public.usuario_empresa ue ON ue.rol_id=rf.rol_id
           WHERE rf.{columna}={tabla}.id AND ue.estado IS TRUE
             AND ue.empresa_id=public.fs_lab_empresa_actor_5at()
         ))""")

    for tabla in ("usuario_empresa","suscripcion","reserva",
                  "inventario","carrito"):
        conn.execute(f"""CREATE POLICY fs_lab_5at_{tabla}_update
           ON public.{tabla} FOR UPDATE TO {aseg_roles}
           USING ({filtro}) WITH CHECK ({filtro})""")
    for tabla in ("reserva","detalle_reserva","movimiento_inventario",
                  "bitacora"):
        conn.execute(f"""CREATE POLICY fs_lab_5at_{tabla}_insert
          ON public.{tabla} FOR INSERT TO {aseg_roles}
          WITH CHECK ({filtro})""")

    # La estructura 52 (5AN) + 52 nuevas del 5AT, ni una más.
    ENSURE(ONE(conn,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
           ==104,"no son 52 políticas transitorias adicionales")

    # Mismos grants mínimos utilizados en 5AN para A/B, pero únicamente
    # a los tres principales individuales de este ensayo.
    conn.execute(f"""GRANT SELECT ON public.empresa,public.modulo,
       public.funcion,public.accion,public.rol_funcion,public.plan_saas,
       public.suscripcion,public.usuario_empresa,public.cliente,
       public.empleado,public.sucursal,public.carrito,public.detalle_carrito,
       public.reserva,public.detalle_reserva,public.inventario,
       public.variante_producto,public.producto,public.categoria,
       public.temporada,public.talla,public.color,public.venta
       TO {aseg_roles}""")
    conn.execute(f"GRANT SELECT (id,estado) ON public.usuario TO {aseg_roles}")
    conn.execute(f"GRANT SELECT (id,nombre,estado) ON public.rol TO {aseg_roles}")
    conn.execute(f"""GRANT INSERT
       (empresa_id,cliente_id,sucursal_id,carrito_id,fecha_reserva,
        fecha_atencion,estado,observacion)
       ON public.reserva TO {aseg_roles}""")
    conn.execute(f"""GRANT INSERT (empresa_id,reserva_id,inventario_id,cantidad)
       ON public.detalle_reserva TO {aseg_roles}""")
    conn.execute(f"""GRANT INSERT
       (empresa_id,inventario_id,usuario_id,tipo,cantidad,fecha_hora,
        observaciones,referencia_tipo,referencia_id)
       ON public.movimiento_inventario TO {aseg_roles}""")
    conn.execute(f"GRANT INSERT ON public.bitacora TO {aseg_roles}")
    conn.execute(f"GRANT UPDATE (estado,observacion) ON public.reserva TO {aseg_roles}")
    conn.execute(f"""GRANT UPDATE (stock_reservado,fecha_actualizacion)
       ON public.inventario TO {aseg_roles}""")
    conn.execute(f"""GRANT UPDATE (estado,fecha_actualizacion)
       ON public.carrito TO {aseg_roles}""")
    conn.execute(f"GRANT UPDATE (estado) ON public.usuario_empresa TO {aseg_roles}")
    conn.execute(f"GRANT UPDATE (estado) ON public.suscripcion TO {aseg_roles}")
    for tabla in ("reserva","detalle_reserva","movimiento_inventario",
                  "bitacora"):
        nombre=ONE(conn,"SELECT pg_get_serial_sequence(%s,'id')",
                   (f"public.{tabla}",))
        ENSURE(nombre is not None,f"{tabla}: secuencia ID no identificada")
        conn.execute(f"GRANT USAGE ON SEQUENCE {nombre} TO {aseg_roles}")
    # No GRANT UPDATE a tabla de identidades ni CREATE ROLE adicional.
    for role in ROLES:
        ENSURE(ONE(conn,"""SELECT has_table_privilege(
           %s,'public.fs_lab_actor_sql_5ar','UPDATE')""",
           (role,)) is False,"actor puede modificar su mapa SQL")
    print("5AT ACL/RLS OK: 52 políticas individuales temporales, "
          "secuencias y columnas DML verificadas")


def crear_carrito_cliente_a2(conn, ids, usuario2):
    empresa=ids["a_empresa"]
    cliente=ONE(conn,"""SELECT id FROM public.cliente
        WHERE empresa_id=%s AND usuario_id=%s""",(empresa,usuario2))
    sucursal=ONE(conn,"""SELECT sucursal_id FROM public.carrito
        WHERE id=%s AND empresa_id=%s""",
        (ids["a_carrito"],empresa))
    carrito=ONE(conn,"""INSERT INTO public.carrito(
         empresa_id,cliente_id,sucursal_id,estado)
         VALUES(%s,%s,%s,'ACTIVO') RETURNING id""",
         (empresa,cliente,sucursal))
    conn.execute("""INSERT INTO public.detalle_carrito(
         empresa_id,carrito_id,inventario_id,cantidad)
         VALUES(%s,%s,%s,1)""",
         (empresa,carrito,ids["a_inventario"]))
    return carrito


def cambiar_rol(conn,role):
    ENSURE(role in ROLES,"principal SQL fuera de ensayo")
    conn.execute(f"SET LOCAL SESSION AUTHORIZATION {role}")
    ENSURE(ONE(conn,"SELECT session_user")==role,"principal incorrecto")
    ENSURE(ONE(conn,"SELECT row_security_active('public.reserva'::regclass)")
           is True,"RLS de reserva inactiva")
    ENSURE(ONE(conn,"SELECT row_security_active('public.inventario'::regclass)")
           is True,"RLS de inventario inactiva")
    ENSURE(ONE(conn,"SELECT count(*) FROM public.empresa")==1,
           "empresa ajena visible a rol individual")


def volver_db(conn):
    conn.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(conn,"SELECT session_user")=="postgres",
           "no se recuperó DBA de laboratorio")


def reservar(conn,ids,usuario,carrito):
    future=datetime.now(timezone.utc)+timedelta(days=2)
    result=conn.execute(CALL_CREATE,(
        ids["a_empresa"],carrito,usuario,future,
        "5AT legítima reversible",None
    )).fetchone()
    ENSURE(result is not None and type(result[0]) is int and result[0]>0,
           "CU16 no devolvió ID de reserva")
    ENSURE(ONE(conn,"""SELECT count(*) FROM public.reserva r
       JOIN public.cliente cl ON cl.id=r.cliente_id
       WHERE r.id=%s AND r.empresa_id=%s AND r.carrito_id=%s
         AND cl.usuario_id=%s""",
       (result[0],ids["a_empresa"],carrito,usuario))==1,
       "CU16 no perteneció al usuario declarado")
    ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
               (ids["a_inventario"],))==5,"CU16 no retuvo 1 unidad")
    return result[0]


def cancelar(conn,ids,usuario,reserva):
    conn.execute(CALL_CHANGE.format("sp_cancelar_reserva_tenant"),(
        ids["a_empresa"],reserva,usuario,"5AT liberación reversible"))
    ENSURE(ONE(conn,"SELECT estado FROM public.reserva WHERE id=%s",
               (reserva,))=="CANCELADA","CU17 no canceló")
    ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
               (ids["a_inventario"],))==4,"CU17 no liberó una unidad")


def ejecutar_positivos(conn,ids,a2,carrito_a2):
    for principal,uid,carrito in (
        (SQL_A1,ids["a_usuario"],ids["a_carrito"]),
        (SQL_A2,a2,carrito_a2),
    ):
        cambiar_rol(conn,principal)
        ENSURE(ONE(conn,"SELECT count(*) FROM public.empresa WHERE id=%s",
                   (ids["b_empresa"],))==0,"empresa B visible para cliente A")
        ENSURE(ONE(conn,
            "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",
            (ids["a_empresa"],uid)) is True,"guardia no acepta actor propio")
        rid=reservar(conn,ids,uid,carrito)
        print(f"5AT CU16 PASS: principal individual {principal} reserva "
              "y stock 5/5")
        cancelar(conn,ids,uid,rid)
        print(f"5AT CU17 PASS: principal individual {principal} cancela "
              "y libera stock 4/5")
        volver_db(conn)

    cambiar_rol(conn,SQL_B1)
    ENSURE(ONE(conn,"SELECT count(*) FROM public.empresa WHERE id=%s",
               (ids["a_empresa"],))==0,"empresa A visible para B")
    ENSURE(ONE(conn,
        "SELECT public.fs_lab_verificar_actor_5ar(%s,%s)",
        (ids["b_empresa"],ids["b_usuario"])) is True,
        "guardia no admite B1 legítimo")
    conn.execute(CALL_CHANGE.format("sp_finalizar_reserva_sin_compra_tenant"),(
        ids["b_empresa"],ids["b_reserva"],ids["b_usuario"],
        "5AT finalizar reversible"))
    ENSURE(ONE(conn,"SELECT estado FROM public.reserva WHERE id=%s",
               (ids["b_reserva"],))=="ATENDIDA","CU18 no finalizó")
    ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE empresa_id=%s",
               (ids["b_empresa"],))==0,"CU18 no liberó inventario")
    print("5AT CU18 PASS: principal individual B1 finalizó sin compra")
    volver_db(conn)


def main():
    print("5AT: CU16/17/18 POSITIVOS SQL INDIVIDUAL + RLS; ROLLBACK")
    sqls=SOURCES()
    password=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=password,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=45000 -c lock_timeout=5000",
    ) as conn:
        PREFLIGHT(conn)
        print("5AT PREFLIGHT OK: PostgreSQL 18 / A-B ficticios")
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='45s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            ids=FIXTURES(conn)
            a2=CREAR_A2(conn,ids["a_empresa"])
            MAPEAR_A(conn,ids["a_empresa"],ids["a_usuario"],a2)
            MAPEAR_B(conn,ids["b_empresa"],ids["b_usuario"])
            carrito_a2=crear_carrito_cliente_a2(conn,ids,a2)
            INSTALAR_CU16(conn,sqls)
            PROTEGER_CU17_18(conn,sqls)
            ENSURE(ONE(conn,
                "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                ==52,"cambió estado de 5AS antes de RLS")
            asegurar_principales(conn)
            crear_politicas_y_permisos_individuales(conn)
            ejecutar_positivos(conn,ids,a2,carrito_a2)
        finally:
            conn.execute("ROLLBACK")
            PREFLIGHT(conn)
            ENSURE(ONE(conn,"""SELECT count(*) FROM pg_catalog.pg_roles
               WHERE rolname=ANY(%s)""",(list(ROLES),))==0,
               "algún principal individual persistió")
            ENSURE(ONE(conn,
                "SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                is True,"persistió el mapa SQL")
            ENSURE(ONE(conn,
                "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                ==41,"no regresaron las 41 políticas originales")
            print("5AT ROLLBACK VERIFICADO: 0 roles, 0 mapa, 0 SP, "
                  "41 políticas, 3 reservas, 4 carritos originales")
        print("5AT OK: CU16/CU17/CU18 permitidos a principales individuales")
        print("5AT PENDIENTE: conexiones reales por actor, pool HTTP/JWT "
              "y modelo robusto de credenciales sin postgres; flags OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AT ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
