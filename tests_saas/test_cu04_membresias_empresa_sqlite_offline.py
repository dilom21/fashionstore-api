"""CU04 SaaS: pruebas de negocio contra SQLite efímero, sin DB comercial.

No usar app.main, get_db, .env ni los puertos 5432/55432/55433.
Importar por ruta SOLO el repositorio puro SQLAlchemy Core.
El schema public se simula con ATTACH DATABASE ':memory:'.
"""
import ast
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT/"app/modules/usuarios/repositories/tenant_membership_repository.py"
API=ROOT/"app/modules/usuarios/api/tenant_membership_router.py"
MAIN=ROOT/"app/main.py"
GATE=ROOT/"app/core/saas_route_gate.py"
spec=spec_from_file_location("cu04_tenant_membership_test_module",REPO)
module=module_from_spec(spec)
spec.loader.exec_module(module)
Repo=module.GestionMembresiasTenantRepository
Error=module.GestionMembresiaTenantError


@pytest.fixture
def session():
    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool)
    @event.listens_for(engine,"connect")
    def attach(conn,record):
        conn.execute("ATTACH DATABASE ':memory:' AS public")
    ddl=[
      "CREATE TABLE public.empresa(id INTEGER PRIMARY KEY,estado BOOLEAN)",
      "CREATE TABLE public.rol(id INTEGER PRIMARY KEY,nombre TEXT,estado BOOLEAN)",
      "CREATE TABLE public.usuario(id INTEGER PRIMARY KEY,correo TEXT,estado BOOLEAN)",
      "CREATE TABLE public.usuario_empresa(id INTEGER PRIMARY KEY,"
       "empresa_id INTEGER,usuario_id INTEGER,rol_id INTEGER,estado BOOLEAN)",
      "CREATE TABLE public.empleado(id INTEGER PRIMARY KEY,"
       "empresa_id INTEGER,usuario_id INTEGER,sucursal_id INTEGER,"
       "nombres TEXT,apellidos TEXT,estado BOOLEAN)",
      "CREATE TABLE public.sucursal(id INTEGER PRIMARY KEY,"
       "empresa_id INTEGER,estado BOOLEAN)",
    ]
    with Session(engine) as db:
        for item in ddl:
            db.execute(text(item))
        for name,rows in {
          "empresa":[(1,1),(2,1)],
          "rol":[(1,"ADMINISTRADOR",1),(2,"CAJERO",1),
                 (3,"ENCARGADO_SUCURSAL",1),(4,"CLIENTE",1),
                 (5,"AUDITOR_DE_EMPRESA_B",1),(6,"INACTIVO",0)],
          "usuario":[(101,"admin.a@demo.invalid",1),
                     (102,"cajero.a@demo.invalid",1),
                     (103,"segundo.a@demo.invalid",1),
                     (104,"sin.empleado@demo.invalid",1),
                     (201,"admin.b@demo.invalid",1)],
          "usuario_empresa":[(1,1,101,1,1),(2,1,102,2,1),
                             (3,1,103,1,0),(4,1,104,2,1),
                             (5,2,201,5,1)],
          "empleado":[(11,1,101,10,"Ana","A",1),
                      (12,1,102,10,"Beto","A",1),
                      (13,1,103,10,"Ceci","A",1),
                      (21,2,201,20,"Dora","B",1)],
          "sucursal":[(10,1,1),(20,2,1)],
        }.items():
            marks=",".join("(:"+str(i)+")" for i in range(len(rows[0])))
            for row in rows:
                args={str(i):x for i,x in enumerate(row)}
                db.execute(text("INSERT INTO public."+name+" VALUES("+marks+")"),
                           args)
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def test_cu04_listar_solamente_membresias_de_su_tenant(session):
    a=Repo.listar(session,1)
    b=Repo.listar(session,2)
    assert {r["usuario_id"] for r in a}=={101,102,103,104}
    assert {r["usuario_id"] for r in b}=={201}
    assert not any("admin.b@" in r["correo"] for r in a)
    assert all("password_hash" not in r for r in a)
    assert a[0]["rol"]=="ADMINISTRADOR"


def test_cu04_roles_no_revelan_custom_de_otro_tenant(session):
    visible=Repo.roles_disponibles(session,1)
    nombres={x["nombre"] for x in visible}
    assert "ADMINISTRADOR" in nombres
    assert "CAJERO" in nombres
    assert "ENCARGADO_SUCURSAL" in nombres
    assert "CLIENTE" in nombres
    assert "AUDITOR_DE_EMPRESA_B" not in nombres
    assert "INACTIVO" not in nombres


def test_cu04_cambiar_rol_empresa_propia_y_no_global(session):
    result=Repo.cambiar_rol(session,empresa_id=1,actor_id=101,
                           usuario_id=102,rol_id=3)
    assert result["rol"]=="ENCARGADO_SUCURSAL"
    session.commit()
    nuevo=session.execute(text("""SELECT rol_id FROM public.usuario_empresa
        WHERE empresa_id=1 AND usuario_id=102""")).scalar_one()
    assert nuevo==3
    global_id=session.execute(text("""SELECT nombre FROM public.rol
        WHERE id=3""")).scalar_one()
    assert global_id=="ENCARGADO_SUCURSAL"
    other=session.execute(text("""SELECT rol_id FROM public.usuario_empresa
        WHERE empresa_id=2 AND usuario_id=201""")).scalar_one()
    assert other==5


def test_cu04_no_cambia_rol_ni_estado_de_otro_tenant(session):
    with pytest.raises(Error) as err:
        Repo.cambiar_rol(session,empresa_id=1,actor_id=101,
                        usuario_id=201,rol_id=2)
    assert err.value.codigo==404
    with pytest.raises(Error) as err2:
        Repo.cambiar_estado(session,empresa_id=1,actor_id=101,
                           usuario_id=201,estado=False)
    assert err2.value.codigo==404
    assert session.execute(text("""SELECT estado FROM public.usuario_empresa
        WHERE usuario_id=201""")).scalar_one()==1


def test_cu04_admin_no_se_auto_revoca(session):
    with pytest.raises(Error) as exc:
        Repo.cambiar_estado(session,empresa_id=1,actor_id=101,
                           usuario_id=101,estado=False)
    assert exc.value.codigo==403
    with pytest.raises(Error) as exc2:
        Repo.cambiar_rol(session,empresa_id=1,actor_id=101,
                        usuario_id=101,rol_id=2)
    assert exc2.value.codigo==403


def test_cu04_no_deja_empresa_sin_admin(session):
    with pytest.raises(Error) as exc:
        Repo.cambiar_estado(session,empresa_id=1,actor_id=201,
                           usuario_id=101,estado=False)
    assert exc.value.codigo==409
    with pytest.raises(Error) as exc2:
        Repo.cambiar_rol(session,empresa_id=1,actor_id=201,
                        usuario_id=101,rol_id=2)
    assert exc2.value.codigo==409
    assert session.execute(text("""SELECT estado,rol_id
      FROM public.usuario_empresa WHERE empresa_id=1 AND usuario_id=101
    """)).one()==(1,1)


def test_cu04_revoca_usuario_normal_no_afecta_otras_empresas(session):
    r=Repo.cambiar_estado(session,empresa_id=1,actor_id=101,
                         usuario_id=102,estado=False)
    assert r["membresia_activa"] is False
    session.commit()
    assert session.execute(text("""SELECT estado FROM public.usuario_empresa
       WHERE empresa_id=1 AND usuario_id=102""")).scalar_one()==0
    assert session.execute(text("""SELECT estado FROM public.usuario_empresa
       WHERE empresa_id=2 AND usuario_id=201""")).scalar_one()==1


def test_cu04_no_asigna_rol_clientes_ni_inactivos(session):
    for rol_id in (4,5,6,999):
        with pytest.raises(Error) as exc:
            Repo.cambiar_rol(session,empresa_id=1,actor_id=101,
                            usuario_id=102,rol_id=rol_id)
        assert exc.value.codigo==422


def test_cu04_no_asigna_rol_personal_sin_empleado(session):
    with pytest.raises(Error) as exc:
        Repo.cambiar_rol(session,empresa_id=1,actor_id=101,
                        usuario_id=104,rol_id=3)
    assert exc.value.codigo==409


def test_cu04_empresa_inactiva_falla_cerrada(session):
    session.execute(text("UPDATE public.empresa SET estado=0 WHERE id=1"))
    with pytest.raises(Error) as exc:
        Repo.listar(session,1)
    assert exc.value.codigo==404


def test_cu04_router_monta_solo_dependencia_admin_y_sin_empresa_en_body():
    src=API.read_text(encoding="utf-8")
    assert ast.parse(src)
    assert "get_current_tenant_admin" in src
    assert "admin.empresa_id" in src
    assert "actor_id=admin.usuario_id" in src
    assert "class Estricto(BaseModel)" in src
    assert 'ConfigDict(extra="forbid")' in src
    assert "empresa_id:int" not in src
    assert '"/usuarios"' in src and '"/roles"' in src
    assert '"/usuarios/{usuario_id}/rol"' in src
    assert '"/usuarios/{usuario_id}/estado"' in src
    main=MAIN.read_text(encoding="utf-8")
    assert "app.include_router(tenant_miembros_router)" in main


def test_cu04_allowlist_solo_cuatro_rutas_exactas_no_legacy():
    # Extraer la función y sus constantes sin importar settings ni app.
    src=GATE.read_text(encoding="utf-8")
    tree=ast.parse(src)
    body=[]
    for node in tree.body:
        # La función reutiliza también las constantes de recursos
        # comerciales y de detalle. Ejecutar la función aislada exige
        # incluirlas en el namespace del test (sin importar app/settings).
        if isinstance(node,ast.Assign) and any(
            isinstance(x,ast.Name) and x.id.startswith(
                ("_RUTA", "_RECURSOS", "_DETALLE")
            )
            for x in node.targets):
            body.append(node)
        if isinstance(node,ast.FunctionDef) and node.name=="_ruta_saas_permitida":
            body.append(node)
    namespace={"re":re,"frozenset":frozenset,
               "settings":SimpleNamespace(saas_reservas_enabled=False,
                   saas_superadmin_enabled=False)}
    exec(compile(ast.Module(body=body,type_ignores=[]),
                 str(GATE),"exec"),namespace)
    gate=namespace["_ruta_saas_permitida"]
    for url in ("/tenant/empresa/usuarios","/tenant/empresa/roles"):
        assert gate("GET",url)
        assert not gate("POST",url)
    for url in ("/tenant/empresa/usuarios/101/rol",
                "/tenant/empresa/usuarios/101/estado"):
        assert gate("PATCH",url)
        assert not gate("GET",url)
        assert not gate("DELETE",url)
    for url in ("/tenant/empresa/usuarios/0/estado",
                "/tenant/empresa/usuarios/-1/rol",
                "/tenant/empresa/usuarios/101/rol/extra",
                "/usuarios","/roles","/usuarios/101"):
        assert not gate("PATCH",url)


def test_cu04_no_se_abre_saas_automaticamente():
    router=API.read_text(encoding="utf-8")
    repo=REPO.read_text(encoding="utf-8")
    gate=GATE.read_text(encoding="utf-8")
    assert "SAAS_AUTH_ENABLED=true" not in router+repo+gate
    assert "session_user" not in router+repo
    assert "get_current_tenant_admin" in router
    assert "NOLOGIN" not in repo
    assert "no es por sí solo RLS SQL" in repo
