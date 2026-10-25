"""5AV: verificador SQL real + adaptador 5AU + RLS/ACL 5AT en LAB PG18.

SOLO 127.0.0.1:55432, base fashionstore_rls_lab_5u. Usa el preflight
estricto 5AM, un único BEGIN, NO COMMIT real, y ROLLBACK obligatorio.

Crea temporalmente un OWNER NOLOGIN y un verificador SECURITY DEFINER
que no puede ser reprogramado por A1/A2/B1. Tres SP temporales delegan
la guarda del actor a ese verificador. El servicio FastAPI (sin HTTP)
prueba identidad verificada, CU16/17 A1+A2 y CU18 B1 con SQL auténtico.

ADVERTENCIA: session_user individual simulado por el DBA con SET LOCAL
SESSION AUTHORIZATION; esto NO acredita login real no-superuser ni pool HTTP.
La auditoría DML directa es negativa: la falta de restricciones de fila
por usuario podría permitir UPDATE fuera de los procedimientos.
Se informa como brecha si se demuestra, NUNCA como aprobación para activar SaaS.
"""
from __future__ import annotations

import os
# Jamás importar el Engine de la aplicación con DATABASE_URL del .env real.
os.environ["DATABASE_URL"]="sqlite+pysqlite:///./saas_5av_import_inerte.db"
os.environ["SECRET_KEY"]="clave-ficticia-5av-solamente-local-mas-de-32-bytes"
os.environ["SAAS_AUTH_ENABLED"]="false"
os.environ["SAAS_RESERVAS_ENABLED"]="false"

from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[2]
if not (ROOT/"app"/"core"/"config.py").is_file():
    raise RuntimeError("5AV: no se encontro paquete app del repositorio")
sys.path.insert(0,str(ROOT))

import psycopg
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.modules.reservas.services import tenant_reserva_transaccional_5al as service

HERE=Path(__file__).resolve().parent
LAB=runpy.run_path(str(HERE/"55_sp_actor_individual_dml_rollback_5at.py"))
PREFLIGHT=LAB["PREFLIGHT"]
FIXTURES=LAB["FIXTURES"]
SOURCES=LAB["SOURCES"]
MAPEAR_A=LAB["MAPEAR_A"]
CREAR_A2=LAB["CREAR_A2"]
MAPEAR_B=LAB["MAPEAR_B"]
CARRO_A2=LAB["crear_carrito_cliente_a2"]
INSTALAR_CU16=LAB["INSTALAR_CU16"]
PROTEGER_CU17_18=LAB["PROTEGER_CU17_18"]
ACL_RLS=LAB["crear_politicas_y_permisos_individuales"]
ONE=LAB["ONE"]
ENSURE=LAB["ENSURE"]
SQL_A1,SQL_A2,SQL_B1=LAB["SQL_A1"],LAB["SQL_A2"],LAB["SQL_B1"]
ROLES=(SQL_A1,SQL_A2,SQL_B1)
HOST,PORT,DB=LAB["HOST"],LAB["PORT"],LAB["DB"]
OWNER="fashionstore_5av_guard_owner"
DIALECT=postgresql.dialect(paramstyle="pyformat")


class Resultado:
    def __init__(self,cursor,con_nombres=False):
        self.cursor=cursor
        self.con_nombres=con_nombres

    def mappings(self):
        return Resultado(self.cursor,True)

    def _fila(self,row):
        if row is None or not self.con_nombres:
            return row
        nombres=[col.name for col in self.cursor.description]
        return dict(zip(nombres,row))

    def one_or_none(self):
        rows=self.cursor.fetchmany(2)
        ENSURE(len(rows)<=1,"consulta de identidad o reserva ambigua")
        return self._fila(rows[0]) if rows else None


class DBRollback:
    """Adaptador SQLAlchemy→psycopg; NUNCA permite confirmar una reserva."""
    def __init__(self,c):
        self.c=c
        self.commits_interceptados=0
        self.info={}

    def execute(self,stmt,params=None):
        sql=stmt.compile(dialect=DIALECT).string
        return Resultado(self.c.execute(sql,params))

    def scalar(self,stmt,params=None):
        value=self.execute(stmt,params).one_or_none()
        return value[0] if value is not None else None

    def commit(self):
        self.commits_interceptados+=1

    def rollback(self):
        # Cualquier error real aborta el ensayo; ROLLBACK final es del DBA.
        pass


def instalar_verificador_5av(c):
    """Mapa protegido y propietario NOLOGIN, no superusuario."""
    c.execute(f"""CREATE ROLE {OWNER} NOLOGIN NOINHERIT
        NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS""")
    c.execute(f"""GRANT SELECT
        (principal_sql,empresa_id,usuario_id)
        ON public.fs_lab_actor_sql_5ar TO {OWNER}""")
    c.execute(f"""CREATE POLICY fs_lab_5av_map_owner_select
        ON public.fs_lab_actor_sql_5ar FOR SELECT TO {OWNER}
        USING (TRUE)""")
    c.execute("""
        CREATE FUNCTION public.fs_verificar_actor_reserva(
            p_empresa_id bigint,p_usuario_id bigint
        ) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $actor_5av$
          SELECT EXISTS(
            SELECT 1 FROM public.fs_lab_actor_sql_5ar m
            WHERE m.principal_sql=session_user::name
              AND m.empresa_id=p_empresa_id
              AND m.usuario_id=p_usuario_id
          )
        $actor_5av$
    """)
    c.execute("""REVOKE ALL ON FUNCTION
        public.fs_verificar_actor_reserva(bigint,bigint) FROM PUBLIC""")
    # ALTER OWNER requiere CREATE sobre el schema para el dueño nuevo.
    # Solo se concede temporalmente mientras el DBA mueve la titularidad.
    c.execute(f"GRANT CREATE ON SCHEMA public TO {OWNER}")
    c.execute(f"""ALTER FUNCTION public.fs_verificar_actor_reserva(
        bigint,bigint) OWNER TO {OWNER}""")
    c.execute(f"REVOKE CREATE ON SCHEMA public FROM {OWNER}")
    c.execute(f"""GRANT EXECUTE ON FUNCTION
        public.fs_verificar_actor_reserva(bigint,bigint)
        TO {','.join(ROLES)}""")
    ENSURE(ONE(c,"""SELECT count(*) FROM pg_catalog.pg_roles
        WHERE rolname=%s AND NOT rolcanlogin AND NOT rolsuper
          AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolinherit""",(OWNER,))==1,
        "dueño protegido no cumple atributos mínimos")
    ENSURE(ONE(c,"""SELECT count(*) FROM pg_policies
        WHERE schemaname='public'""")==105,
        "se esperaba una sola política adicional de mapa privado")
    for principal in ROLES:
        ENSURE(ONE(c,"""SELECT has_table_privilege(
            %s,'public.fs_lab_actor_sql_5ar','SELECT')""",(principal,))
            is False,"actor puede leer mapa de identidad")
        ENSURE(ONE(c,"""SELECT has_table_privilege(
            %s,'public.fs_lab_actor_sql_5ar','UPDATE')""",(principal,))
            is False,"actor puede alterar mapa de identidad")


def usar_unico_verificador_en_tres_sp(c):
    """La misma función de 5AU se usa realmente dentro de cada CALL."""
    for name in (
        "sp_crear_reserva_tenant",
        "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ):
        ddl=ONE(c,"""SELECT pg_get_functiondef(p.oid)
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=%s
              AND p.prokind='p' AND NOT p.prosecdef""",(name,))
        original="public.fs_lab_verificar_actor_5ar(p_empresa_id,p_usuario_id)"
        reemplazo="public.fs_verificar_actor_reserva(p_empresa_id,p_usuario_id)"
        ENSURE(isinstance(ddl,str) and ddl.count(original)==1,
               f"guardia preexistente de {name} no coincide")
        c.execute(ddl.replace(original,reemplazo,1))
        nuevo=ONE(c,"""SELECT pg_get_functiondef(p.oid)
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=%s
              AND p.prokind='p' AND NOT p.prosecdef""",(name,))
        ENSURE(nuevo.count(reemplazo)==1 and original not in nuevo,
               f"SP {name} no llama al verificador auditado")
    print("5AV GUARDIA SP OK: CU16/CU17/CU18 consultan fs_verificar_actor_reserva")


def tenant(usuario,empresa,slug,contexto,rol):
    return TenantContext(
        usuario_id=usuario,empresa_id=empresa,empresa_slug=slug,
        empresa_nombre=slug,rol_id=1,rol_nombre=rol,
        contexto=contexto,plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


def como_rol(c,rol):
    ENSURE(rol in ROLES or rol in (LAB["BASE"]["TENANT_A"],),
           "rol fuera de la prueba")
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {rol}")
    ENSURE(ONE(c,"SELECT session_user")==rol,"session_user inesperado")


def como_dba(c):
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(c,"SELECT session_user")=="postgres","no se recupero DBA")


def verificar_5au_y_llamadas(c,ids,a2,carrito2):
    db=DBRollback(c)
    a,b=ids["a_empresa"],ids["b_empresa"]
    a1,b1=ids["a_usuario"],ids["b_usuario"]
    actor1=tenant(a1,a,"empresa-lab-a","cliente","CLIENTE")
    actor2=tenant(a2,a,"empresa-lab-a","cliente","CLIENTE")
    admin=tenant(b1,b,"empresa-lab-b","personal","ADMINISTRADOR")
    owner_anterior=settings.saas_actor_sql_owner
    try:
        settings.saas_actor_sql_owner=OWNER
        # Bajo A1, el JWT de A2 NO queda acreditado aunque manipulen GUC.
        como_rol(c,SQL_A1)
        c.execute("SELECT set_config('app.usuario_id',%s,true)",(str(a2),))
        try:
            service.verificar_principal_reserva(db,actor2)
        except service.ReservaTenantDeshabilitada:
            print("5AV ACTOR AJENO PASS: A1 no representa A2 en servicio 5AU")
        else:
            raise RuntimeError("5AV BRECHA: servicio admitió A1 como A2")
        ENSURE(service.verificar_principal_reserva(db,actor1)==SQL_A1,
               "verificador rechaza A1 legítimo")
        for principal,actor,carrito in (
            (SQL_A1,actor1,ids["a_carrito"]),
            (SQL_A2,actor2,carrito2),
        ):
            if principal != SQL_A1:
                como_dba(c)
                como_rol(c,principal)
            rid=service.crear_reserva_transaccional_sin_exposicion_http(
                db,tenant=actor,carrito_id=carrito,
                fecha_atencion=datetime.now(timezone.utc)+timedelta(days=2),
                observacion="5AV identidad verificada"
            )
            ENSURE(ONE(c,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (ids["a_inventario"],))==5,"CU16 no retuvo una unidad")
            print(f"5AV CU16 PASS: {principal} → servicio 5AU → SP → stock 5/5")
            estado=service.cancelar_reserva_transaccional_tenant(
                db,tenant=actor,reserva_id=rid,observacion="5AV rollback")
            ENSURE(estado=="CANCELADA","CU17 estado no cancelado")
            ENSURE(ONE(c,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (ids["a_inventario"],))==4,"CU17 no liberó una unidad")
            print(f"5AV CU17 PASS: {principal} libera stock 4/5")
            if principal==SQL_A2:
                reserva_a2=rid
        como_dba(c)
        como_rol(c,SQL_B1)
        estado=service.finalizar_reserva_sin_compra_transaccional_tenant(
            db,tenant=admin,reserva_id=ids["b_reserva"],
            observacion="5AV atención finalizada"
        )
        ENSURE(estado=="ATENDIDA","CU18 no finalizó")
        ENSURE(ONE(c,"SELECT stock_reservado FROM public.inventario WHERE empresa_id=%s",
                   (b,))==0,"CU18 no liberó inventario B")
        print("5AV CU18 PASS: B1 legítimo finalizó sin compra")
        como_dba(c)

        # Negación del mismo servicio ante login compartido A/B, incluso si
        # el SP tiene EXECUTE. No llamar SP de negocio con ese principal.
        como_rol(c,LAB["BASE"]["TENANT_A"])
        try:
            service.verificar_principal_reserva(db,actor1)
        except service.ReservaTenantDeshabilitada:
            print("5AV PRINCIPAL COMPARTIDO PASS: SQL A no acredita actor A1")
        else:
            raise RuntimeError("5AV BRECHA: rol compartido superó 5AU")
        como_dba(c)
        ENSURE(db.commits_interceptados==5,
               "deben interceptarse exactamente 5 COMMIT de CU16-18")
        return reserva_a2
    finally:
        settings.saas_actor_sql_owner=owner_anterior


def auditar_dml_directo(c,ids,reserva_a2):
    """Un UPDATE directo exitoso es una BRECHA, aunque el SP esté protegido."""
    a=ids["a_empresa"]
    como_rol(c,SQL_A1)
    c.execute("SAVEPOINT sp_audit_5av")
    vulnerable=False
    try:
        resultado=c.execute("""UPDATE public.reserva
            SET observacion='5AV_INTENTO_DML_DIRECTO'
            WHERE id=%s AND empresa_id=%s
            RETURNING id""",(reserva_a2,a)).fetchone()
        vulnerable=resultado is not None
    except psycopg.Error as err:
        code=err.sqlstate
        c.execute("ROLLBACK TO SAVEPOINT sp_audit_5av")
        if code not in ("42501","P0002"):
            raise
    else:
        c.execute("ROLLBACK TO SAVEPOINT sp_audit_5av")
    c.execute("RELEASE SAVEPOINT sp_audit_5av")
    if vulnerable:
        print("5AV ALERTA DML: A1 pudo ejecutar UPDATE directo sobre reserva "
              "de A2. ACL/RLS individual todavía NO segura.")
    else:
        print("5AV DML DIRECTO BLOQUEADO: UPDATE directo de A1 a reserva A2")
    como_dba(c)
    return vulnerable


def main():
    print("5AV: servicio 5AU + actor SQL PostgreSQL real; auditoría reversible")
    fuentes=SOURCES()
    password=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=password,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=50000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5AV PREFLIGHT OK: laboratorio PG18 A/B aislado")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='50s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids=FIXTURES(c)
            a2=CREAR_A2(c,ids["a_empresa"])
            MAPEAR_A(c,ids["a_empresa"],ids["a_usuario"],a2)
            MAPEAR_B(c,ids["b_empresa"],ids["b_usuario"])
            carrito2=CARRO_A2(c,ids,a2)
            INSTALAR_CU16(c,fuentes)
            PROTEGER_CU17_18(c,fuentes)
            ACL_RLS(c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==104,"RLS individuales no coinciden con 5AT")
            instalar_verificador_5av(c)
            usar_unico_verificador_en_tres_sp(c)
            reserva2=verificar_5au_y_llamadas(c,ids,a2,carrito2)
            brecha_dml=auditar_dml_directo(c,ids,reserva2)
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,"SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                       ([OWNER,*ROLES],))==0,
                   "persistió alguno de los roles SQL de prueba")
            ENSURE(ONE(c,"SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                   is True,"persistió el mapa privado de identidad")
            ENSURE(ONE(c,"SELECT to_regprocedure(" 
                "'public.fs_verificar_actor_reserva(bigint,bigint)') IS NULL")
                is True,"función de identidad no se revirtió")
            ENSURE(ONE(c,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                   ==41,"policies originales no se restauraron")
            print("5AV ROLLBACK OK: 0 roles, 0 mapa, 0 SP definitivos, "
                  "41 policies y stock original")
        print("5AV SERVICIO+SQL OK: principal individual y procedimientos coinciden")
        if brecha_dml:
            print("5AV BLOQUEO DE DESPLIEGUE: permisos DML directos "
                  "aún exponen reservas entre usuarios del mismo tenant")
        else:
            print("5AV PENDIENTE: auditoría RLS/DML completa de todas las tablas")
        print("5AV NO valida login no-superuser real, pool HTTP ni JWT; "
              "flags SaaS OFF, jamás usar en empresas de entrega")


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print(f"5AV ERROR: {type(err).__name__}: {err}",file=sys.stderr)
        sys.exit(2)
