"""CU01 primer administrador: SQLite ':memory:' solamente, no PostgreSQL."""
import ast
from datetime import date,timedelta
from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from sqlalchemy import create_engine,event,text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT/"app/modules/sucursales/repositories/saas_initial_admin_repository.py"
ROUTER=ROOT/"app/modules/sucursales/api/saas_superadmin_router.py"
GATE=ROOT/"app/core/saas_route_gate.py"
s=spec_from_file_location("test_only_cu01_bootstrap",REPO)
mod=module_from_spec(s)
s.loader.exec_module(mod)
registrar=mod.registrar_primer_administrador
Error=mod.SuperadminSaaSError


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool)
    @event.listens_for(engine,"connect")
    def on_connect(conn,record):
        conn.execute("ATTACH DATABASE ':memory:' AS public")
    defs=[
        "CREATE TABLE public.empresa(id INTEGER PRIMARY KEY,estado BOOLEAN)",
        "CREATE TABLE public.plan_saas(id INTEGER PRIMARY KEY,"
          "max_sucursales_activas INTEGER,estado BOOLEAN)",
        "CREATE TABLE public.suscripcion(empresa_id INTEGER,plan_saas_id INTEGER,"
          "estado TEXT,fecha_inicio DATE,fecha_vencimiento DATE)",
        "CREATE TABLE public.ciudad(id INTEGER PRIMARY KEY,estado BOOLEAN)",
        "CREATE TABLE public.sucursal(id INTEGER PRIMARY KEY,"
          "empresa_id INTEGER,ciudad_id INTEGER,nombre TEXT,direccion TEXT,"
          "telefono TEXT,estado BOOLEAN)",
        "CREATE TABLE public.rol(id INTEGER PRIMARY KEY,nombre TEXT,"
          "estado BOOLEAN)",
        "CREATE TABLE public.usuario(id INTEGER PRIMARY KEY,rol_id INTEGER,"
          "correo TEXT UNIQUE,password_hash TEXT,fecha_creacion TIMESTAMP,"
          "estado BOOLEAN)",
        "CREATE TABLE public.empleado(id INTEGER PRIMARY KEY,"
          "empresa_id INTEGER,usuario_id INTEGER,sucursal_id INTEGER,"
          "nombres TEXT,apellidos TEXT,ci TEXT,telefono TEXT,"
          "fecha_contratacion DATE,estado BOOLEAN)",
        "CREATE TABLE public.usuario_empresa(id INTEGER PRIMARY KEY,"
          "empresa_id INTEGER,usuario_id INTEGER,rol_id INTEGER,"
          "estado BOOLEAN)",
    ]
    with Session(engine) as session:
        for ddl in defs:
            session.execute(text(ddl))
        start=(date.today()-timedelta(days=1)).isoformat()
        end=(date.today()+timedelta(days=60)).isoformat()
        session.execute(text("INSERT INTO public.empresa VALUES (1,1),(2,1)"))
        session.execute(text("INSERT INTO public.plan_saas VALUES (1,2,1)"))
        session.execute(text("""INSERT INTO public.suscripcion VALUES
          (1,1,'ACTIVA',:start,:end),
          (2,1,'ACTIVA',:start,:end)"""),
          {"start":start,"end":end})
        session.execute(text("INSERT INTO public.ciudad VALUES (1,1),(2,0)"))
        session.execute(text("""INSERT INTO public.rol VALUES
          (1,'CLIENTE',1),(2,'ADMINISTRADOR',1),(3,'CAJERO',1)"""))
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


PASSWORD="ClavePrueba#2026"


def payload(**overrides):
    data={"empresa_id":1,"correo":" Admin@Ejemplo.invalid ","password":PASSWORD,
        "nombres":" Ana ","apellidos":" Demo ","ci":"CI-FICTICIO-100",
        "ciudad_id":1,"nombre_sucursal":"Casa Matriz",
        "direccion_sucursal":"Av. Ficticia 123","telefono":None}
    data.update(overrides)
    return data


def count(db,tabla):
    return db.execute(text(f"SELECT count(*) FROM public.{tabla}")).scalar_one()


def test_cu01_bootstrap_crea_4_registros_y_sucursal_en_tenant(db):
    data=registrar(db,**payload())
    db.commit()
    assert data["empresa_id"]==1
    assert data["correo"]=="admin@ejemplo.invalid"
    assert data["rol_empresa"]=="ADMINISTRADOR"
    assert set(data)=={"empresa_id","usuario_id","empleado_id",
                       "membresia_id","sucursal_id","correo","rol_empresa"}
    for name in ("sucursal","usuario","empleado","usuario_empresa"):
        assert count(db,name)==1
    assert db.execute(text("SELECT empresa_id FROM public.sucursal")).scalar_one()==1
    assert db.execute(text("SELECT empresa_id FROM public.empleado")).scalar_one()==1
    assert db.execute(text("SELECT empresa_id FROM public.usuario_empresa")).scalar_one()==1
    assert db.execute(text("SELECT rol_id FROM public.usuario")).scalar_one()==1
    assert db.execute(text("SELECT rol_id FROM public.usuario_empresa")).scalar_one()==2
    stored=db.execute(text("SELECT password_hash FROM public.usuario")).scalar_one()
    assert stored!=PASSWORD and stored.startswith("$argon2")
    assert PasswordHasher().verify(stored,PASSWORD) is True
    assert "password_hash" not in data and "password" not in data


def test_cu01_bootstrap_solo_se_puede_hacer_una_vez(db):
    registrar(db,**payload())
    with pytest.raises(Error) as exc:
        registrar(db,**payload(correo="otro@ejemplo.invalid",ci="OTRO-CI"))
    assert exc.value.codigo==409
    assert count(db,"sucursal")==1 and count(db,"usuario_empresa")==1


def test_cu01_bootstrap_no_otorga_rol_admin_global(db):
    registrar(db,**payload())
    global_role=db.execute(text("SELECT rol_id FROM public.usuario")).scalar_one()
    local_role=db.execute(text("SELECT rol_id FROM public.usuario_empresa")).scalar_one()
    assert global_role==1 and local_role==2 and global_role!=local_role


def test_cu01_bootstrap_rechaza_password_debil_y_no_inserta(db):
    with pytest.raises(Error) as exc:
        registrar(db,**payload(password="abc123"))
    assert exc.value.codigo==422
    assert count(db,"usuario")==0 and count(db,"sucursal")==0


def test_cu01_bootstrap_rechaza_ciudad_inactiva_y_no_inserta(db):
    with pytest.raises(Error) as exc:
        registrar(db,**payload(ciudad_id=2))
    assert exc.value.codigo==404
    assert count(db,"usuario_empresa")==0 and count(db,"empleado")==0


def test_cu01_bootstrap_rechaza_empresa_que_no_existe(db):
    with pytest.raises(Error) as exc:
        registrar(db,**payload(empresa_id=999))
    assert exc.value.codigo==404


def test_cu01_bootstrap_rechaza_correo_ya_registrado(db):
    db.execute(text("""INSERT INTO public.usuario
       (rol_id,correo,password_hash,fecha_creacion,estado)
       VALUES(1,'admin@ejemplo.invalid','ficticio','2026-10-25',1)"""))
    with pytest.raises(Error) as exc:
        registrar(db,**payload())
    assert exc.value.codigo==409
    assert count(db,"sucursal")==0


def test_cu01_bootstrap_rechaza_roles_inactivos(db):
    db.execute(text("UPDATE public.rol SET estado=0 WHERE nombre='ADMINISTRADOR'"))
    with pytest.raises(Error) as exc:
        registrar(db,**payload())
    assert exc.value.codigo==409
    assert count(db,"usuario_empresa")==0


def test_cu01_bootstrap_rechaza_membresia_o_sucursal_previa(db):
    db.execute(text("""INSERT INTO public.usuario_empresa
        (empresa_id,usuario_id,rol_id,estado) VALUES (1,77,1,1)"""))
    with pytest.raises(Error) as exc:
        registrar(db,**payload())
    assert exc.value.codigo==409
    db.execute(text("DELETE FROM public.usuario_empresa"))
    db.execute(text("""INSERT INTO public.sucursal
       (empresa_id,ciudad_id,nombre,direccion,estado)
       VALUES(1,1,'Anterior','Ficticia',1)"""))
    with pytest.raises(Error) as exc2:
        registrar(db,**payload())
    assert exc2.value.codigo==409


def test_cu01_bootstrap_no_altera_segunda_empresa(db):
    registrar(db,**payload())
    db.commit()
    assert db.execute(text("""SELECT count(*) FROM public.usuario_empresa
         WHERE empresa_id=2""")).scalar_one()==0
    assert db.execute(text("""SELECT count(*) FROM public.sucursal
         WHERE empresa_id=2""")).scalar_one()==0
    assert db.execute(text("""SELECT count(*) FROM public.empresa
         WHERE id=2 AND estado=1""")).scalar_one()==1


def test_cu01_bootstrap_rechaza_suscripcion_vencida(db):
    db.execute(text("""UPDATE public.suscripcion
       SET fecha_vencimiento='2020-01-01' WHERE empresa_id=1"""))
    with pytest.raises(Error) as exc:
        registrar(db,**payload())
    assert exc.value.codigo==409


def test_cu01_bootstrap_router_y_password_sin_respuesta():
    router=ROUTER.read_text(encoding="utf-8")
    source=REPO.read_text(encoding="utf-8")
    assert ast.parse(router)
    assert ast.parse(source)
    assert '"/empresas/{empresa_id}/administrador-inicial"' in router
    assert "dependencies=[Depends(get_superadmin_saas)]" in router
    assert "registrar_primer_administrador(" in router
    assert 'Field(min_length=8,max_length=128,repr=False)' in router
    assert 'model_config=ConfigDict(extra="forbid")' in router
    assert "_hasher.hash(password)" in source
    assert '"password_hash":' not in source
    assert 'global CLIENTE' not in source or 'rol legacy' in source


def test_cu01_bootstrap_gate_allowlist_exacta_y_doble_optin():
    source=GATE.read_text(encoding="utf-8")
    tree=ast.parse(source)
    defs=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(
           isinstance(n,ast.Name) and n.id.startswith(
               ("_RUTA","_RECURSOS","_DETALLE"))
           for n in node.targets):
            defs.append(node)
        if isinstance(node,ast.FunctionDef) and node.name=="_ruta_saas_permitida":
            defs.append(node)
    cfg=SimpleNamespace(saas_reservas_enabled=False,
                        saas_superadmin_enabled=True)
    scope={"settings":cfg,"re":re,"frozenset":frozenset}
    exec(compile(ast.Module(body=defs,type_ignores=[]),
                str(GATE),"exec"),scope)
    gate=scope["_ruta_saas_permitida"]
    assert gate("POST","/saas/superadmin/empresas/1/administrador-inicial")
    for method,path in (
      ("GET","/saas/superadmin/empresas/1/administrador-inicial"),
      ("POST","/saas/superadmin/empresas/0/administrador-inicial"),
      ("POST","/saas/superadmin/empresas/2/administrador-inicial/extra"),
      ("POST","/saas/superadmin/empresas/-3/administrador-inicial"),
      ("PATCH","/saas/superadmin/empresas/1/administrador-inicial")):
        assert not gate(method,path)
    cfg.saas_superadmin_enabled=False
    assert not gate("POST","/saas/superadmin/empresas/1/administrador-inicial")
