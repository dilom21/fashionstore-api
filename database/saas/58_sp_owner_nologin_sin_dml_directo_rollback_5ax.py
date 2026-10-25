"""5AX: bloquear DML directo sin romper CU16–CU18 (SOLO LAB PG18).

Corrige las dos alertas 5AW. No basta con RLS de dueño: un usuario con
UPDATE sobre su propia reserva/inventario puede saltarse transiciones
comerciales. Por ello se revocan INSERT/UPDATE/DELETE directos a A1/A2/B1
y se ensayan los SP como SECURITY DEFINER con un PROPIETARIO NOLOGIN
restringido, distinta cuenta del verificador SQL de actor.

RLS y privilegios MINIMOS para el dueño se instalan únicamente dentro
del BEGIN...ROLLBACK PG18 127.0.0.1:55432/fashionstore_rls_lab_5u.
Se copian SOLO las policies de 5AT/5AW a dicho dueño (54 policies).
Los SP siguen comprobando session_user/usuario, rol y reglas de stock.
Todas las escrituras de clientes mediante SQL directo deben fallar
por ACL 42501; las cinco operaciones CU16/17/18 válidas deben pasar.

Esto NO es un despliegue ni una validación de login real sin DBA,
HTTP/JWT, seguridad del pool, ni concurrencia. Flags SaaS OFF.
NO utilizar en 5432 ni Supabase; 0 COMMIT.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

HERE=Path(__file__).resolve().parent
X=runpy.run_path(str(HERE/"57_restringir_reserva_rls_actor_rollback_5aw.py"))
M=X["M"]
PREFLIGHT, FIXTURES, SOURCES=X["PREFLIGHT"],X["FIXTURES"],X["SOURCES"]
ONE, ENSURE=X["ONE"],X["ENSURE"]
HOST,PORT,DB=X["HOST"],X["PORT"],X["DB"]
ROLES=X["ROLES"]
# Los viejos principales empresariales conservaron GRANT DML del 5AJ/5AN.
# Se revocan igualmente DENTRO del ensayo, pero se restituyen con ROLLBACK.
SHARED=("fashionstore_5ab_a","fashionstore_5ab_b")
TARGETS=ROLES+SHARED
A1,A2,B1=X["SQL_A1"],X["SQL_A2"],X["SQL_B1"]
VERIFIER_OWNER=X["OWNER"]
WRITER="fashionstore_5ax_sp_writer"
TABLES=("reserva","detalle_reserva","movimiento_inventario","bitacora",
        "inventario","carrito","usuario_empresa","suscripcion")
PROCEDURES={
    "sp_crear_reserva_tenant":"bigint,bigint,bigint,timestamptz,text,bigint",
    "sp_cancelar_reserva_tenant":"bigint,bigint,bigint,text",
    "sp_finalizar_reserva_sin_compra_tenant":"bigint,bigint,bigint,text",
}


def sql_names(names):
    """Identificadores fijos del laboratorio, jamás se reciben desde JWT."""
    for name in names:
        ENSURE(name.isidentifier() and not name.startswith("_"),
               "identificador de columna no esperado")
    return ",".join(names)


def preparar_writer(c):
    c.execute(f"""CREATE ROLE {WRITER} NOLOGIN NOINHERIT
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS""")
    c.execute(f"GRANT USAGE ON SCHEMA public TO {WRITER}")
    # La guardia 5AV debe poder ejecutarse DENTRO del procedimiento como
    # propietario no-login, y las policies invocan función 5AT de empresa.
    c.execute(f"""GRANT EXECUTE ON FUNCTION
       public.fs_verificar_actor_reserva(bigint,bigint) TO {WRITER}""")
    c.execute(f"""GRANT EXECUTE ON FUNCTION
       public.fs_lab_empresa_actor_5at() TO {WRITER}""")
    ENSURE(ONE(c,"""SELECT count(*) FROM pg_catalog.pg_roles
      WHERE rolname=%s AND NOT rolcanlogin AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb
        AND NOT rolinherit""",(WRITER,))==1,
        "5AX propietario SP no restringido")
    for caller in ROLES:
        ENSURE(ONE(c,"SELECT pg_has_role(%s::name,%s::name,'MEMBER')",
                   (caller,WRITER)) is False,
               "5AX actor puede adquirir privilegios del writer")


def copiar_rls_a_writer(c):
    """RLS sobre filas, aplicado al NOLOGIN writer en SECURITY DEFINER.

    Se copian políticas 5AT + 5AW del arnés ya probado: 52+2.
    Los checks derivan empresa de session_user mediante mapa privado.
    """
    src=c.execute("""SELECT tablename,policyname,permissive,cmd,qual,with_check
       FROM pg_catalog.pg_policies
       WHERE schemaname='public' AND (
          policyname LIKE 'fs_lab_5at_%'
          OR policyname LIKE 'fs_lab_5aw_%'
       )
       ORDER BY tablename,policyname""").fetchall()
    ENSURE(len(src)==54,"no están las 54 policies de usuario esperadas")
    actions={"SELECT","INSERT","UPDATE","DELETE","ALL"}
    for table,name,permissive,cmd,qual,with_check in src:
        ENSURE(table.isidentifier() and name.isidentifier()
               and cmd in actions and permissive in {"PERMISSIVE","RESTRICTIVE"},
               "policy de origen inesperada")
        nueva=name.replace("fs_lab_5at_","fs_lab_5ax_").replace(
            "fs_lab_5aw_","fs_lab_5ax_")
        ENSURE(nueva!=name and len(nueva)<=63,"policy 5AX inesperada")
        expr=f"""CREATE POLICY {nueva} ON public.{table}
           AS {permissive} FOR {cmd} TO {WRITER}"""
        if qual is not None:
            expr+=f" USING ({qual})"
        if with_check is not None:
            expr+=f" WITH CHECK ({with_check})"
        c.execute(expr)
    ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
           ==161,"políticas 5AX inesperadas: deben ser 107+54")
    print("5AX RLS WRITER OK: 54 policies para NOLOGIN, "
          "actor derivado de session_user, sin BYPASSRLS")


def grant_lecturas_y_escrituras_writer(c):
    """Sólo lo necesario para el DML interno y FOR SHARE de los SP."""
    c.execute(f"""GRANT SELECT ON
      public.empresa,public.modulo,public.funcion,public.accion,
      public.rol_funcion,public.plan_saas,public.suscripcion,
      public.usuario_empresa,public.cliente,public.empleado,
      public.sucursal,public.carrito,public.detalle_carrito,
      public.reserva,public.detalle_reserva,public.inventario,
      public.variante_producto,public.producto,public.categoria,
      public.temporada,public.talla,public.color,public.venta
      TO {WRITER}""")
    c.execute(f"GRANT SELECT (id,estado) ON public.usuario TO {WRITER}")
    c.execute(f"GRANT SELECT (id,nombre,estado) ON public.rol TO {WRITER}")
    c.execute(f"""GRANT INSERT
      (empresa_id,cliente_id,sucursal_id,carrito_id,fecha_reserva,
       fecha_atencion,estado,observacion)
      ON public.reserva TO {WRITER}""")
    c.execute(f"""GRANT INSERT
      (empresa_id,reserva_id,inventario_id,cantidad)
      ON public.detalle_reserva TO {WRITER}""")
    c.execute(f"""GRANT INSERT
      (empresa_id,inventario_id,usuario_id,tipo,cantidad,
       fecha_hora,observaciones,referencia_tipo,referencia_id)
      ON public.movimiento_inventario TO {WRITER}""")
    c.execute(f"GRANT INSERT ON public.bitacora TO {WRITER}")
    c.execute(f"GRANT UPDATE (estado,observacion) ON public.reserva TO {WRITER}")
    c.execute(f"""GRANT UPDATE (stock_reservado,fecha_actualizacion)
      ON public.inventario TO {WRITER}""")
    c.execute(f"""GRANT UPDATE (estado,fecha_actualizacion)
      ON public.carrito TO {WRITER}""")
    c.execute(f"GRANT UPDATE (estado) ON public.usuario_empresa TO {WRITER}")
    c.execute(f"GRANT UPDATE (estado) ON public.suscripcion TO {WRITER}")
    for table in ("reserva","detalle_reserva","movimiento_inventario",
                  "bitacora"):
        seq=ONE(c,"SELECT pg_get_serial_sequence(%s,'id')",
                (f"public.{table}",))
        ENSURE(seq is not None,"secuencia faltante de "+table)
        ENSURE(seq.startswith("public.") and seq.split(".")[-1].replace('"','').isidentifier(),
               "secuencia inesperada")
        c.execute(f"GRANT USAGE ON SEQUENCE {seq} TO {WRITER}")


def retirar_dml_directo_de_actores(c):
    """Revocar TAMBIÉN permisos por columna, no sólo permisos de tabla."""
    targets=", ".join(TARGETS)
    for table in TABLES:
        ENSURE(table.isidentifier(),"tabla no permitida")
        c.execute(f"""REVOKE INSERT,UPDATE,DELETE ON public.{table}
             FROM {targets}""")
        columns=[r[0] for r in c.execute("""SELECT a.attname
           FROM pg_catalog.pg_attribute a
           WHERE a.attrelid=%s::regclass
             AND a.attnum>0 AND NOT a.attisdropped
           ORDER BY a.attnum""",(f"public.{table}",)).fetchall()]
        ENSURE(0<len(columns)<90,"catálogo de columnas inesperado")
        cols=sql_names(columns)
        c.execute(f"""REVOKE INSERT ({cols}), UPDATE ({cols})
            ON public.{table} FROM {targets}""")
    for role in TARGETS:
        for table in TABLES:
            count=ONE(c,"""SELECT count(*) FROM pg_catalog.pg_attribute a
               WHERE a.attrelid=%s::regclass
                 AND a.attnum>0 AND NOT a.attisdropped
                 AND (has_column_privilege(%s,a.attrelid,a.attnum,'INSERT')
                      OR has_column_privilege(%s,a.attrelid,a.attnum,'UPDATE'))
            """,(f"public.{table}",role,role))
            ENSURE(count==0,f"{role} aún tiene DML directo sobre {table}")
    print("5AX DML DIRECTO BLOQUEADO: 5 roles (A1,A2,B1,A,B) "
          "sin INSERT/UPDATE sobre 8 tablas comerciales")


def instalar_sp_como_definer(c):
    """Conservar guardia 5AV, cambiar sólo owner/seguridad de los 3 SP."""
    c.execute(f"GRANT CREATE ON SCHEMA public TO {WRITER}")
    for proc,signature in PROCEDURES.items():
        c.execute(f"""ALTER PROCEDURE public.{proc}({signature})
          OWNER TO {WRITER}""")
        c.execute(f"""ALTER PROCEDURE public.{proc}({signature})
          SECURITY DEFINER""")
        c.execute(f"""ALTER PROCEDURE public.{proc}({signature})
          SET search_path = pg_catalog, pg_temp""")
    c.execute(f"REVOKE CREATE ON SCHEMA public FROM {WRITER}")
    for proc in PROCEDURES:
        correct=ONE(c,"""SELECT count(*) FROM pg_catalog.pg_proc p
           WHERE p.oid=(SELECT p2.oid FROM pg_catalog.pg_proc p2
             JOIN pg_catalog.pg_namespace n ON n.oid=p2.pronamespace
             WHERE n.nspname='public' AND p2.proname=%s
               AND p2.prokind='p')
             AND p.prosecdef
             AND pg_get_userbyid(p.proowner)=%s
             AND p.proconfig @>
                 ARRAY['search_path=pg_catalog, pg_temp']::text[]
        """,(proc,WRITER))
        ENSURE(correct==1,"SP sin titular seguro/search_path: "+proc)
    ENSURE(ONE(c,"SELECT has_schema_privilege(%s,'public','CREATE')",
               (WRITER,)) is False,"writer conserva CREATE schema")
    ENSURE(ONE(c,"SELECT has_database_privilege(%s,current_database(),'CREATE')",
               (WRITER,)) is False,"writer conserva CREATE database")
    print("5AX PROCEDIMIENTOS OK: CU16-18 SECURITY DEFINER "
          "NOLOGIN, search_path seguro y guardia de identidad")


def auditar_reserva_ajena_sin_grant(c,ids,reserva_a2):
    """Después de REVOKE, el 5AW previo no aplica (exigía GRANT UPDATE).

    A1 aún puede leer sus propias reservas, nunca las de A2. La política
    restrictiva de lectura debe mantenerse efectiva tras cambiar SP owner.
    """
    M["como_rol"](c,A1)
    try:
        ENSURE(ONE(c,"SELECT row_security_active('public.reserva'::regclass)")
               is True,"RLS de lectura de reserva inactiva")
        ENSURE(ONE(c,"SELECT has_table_privilege(session_user,"
                   "'public.reserva','SELECT')") is True,
               "prueba inconclusa sin SELECT de reservas")
        ENSURE(ONE(c,"SELECT count(*) FROM public.reserva WHERE id=%s",
                   (reserva_a2,))==0,
               "BRECHA RLS: A1 puede leer reserva de A2")
        print("5AX RLS PASS: A1 no puede leer reserva ajena A2")
    finally:
        M["como_dba"](c)


def auditar_guardia_definer(c,ids,a2,carrito2):
    """Con SECURITY DEFINER el SP debe seguir rechazando actor suplantado.

    Se exige error EXACTO de la guardia, no 42501 por ausencia de ACL.
    """
    from datetime import datetime, timedelta, timezone
    shared=M["LAB"]["BASE"]["TENANT_A"]
    for role,falso in ((A1,a2),(shared,ids["a_usuario"])):
        if role==shared:
            ENSURE(ONE(c,"SELECT has_function_privilege("
                       "%s,'public.sp_crear_reserva_tenant("
                       "bigint,bigint,bigint,timestamptz,text,bigint)',"
                       "'EXECUTE')",(role,)) is True,
                   "rol empresarial no tiene EXECUTE; auditoria inconclusa")
        M["como_rol"](c,role)
        c.execute("SAVEPOINT sp_5ax_guardia")
        try:
            c.execute(M["LAB"]["CALL_CREATE"],(
                ids["a_empresa"],carrito2,falso,
                datetime.now(timezone.utc)+timedelta(days=2),
                "5AX prueba de suplantacion",None
            ))
        except psycopg.Error as exc:
            code,reason=exc.sqlstate,exc.diag.message_primary
            c.execute("ROLLBACK TO SAVEPOINT sp_5ax_guardia")
            ENSURE(code=="42501" and
                   reason=="Actor SQL no coincide con usuario de la empresa",
                   f"guardia SECURITY DEFINER no probada: {code}/{reason}")
            print("5AX IDENTIDAD PASS: SECURITY DEFINER rechazó "
                  "suplantación de "+role)
        else:
            c.execute("ROLLBACK TO SAVEPOINT sp_5ax_guardia")
            raise RuntimeError("5AX BRECHA: SP DEFINER aceptó actor ajeno")
        c.execute("RELEASE SAVEPOINT sp_5ax_guardia")
        M["como_dba"](c)


def auditar_dml_sin_privilegios(c,ids,reserva_a2):
    """Ejecutar intentos reales bajo actores, no solo leer ACL."""
    pruebas=(
        (A1,"UPDATE public.reserva SET observacion='5AX_DIRECTO' "
            "WHERE id=%s RETURNING id",(reserva_a2,)),
        (A2,"UPDATE public.reserva SET observacion='5AX_PROPIO' "
            "WHERE id=%s RETURNING id",(reserva_a2,)),
        (A1,"UPDATE public.inventario SET fecha_actualizacion=clock_timestamp() "
            "WHERE id=%s RETURNING id",(ids["a_inventario"],)),
        (A2,"UPDATE public.carrito SET estado='ACTIVO' "
            "WHERE id=%s RETURNING id",(ids["a_carrito"],)),
        (SHARED[0],"UPDATE public.inventario "
            "SET fecha_actualizacion=clock_timestamp() "
            "WHERE id=%s RETURNING id",(ids["a_inventario"],)),
    )
    for role,sql,args in pruebas:
        if role in ROLES or role==SHARED[0]:
            M["como_rol"](c,role)
        else:
            raise RuntimeError("5AX principal inesperado")
        c.execute("SAVEPOINT sp_5ax_dml")
        try:
            c.execute(sql,args).fetchone()
        except psycopg.Error as exc:
            code=exc.sqlstate
            c.execute("ROLLBACK TO SAVEPOINT sp_5ax_dml")
            ENSURE(code=="42501",
                   f"error distinto a ACL DML {role}: {code}")
            print("5AX ACL PASS: operación SQL directa rechazada "+role)
        else:
            c.execute("ROLLBACK TO SAVEPOINT sp_5ax_dml")
            raise RuntimeError("5AX BRECHA: SQL DML directo todavía permitido")
        c.execute("RELEASE SAVEPOINT sp_5ax_dml")
        M["como_dba"](c)


def main():
    print("5AX: cerrar DML directo + SP protegidos, TODO ROLLBACK LAB")
    sources=SOURCES()
    pw=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=pw,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=60000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5AX PREFLIGHT OK: 41 policies originales en 55432")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='60s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids=FIXTURES(c)
            a2=M["CREAR_A2"](c,ids["a_empresa"])
            M["MAPEAR_A"](c,ids["a_empresa"],ids["a_usuario"],a2)
            M["MAPEAR_B"](c,ids["b_empresa"],ids["b_usuario"])
            carrito2=M["CARRO_A2"](c,ids,a2)
            M["INSTALAR_CU16"](c,sources)
            M["PROTEGER_CU17_18"](c,sources)
            M["ACL_RLS"](c)
            M["instalar_verificador_5av"](c)
            M["usar_unico_verificador_en_tres_sp"](c)
            X["preparar_restriccion_individual"](c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==107,"no hay 107 policies de 5AW")
            preparar_writer(c)
            copiar_rls_a_writer(c)
            grant_lecturas_y_escrituras_writer(c)
            retirar_dml_directo_de_actores(c)
            instalar_sp_como_definer(c)
            # Solo este ensayo local habilita el modo DEFINER en el
            # preflight 5AY del servicio. Restituir incluso ante error.
            from app.core.config import settings
            original_sp_owner=settings.saas_sp_sql_owner
            try:
                settings.saas_sp_sql_owner=WRITER
                res_a2=M["verificar_5au_y_llamadas"](c,ids,a2,carrito2)
                auditar_guardia_definer(c,ids,a2,carrito2)
                auditar_reserva_ajena_sin_grant(c,ids,res_a2)
                auditar_dml_sin_privilegios(c,ids,res_a2)
            finally:
                settings.saas_sp_sql_owner=original_sp_owner
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,"""SELECT count(*) FROM pg_catalog.pg_roles
                         WHERE rolname=ANY(%s)""",([WRITER,VERIFIER_OWNER,*ROLES],))
                   ==0,"roles individuales o propietarios persistieron")
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==41,"41 policies originales no restauradas")
            ENSURE(ONE(c,"SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                   is True,"persistió mapa de identidad")
            print("5AX ROLLBACK OK: 0 roles/owners, 0 mapa, 0 SP "
                  "permanentes, 41 policies, 3 reservas y 4 carritos")
        print("5AX OK LAB: sin DML directo, CU16–CU18 autorizados por SP")
        print("5AX PENDIENTE: seguridad de DEFINER/policies completa, "
              "login real no superusuario, pool HTTP/JWT y concurrencia; "
              "flags SaaS OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AX ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
