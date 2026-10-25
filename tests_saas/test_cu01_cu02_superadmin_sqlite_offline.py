"""CU01/CU02: pruebas funcionales en SQLite ':memory:', sin PostgreSQL."""
import ast
import hashlib
from importlib.util import module_from_spec,spec_from_file_location
from datetime import date
from pathlib import Path
import re
import secrets
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,event,text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT/"app/modules/sucursales/repositories/saas_superadmin_repository.py"
API=ROOT/"app/modules/sucursales/api/saas_superadmin_router.py"
AUTH=ROOT/"app/core/saas_superadmin_auth.py"
CONFIG=ROOT/"app/core/config.py"
GATE=ROOT/"app/core/saas_route_gate.py"
MAIN=ROOT/"app/main.py"
spec=spec_from_file_location("cu01_02_pure_repo",REPO)
module=module_from_spec(spec)
spec.loader.exec_module(module)
Repo=module.SuperadminSaaSRepository
Error=module.SuperadminSaaSError


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool)
    @event.listens_for(engine,"connect")
    def attach(connection,record):
        connection.execute("ATTACH DATABASE ':memory:' AS public")
    sqls=[
      """CREATE TABLE public.empresa(
        id INTEGER PRIMARY KEY, slug TEXT UNIQUE, razon_social TEXT,
        nombre_comercial TEXT, nit TEXT,correo_contacto TEXT,
        telefono TEXT, estado BOOLEAN)""",
      """CREATE TABLE public.plan_saas(
        id INTEGER PRIMARY KEY,codigo TEXT UNIQUE,nombre TEXT,
        descripcion TEXT,max_sucursales_activas INTEGER,
        permite_productos BOOLEAN,permite_ventas BOOLEAN,
        permite_reservas BOOLEAN,permite_offline BOOLEAN,
        permite_reportes_convencionales BOOLEAN,permite_reportes_ia BOOLEAN,
        estado BOOLEAN)""",
      """CREATE TABLE public.suscripcion(
        id INTEGER PRIMARY KEY,empresa_id INTEGER,plan_saas_id INTEGER,
        fecha_inicio DATE,fecha_vencimiento DATE,estado TEXT,
        observacion TEXT)""",
      """CREATE UNIQUE INDEX public.uq_sub_activa
         ON suscripcion(empresa_id) WHERE estado='ACTIVA'""",
      "CREATE TABLE public.empresa_configuracion(empresa_id INTEGER PRIMARY KEY)",
    ]
    with Session(engine) as db:
        for ddl in sqls:
            db.execute(text(ddl))
        db.execute(text("""INSERT INTO public.plan_saas VALUES
            (1,'BASICO','Básico',NULL,2,1,1,1,1,1,0,1),
            (2,'PROFESIONAL','Pro',NULL,10,1,1,1,1,1,1,1),
            (3,'INACTIVO','Off',NULL,1,0,0,0,0,0,0,0)"""))
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def alta(db,slug="empresa-a",plan_id=1):
    return Repo.crear_empresa(db,
       razon_social="Empresa Ficticia",
       nombre_comercial="Tienda Demo",slug=slug,nit=None,
       correo_contacto=None,telefono=None,plan_id=plan_id,
       fecha_inicio=date(2026,10,25),fecha_vencimiento=date(2026,11,25))


def test_cu01_alta_empresa_suscripcion_y_config_atómicas(db):
    row=alta(db)
    db.commit()
    eid=row["empresa"]["id"]
    assert row["empresa"]["slug"]=="empresa-a"
    assert row["empresa"]["estado"] is True
    assert row["suscripcion"]["empresa_id"]==eid
    assert row["suscripcion"]["estado"]=="ACTIVA"
    assert db.execute(text(
        "SELECT count(*) FROM public.empresa_configuracion WHERE empresa_id=:x"
        ),{"x":eid}).scalar_one()==1
    assert len(Repo.listar_empresas(db))==1


def test_cu01_rechaza_slug_repetido_o_plan_inactivo(db):
    alta(db)
    with pytest.raises(Error) as e:
        alta(db)
    assert e.value.codigo==409
    with pytest.raises(Error) as e:
        alta(db,"otra-empresa",3)
    assert e.value.codigo==404
    assert len(Repo.listar_empresas(db))==1


def test_cu01_desactivar_empresa_no_toca_otra(db):
    a=alta(db)["empresa"]["id"]
    b=alta(db,"empresa-b")["empresa"]["id"]
    assert Repo.cambiar_estado_empresa(db,a,False)["estado"] is False
    assert Repo.cambiar_estado_empresa(db,b,True)["estado"] is True
    assert len(Repo.listar_empresas(db))==2
    assert db.execute(text("SELECT count(*) FROM public.suscripcion")).scalar_one()==2


def test_cu01_fecha_de_inicio_posterior_no_permitida(db):
    with pytest.raises(Error) as e:
        Repo.crear_empresa(db,razon_social="A",nombre_comercial="B",
          slug="a",nit=None,correo_contacto=None,telefono=None,plan_id=1,
          fecha_inicio=date(2026,12,1),fecha_vencimiento=date(2026,11,1))
    assert e.value.codigo==422


def test_cu02_crear_plan_y_rechazar_codigo_repetido(db):
    data=dict(codigo="PYME",nombre="Pyme",descripcion=None,
      max_sucursales_activas=5,permite_productos=True,permite_ventas=True,
      permite_reservas=True,permite_offline=True,
      permite_reportes_convencionales=True,permite_reportes_ia=False)
    created=Repo.crear_plan(db,data)
    assert created["codigo"]=="PYME"
    assert created["estado"] is True
    with pytest.raises(Error) as exc:
        Repo.crear_plan(db,data)
    assert exc.value.codigo==409
    assert len(Repo.listar_planes(db))==4


def test_cu02_plan_activo_con_suscripciones_no_desactivable(db):
    alta(db)
    with pytest.raises(Error) as e:
        Repo.cambiar_estado_plan(db,1,False)
    assert e.value.codigo==409
    assert Repo.cambiar_estado_plan(db,2,False)["estado"] is False


def test_cu02_reasignar_con_historial_y_un_solo_plan_activo(db):
    company=alta(db)["empresa"]["id"]
    nueva=Repo.asignar_suscripcion(db,empresa_id=company,plan_id=2,
      fecha_inicio=date(2026,10,26),fecha_vencimiento=date(2026,12,26))
    db.commit()
    assert nueva["plan_saas_id"]==2
    rows=Repo.listar_suscripciones(db,company)
    assert len(rows)==2
    assert {s["estado"] for s in rows}=={"ACTIVA","CANCELADA"}
    assert len([s for s in rows if s["estado"]=="ACTIVA"])==1


def test_cu02_rechazar_suscripcion_de_empresa_ajena(db):
    a=alta(db)["empresa"]["id"]
    b=alta(db,"empresa-b")["empresa"]["id"]
    foreign=Repo.listar_suscripciones(db,b)[0]["id"]
    with pytest.raises(Error) as e:
        Repo.cambiar_estado_suscripcion(db,empresa_id=a,
          suscripcion_id=foreign,estado="SUSPENDIDA")
    assert e.value.codigo==404
    assert Repo.listar_suscripciones(db,b)[0]["estado"]=="ACTIVA"


def test_cu02_suspension_sin_reactivacion_automatica(db):
    company=alta(db)["empresa"]["id"]
    sub=Repo.listar_suscripciones(db,company)[0]["id"]
    result=Repo.cambiar_estado_suscripcion(db,empresa_id=company,
       suscripcion_id=sub,estado="SUSPENDIDA")
    assert result["estado"]=="SUSPENDIDA"
    with pytest.raises(Error) as e:
        Repo.cambiar_estado_suscripcion(db,empresa_id=company,
           suscripcion_id=sub,estado="ACTIVA")
    assert e.value.codigo==409
    with pytest.raises(Error) as e:
        Repo.cambiar_estado_suscripcion(db,empresa_id=company,
           suscripcion_id=sub,estado="CANCELADA")
    assert e.value.codigo==409


def test_cu02_asignacion_fecha_incorrecta_no_cancela_vigente(db):
    company=alta(db)["empresa"]["id"]
    with pytest.raises(Error) as e:
        Repo.asignar_suscripcion(db,empresa_id=company,plan_id=2,
           fecha_inicio=date(2026,11,10),
           fecha_vencimiento=date(2026,11,9))
    assert e.value.codigo==422
    assert Repo.listar_suscripciones(db,company)[0]["estado"]=="ACTIVA"


def _get_superadmin_function():
    tree=ast.parse(AUTH.read_text(encoding="utf-8"))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
            and n.name=="get_superadmin_saas")
    # Evitar evaluación de Header() en la anotación/default al extraer:
    # importar los componentes originales de FastAPI en el namespace.
    namespace={
       "Header":lambda default=None:default,
       "HTTPException":HTTPException,"hashlib":hashlib,
       "secrets":secrets,"re":re,
       "settings":SimpleNamespace(saas_auth_enabled=True,
                                 saas_superadmin_enabled=True,
                                 saas_superadmin_key_sha256=None)
    }
    exec(compile(ast.Module(body=[fn],type_ignores=[]),
                 str(AUTH),"exec"),namespace)
    return namespace["get_superadmin_saas"],namespace["settings"]


def test_cu01_02_superadmin_no_acepta_jwt_tenant_o_clave_incorrecta():
    f,settings=_get_superadmin_function()
    secret="clave-superadmin-de-pruebas-muy-larga-aleatoria-abcdef12345"
    settings.saas_superadmin_key_sha256=hashlib.sha256(
        secret.encode()).hexdigest()
    assert f(secret) is True
    for value in (None,"token.jwt.falso","abc","X"*48):
        with pytest.raises(HTTPException) as exc:
            f(value)
        assert exc.value.status_code==401


def test_cu01_02_doble_flag_apagado_da_503():
    f,settings=_get_superadmin_function()
    secret="clave-superadmin-de-pruebas-muy-larga-aleatoria-abcdef12345"
    settings.saas_superadmin_key_sha256=hashlib.sha256(
        secret.encode()).hexdigest()
    settings.saas_auth_enabled=False
    with pytest.raises(HTTPException) as exc:
        f(secret)
    assert exc.value.status_code==503
    settings.saas_auth_enabled=True
    settings.saas_superadmin_enabled=False
    with pytest.raises(HTTPException) as exc:
        f(secret)
    assert exc.value.status_code==503
    settings.saas_superadmin_enabled=True
    settings.saas_superadmin_key_sha256=None
    with pytest.raises(HTTPException) as exc:
        f(secret)
    assert exc.value.status_code==503


def test_cu01_02_router_global_requiere_auth_sin_empresa_id_en_header():
    source=API.read_text(encoding="utf-8")
    assert ast.parse(source)
    assert "Depends(get_superadmin_saas)" in source
    assert 'prefix="/saas/superadmin"' in source
    assert "get_current_tenant_admin" not in source
    assert "emitir_token_empresa" not in source
    assert (source.count("@router.get(")+source.count("@router.post(")
            +source.count("@router.patch("))==9
    assert "app.include_router(saas_superadmin_router)" in MAIN.read_text(
        encoding="utf-8")
    assert "saas_superadmin_enabled: bool = False" in CONFIG.read_text(
        encoding="utf-8")


def test_cu01_02_gate_separa_admin_tenant_de_global():
    source=GATE.read_text(encoding="utf-8")
    assert ast.parse(source)
    assert "_RUTAS_SAAS_SUPERADMIN" in source
    assert "settings.saas_superadmin_enabled" in source
    assert '("PATCH", "/tenant/empresa/plan")' not in source
    assert "re.compile(" in source
    assert "saas_auth_enabled" in source
    assert "No es reemplazo de RLS" in source
