"""5AU: el adaptador de reservas falla cerrado sin identidad SQL individual.

Pruebas unitarias sin PG/Supabase ni credenciales. Un FakeDB entrega
metadata mínima; jamás se cambia el contenido de la base del estudiante.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"
os.environ["SAAS_RESERVAS_ENABLED"] = "false"

import pytest

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.modules.reservas.services import tenant_reserva_transaccional_5al as servicio

OWNER = "fashionstore_actor_guard_owner"
PRINCIPAL = "fashionstore_sql_actor_101"


def _tenant(usuario=101,empresa=3):
    return TenantContext(
        usuario_id=usuario, empresa_id=empresa, empresa_slug="empresa-lab-a",
        empresa_nombre="Ficticia A", rol_id=7, rol_nombre="CLIENTE",
        contexto="cliente", plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


def _preflight():
    return {
        "principal": PRINCIPAL,
        "restringido": True,
        "rls_empresas": True,
        "rls_reservas": True,
        "rls_inventario": True,
        "empresas_visibles": 1,
        "empresa_esperada_visible": 1,
        "planes_reserva_vigentes": 1,
        "procedimientos_autorizados": 1,
    }


def _meta():
    return {
        "propietario": OWNER,
        "security_definer": True,
        "path_seguro": True,
        "puede_ejecutar": True,
        "propietario_restringido": True,
        "llamador_sin_ddl": True,
        "llamador_ajeno_al_propietario": True,
        "publico_revocado": True,
    }


class Resultado:
    def __init__(self,registro):
        self.registro=registro
    def mappings(self):
        return self
    def one_or_none(self):
        return self.registro


class FakeDB:
    def __init__(self,*,preflight=None,meta=None,actor=True):
        self.preflight=_preflight() if preflight is None else preflight
        self.meta=_meta() if meta is None else meta
        self.actor=actor
        self.calls=[]
        self.commits=0
        self.rollbacks=0

    def execute(self,statement,params=None):
        sql=str(statement)
        self.calls.append((sql,params))
        if "FROM pg_roles r WHERE r.rolname=session_user" in sql:
            return Resultado(self.preflight)
        if "to_regprocedure" in sql:
            return Resultado(self.meta)
        if "CALL public.sp_" in sql:
            raise AssertionError("una operacion comercial alcanzó CALL")
        raise AssertionError("SQL inesperado en test: "+sql[:130])

    def scalar(self,statement,params):
        sql=str(statement)
        self.calls.append((sql,params))
        if "public.fs_verificar_actor_reserva(" not in sql:
            raise AssertionError("lectura SQL inesperada: "+sql[:100])
        return self.actor

    def rollback(self):
        self.rollbacks+=1

    def commit(self):
        self.commits+=1


def test_5au_config_por_defecto_bloquea_antes_de_funcion(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",None)
    db=FakeDB()
    with pytest.raises(servicio.ReservaTenantDeshabilitada,
                       match="no configurada"):
        servicio.verificar_principal_reserva(db,_tenant())
    assert len(db.calls)==1
    assert not db.commits


@pytest.mark.parametrize("cambio",[
    ("propietario","actor_falso"),
    ("security_definer",False),
    ("path_seguro",False),
    ("puede_ejecutar",False),
    ("propietario_restringido",False),
    ("llamador_sin_ddl",False),
    ("llamador_ajeno_al_propietario",False),
    ("publico_revocado",False),
])
def test_5au_rechaza_meta_insegura(monkeypatch,cambio):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    meta=_meta()
    meta[cambio[0]]=cambio[1]
    db=FakeDB(meta=meta)
    with pytest.raises(servicio.ReservaTenantDeshabilitada,
                       match="inseguro"):
        servicio.verificar_principal_reserva(db,_tenant())
    assert len(db.calls)==2
    assert not any("fs_verificar_actor_reserva(:empresa_id" in sql
                   for sql,_ in db.calls)


def test_5au_funcion_no_instalada_falla_cerrado(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    db=FakeDB()
    db.meta={}
    with pytest.raises(servicio.ReservaTenantDeshabilitada):
        servicio.verificar_principal_reserva(db,_tenant())
    assert len(db.calls)==2


def test_5au_principal_rol_compartido_no_acredita_actor(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    db=FakeDB(actor=False)
    with pytest.raises(servicio.ReservaTenantDeshabilitada,
                       match="no acredita"):
        servicio.verificar_principal_reserva(db,_tenant())
    assert len(db.calls)==3


def test_5au_actor_correspondiente_autoriza_solo_preflight(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    db=FakeDB(actor=True)
    assert servicio.verificar_principal_reserva(db,_tenant())==PRINCIPAL
    assert db.calls[-1][1] == {"empresa_id":3,"usuario_id":101}
    assert all("CALL public.sp_" not in sql for sql,_ in db.calls)
    assert db.commits==db.rollbacks==0


def test_5au_no_confunde_guc_de_auditoria_con_principal(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    db=FakeDB(actor=False)
    with pytest.raises(servicio.ReservaTenantDeshabilitada):
        servicio.verificar_principal_reserva(db,_tenant(usuario=102))
    assert all("current_setting('app.usuario_id')" not in sql
               for sql,_ in db.calls)


@pytest.mark.parametrize("accion",["crear","cancelar","finalizar"])
def test_5au_los_tres_cu_no_llaman_sp_si_guardia_falla(monkeypatch,accion):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",None)
    db=FakeDB()
    tenant=_tenant()
    if accion=="crear":
        metodo=lambda: servicio.crear_reserva_transaccional_sin_exposicion_http(
            db,tenant=tenant,carrito_id=20,
            fecha_atencion=datetime.now(timezone.utc)+timedelta(days=1))
    elif accion=="cancelar":
        metodo=lambda: servicio.cancelar_reserva_transaccional_tenant(
            db,tenant=tenant,reserva_id=33)
    else:
        tenant=TenantContext(
            usuario_id=201,empresa_id=3,empresa_slug="empresa-lab-a",
            empresa_nombre="Ficticia A",rol_id=3,rol_nombre="ADMINISTRADOR",
            contexto="personal",plan_codigo="PROFESIONAL",
            permite_reportes_ia=False)
        metodo=lambda: servicio.finalizar_reserva_sin_compra_transaccional_tenant(
            db,tenant=tenant,reserva_id=33)
    with pytest.raises(servicio.ReservaTenantDeshabilitada):
        metodo()
    assert db.rollbacks==1
    assert db.commits==0
    assert not any("CALL public.sp_" in sql for sql,_ in db.calls)


def test_5au_no_hay_identidad_individual_si_rls_es_insuficiente(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",OWNER)
    wrong=_preflight()
    wrong["empresas_visibles"]=2
    db=FakeDB(preflight=wrong)
    with pytest.raises(servicio.ReservaTenantDeshabilitada):
        servicio.verificar_principal_reserva(db,_tenant())
    assert len(db.calls)==1



def test_5au_verificador_sin_procedencia_no_es_confiable():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "app/modules/reservas/services/tenant_reserva_transaccional_5al.py").read_text(encoding="utf-8")
    assert "pg_has_role((SELECT oid FROM pg_roles WHERE rolname=session_user)" in source
    assert "p.proowner,'MEMBER'" in source
    assert "NOT has_schema_privilege(session_user,'public','CREATE')" in source
    assert "pg_get_userbyid(p.proowner)" in source
    assert "acl.grantee=0" in source
    assert "app.usuario_id" in source  # advertencia, nunca autorizacion
    assert "current_setting('app.usuario_id')" not in source
