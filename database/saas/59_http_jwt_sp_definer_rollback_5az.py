"""5AZ: FastAPI HTTP/JWT autentico + PostgreSQL REAL, modelo 5AX sin DML.

SOLO PG18 127.0.0.1:55432/fashionstore_rls_lab_5u (preflight 5AM).
NINGUN dato, rol, procedimiento o permiso persiste: BEGIN/ROLLBACK.
La dependencia JWT/membresía y los routers son REALES. Sólo el get_db
es reemplazado por un adaptador sobre la conexión del LAB y commit()
se intercepta para garantizar ROLLBACK. Las identidades SQL individuales
son SIMULADAS con SET LOCAL SESSION AUTHORIZATION del DBA dentro del
LAB, no logins reales de usuario ni un pool de producción.

Flujos: A1 y A2 → JWT/HTTP CU16→CU17; B1 → CU18; misma empresa con
SQL A1 y JWT A2 produce 503 sin CALL; falso JWT y contexto cruzado
producen 401/403; faltante 401; y DML SQL directo sigue rechazado.
FLAGS SaaS sólo durante TestClient y restaurados siempre.
NO validar ni instalar en puerto 5432 / Supabase.
"""
from __future__ import annotations

import os
os.environ["DATABASE_URL"]="sqlite+pysqlite:///./saas_5az_import_inerte.db"
os.environ["SECRET_KEY"]="solo-test-5az-token-real-laboratorio-mas-de-32bytes"
os.environ["SAAS_AUTH_ENABLED"]="false"
os.environ["SAAS_RESERVAS_ENABLED"]="false"

from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[2]
if not (ROOT/"app"/"core"/"config.py").is_file():
    raise RuntimeError("5AZ abortado: raíz de repositorio no comprobada")
sys.path.insert(0,str(ROOT))

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import BloqueoRutasLegacySaaS
from app.core.tenant_context import emitir_token_empresa
from app.modules.autenticacion_seguridad.api.tenant_router import router as auth_router
from app.modules.reservas.api.tenant_reserva_write_router import router as reserva_router

HERE=Path(__file__).resolve().parent
X=runpy.run_path(str(HERE/"58_sp_owner_nologin_sin_dml_directo_rollback_5ax.py"))
LAB=runpy.run_path(str(HERE/"51_http_jwt_postgresql_rollback_5ap.py"))
PREFLIGHT=X["PREFLIGHT"]
FIXTURES=X["FIXTURES"]
SOURCES=X["SOURCES"]
ONE=X["ONE"]
ENSURE=X["ENSURE"]
HOST,PORT,DB=X["HOST"],X["PORT"],X["DB"]
A1,A2,B1=X["A1"],X["A2"],X["B1"]
ROLES=X["ROLES"]
OWNER=X["WRITER"]
VERIFIER_OWNER=X["VERIFIER_OWNER"]
SessionRollback=LAB["SesionReversible"]
respuesta=LAB["require_response"]


def entrar(c,role):
    ENSURE(role in ROLES,"5AZ: solo roles individuales A1/A2/B1")
    c.execute(f"SET LOCAL SESSION AUTHORIZATION {role}")
    ENSURE(ONE(c,"SELECT session_user")==role,
           "principal SQL del escenario no coincide")
    ENSURE(ONE(c,"SELECT count(*) FROM public.empresa")==1,
           "RLS no aísla exactamente una empresa")


def salir(c):
    c.execute("SET LOCAL SESSION AUTHORIZATION DEFAULT")
    ENSURE(ONE(c,"SELECT session_user")=="postgres",
           "no se restauró administrador del LAB")


def headers(t):
    return {"Authorization":f"Bearer {t}"}


def verificar_http(c,ids,a2,carrito2):
    api=FastAPI()
    api.add_middleware(BloqueoRutasLegacySaaS)
    api.include_router(auth_router)
    api.include_router(reserva_router)
    db=SessionRollback(c)

    def laboratorio():
        yield db

    api.dependency_overrides[get_db]=laboratorio
    initial=(
        settings.saas_auth_enabled,settings.saas_reservas_enabled,
        settings.saas_actor_sql_owner,settings.saas_sp_sql_owner,
    )
    try:
        settings.saas_auth_enabled=True
        settings.saas_reservas_enabled=True
        settings.saas_actor_sql_owner=VERIFIER_OWNER
        settings.saas_sp_sql_owner=OWNER

        user_a=emitir_token_empresa(
            usuario_id=ids["a_usuario"],
            empresa_id=ids["a_empresa"],contexto="cliente")
        user_a2=emitir_token_empresa(
            usuario_id=a2,empresa_id=ids["a_empresa"],contexto="cliente")
        admin_b=emitir_token_empresa(
            usuario_id=ids["b_usuario"],
            empresa_id=ids["b_empresa"],contexto="personal")
        cross=emitir_token_empresa(
            usuario_id=ids["a_usuario"],
            empresa_id=ids["b_empresa"],contexto="cliente")
        future=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
        entrar(c,A1)
        with TestClient(api) as http:
            first_sql_count=len(db.calls)
            respuesta(http.post("/tenant/reservas",json={}),401,"sin JWT")
            ENSURE(len(db.calls)==first_sql_count,
                   "consulta SQL comercial antes de comprobar JWT")
            respuesta(http.get("/auth/tenant/me",headers=headers(user_a+"x")),
                      401,"JWT falsificado")
            respuesta(http.get("/auth/tenant/me",headers=headers(cross)),
                      403,"JWT A intentando empresa B")
            respuesta(http.post("/tenant/reservas",
                headers=headers(user_a),
                json={"carrito_id":ids["a_carrito"],
                      "fecha_atencion":future,"usuario_id":a2}),
                422,"JSON intenta inyectar actor")
            ENSURE(not any("CALL public.sp_" in s for s in db.calls),
                   "CALL ejecutado en peticiones denegadas")
            perfil=respuesta(http.get("/auth/tenant/me",
                            headers=headers(user_a)),200,"A1 /me")
            ENSURE(perfil["usuario_id"]==ids["a_usuario"],
                   "perfil A1 no coincide")
            print("5AZ AUTH PASS: JWT falso/sin JWT, empresa cruzada y JSON actor")

            # A2 tiene JWT y membresía válidos, pero una conexión como A1
            # JAMÁS debe autorizar una operación comercial como A2.
            before=len([s for s in db.calls if "CALL public.sp_" in s])
            denied=http.post("/tenant/reservas",headers=headers(user_a2),
                json={"carrito_id":carrito2,"fecha_atencion":future})
            ENSURE(denied.status_code==503,
                   "misma empresa, SQL A1+JWT A2 se esperaba HTTP 503: "
                   +denied.text[:200])
            after=len([s for s in db.calls if "CALL public.sp_" in s])
            ENSURE(before==after,"SP se ejecutó antes de negar actor A2")
            ENSURE(ONE(c,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (ids["a_inventario"],))==4,
                   "actor SQL incorrecto alteró stock")
            print("5AZ ACTOR DENEGADO PASS: JWT A2 bajo SQL A1 → 503, 0 CALL")

            for principal,token,usuario,carrito in (
                (A1,user_a,ids["a_usuario"],ids["a_carrito"]),
                (A2,user_a2,a2,carrito2),
            ):
                if principal==A2:
                    salir(c)
                    entrar(c,A2)
                perfil=respuesta(http.get("/auth/tenant/me",
                        headers=headers(token)),200,"/me cliente")
                ENSURE(perfil["usuario_id"]==usuario,
                       "identidad cliente no coincide")
                creada=respuesta(http.post("/tenant/reservas",
                    headers=headers(token),json={
                        "carrito_id":carrito,"fecha_atencion":future,
                        "observacion":"5AZ JWT HTTP y SQL en rollback",
                    }),201,f"CU16 {principal}")
                rid=creada["reserva_id"]
                ENSURE(type(rid) is int and rid>0,
                       "CU16 no devolvió reserva")
                ENSURE(ONE(c,
                    "SELECT stock_reservado FROM public.inventario WHERE id=%s",
                    (ids["a_inventario"],))==5,"CU16 stock inválido")
                print(f"5AZ CU16 PASS: JWT+HTTP 201+SP DEFINER ({principal})")
                cancelada=respuesta(http.patch(
                    f"/tenant/reservas/{rid}/cancelar",
                    headers=headers(token),json={"observacion":"5AZ cancelar"}),
                    200,f"CU17 {principal}")
                ENSURE(cancelada["estado"]=="CANCELADA",
                       "CU17 estado inválido")
                ENSURE(ONE(c,
                    "SELECT stock_reservado FROM public.inventario WHERE id=%s",
                    (ids["a_inventario"],))==4,"CU17 stock no restaurado")
                print(f"5AZ CU17 PASS: JWT+HTTP 200+SP DEFINER ({principal})")
                if principal==A2:
                    reserva2=rid

            salir(c)
            entrar(c,B1)
            perfil=respuesta(http.get("/auth/tenant/me",
                       headers=headers(admin_b)),200,"/me B1")
            ENSURE(perfil["usuario_id"]==ids["b_usuario"],
                   "administrador B1 incorrecto")
            end=respuesta(http.patch(
                f"/tenant/reservas/{ids['b_reserva']}/finalizar-sin-compra",
                headers=headers(admin_b),json={"observacion":"5AZ B1"}),
                200,"CU18 B1")
            ENSURE(end["estado"]=="ATENDIDA","CU18 no finalizó")
            ENSURE(ONE(c,
                "SELECT stock_reservado FROM public.inventario WHERE empresa_id=%s",
                (ids["b_empresa"],))==0,"CU18 no liberó stock")
            print("5AZ CU18 PASS: JWT admin B1 → HTTP 200 → SP DEFINER")

        salir(c)
        ENSURE(db.commits_bloqueados==5,
               "se esperaban 5 commits interceptados del servicio")
        print("5AZ HTTP+SQL PASS: 5 operaciones, JWT/membresías reales, "
              "0 COMMIT persistentes")
        return reserva2
    finally:
        (settings.saas_auth_enabled,settings.saas_reservas_enabled,
         settings.saas_actor_sql_owner,settings.saas_sp_sql_owner)=initial
        api.dependency_overrides.clear()


def main():
    print("5AZ: JWT real + TestClient + SP DEFINER / ACL sin DML; ROLLBACK")
    src=SOURCES()
    password=getpass("Contrasena postgres SOLO LAB 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=password,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=65000 -c lock_timeout=5000",
    ) as c:
        PREFLIGHT(c)
        print("5AZ PREFLIGHT OK: PG18, 41 policies y dos empresas ficticias")
        try:
            c.execute("BEGIN")
            c.execute("SET LOCAL statement_timeout='65s'")
            c.execute("SET LOCAL lock_timeout='5s'")
            ids=FIXTURES(c)
            a2=X["M"]["CREAR_A2"](c,ids["a_empresa"])
            X["M"]["MAPEAR_A"](c,ids["a_empresa"],ids["a_usuario"],a2)
            X["M"]["MAPEAR_B"](c,ids["b_empresa"],ids["b_usuario"])
            carrito2=X["M"]["CARRO_A2"](c,ids,a2)
            X["M"]["INSTALAR_CU16"](c,src)
            X["M"]["PROTEGER_CU17_18"](c,src)
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
                   ==161,"5AZ: policies individuales/owner distintas de 5AX")
            reserva2=verificar_http(c,ids,a2,carrito2)
            X["auditar_reserva_ajena_sin_grant"](c,ids,reserva2)
            X["auditar_dml_sin_privilegios"](c,ids,reserva2)
        finally:
            c.execute("ROLLBACK")
            PREFLIGHT(c)
            ENSURE(ONE(c,
                "SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                ([OWNER,VERIFIER_OWNER,*ROLES],))==0,
                "roles individuales o propietarios persistieron")
            ENSURE(ONE(c,
                "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
                ==41,"41 policies originales no restauradas")
            ENSURE(ONE(c,
                "SELECT to_regclass('public.fs_lab_actor_sql_5ar') IS NULL")
                is True,"persistió mapa de identidades")
            print("5AZ ROLLBACK VERIFICADO: 0 roles nuevos, 0 mapa, "
                  "0 SP comerciales, 41 policies y stock original")
        print("5AZ OK: HTTP/JWT autentico + SP DEFINER + RLS/ACL LAB")
        print("5AZ PENDIENTE: login PG no-superuser REAL, pool API por actor, "
              "concurrencia y auditoría de seguridad; flags OFF")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AZ ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(2)
