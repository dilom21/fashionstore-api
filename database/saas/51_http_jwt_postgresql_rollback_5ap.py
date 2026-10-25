"""5AP: HTTP/JWT real + PostgreSQL real (LAB) + rollback obligatorio.

El TestClient invoca dependencias reales, SQL del repositorio de membresia,
preflight de principal SQL, y los 3 procedimientos de CU16/CU17/CU18.
Solo la gestion de transaccion DBAPI se adapta: commit() no-op para
PRESERVAR el BEGIN exterior y revertir íntegramente con ROLLBACK.
No es instalacion y NO asegura identidad individual ante acceso SQL directo.

Destino fijo: PostgreSQL 18 127.0.0.1:55432 / fashionstore_rls_lab_5u
con 2 empresas ficticias, no servidor 5432 ni Supabase.
"""
from __future__ import annotations

import os
# Evitar siquiera construir un Engine apuntando a DATABASE_URL de .env:
# SOLO para importar los routers: toda operación SQL de este ensayo va
# al psycopg explícito y protegido por el preflight 5AM.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_5ap_import_inerte.db"
os.environ["SECRET_KEY"] = "solamente-prueba-5ap-jwt-firmado-36-caracteres"
os.environ["SAAS_AUTH_ENABLED"] = "false"
os.environ["SAAS_RESERVAS_ENABLED"] = "false"

from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
import runpy
import sys

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import BloqueoRutasLegacySaaS
from app.core.tenant_context import emitir_token_empresa
from app.modules.autenticacion_seguridad.api.tenant_router import router as auth_router
from app.modules.reservas.api.tenant_reserva_write_router import router as reserva_router

ROOT = Path(__file__).resolve().parent
MOD = runpy.run_path(str(ROOT / "49_ensayo_sp_tenant_reversible_5an.py"))
PREFLIGHT = MOD["preflight"]
CARGAR_SQL = MOD["sources"]
FIXTURES = MOD["fixtures"]
ACL = MOD["grant_minimal_lab"]
AS_ROLE = MOD["as_role"]
AS_DBA = MOD["reset_admin"]
ONE = MOD["one"]
ENSURE = MOD["ensure"]
A = MOD["A"]
B = MOD["B"]
HOST, PORT, DB = MOD["HOST"], MOD["PORT"], MOD["DB"]
DIALECT = postgresql.dialect(paramstyle="pyformat")


class Resultado:
    """Adaptador de filas psycopg para Result de SQLAlchemy en este ensayo."""
    def __init__(self, cursor, como_dict=False):
        self.cursor = cursor
        self.como_dict = como_dict

    def mappings(self):
        return Resultado(self.cursor, True)

    def _convertir(self, row):
        if row is None or not self.como_dict:
            return row
        keys = [col.name for col in self.cursor.description]
        return dict(zip(keys,row))

    def all(self):
        return [self._convertir(row) for row in self.cursor.fetchall()]

    def one_or_none(self):
        rows = self.cursor.fetchmany(2)
        ENSURE(len(rows) <= 1, "consulta de membresia/SP ambigua")
        return self._convertir(rows[0]) if rows else None


class SesionReversible:
    """Ejecuta SQL REAL bajo A/B; jamás puede confirmar el ensayo."""
    def __init__(self, conn):
        self.conn = conn
        self.info = {}
        self.calls = []
        self.commits_bloqueados = 0

    def execute(self, stmt, params=None):
        compiled = stmt.compile(dialect=DIALECT)
        sql = compiled.string
        self.calls.append(sql)
        cursor = self.conn.execute(sql, params)
        return Resultado(cursor)

    def scalar(self, stmt, params=None):
        r = self.execute(stmt,params).one_or_none()
        return r[0] if r is not None else None

    def commit(self):
        # Los servicios FastAPI llamarían commit real en un Session
        # normal. Aqui se impide la persistencia de cualquier DML 5AP.
        self.commits_bloqueados += 1

    def rollback(self):
        # La envoltura superior es la única que hace ROLLBACK real.
        # Si una operación SQL falla, abortar el ensayo completo.
        pass


def require_response(resp, expected, label):
    ENSURE(resp.status_code == expected,
           f"{label}: HTTP esperado {expected}, obtuve {resp.status_code}: "
           f"{resp.text[:400]}")
    return resp.json()


def hacer_ensayo_http(conn, actores):
    api = FastAPI()
    api.add_middleware(BloqueoRutasLegacySaaS)
    api.include_router(auth_router)
    api.include_router(reserva_router)
    db = SesionReversible(conn)

    def db_de_laboratorio():
        yield db

    api.dependency_overrides[get_db] = db_de_laboratorio
    antes = (settings.saas_auth_enabled,settings.saas_reservas_enabled)
    try:
        settings.saas_auth_enabled = True
        settings.saas_reservas_enabled = True

        cliente_a = emitir_token_empresa(
            usuario_id=actores["a_usuario"],
            empresa_id=actores["a_empresa"],contexto="cliente"
        )
        admin_b = emitir_token_empresa(
            usuario_id=actores["b_usuario"],
            empresa_id=actores["b_empresa"],contexto="personal"
        )
        def headers(t): return {"Authorization":f"Bearer {t}"}

        # No atender ninguna petición con principal postgres superusuario.
        AS_ROLE(conn,A)
        with TestClient(api) as http:
            # Rechazos 401 y 422 sin llegar a CALL comercial. El 422 puede
            # consultar la membresía, ya que FastAPI resuelve dependencias
            # antes de validar el cuerpo de la petición.
            antes_call = len(db.calls)
            require_response(http.post("/tenant/reservas",json={}),401,
                             "sin JWT")
            ENSURE(len(db.calls)==antes_call,"sin token hubo lectura SQL")
            require_response(http.post(
                "/tenant/reservas",
                headers=headers(cliente_a),
                json={"carrito_id":actores["a_carrito"],
                      "fecha_atencion":(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
                      "usuario_id":actores["b_usuario"]},
            ),422,"suplantacion actor JSON")
            ENSURE(not any("CALL public.sp_" in sql for sql in db.calls),
                   "petición con actor JSON alcanzó CALL comercial")
            ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (actores["a_inventario"],))==4,
                   "actor JSON alteró inventario")

            
            ENSURE(ONE(conn,"SELECT count(*) FROM public.empresa")==1,
                   "SQL A ve otra empresa")
            resp_me = require_response(
                http.get("/auth/tenant/me",headers=headers(cliente_a)),
                200,"JWT + membresia A")
            ENSURE(resp_me["usuario_id"]==actores["a_usuario"]
                   and resp_me["empresa_id"]==actores["a_empresa"],
                   "identidad A incorrecta")
            # JWT A jamás autoriza empresa B, aunque ambas estén en mismo PG.
            cruzado = emitir_token_empresa(
                usuario_id=actores["a_usuario"],
                empresa_id=actores["b_empresa"], contexto="cliente"
            )
            require_response(
                http.get("/auth/tenant/me",headers=headers(cruzado)),
                403,"JWT A con empresa B")

            fecha=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
            creada=require_response(http.post(
                "/tenant/reservas",headers=headers(cliente_a),json={
                    "carrito_id":actores["a_carrito"],
                    "fecha_atencion":fecha,
                    "observacion":"5AP HTTP + SQL reversible",
                }),201,"CU16 real HTTP→SP")
            rid=creada["reserva_id"]
            ENSURE(type(rid) is int and rid>0,"CU16 ID no positivo")
            ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (actores["a_inventario"],))==5,"CU16 stock incorrecto")
            print("5AP CU16 PASS: JWT cliente A → HTTP 201 → CALL SQL / stock 5/5")

            cancelada=require_response(http.patch(
                f"/tenant/reservas/{rid}/cancelar",headers=headers(cliente_a),
                json={"observacion":"5AP cancelar"}
            ),200,"CU17 real HTTP→SP")
            ENSURE(cancelada["estado"]=="CANCELADA","CU17 sin estado CANCELADA")
            ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE id=%s",
                       (actores["a_inventario"],))==4,"CU17 no liberó stock")
            print("5AP CU17 PASS: JWT cliente A → HTTP 200 → CALL SQL / liberación")
            AS_DBA(conn)

            AS_ROLE(conn,B)
            ENSURE(ONE(conn,"SELECT count(*) FROM public.empresa")==1,
                   "SQL B ve otra empresa")
            resp_me = require_response(
                http.get("/auth/tenant/me",headers=headers(admin_b)),
                200,"JWT + membresia B")
            ENSURE(resp_me["usuario_id"]==actores["b_usuario"]
                   and resp_me["empresa_id"]==actores["b_empresa"],
                   "identidad B incorrecta")
            cerrada=require_response(http.patch(
                f"/tenant/reservas/{actores['b_reserva']}/finalizar-sin-compra",
                headers=headers(admin_b),json={"observacion":"5AP finalizar"}
            ),200,"CU18 real HTTP→SP")
            ENSURE(cerrada["estado"]=="ATENDIDA","CU18 sin estado ATENDIDA")
            ENSURE(ONE(conn,"SELECT stock_reservado FROM public.inventario WHERE empresa_id=%s",
                       (actores["b_empresa"],))==0,"CU18 no liberó stock")
            print("5AP CU18 PASS: JWT admin B → HTTP 200 → CALL SQL / stock liberado")
            AS_DBA(conn)

        ENSURE(db.commits_bloqueados==3,
               "el adaptador de prueba debía interceptar exactamente 3 COMMIT")
        print("5AP JWT+HTTP+SQL PASS: membresias reales, 3 CALL, 3 commits interceptados")
    finally:
        api.dependency_overrides.clear()
        settings.saas_auth_enabled,settings.saas_reservas_enabled = antes


def main():
    print("5AP: JWT + HTTP FASTAPI + POSTGRESQL PG18 EN UN SOLO ROLLBACK")
    src = CARGAR_SQL()
    secret=getpass("Contrasena postgres SOLO laboratorio 55432: ")
    with psycopg.connect(
        host=HOST,port=PORT,dbname=DB,user="postgres",password=secret,
        sslmode="disable",connect_timeout=8,autocommit=True,
        options="-c statement_timeout=35000 -c lock_timeout=5000",
    ) as conn:
        PREFLIGHT(conn)
        print("5AP PREFLIGHT OK: laboratorio exclusivo A/B en 55432")
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='35s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            actores = FIXTURES(conn)
            for ddl in src.values():
                conn.execute(ddl)
            ACL(conn)
            ENSURE(ONE(conn,"SELECT count(*) FROM pg_policies WHERE schemaname='public'")==52,
                   "policies temporales esperadas: 52")
            hacer_ensayo_http(conn,actores)
        finally:
            conn.execute("ROLLBACK")
            PREFLIGHT(conn)
            print("5AP ROLLBACK VERIFICADO: 0 SP persistentes, 41 policies, "
                  "3 reservas originales, 4 carritos originales")
        print("5AP OK: flujos HTTP+JWT+SQL demostrados en laboratorio aislado")
        print("5AP PENDIENTE: establecer vínculo DB inviolable entre"
              " session_user y p_usuario_id; NO activar SaaS")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"5AP ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
