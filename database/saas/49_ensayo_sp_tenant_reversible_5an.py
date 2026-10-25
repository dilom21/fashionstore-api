"""5AN: CU16/CU17/CU18 con CALL SQL + RLS y fixtures, ROLLBACK TOTAL.

Solo clúster PG18 127.0.0.1:55432 / fashionstore_rls_lab_5u.
NO toca las 5 empresas de demostración del servidor 5432 ni Supabase.
SET LOCAL SESSION AUTHORIZATION simula el principal A/B sobre la
MISMA transacción donde se prepararon actores, ACL y SP. PostgreSQL
comprueba GRANT, RLS y permisos con session_user del rol de prueba.
No es una prueba de JWT/HTTP ni de login de usuario final.

No se ejecutan COMMIT ni DROP. Las inserciones, UPDATE, GRANT,
CREATE POLICY y CREATE PROCEDURE se revierten INCONDICIONALMENTE.
Se requiere estado exacto del laboratorio tras el ensayo 5AK.
"""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg

ROOT = Path(__file__).resolve().parent
M = runpy.run_path(str(ROOT / "48_compilar_sp_tenant_transaccion_rollback_5am.py"))
preflight, sources, catalog = M["preflight"], M["load_sources"], M["catalog_check"]
HOST, PORT, DB = M["HOST"], M["PORT"], M["DB"]
A, B = "fashionstore_5ab_a", "fashionstore_5ab_b"
CALL_CREATE = (
    "CALL public.sp_crear_reserva_tenant("
    "%s::bigint,%s::bigint,%s::bigint,%s::timestamptz,%s::text,%s::bigint)"
)
CALL_CHANGE = "CALL public.{}(%s::bigint,%s::bigint,%s::bigint,%s::text)"


def ensure(ok, message):
    if not ok:
        raise RuntimeError("5AN BLOQUEADO: " + message)


def one(c, sql, args=None):
    q = c.execute(sql) if args is None else c.execute(sql, args)
    return q.fetchone()[0]


def as_role(c, login):
    ensure(login in (A,B), "rol fuera de la lista")
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {login}")
    ensure(one(c, "SELECT session_user") == login, "no se obtuvo principal SQL")
    ensure(one(c, "SELECT row_security_active('public.reserva'::regclass)"),
           "RLS reserva no activa")
    ensure(one(c, "SELECT row_security_active('public.inventario'::regclass)"),
           "RLS inventario no activo")


def reset_admin(c):
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ensure(one(c, "SELECT session_user") == "postgres", "no se recupero DBA lab")


def grant_minimal_lab(c):
    # Estos permisos son TRANSITORIOS dentro de la misma transacción.
    users = f"{A}, {B}"
    c.execute(f"""
        GRANT SELECT ON public.modulo, public.funcion,
          public.accion, public.rol_funcion, public.plan_saas,
          public.suscripcion, public.usuario_empresa,
          public.cliente, public.empleado, public.sucursal,
          public.carrito, public.detalle_carrito, public.reserva,
          public.detalle_reserva, public.inventario,
          public.variante_producto, public.producto,
          public.categoria, public.temporada, public.talla,
          public.color, public.venta
        TO {users}
    """)
    # No conceder acceso a password_hash (usuario global).
    c.execute(f"GRANT SELECT (id,estado) ON public.usuario TO {users}")
    # Rol sólo necesita estas columnas en el SP: no mostrar directorio global.
    # INSERT de identidades siempre ocurre ANTES, como postgres ficticio.
    c.execute(f"GRANT SELECT (id,nombre,estado) ON public.rol TO {users}")
    c.execute(f"""GRANT INSERT
        (empresa_id,cliente_id,sucursal_id,carrito_id,fecha_reserva,
         fecha_atencion,estado,observacion)
        ON public.reserva TO {users}""")
    c.execute(f"""GRANT UPDATE (estado,observacion)
        ON public.reserva TO {users}""")
    c.execute(f"""GRANT UPDATE (stock_reservado,fecha_actualizacion)
        ON public.inventario TO {users}""")
    c.execute(f"""GRANT INSERT
        (empresa_id,inventario_id,usuario_id,tipo,cantidad,
         fecha_hora,observaciones,referencia_tipo,referencia_id)
        ON public.movimiento_inventario TO {users}""")
    # Postgres FOR SHARE usa ROW SHARE, cuyo acceso exige además privilegio UPDATE.
    # Este privilegio no se usa para alterar filas en el ensayo.
    c.execute(f"GRANT UPDATE (estado) ON public.usuario_empresa TO {users}")
    c.execute(f"GRANT UPDATE (estado) ON public.suscripcion TO {users}")

    # plan_saas es tabla global con RLS deny-all del bootstrap.
    c.execute(f"""CREATE POLICY fs_lab_5an_plan_select
      ON public.plan_saas FOR SELECT TO {users}
      USING (estado IS TRUE)""")
    # usuario/rol no tienen empresa_id: para estos recursos la identidad
    # se deriva EXCLUSIVAMENTE de la membresia de la empresa del principal
    # SQL (session_user). No usar GUC editable ni abrir SELECT global.
    # ENABLE y CREATE pertenecen al BEGIN ... ROLLBACK del LAB.
    c.execute("ALTER TABLE public.usuario ENABLE ROW LEVEL SECURITY")
    c.execute("ALTER TABLE public.rol ENABLE ROW LEVEL SECURITY")
    principal_empresa = f"""(
        SELECT e.id FROM public.empresa e WHERE e.slug =
        CASE session_user WHEN '{A}' THEN 'empresa-lab-a'
                          WHEN '{B}' THEN 'empresa-lab-b' END
    )"""
    c.execute(f"""CREATE POLICY fs_lab_5an_usuario_select
        ON public.usuario FOR SELECT TO {users}
        USING (EXISTS (
            SELECT 1 FROM public.usuario_empresa ue
            WHERE ue.usuario_id = usuario.id AND ue.estado IS TRUE
              AND ue.empresa_id = {principal_empresa}
        ))""")
    c.execute(f"""CREATE POLICY fs_lab_5an_rol_select
        ON public.rol FOR SELECT TO {users}
        USING (EXISTS (
            SELECT 1 FROM public.usuario_empresa ue
            WHERE ue.rol_id = rol.id AND ue.estado IS TRUE
              AND ue.empresa_id = {principal_empresa}
        ))""")

    for t in ("usuario_empresa","suscripcion","reserva"):
        # Las 34 tablas tenant ya tienen policy fs_lab_tenant_select; los
        # derechos nuevos son para FOR SHARE / UPDATE sujeto a RLS.
        c.execute(f"""CREATE POLICY fs_lab_5an_{t}_update
            ON public.{t} FOR UPDATE TO {users}
            USING (empresa_id=(SELECT id FROM public.empresa WHERE slug=
              CASE session_user
                WHEN '{A}' THEN 'empresa-lab-a'
                WHEN '{B}' THEN 'empresa-lab-b'
              END))
            WITH CHECK (empresa_id=(SELECT id FROM public.empresa WHERE slug=
              CASE session_user
                WHEN '{A}' THEN 'empresa-lab-a'
                WHEN '{B}' THEN 'empresa-lab-b'
              END))""")

    # Ya existe la policy UPDATE de inventario y carrito desde 5AJ.
    # Inserciones de reserva/detalle/movimiento dependen de 5AJ.
    firmas = {
        "sp_crear_reserva_tenant": "bigint,bigint,bigint,timestamptz,text,bigint",
        "sp_cancelar_reserva_tenant": "bigint,bigint,bigint,text",
        "sp_finalizar_reserva_sin_compra_tenant": "bigint,bigint,bigint,text",
    }
    ensure(set(firmas) == set(M["PROCEDURES"]),
           "firmas SQL y borradores 5AM no coinciden")
    for name, signatures in firmas.items():
        c.execute(
            f"GRANT EXECUTE ON PROCEDURE public.{name}({signatures}) TO {users}"
        )


def fixtures(c):
    ensure(one(c, "SELECT count(*) FROM public.rol")==0,
           "ya hay roles funcionales; no mezclar fixtures")
    ensure(one(c, "SELECT count(*) FROM public.usuario")==0,
           "ya hay usuarios en laboratorio")
    ensure(one(c, "SELECT count(*) FROM public.usuario_empresa")==0,
           "ya hay membresias en laboratorio")
    ensure(one(c, "SELECT count(*) FROM public.plan_saas")==0,
           "ya hay planes en laboratorio")
    ensure(one(c, "SELECT count(*) FROM public.suscripcion")==0,
           "ya hay suscripciones en laboratorio")

    def role(name):
        return one(c,"""INSERT INTO public.rol(nombre,descripcion,estado)
                 VALUES(%s,'Rol ficticio 5AN',TRUE) RETURNING id""",(name,))

    client_role=role("CLIENTE")
    admin_role=role("ADMINISTRADOR")
    plan_id=one(c,"""INSERT INTO public.plan_saas
        (codigo,nombre,max_sucursales_activas,permite_reservas,estado)
        VALUES('PROFESIONAL','Plan ficticio 5AN',10,TRUE,TRUE)
        RETURNING id""")
    tenants=dict(c.execute("""SELECT slug,id FROM public.empresa
         WHERE slug IN ('empresa-lab-a','empresa-lab-b')""").fetchall())
    a_id,b_id=tenants["empresa-lab-a"],tenants["empresa-lab-b"]
    for empresa_id in (a_id,b_id):
        c.execute("""INSERT INTO public.suscripcion
          (empresa_id,plan_saas_id,fecha_inicio,fecha_vencimiento,estado)
          VALUES(%s,%s,%s,%s,'ACTIVA')""",(
            empresa_id,plan_id,date.today()-timedelta(days=2),
            date.today()+timedelta(days=30),
        ))

    def user(email,rol_id,empresa_id):
        usuario_id=one(c,"""INSERT INTO public.usuario(
            rol_id,correo,password_hash,estado)
            VALUES(%s,%s,'!FICTIONAL-NONLOGIN-5AN',TRUE)
            RETURNING id""",(rol_id,email))
        c.execute("""INSERT INTO public.usuario_empresa
            (usuario_id,empresa_id,rol_id,estado)
            VALUES(%s,%s,%s,TRUE)""",(usuario_id,empresa_id,rol_id))
        return usuario_id

    ua=user("lab-a-cliente-5an@demo.invalid",client_role,a_id)
    ub=user("lab-b-admin-5an@demo.invalid",admin_role,b_id)
    # A: reutilizar SOLO el carrito competidor que perdio 5AK.
    candidatos=c.execute("""SELECT ca.id,ca.cliente_id,dc.id
        FROM public.carrito ca
        JOIN public.detalle_carrito dc ON dc.carrito_id=ca.id
          AND dc.empresa_id=ca.empresa_id
        WHERE ca.empresa_id=%s AND ca.estado='ACTIVO'
          AND NOT EXISTS(
            SELECT 1 FROM public.reserva r WHERE r.carrito_id=ca.id
          ) AND dc.cantidad=4
        ORDER BY ca.id""",(a_id,)).fetchall()
    ensure(len(candidatos)==1, "carrito A de competencia no único")
    ca_id,cli_id,detalle_id=candidatos[0]
    c.execute("UPDATE public.cliente SET usuario_id=%s WHERE id=%s AND empresa_id=%s",
              (ua,cli_id,a_id))
    c.execute("UPDATE public.detalle_carrito SET cantidad=1 WHERE id=%s AND empresa_id=%s",
              (detalle_id,a_id))

    # B: preparar reserva original como CONFIRMADA y ajustar stock
    # de acuerdo con el único detalle ficticio existente. ROLLBACK total.
    b_res=c.execute("""SELECT re.id,dr.inventario_id,dr.cantidad,re.sucursal_id
        FROM public.reserva re JOIN public.detalle_reserva dr
          ON dr.reserva_id=re.id AND dr.empresa_id=re.empresa_id
        WHERE re.empresa_id=%s AND re.estado='PENDIENTE'""",
        (b_id,)).fetchall()
    ensure(len(b_res)==1 and b_res[0][2]==1,
           "reserva B original no unica o detalle inesperado")
    rb, ib, qb, sb = b_res[0]
    c.execute("""UPDATE public.reserva SET estado='CONFIRMADA'
        WHERE id=%s AND empresa_id=%s""",(rb,b_id))
    c.execute("""UPDATE public.inventario SET stock_reservado=1
        WHERE id=%s AND empresa_id=%s AND stock_actual=5
          AND stock_reservado=0""",(ib,b_id))
    ensure(one(c,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
               (ib,))==1,"stock B no preparado")
    # Empleado admin B + funciones y acción necesarios para CU18.
    c.execute("""INSERT INTO public.empleado
        (empresa_id,usuario_id,sucursal_id,nombres,apellidos,ci,
         fecha_contratacion,estado)
        VALUES(%s,%s,%s,'Admin','Laboratorio','LAB-5AN-B-ADM',%s,TRUE)""",
        (b_id,ub,sb,date.today()))
    mid=one(c,"""INSERT INTO public.modulo(nombre,estado)
        VALUES('RESERVAS',TRUE) RETURNING id""")
    fid=one(c,"""INSERT INTO public.funcion(modulo_id,nombre,estado)
        VALUES(%s,'GESTIONAR_RESERVAS',TRUE) RETURNING id""",(mid,))
    aid=one(c,"""INSERT INTO public.accion(nombre,estado)
        VALUES('EJECUTAR',TRUE) RETURNING id""")
    c.execute("""INSERT INTO public.rol_funcion(rol_id,funcion_id,accion_id)
        VALUES(%s,%s,%s)""",(admin_role,fid,aid))
    return dict(a_empresa=a_id,b_empresa=b_id,
                a_usuario=ua,b_usuario=ub,
                a_carrito=ca_id,b_reserva=rb,
                a_inventario=one(c,
                    "SELECT inventario_id FROM public.detalle_carrito WHERE id=%s",
                    (detalle_id,)))



def diagnosticar_membresia(c, empresa_id, usuario_id, rol):
    """Visibilidad RLS del actor, con y sin FOR SHARE; solo datos ficticios."""
    tablas = {
        "empresa": ("SELECT count(*) FROM public.empresa WHERE id=%s AND estado IS TRUE", (empresa_id,)),
        "usuario": ("SELECT count(*) FROM public.usuario WHERE id=%s AND estado IS TRUE", (usuario_id,)),
        "rol": ("SELECT count(*) FROM public.rol WHERE nombre=%s AND estado IS TRUE", (rol,)),
        "membresia": ("SELECT count(*) FROM public.usuario_empresa WHERE empresa_id=%s AND usuario_id=%s AND estado IS TRUE", (empresa_id,usuario_id)),
        "suscripcion": ("SELECT count(*) FROM public.suscripcion WHERE empresa_id=%s AND estado='ACTIVA' AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date BETWEEN fecha_inicio AND fecha_vencimiento", (empresa_id,)),
        "plan": ("SELECT count(*) FROM public.plan_saas WHERE estado IS TRUE AND permite_reservas IS TRUE", None),
    }
    diagnostico = {name: one(c,sql,params) for name,(sql,params) in tablas.items()}
    join = """
        SELECT ue.rol_id
        FROM public.usuario_empresa ue
        JOIN public.usuario u ON u.id=ue.usuario_id AND u.estado IS TRUE
        JOIN public.empresa e ON e.id=ue.empresa_id AND e.estado IS TRUE
        JOIN public.rol r ON r.id=ue.rol_id AND r.estado IS TRUE
        JOIN public.suscripcion ss ON ss.empresa_id=e.id AND ss.estado='ACTIVA'
        JOIN public.plan_saas pl ON pl.id=ss.plan_saas_id
        WHERE ue.empresa_id=%s AND ue.usuario_id=%s
          AND ue.estado IS TRUE AND r.nombre=%s
          AND pl.estado IS TRUE AND pl.permite_reservas IS TRUE
          AND (clock_timestamp() AT TIME ZONE 'America/La_Paz')::date
              BETWEEN ss.fecha_inicio AND ss.fecha_vencimiento
    """
    parametros=(empresa_id,usuario_id,rol)
    diagnostico["join"] = len(c.execute(join,parametros).fetchall())
    diagnostico["join_for_share"] = len(
        c.execute(join + " FOR SHARE OF ue, ss",parametros).fetchall()
    )
    print("5AN AUTH RLS: " + ", ".join(
        f"{nombre}={valor}" for nombre,valor in diagnostico.items()
    ))
    ensure(all(valor == 1 for valor in diagnostico.values()),
           "validacion de membresia no visible con permisos RLS/FOR SHARE")

def run_calls(c, ids):
    a,b=ids["a_empresa"],ids["b_empresa"]
    ua,ub=ids["a_usuario"],ids["b_usuario"]
    future=datetime.now(timezone.utc)+timedelta(days=2)
    as_role(c,A)
    ensure(one(c,"SELECT count(*) FROM public.empresa")==1,
           "A ve mas de una empresa")
    ensure(one(c,"SELECT id FROM public.empresa")==a,
           "A principal no coincide")
    # Incluso cuando conoce IDs ajenos, A no puede consultar usuario/rol B.
    ensure(one(c,"SELECT count(*) FROM public.usuario WHERE id=%s",(ub,)) == 0,
           "usuario B se filtro al principal A")
    ensure(one(c,"SELECT count(*) FROM public.rol WHERE nombre='ADMINISTRADOR'") == 0,
           "rol de B se filtro al principal A")
    diagnosticar_membresia(c,a,ua,"CLIENTE")
    result=c.execute(CALL_CREATE,(a,ids["a_carrito"],ua,future,
                                  "Ensayo 5AN rollback",None)).fetchone()
    ensure(result is not None and isinstance(result[0],int) and result[0]>0,
           "SP CU16 no devolvio ID")
    new_id=result[0]
    ensure(one(c,
        "SELECT stock_reservado FROM public.inventario WHERE id=%s",
        (ids["a_inventario"],))==5,"CU16 no reservo la unidad libre")
    print("5AN CU16 PASS: cliente A crea reserva y stock 5/5")
    c.execute(CALL_CHANGE.format("sp_cancelar_reserva_tenant"),
              (a,new_id,ua,"Cancelacion 5AN"))
    ensure(one(c,"SELECT estado FROM public.reserva WHERE id=%s",(new_id,))==
           "CANCELADA","CU17 no cancelo")
    ensure(one(c,
        "SELECT stock_reservado FROM public.inventario WHERE id=%s",
        (ids["a_inventario"],))==4,
        "CU17 no libero exactamente 1 unidad")
    print("5AN CU17 PASS: el cliente A cancela y libera unidad sin venta")
    reset_admin(c)

    as_role(c,B)
    ensure(one(c,"SELECT count(*) FROM public.empresa")==1,
           "B ve mas de una empresa")
    ensure(one(c,"SELECT id FROM public.empresa")==b,
           "B principal no coincide")
    ensure(one(c,"SELECT count(*) FROM public.usuario WHERE id=%s",(ua,)) == 0,
           "usuario A se filtro al principal B")
    ensure(one(c,"SELECT count(*) FROM public.rol WHERE nombre='CLIENTE'") == 0,
           "rol de A se filtro al principal B")
    diagnosticar_membresia(c,b,ub,"ADMINISTRADOR")
    c.execute(CALL_CHANGE.format("sp_finalizar_reserva_sin_compra_tenant"),
              (b,ids["b_reserva"],ub,"Finalizacion 5AN"))
    ensure(one(c,"SELECT estado FROM public.reserva WHERE id=%s",
               (ids["b_reserva"],))=="ATENDIDA",
           "CU18 no finalizo")
    ensure(one(c,"SELECT stock_reservado FROM public.inventario WHERE empresa_id=%s",
               (b,))==0,"CU18 no libero stock B")
    print("5AN CU18 PASS: personal B finaliza atencion y libera stock")
    reset_admin(c)


def main():
    print("5AN: INTEGRACION SQL CU16-18 EN TRANSACCION CON ROLLBACK")
    src=sources()
    secret=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=secret,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=25000 -c lock_timeout=5000",
    ) as c:
        preflight(c)
        print("5AN PREFLIGHT OK: 2 tenants ficticios; 41 policies originales")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='25s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids=fixtures(c)
            for sql in src.values():
                c.execute(sql)
            # revocados a PUBLIC por los drafts; permitir EXECUTE A/B
            # sólo en esta transacción para probar operaciones.
            grant_minimal_lab(c)
            ensure(one(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==47,
                   "policies temporales no son exactamente seis")
            run_calls(c,ids)
            print("5AN CALL PASS: creación, cancelación y finalización")
        finally:
            # Después de ROLLBACK se restituyen tanto rol como estructura.
            c.execute("ROLLBACK")
            preflight(c)
            print("5AN ROLLBACK Y PREFLIGHT OK: laboratorio restaurado")
        print("5AN OK: ROLLBACK completo, 0 SP CU16-18 permanentes, "
              "5 empresas finales intactas")
        print("5AN FALTA: JWT HTTP y aislamiento de usuario dentro "
              "del mismo tenant, no probado por SET SESSION AUTHORIZATION")


if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print(f"5AN ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
