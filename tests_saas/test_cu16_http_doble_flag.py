"""CU16 SaaS: contrato HTTP real con doble feature flag; DB simulada.

No conecta PostgreSQL ni instala SP; prueba que el endpoint no está abierto
hasta validar todas las dependencias y habilitar explicitamente ambos flags.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-test-cu16-http"
os.environ["SAAS_AUTH_ENABLED"] = "false"
os.environ["SAAS_RESERVAS_ENABLED"] = "false"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.core.saas_route_gate import BloqueoRutasLegacySaaS, _ruta_saas_permitida
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.reservas.api import tenant_reserva_write_router as mod
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    ReservaTenantDeshabilitada, ReservaTenantIntegridadError
)


class FakeDB:
    pass


@pytest.fixture()
def escenario(monkeypatch):
    app=FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)
    app.include_router(mod.router)
    ctx={"empresa_id":3,"rol":"CLIENTE","contexto":"cliente"}

    def tenant():
        return TenantContext(
            usuario_id=42,
            empresa_id=ctx["empresa_id"],
            empresa_slug="elegancia-sur",
            empresa_nombre="Elegancia Sur",
            rol_id=7, rol_nombre=ctx["rol"],
            contexto=ctx["contexto"],
            plan_codigo="PROFESIONAL",
            permite_reportes_ia=True,
        )

    fake=FakeDB()

    def db():
        yield fake

    app.dependency_overrides[get_db]=db
    app.dependency_overrides[get_current_tenant_context]=tenant
    monkeypatch.setattr(settings,"saas_auth_enabled",True)
    monkeypatch.setattr(settings,"saas_reservas_enabled",False)
    try:
        with TestClient(app) as client:
            yield client,ctx,fake,monkeypatch
    finally:
        app.dependency_overrides.clear()


def payload(**changes):
    return {
        "carrito_id":21,
        "fecha_atencion":(datetime.now(timezone.utc)+timedelta(days=3)).isoformat(),
        **changes
    }


def test_default_off_independiente_saas_login(escenario):
    client,_,_,_=escenario
    assert _ruta_saas_permitida("POST","/tenant/reservas") is False
    assert client.post("/tenant/reservas",json=payload()).status_code==503


def test_cu16_ruta_crea_con_actor_jwt_inmutable(escenario):
    client,ctx,db,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    llamadas=[]

    def ejecutar(_db,*,tenant,carrito_id,fecha_atencion,observacion):
        llamadas.append((tenant.empresa_id,tenant.usuario_id,carrito_id,observacion))
        assert _db is db
        assert fecha_atencion.tzinfo is not None
        return 981

    mp.setattr(mod,"crear_reserva_transaccional_sin_exposicion_http",ejecutar)
    res=client.post("/tenant/reservas",json=payload(observacion="Prueba"))
    assert res.status_code==201,res.text
    assert res.json()=={"reserva_id":981,"carrito_id":21,"estado":"PENDIENTE"}
    assert llamadas==[(3,42,21,"Prueba")]


def test_empresa_en_body_no_se_puede_suplantar(escenario):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    response=client.post("/tenant/reservas",json=payload(empresa_id=4))
    assert response.status_code==422


def test_cliente_id_en_body_no_se_puede_suplantar(escenario):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    response=client.post("/tenant/reservas",json=payload(usuario_id=8))
    assert response.status_code==422


@pytest.mark.parametrize("invalid",[0,-1])
def test_rechaza_carrito_invalido(escenario,invalid):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    response=client.post("/tenant/reservas",json=payload(carrito_id=invalid))
    assert response.status_code==422


def test_rechaza_fecha_naive(escenario):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    fecha=(datetime.now()+timedelta(days=3)).replace(tzinfo=None).isoformat()
    assert client.post("/tenant/reservas",json=payload(fecha_atencion=fecha)).status_code==422


def test_rechaza_fecha_pasada(escenario):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    fecha=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
    assert client.post("/tenant/reservas",json=payload(fecha_atencion=fecha)).status_code==422


@pytest.mark.parametrize("other",[
    {"contexto":"personal","rol":"ADMINISTRADOR"},
    {"contexto":"personal","rol":"CAJERO"},
])
def test_solo_contexto_cliente(escenario,other):
    client,ctx,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    ctx.update(other)
    assert client.post("/tenant/reservas",json=payload()).status_code==403


@pytest.mark.parametrize("exc,code",[
    (ReservaTenantDeshabilitada("Sin RLS"),503),
    (ReservaTenantIntegridadError("ID falso"),409),
    (PermissionError("Actor distinto"),403),
    (SQLAlchemyError("DB oculta"),503),
    (IntegrityError("insert",None,Exception("Unique")),409),
])
def test_error_seguro_y_sin_errores_sql(escenario,exc,code):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)

    def ejecutar(*args,**kwargs):
        raise exc

    mp.setattr(mod,"crear_reserva_transaccional_sin_exposicion_http",ejecutar)
    r=client.post("/tenant/reservas",json=payload())
    assert r.status_code==code
    assert "ID falso" not in r.text and "Sin RLS" not in r.text
    assert "DB oculta" not in r.text and "Unique" not in r.text


def test_no_abre_cancelacion_ni_otros_sp(escenario):
    client,_,_,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    for method,url in (
        ("PATCH","/tenant/reservas/1/cancelar"),
        ("POST","/tenant/reservas/1/cancelar"),
        ("POST","/tenant/reservas/crear"),
        ("POST","/tenant/comercial/ventas"),
        ("POST","/tenant/comercial/pagos"),
    ):
        assert client.request(method,url,json={}).status_code==503


def test_doble_flag_no_bypass_directo_route(escenario):
    _,_,db,mp=escenario
    mp.setattr(settings,"saas_reservas_enabled",True)
    mp.setattr(settings,"saas_auth_enabled",False)
    with pytest.raises(Exception) as exc:
        mod.crear_reserva_tenant(
            mod.CrearReservaTenantRequest.model_validate(payload()),
            TenantContext(
                usuario_id=42,empresa_id=3,empresa_slug="elegancia-sur",
                empresa_nombre="Demo",rol_id=2,rol_nombre="CLIENTE",
                contexto="cliente",plan_codigo="PROFESIONAL",
                permite_reportes_ia=True
            ),db
        )
    assert getattr(exc.value,"status_code",None)==503
