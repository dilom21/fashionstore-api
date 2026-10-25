"""5AY: pruebas unitarias del modo DEFINER auditable, sin PostgreSQL.

No cambian flags SaaS globales ni credenciales persistentes. El modo
SECURITY DEFINER debe permanecer bloqueado si propietario/ACL son inseguros.
"""
import os
os.environ["DATABASE_URL"]="sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"]="test-5ay-key-local-no-production-12345"
os.environ["SAAS_AUTH_ENABLED"]="false"
os.environ["SAAS_RESERVAS_ENABLED"]="false"

import pytest

from app.core.config import settings
from app.core.tenant_context import TenantContext
from app.modules.reservas.services import tenant_reserva_transaccional_5al as service

WRITER="fashionstore_5ax_sp_writer"
VERIFY_OWNER="fashionstore_5av_guard_owner"
ACTOR="fashionstore_5ar_a1"


def _context():
    return TenantContext(
        usuario_id=101,empresa_id=1,empresa_slug="empresa-lab-a",
        empresa_nombre="Empresa ficticia A",rol_id=1,
        rol_nombre="CLIENTE",contexto="cliente",
        plan_codigo="PROFESIONAL",permite_reportes_ia=False,
    )


def _base(autorizados=0):
    return {
        "principal":ACTOR,
        "restringido":True,
        "rls_empresas":True,
        "rls_reservas":True,
        "rls_inventario":True,
        "empresas_visibles":1,
        "empresa_esperada_visible":1,
        "planes_reserva_vigentes":1,
        "procedimientos_autorizados":autorizados,
    }


def _sp():
    return {
        "propietario":WRITER,
        "security_definer":True,
        "path_seguro":True,
        "puede_ejecutar":True,
        "propietario_restringido":True,
        "owner_sin_ddl":True,
        "llamador_ajeno_al_propietario":True,
        "llamador_sin_ddl":True,
        "publico_revocado":True,
    }


def _actor():
    return {
        "propietario":VERIFY_OWNER,
        "security_definer":True,
        "path_seguro":True,
        "puede_ejecutar":True,
        "propietario_restringido":True,
        "llamador_sin_ddl":True,
        "llamador_ajeno_al_propietario":True,
        "publico_revocado":True,
    }


class Result:
    def __init__(self,row): self.row=row
    def mappings(self): return self
    def one_or_none(self): return self.row


class DB:
    def __init__(self,*,base=None,sp=None,actor=None,valid_actor=True):
        self.base=base if base is not None else _base()
        self.sp=sp if sp is not None else _sp()
        self.actor=actor if actor is not None else _actor()
        self.valid_actor=valid_actor
        self.calls=[]

    def execute(self,sql,params=None):
        sql=str(sql)
        self.calls.append((sql,params))
        if "FROM pg_roles r WHERE r.rolname=session_user" in sql:
            return Result(self.base)
        if "WHERE n.nspname='public' AND p.proname=:procedimiento" in sql:
            return Result(self.sp)
        if "to_regprocedure" in sql:
            return Result(self.actor)
        raise AssertionError("SQL no esperado: "+sql[:160])

    def scalar(self,sql,params):
        self.calls.append((str(sql),params))
        assert "public.fs_verificar_actor_reserva(" in str(sql)
        return self.valid_actor


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(settings,"saas_actor_sql_owner",VERIFY_OWNER)
    monkeypatch.setattr(settings,"saas_sp_sql_owner",WRITER)


def test_5ay_owner_seguro_verifica_actor_y_habilita_solo_preflight():
    db=DB()
    assert service.verificar_principal_reserva(db,_context())==ACTOR
    assert len(db.calls)==4
    assert db.calls[1][1]=={"procedimiento":"sp_crear_reserva_tenant"}
    assert db.calls[3][1]=={"empresa_id":1,"usuario_id":101}
    assert not any("CALL public.sp_" in sql for sql,_ in db.calls)


@pytest.mark.parametrize("field",[
    "security_definer","path_seguro","puede_ejecutar",
    "propietario_restringido","owner_sin_ddl",
    "llamador_ajeno_al_propietario","llamador_sin_ddl",
    "publico_revocado",
])
def test_5ay_definer_inseguro_falla_antes_de_funcion(field):
    meta=_sp()
    meta[field]=False
    db=DB(sp=meta)
    with pytest.raises(service.ReservaTenantDeshabilitada,match="inseguro"):
        service.verificar_principal_reserva(db,_context())
    assert len(db.calls)==2


@pytest.mark.parametrize("owner",[
    None,"",ACTOR,VERIFY_OWNER,"owner-desconocido"
])
def test_5ay_owner_omitido_o_incorrecto_denegado(monkeypatch,owner):
    monkeypatch.setattr(settings,"saas_sp_sql_owner",owner)
    db=DB()
    with pytest.raises(service.ReservaTenantDeshabilitada):
        service.verificar_principal_reserva(db,_context())
    assert len(db.calls)<=2


def test_5ay_procedure_inexistente_falla_cerrado():
    db=DB(sp={})
    with pytest.raises(service.ReservaTenantDeshabilitada,match="inseguro"):
        service.verificar_principal_reserva(db,_context())
    assert len(db.calls)==2


def test_5ay_actor_de_otro_usuario_sigue_denegado():
    db=DB(valid_actor=False)
    with pytest.raises(service.ReservaTenantDeshabilitada,match="no acredita"):
        service.verificar_principal_reserva(db,_context())
    assert len(db.calls)==4


def test_5ay_invoker_exige_modo_definer_apagado(monkeypatch):
    db=DB(base=_base(autorizados=1))
    with pytest.raises(service.ReservaTenantDeshabilitada,match="sigue INVOKER"):
        service.verificar_principal_reserva(db,_context())
    monkeypatch.setattr(settings,"saas_sp_sql_owner",None)
    assert service.verificar_principal_reserva(db,_context())==ACTOR
    # INVOKER 5AU jamás consulta metadata de DEFINER.
    assert len([sql for sql,_ in db.calls
                if "WHERE n.nspname='public' AND p.proname=:procedimiento" in sql])==0


def test_5ay_multiple_procedimientos_no_autorizados(monkeypatch):
    monkeypatch.setattr(settings,"saas_sp_sql_owner",None)
    db=DB(base=_base(autorizados=2))
    with pytest.raises(service.ReservaTenantDeshabilitada):
        service.verificar_principal_reserva(db,_context())


def test_5ay_no_se_confiere_create_sobre_schema_ni_guc():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/
         "app/modules/reservas/services/tenant_reserva_transaccional_5al.py").read_text(encoding="utf-8")
    assert "NOT has_schema_privilege(p.proowner,'public','CREATE')" in src
    assert "NOT has_schema_privilege(session_user,'public','CREATE')" in src
    assert "acl.grantee=0" in src
    assert "p.prosecdef" in src
    assert "rolcanlogin IS FALSE" in src
    assert "pg_has_role" in src
    assert "current_setting('app.usuario_id')" not in src
