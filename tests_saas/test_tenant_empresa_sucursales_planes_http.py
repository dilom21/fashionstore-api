"""Pruebas HTTP+SQLite para administración SaaS de 5 empresas.

Prueban rutas FastAPI, dependencia de tenant, escrituras COMMIT/rollback,
capacidad configurable y aislamiento por empresa, sin tocar PostgreSQL 5432
ni el laboratorio PG18. El bloqueo FOR UPDATE se verifica en PostgreSQL aparte.
"""
import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "test-saassim-no-prod-5empresas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    Boolean, Column, Date, Integer, MetaData, String, Table, create_engine,
    insert, select,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import BloqueoRutasLegacySaaS, _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.sucursales.api.tenant_saas_admin_router import router
from app.modules.sucursales.repositories.tenant_saas_admin_repository import _hoy


META = MetaData()
EMPRESA=Table("empresa",META,
    Column("id",Integer,primary_key=True),
    Column("slug",String),Column("nombre_comercial",String),
    Column("estado",Boolean))
PLAN=Table("plan_saas",META,
    Column("id",Integer,primary_key=True),
    Column("codigo",String),Column("nombre",String),
    Column("max_sucursales_activas",Integer),
    Column("permite_productos",Boolean),Column("permite_ventas",Boolean),
    Column("permite_reservas",Boolean),Column("permite_offline",Boolean),
    Column("permite_reportes_convencionales",Boolean),
    Column("permite_reportes_ia",Boolean),Column("estado",Boolean))
SUSCRIPCION=Table("suscripcion",META,
    Column("id",Integer,primary_key=True),Column("empresa_id",Integer),
    Column("plan_saas_id",Integer),Column("fecha_inicio",Date),
    Column("fecha_vencimiento",Date),Column("estado",String))
CIUDAD=Table("ciudad",META,
    Column("id",Integer,primary_key=True),Column("estado",Boolean))
SUCURSAL=Table("sucursal",META,
    Column("id",Integer,primary_key=True),Column("empresa_id",Integer),
    Column("ciudad_id",Integer),Column("nombre",String),
    Column("direccion",String),Column("telefono",String),
    Column("estado",Boolean))
EMPLEADO=Table("empleado",META,
    Column("id",Integer,primary_key=True),Column("empresa_id",Integer),
    Column("sucursal_id",Integer),Column("estado",Boolean))
INVENTARIO=Table("inventario",META,
    Column("id",Integer,primary_key=True),Column("empresa_id",Integer),
    Column("sucursal_id",Integer),Column("stock_reservado",Integer))
RESERVA=Table("reserva",META,
    Column("id",Integer,primary_key=True),Column("empresa_id",Integer),
    Column("sucursal_id",Integer),Column("estado",String))

COMPANIES = (
    ("vanter-men",2,4),
    ("urbanwear",1,1),
    ("elegancia-sur",2,3),
    ("denim-house",1,2),
    ("sportline",2,3),
)


@pytest.fixture()
def caso(monkeypatch):
    engine=create_engine("sqlite+pysqlite://",
        connect_args={"check_same_thread":False},
        poolclass=StaticPool)
    META.create_all(engine)
    today=_hoy()
    with engine.begin() as c:
        for pid,code,maximum,ai in ((1,"BASICO",2,False),
                                     (2,"PROFESIONAL",10,True)):
            c.execute(insert(PLAN).values(
                id=pid,codigo=code,nombre=code,estado=True,
                max_sucursales_activas=maximum,
                permite_productos=True,permite_ventas=True,
                permite_reservas=True,permite_offline=True,
                permite_reportes_convencionales=True,
                permite_reportes_ia=ai))
        c.execute(insert(CIUDAD).values(id=1,estado=True))
        sid=1
        for company_id,(slug,plan_id,num) in enumerate(COMPANIES,start=1):
            c.execute(insert(EMPRESA).values(id=company_id,slug=slug,
                nombre_comercial=slug,estado=True))
            c.execute(insert(SUSCRIPCION).values(
                id=company_id,empresa_id=company_id,plan_saas_id=plan_id,
                fecha_inicio=today-timedelta(days=1),
                fecha_vencimiento=today+timedelta(days=365),estado="ACTIVA"))
            for index in range(num):
                c.execute(insert(SUCURSAL).values(
                    id=sid,empresa_id=company_id,ciudad_id=1,
                    nombre=f"{slug} {index+1}",direccion="Academica",
                    telefono=None,estado=True))
                sid+=1

    factory=sessionmaker(engine,expire_on_commit=False)
    estado={"empresa_id":2,"rol":"ADMINISTRADOR","contexto":"personal"}
    app=FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)
    app.include_router(router)

    def db_override():
        with factory() as session:
            yield session

    def tenant_override():
        company_id=estado["empresa_id"]
        slug=COMPANIES[company_id-1][0]
        return TenantContext(
            usuario_id=1000+company_id,empresa_id=company_id,
            empresa_slug=slug,empresa_nombre=slug,
            rol_id=3,rol_nombre=estado["rol"],
            contexto=estado["contexto"],
            plan_codigo="BASICO" if company_id in (2,4) else "PROFESIONAL",
            permite_reportes_ia=company_id not in (2,4),
        )

    monkeypatch.setattr(settings,"saas_auth_enabled",True)
    app.dependency_overrides[get_db]=db_override
    app.dependency_overrides[get_current_tenant_context]=tenant_override
    try:
        with TestClient(app) as client:
            yield client,estado,factory,app
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def contar(factory,empresa_id):
    with factory() as session:
        return len(session.execute(select(SUCURSAL.c.id).where(
            SUCURSAL.c.empresa_id==empresa_id,
            SUCURSAL.c.estado.is_(True))).all())


def crear(client,sufijo):
    return client.post("/tenant/empresa/sucursales",json={
        "ciudad_id":1,"nombre":f"Nueva {sufijo}","direccion":"Ficticia"
    })


def test_cinco_empresas_separadas_plan_y_sucursales(caso):
    client,estado,_,_=caso
    for company,(slug,plan,num) in enumerate(COMPANIES,start=1):
        estado["empresa_id"]=company
        datos=client.get("/tenant/empresa/me")
        assert datos.status_code==200,datos.text
        assert datos.json()["slug"]==slug
        assert datos.json()["suscripcion"]["sucursales_activas"]==num
        expected="BASICO" if plan==1 else "PROFESIONAL"
        assert datos.json()["suscripcion"]["codigo"]==expected
        sucursales=client.get("/tenant/empresa/sucursales")
        assert sucursales.status_code==200
        assert len(sucursales.json())==num
        assert all(slug in x["nombre"] for x in sucursales.json())
        assert "password_hash" not in datos.text


def test_consultar_suscripcion_vigente_y_capacidades(caso):
    client,_,_,_=caso
    respuesta=client.get("/tenant/empresa/suscripcion")
    assert respuesta.status_code==200
    data=respuesta.json()
    assert data["codigo"]=="BASICO"
    assert data["estado_suscripcion"]=="ACTIVA"
    assert data["permite_reportes_ia"] is False
    assert data["permite_reservas"] is True
    assert data["max_sucursales_activas"]==2


def test_basico_limite_2_backend_y_sin_comit_de_exceso(caso):
    client,_,factory,_=caso
    assert crear(client,"Uno").status_code==201
    assert contar(factory,2)==2
    otro=crear(client,"Dos")
    assert otro.status_code==409
    assert contar(factory,2)==2
    plan=client.get("/tenant/empresa/plan").json()
    assert plan["max_sucursales_activas"]==2
    assert plan["sucursales_activas"]==2


def test_profesional_limite_registrado_10_y_rechaza_11(caso):
    client, estado, _, _ = caso
    professional_company = None
    for company_id, _ in enumerate(COMPANIES, start=1):
        estado["empresa_id"] = company_id
        plan = client.get("/tenant/empresa/plan")
        assert plan.status_code == 200, plan.text
        if plan.json()["codigo"] == "PROFESIONAL":
            professional_company = company_id
            break

    assert professional_company is not None
    plan = client.get("/tenant/empresa/plan").json()
    maximum = plan["max_sucursales_activas"]
    active = plan["sucursales_activas"]
    assert maximum == 10
    assert active < maximum

    for index in range(active, maximum):
        response = crear(client, f"Profesional {index + 1}")
        assert response.status_code == 201, response.text

    assert client.get("/tenant/empresa/plan").json()["sucursales_activas"] == maximum
    rejected = crear(client, "Profesional 11")
    assert rejected.status_code == 409
    assert client.get("/tenant/empresa/plan").json()["sucursales_activas"] == maximum


def test_upgrade_y_downgrade_respeta_sucursales(caso):
    """El cambio de plan por autoservicio NO existe: CU02 lo reserva a superadmin.

    El contrato real es 503 en la ruta tenant. El limite del plan se sigue
    aplicando: con BASICO (2 sucursales) la segunda alta entra y la tercera no.
    """
    client,_,factory,_=caso
    assert client.patch("/tenant/empresa/plan",json={
        "codigo":" profesional "}).status_code==503
    assert crear(client,"Tres").status_code==201
    assert crear(client,"Cuatro").status_code==409
    assert contar(factory,2)==2
    plan=client.get("/tenant/empresa/plan").json()
    assert plan["codigo"]=="BASICO"
    assert plan["sucursales_activas"]==2


def test_cambio_de_plan_por_autoservicio_bloqueado(caso):
    client,estado,_,_=caso
    estado["empresa_id"]=2
    assert client.patch("/tenant/empresa/plan",json={
        "codigo":"PROFESIONAL"}).status_code==503
    assert client.patch("/tenant/empresa/plan",json={
        "codigo":"BASICO"}).status_code==503
    assert client.get("/tenant/empresa/plan").json()["codigo"]=="BASICO"


@pytest.mark.parametrize("ruta",["/tenant/empresa/plan",
    "/tenant/empresa/sucursales","/tenant/empresa/me"])
def test_contexto_cliente_puede_consultar_solo_propio(caso,ruta):
    client,estado,_,_=caso
    estado["contexto"]="cliente"
    estado["rol"]="CLIENTE"
    assert client.get(ruta).status_code==200


def test_contexto_cliente_no_puede_administrar(caso):
    client,estado,factory,_=caso
    estado["contexto"]="cliente"
    estado["rol"]="CLIENTE"
    assert crear(client,"X").status_code==403
    # La ruta de cambio de plan ni siquiera llega a la autorizacion: el gate la
    # cierra con 503 (CU02 exige superadmin global). Nunca se abre para el cliente.
    assert client.patch("/tenant/empresa/plan",json={
        "codigo":"PROFESIONAL"}).status_code==503
    assert contar(factory,2)==1


def test_patch_plan_tenant_sigue_fuera_de_la_allowlist(caso, monkeypatch):
    """Ni con el opt-in de superadmin se habilita el PATCH tenant de plan."""
    client,estado,_,_=caso
    monkeypatch.setattr(settings,"saas_superadmin_enabled",True)
    estado["rol"]="ADMINISTRADOR"
    assert client.patch("/tenant/empresa/plan",json={
        "codigo":"PROFESIONAL"}).status_code==503
    assert _ruta_saas_permitida("PATCH","/tenant/empresa/plan") is False


def test_no_permitir_editar_sucursal_ajena(caso):
    client,estado,factory,_=caso
    assert client.patch("/tenant/empresa/sucursales/1/estado",json={
        "estado":False}).status_code==404
    assert contar(factory,1)==4
    assert contar(factory,2)==1


def test_detalle_y_edicion_tenant_segura(caso):
    client, _, factory, _ = caso
    own = client.get("/tenant/empresa/sucursales").json()[0]["id"]
    detalle = client.get(f"/tenant/empresa/sucursales/{own}")
    assert detalle.status_code == 200
    assert detalle.json()["id"] == own
    actualizado = client.patch(
        f"/tenant/empresa/sucursales/{own}",
        json={"nombre": "Sucursal editada", "direccion": "Nueva direccion",
              "telefono": "70000000"},
    )
    assert actualizado.status_code == 200
    assert actualizado.json()["nombre"] == "Sucursal editada"
    assert client.get(f"/tenant/empresa/sucursales/{own}").json()[
        "direccion"
    ] == "Nueva direccion"
    assert contar(factory, 2) == 1


def test_detalle_y_edicion_ajena_no_revelan_datos(caso):
    client, estado, _, _ = caso
    estado["empresa_id"] = 1
    ajena = client.get("/tenant/empresa/sucursales").json()[0]["id"]
    estado["empresa_id"] = 2
    assert client.get(f"/tenant/empresa/sucursales/{ajena}").status_code == 404
    assert client.patch(
        f"/tenant/empresa/sucursales/{ajena}",
        json={"nombre": "No debe cambiar"},
    ).status_code == 404


@pytest.mark.parametrize("payload", [
    {"empresa_id": 1, "nombre": "No", "direccion": "X"},
    {"nombre": " ", "direccion": "X"},
    {"nombre": "Nuevo", "direccion": " "},
    {"ciudad_id": 999, "nombre": "Nuevo", "direccion": "X"},
])
def test_edicion_rechaza_tenant_inyectado_y_payload_invalido(caso, payload):
    client, _, _, _ = caso
    own = client.get("/tenant/empresa/sucursales").json()[0]["id"]
    assert client.patch(f"/tenant/empresa/sucursales/{own}",
                        json=payload).status_code in (404, 422)


def test_edicion_requiere_administrador(caso):
    client, estado, _, _ = caso
    estado["contexto"] = "cliente"
    estado["rol"] = "CLIENTE"
    own = client.get("/tenant/empresa/sucursales").json()[0]["id"]
    assert client.patch(f"/tenant/empresa/sucursales/{own}",
                        json={"nombre": "No"}).status_code == 403


def test_edicion_duplica_nombre_en_ciudad_y_allowlist_exacta(caso):
    client, _, _, _ = caso
    own, other = client.get("/tenant/empresa/sucursales").json()[0]["id"], None
    created = crear(client, "Segunda")
    assert created.status_code == 201
    other = created.json()["id"]
    existing_name = client.get(
        f"/tenant/empresa/sucursales/{own}"
    ).json()["nombre"]
    duplicate = client.patch(f"/tenant/empresa/sucursales/{other}", json={
        "nombre": existing_name,
    })
    assert duplicate.status_code == 409
    assert _ruta_saas_permitida(
        "GET", f"/tenant/empresa/sucursales/{own}"
    ) is True
    assert _ruta_saas_permitida(
        "PATCH", f"/tenant/empresa/sucursales/{own}"
    ) is True
    assert _ruta_saas_permitida(
        "GET", f"/tenant/empresa/sucursales/{own}/extra"
    ) is False


def test_no_habilitar_sucursal_que_excede_plan(caso):
    client,estado,factory,_=caso
    assert crear(client,"Unica Extra").status_code==201
    with factory.begin() as db:
        db.execute(insert(SUCURSAL).values(
            id=1000,empresa_id=2,ciudad_id=1,
            nombre="Inactiva",direccion="Demo",estado=False))
    resultado=client.patch("/tenant/empresa/sucursales/1000/estado",
                          json={"estado":True})
    assert resultado.status_code==409
    assert contar(factory,2)==2


def test_desactivar_sucursal_si_no_hay_operaciones(caso):
    client,_,factory,_=caso
    own=client.get("/tenant/empresa/sucursales").json()[0]["id"]
    resp=client.patch(f"/tenant/empresa/sucursales/{own}/estado",
                      json={"estado":False})
    assert resp.status_code==200
    assert contar(factory,2)==0
    assert client.patch(f"/tenant/empresa/sucursales/{own}/estado",
                         json={"estado":True}).status_code==200


def test_no_desactivar_con_stock_reservado(caso):
    client,_,factory,_=caso
    own=client.get("/tenant/empresa/sucursales").json()[0]["id"]
    with factory.begin() as db:
        db.execute(insert(INVENTARIO).values(
            id=100,empresa_id=2,sucursal_id=own,stock_reservado=3))
    assert client.patch(f"/tenant/empresa/sucursales/{own}/estado",json={
        "estado":False}).status_code==409
    assert contar(factory,2)==1


def test_404_ciudad_inexistente_y_sin_crear(caso):
    client,_,factory,_=caso
    resp=client.post("/tenant/empresa/sucursales",json={
        "ciudad_id":999,"nombre":"Sin ciudad","direccion":"X"})
    assert resp.status_code==404
    assert contar(factory,2)==1


@pytest.mark.parametrize("payload",[
    {"empresa_id":1,"ciudad_id":1,"nombre":"X","direccion":"X"},
    {"ciudad_id":1,"nombre":" ","direccion":"Y"},
    {"ciudad_id":1,"nombre":"X","direccion":" "},
    {"ciudad_id":0,"nombre":"X","direccion":"Y"},
])
def test_422_body_invalido_o_tenant_inyectado(caso,payload):
    client,_,factory,_=caso
    assert client.post("/tenant/empresa/sucursales",json=payload).status_code==422
    assert contar(factory,2)==1


def test_suscripcion_expirada_deniega_operaciones(caso):
    client,_,factory,_=caso
    with factory.begin() as db:
        db.execute(SUSCRIPCION.update().where(
            SUSCRIPCION.c.empresa_id==2).values(
                fecha_vencimiento=_hoy()-timedelta(days=1)))
    assert client.get("/tenant/empresa/plan").status_code==403
    assert crear(client,"No").status_code==403


@pytest.mark.parametrize("metodo,ruta",[
    ("POST","/tenant/empresa"),("DELETE","/tenant/empresa"),
    ("PATCH","/tenant/empresa/plan/extra"),
    ("DELETE","/tenant/empresa/sucursales/2"),
    ("PATCH","/tenant/empresa/sucursales/02/estado"),
    ("POST","/tenant/reservas"),
])
def test_no_se_abren_rutas_extra(caso,metodo,ruta):
    client,_,_,_=caso
    assert client.request(metodo,ruta,json={}).status_code==503
