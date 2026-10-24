"""B3/P5N: pruebas aisladas de actor auditado SaaS por transaccion.

Sin PostgreSQL/Supabase: se simulan Session y Connection; no se instalan
politicas RLS ni se demuestra identidad SQL frente a GUC modificables.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "clave-ficticia-saas-solo-5n")
os.environ.setdefault("SAAS_AUTH_ENABLED", "false")

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import _aplicar_contexto_request
from app.core.tenant_context import emitir_token_empresa
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)


class SesionFalsa:
    def __init__(self, fallar_en=0):
        self.info = {}
        self.statements = []
        self.fallar_en = fallar_en

    def execute(self, sentencia, parametros):
        self.statements.append((str(sentencia), parametros))
        if self.fallar_en and len(self.statements) == self.fallar_en:
            raise SQLAlchemyError("error academico de transaccion")
        return None


class ConexionFalsa:
    def __init__(self):
        self.statements = []

    def execute(self, sentencia, parametros):
        self.statements.append((str(sentencia), parametros))


def credenciales(usuario=42, empresa=15):
    token = emitir_token_empresa(
        usuario_id=usuario, empresa_id=empresa, contexto="personal"
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def membresia(empresa=15):
    return {
        "empresa_id": empresa,
        "empresa_slug": "empresa-ficticia",
        "empresa_nombre": "Empresa Ficticia",
        "rol_id": 3,
        "rol_nombre": "CAJERO",
        "plan_codigo": "PROFESIONAL",
        "permite_reportes_ia": False,
    }


def permitir(monkeypatch, fn=None):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_membresia_vigente",
        fn or (lambda db, **kwargs: membresia()),
    )


def test_5n_actor_y_empresa_solo_tras_membresia(monkeypatch):
    vistos = []

    def recuperar(db, **kwargs):
        vistos.append((dict(db.info), kwargs))
        return membresia()

    permitir(monkeypatch, recuperar)
    db = SesionFalsa()
    tenant = get_current_tenant_context(credentials=credenciales(), db=db)

    assert vistos == [({}, {"usuario_id": 42, "empresa_id": 15, "contexto": "personal"})]
    assert tenant.usuario_id == 42 and tenant.empresa_id == 15
    assert db.info == {"usuario_id": 42, "empresa_id": 15}
    assert len(db.statements) == 2
    assert "set_config('app.usuario_id', :usuario_id, true)" in db.statements[0][0]
    assert db.statements[0][1] == {"usuario_id": "42"}
    assert "set_config('app.empresa_id', :empresa_id, true)" in db.statements[1][0]
    assert db.statements[1][1] == {"empresa_id": "15"}


def test_5n_token_ausente_no_establece_actor(monkeypatch):
    permitir(monkeypatch)
    db = SesionFalsa()
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=None, db=db)
    assert err.value.status_code == 401
    assert db.info == {} and db.statements == []


def test_5n_token_malformado_no_establece_actor(monkeypatch):
    permitir(monkeypatch)
    db = SesionFalsa()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token-invalido")
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=creds, db=db)
    assert err.value.status_code == 401
    assert db.info == {} and db.statements == []


def test_5n_membresia_revocada_no_establece_actor(monkeypatch):
    permitir(monkeypatch, lambda db, **kw: None)
    db = SesionFalsa()
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=credenciales(), db=db)
    assert err.value.status_code == 403
    assert db.info == {} and db.statements == []


def test_5n_saas_inactivo_no_establece_actor(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    db = SesionFalsa()
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=credenciales(), db=db)
    assert err.value.status_code == 503
    assert db.info == {} and db.statements == []


def test_5n_segundo_set_config_falla_sin_guardar_info(monkeypatch):
    permitir(monkeypatch)
    db = SesionFalsa(fallar_en=2)
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=credenciales(), db=db)
    assert err.value.status_code == 503
    assert db.info == {}  # evita reutilizacion parcial de Session.info
    assert len(db.statements) == 2


def test_5n_primer_set_config_falla_sin_guardar_info(monkeypatch):
    permitir(monkeypatch)
    db = SesionFalsa(fallar_en=1)
    with pytest.raises(HTTPException) as err:
        get_current_tenant_context(credentials=credenciales(), db=db)
    assert err.value.status_code == 503
    assert db.info == {}
    assert len(db.statements) == 1


def test_5n_listener_reaplica_actor_empresa_ip_en_nueva_transaccion():
    sesion = SimpleNamespace(
        info={"request_ip": "203.0.113.7", "empresa_id": 15, "usuario_id": 42}
    )
    conn = ConexionFalsa()
    _aplicar_contexto_request(sesion, None, conn)
    assert len(conn.statements) == 3
    assert [p for _, p in conn.statements] == [
        {"ip": "203.0.113.7"},
        {"empresa_id": "15"},
        {"usuario_id": "42"},
    ]
    assert all("set_config(" in query and ", true)" in query
               for query, _ in conn.statements)


def test_5n_listener_sin_usuario_no_inventa_actor():
    sesion = SimpleNamespace(info={"request_ip": "198.51.100.5", "empresa_id": 15})
    conn = ConexionFalsa()
    _aplicar_contexto_request(sesion, None, conn)
    assert len(conn.statements) == 2
    assert all("app.usuario_id" not in sql for sql, _ in conn.statements)


def test_5n_listener_sin_contexto_no_establece_guc():
    conn = ConexionFalsa()
    _aplicar_contexto_request(SimpleNamespace(info={}), None, conn)
    assert conn.statements == []


def test_5n_listener_tras_rollback_reaplica_solo_si_hay_contexto():
    sesion = SimpleNamespace(info={"empresa_id": 15, "usuario_id": 42})
    primer = ConexionFalsa()
    _aplicar_contexto_request(sesion, None, primer)
    segundo = ConexionFalsa()
    _aplicar_contexto_request(sesion, None, segundo)
    assert primer.statements == segundo.statements
    assert len(segundo.statements) == 2


def test_5n_sessions_independientes_no_comparten_usuario():
    sesion_a = SimpleNamespace(info={"empresa_id": 15, "usuario_id": 42})
    sesion_b = SimpleNamespace(info={"empresa_id": 20, "usuario_id": 84})
    conn_a, conn_b = ConexionFalsa(), ConexionFalsa()
    _aplicar_contexto_request(sesion_a, None, conn_a)
    _aplicar_contexto_request(sesion_b, None, conn_b)
    assert conn_a.statements[-1][1] == {"usuario_id": "42"}
    assert conn_b.statements[-1][1] == {"usuario_id": "84"}
    assert conn_a.statements[-2][1] == {"empresa_id": "15"}
    assert conn_b.statements[-2][1] == {"empresa_id": "20"}
