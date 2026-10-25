"""5AL: ensayos unitarios SIN abrir rutas CU16 ni usar PostgreSQL real."""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest

from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    verificar_principal_reserva,
    crear_reserva_transaccional_sin_exposicion_http,
    ReservaTenantDeshabilitada,
    ReservaTenantIntegridadError,
    cancelar_reserva_transaccional_tenant,
    finalizar_reserva_sin_compra_transaccional_tenant,
)

ROOT=Path(__file__).resolve().parents[1]
CODE=(ROOT/"app/modules/reservas/services/tenant_reserva_transaccional_5al.py").read_text(
    encoding="utf-8"
)


def tenant():
    return TenantContext(
        usuario_id=42,empresa_id=15,empresa_slug="elegancia-sur",
        empresa_nombre="Empresa Demo",rol_id=3,rol_nombre="CLIENTE",
        contexto="cliente",plan_codigo="PROFESIONAL",permite_reportes_ia=True,
    )


def date():
    return datetime.now(timezone.utc)+timedelta(days=2)


def principal(**patch):
    r=dict(
        principal="fashionstore_sql_tenant_ficticio",
        restringido=True,
        rls_empresas=True,rls_reservas=True,rls_inventario=True,
        empresas_visibles=1,empresa_esperada_visible=1,planes_reserva_vigentes=1,
        procedimientos_autorizados=1,
    )
    return {**r,**patch}


class QueryResult:
    def __init__(self,row):
        self.row=row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class FakeDB:
    def __init__(self,row=None,reserva=404,coincide=None,error=None):
        self.row=principal() if row is None else row
        self.reserva=reserva
        self.coincide=reserva if coincide is None else coincide
        self.error=error
        self.queries=[]
        self.commits=0
        self.rollbacks=0
        self.params=[]

    def execute(self,sql,args=None):
        query=str(sql)
        self.queries.append(query)
        self.params.append(dict(args) if args is not None else None)
        if "SELECT session_user AS principal" in query:
            return QueryResult(self.row)
        if "CALL public.sp_crear_reserva_tenant" in query:
            if self.error is not None:
                raise self.error
            return QueryResult((self.reserva,))
        raise AssertionError("Sentencia inesperada")

    def scalar(self,sql,args=None):
        self.queries.append(str(sql))
        self.params.append(dict(args))
        return self.coincide

    def commit(self):
        self.commits+=1

    def rollback(self):
        self.rollbacks+=1


def run(db,t=None,cart=17):
    return crear_reserva_transaccional_sin_exposicion_http(
        db,tenant=tenant() if t is None else t,carrito_id=cart,fecha_atencion=date()
    )


def test_cu16_adaptador_exito_atomico_y_ids_del_tenant():
    db=FakeDB()
    assert run(db)==404
    assert db.commits==1 and db.rollbacks==0
    assert len(db.queries)==3
    args=db.params[1]
    assert args["empresa_id"]==15 and args["usuario_id"]==42
    assert args["carrito_id"]==17 and args["reserva_id"] is None
    assert db.params[2]["empresa_id"]==15
    assert db.params[2]["usuario_id"]==42
    assert db.params[2]["reserva_id"]==404


@pytest.mark.parametrize("change",[
    {"restringido":False},
    {"rls_empresas":False},{"rls_reservas":False},{"rls_inventario":False},
    {"empresas_visibles":2},{"empresa_esperada_visible":0},
    {"planes_reserva_vigentes":0},{"planes_reserva_vigentes":2},
    {"procedimientos_autorizados":0},{"procedimientos_autorizados":2},
    {"principal":""},
])
def test_cu16_falla_cerrado_sin_principal_aislado(change):
    db=FakeDB(row=principal(**change))
    with pytest.raises(ReservaTenantDeshabilitada):
        run(db)
    assert db.commits==0 and db.rollbacks==1
    assert len(db.queries)==1


def test_cu16_rechaza_falta_fila_principal():
    db=FakeDB(row={})
    with pytest.raises(ReservaTenantDeshabilitada):
        run(db)
    assert db.commits==0 and db.rollbacks==1


@pytest.mark.parametrize("invalid",[-1,0,True,"404",None])
def test_cu16_rechaza_reserva_id_invalida(invalid):
    db=FakeDB(reserva=invalid)
    with pytest.raises(ReservaTenantIntegridadError):
        run(db)
    assert db.commits==0 and db.rollbacks==1


def test_cu16_rechaza_procedimiento_devuelve_reserva_ajena():
    db=FakeDB(reserva=404,coincide=405)
    with pytest.raises(ReservaTenantIntegridadError):
        run(db)
    assert db.commits==0 and db.rollbacks==1


def test_cu16_error_sql_hace_rollback_y_no_commit():
    from sqlalchemy.exc import SQLAlchemyError
    db=FakeDB(error=SQLAlchemyError("error SQL simulado"))
    with pytest.raises(SQLAlchemyError):
        run(db)
    assert db.commits==0 and db.rollbacks==1


@pytest.mark.parametrize("cart",[0,-1,True,"17",None])
def test_cu16_rechaza_carrito_invalido_antes_sql(cart):
    db=FakeDB()
    with pytest.raises(ValueError):
        run(db,cart=cart)
    assert db.queries==[] and db.commits==0


def test_cu16_contrato_no_permite_usuario_personal():
    from dataclasses import replace
    db=FakeDB()
    with pytest.raises(PermissionError):
        run(db,t=replace(tenant(),contexto="personal",rol_nombre="CAJERO"))
    assert db.queries==[]


def test_cu16_uso_de_contexto_sql_no_sustituye_jwt_y_membresia():
    assert "preparar_creacion_reserva(" in CODE
    assert "get_current_tenant_cliente" in CODE
    assert "JWT+membresía" in CODE
    assert "NOT r.rolsuper AND NOT r.rolbypassrls" in CODE
    assert "NOT r.rolinherit" in CODE


def test_cu16_rutas_de_escritura_no_se_abren():
    assert _ruta_saas_permitida("POST","/tenant/reservas") is False
    assert _ruta_saas_permitida("PATCH","/tenant/reservas/1/cancelar") is False
    # FastAPI 0.137+ preserva routers incluidos como _IncludedRouter
    # sin .path; enumerar rutas efectivas con la API oficial.
    from fastapi.routing import iter_route_contexts
    from app.main import app
    rutas = list(iter_route_contexts(app.routes))
    assert any(
        context.path.rstrip("/") == "/tenant/reservas"
        and "POST" in getattr(context.route, "methods", set())
        for context in rutas
    )
    assert any(
        context.path == "/tenant/reservas/{reserva_id}/cancelar"
        and "PATCH" in getattr(context.route, "methods", set())
        for context in rutas
    )
    assert any(
        context.path == "/tenant/reservas/{reserva_id}/finalizar-sin-compra"
        and "PATCH" in getattr(context.route, "methods", set())
        for context in rutas
    )
    assert "app.include_router" not in CODE


def test_cu16_sigue_sin_5_empresas_modificadas():
    assert "5432" not in CODE
    assert "CREATE PROCEDURE" not in CODE
    assert "CREATE POLICY" not in CODE
    assert "supabase" not in CODE.lower()



class CambioDB(FakeDB):
    def __init__(self, estado, fail=None, **kwargs):
        super().__init__(**kwargs)
        self.estado=estado
        self.fail=fail

    def execute(self,sql,args=None):
        query=str(sql)
        self.queries.append(query)
        self.params.append(dict(args) if args is not None else None)
        if "SELECT session_user AS principal" in query:
            return QueryResult(self.row)
        if "CALL public.sp_cancelar_reserva_tenant" in query or (
            "CALL public.sp_finalizar_reserva_sin_compra_tenant" in query
        ):
            if self.fail:
                raise self.fail
            return QueryResult(None)
        raise AssertionError("Sentencia inesperada")

    def scalar(self,sql,args=None):
        self.queries.append(str(sql))
        self.params.append(dict(args))
        return self.estado


def test_cancelacion_atomica_con_principal_y_tenant():
    db=CambioDB("CANCELADA")
    estado=cancelar_reserva_transaccional_tenant(
        db,tenant=tenant(),reserva_id=77,observacion="No asistiré"
    )
    assert estado=="CANCELADA" and db.commits==1 and db.rollbacks==0
    assert db.params[0]["procedimiento"]=="sp_cancelar_reserva_tenant"
    assert db.params[1]["empresa_id"]==15 and db.params[1]["usuario_id"]==42
    assert db.params[2]["reserva_id"]==77
    assert db.params[2]["cliente"] is True


def test_finalizacion_atomica_personal():
    from dataclasses import replace
    db=CambioDB("ATENDIDA")
    staff=replace(tenant(),rol_nombre="CAJERO",contexto="personal")
    estado=finalizar_reserva_sin_compra_transaccional_tenant(
        db,tenant=staff,reserva_id=81,observacion=None
    )
    assert estado=="ATENDIDA" and db.commits==1 and db.rollbacks==0
    assert db.params[0]["procedimiento"]=="sp_finalizar_reserva_sin_compra_tenant"
    assert db.params[2]["cliente"] is False


@pytest.mark.parametrize("accion,estado",[
    ("cancelar","ATENDIDA"),("finalizar","CANCELADA"),
])
def test_cambio_reserva_rechaza_estado_inesperado_y_revierte(accion,estado):
    from dataclasses import replace
    db=CambioDB(estado)
    staff=replace(tenant(),rol_nombre="ADMINISTRADOR",contexto="personal")
    ejecutor=(cancelar_reserva_transaccional_tenant if accion=="cancelar"
              else finalizar_reserva_sin_compra_transaccional_tenant)
    with pytest.raises(ReservaTenantIntegridadError):
        ejecutor(db,tenant=staff,reserva_id=99)
    assert db.commits==0 and db.rollbacks==1


def test_cambio_reserva_no_ejecuta_sp_si_falta_principal_restringido():
    db=CambioDB("CANCELADA",row=principal(restringido=False))
    with pytest.raises(ReservaTenantDeshabilitada):
        cancelar_reserva_transaccional_tenant(db,tenant=tenant(),reserva_id=99)
    assert db.commits==0 and db.rollbacks==1
    assert not any("CALL public." in query for query in db.queries)


def test_cambio_reserva_rechaza_actor_de_rol_incorrecto_sin_sql():
    from dataclasses import replace
    db=CambioDB("ATENDIDA")
    with pytest.raises(PermissionError):
        finalizar_reserva_sin_compra_transaccional_tenant(
            db,tenant=replace(tenant(),contexto="cliente",rol_nombre="CLIENTE"),
            reserva_id=10
        )
    assert db.queries==[] and db.commits==0
