"""B3/P5R: 16 pruebas unitarias de bootstrap SaaS con proyección mínima.

No conecta a PostgreSQL ni Supabase y no abre rutas SaaS reales: sesiones
falsas, dos empresas inventadas y Argon2 simulado solamente en unit tests.
"""
import os
from pathlib import Path
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.tenant_context import leer_token_empresa
from app.modules.autenticacion_seguridad.api.tenant_router import (
    TenantLoginRequest, login_tenant,
)
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    CredencialesBootstrapTenant, TenantAuthRepository,
)
from app.modules.autenticacion_seguridad.services import service as service_module
from app.modules.autenticacion_seguridad.services.service import AuthService


ROOT = Path(__file__).resolve().parents[1]


class DBFalsa:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    def execute(self, statement, params):
        self.queries.append((str(statement), dict(params)))
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: list(self.rows))
        )


def usuario_ficticio(uid=42):
    return {"usuario_id": uid, "password_hash": "hash-simulado"}


def membresia_ficticia():
    return {
        "empresa_id": 15, "empresa_slug": "empresa-demo",
        "empresa_nombre": "Empresa Demo",
        "rol_id": 3, "rol_nombre": "CLIENTE",
        "plan_codigo": "BASICO", "permite_reportes_ia": False,
    }


def login_request():
    return TenantLoginRequest(
        correo="DEMO@EXAMPLE.COM", password="ClaveCorrecta!",
        empresa_slug="empresa-demo", contexto="cliente",
    )


def test_5r_consulta_solo_id_y_hash_y_normaliza_correo():
    db = DBFalsa([usuario_ficticio()])
    cred = TenantAuthRepository.obtener_credenciales_bootstrap(
        db, "   DEMO@EXAMPLE.COM  "
    )
    assert cred == CredencialesBootstrapTenant(42, "hash-simulado")
    assert len(db.queries) == 1
    sql, params = db.queries[0]
    assert "SELECT u.id AS usuario_id, u.password_hash" in sql
    assert "FROM public.usuario AS u" in sql
    assert "u.correo = :correo" in sql and "u.estado IS TRUE" in sql
    assert "LIMIT 2" in sql and params == {"correo": "demo@example.com"}
    assert "SELECT *" not in sql and "JOIN" not in sql
    assert "rol" not in sql.lower() and "empresa_id" not in sql


def test_5r_repositorio_falla_cerrado_si_no_existe_usuario():
    assert TenantAuthRepository.obtener_credenciales_bootstrap(
        DBFalsa(), "demo@example.com"
    ) is None


def test_5r_repositorio_falla_cerrado_si_hay_duplicados():
    db = DBFalsa([usuario_ficticio(), usuario_ficticio(uid=99)])
    assert TenantAuthRepository.obtener_credenciales_bootstrap(
        db, "demo@example.com"
    ) is None


def test_5r_repositorio_no_consulta_correo_vacio():
    db = DBFalsa([usuario_ficticio()])
    assert TenantAuthRepository.obtener_credenciales_bootstrap(db, "   ") is None
    assert db.queries == []


def test_5r_el_hash_no_aparece_en_repr_de_credenciales():
    cred = CredencialesBootstrapTenant(usuario_id=42, password_hash="SECRETO")
    assert "SECRETO" not in repr(cred)
    assert cred.usuario_id == 42 and cred.password_hash == "SECRETO"


def test_5r_servicio_verifica_argon2_y_solo_retorna_id(monkeypatch):
    verificaciones = []
    monkeypatch.setattr(
        service_module, "verify_password",
        lambda password, h: verificaciones.append((password, h)) or True,
    )
    db = DBFalsa([usuario_ficticio()])
    result = AuthService.autenticar_credenciales_tenant(
        db, "  DEMO@EXAMPLE.COM ", "ClaveCorrecta!"
    )
    assert result == 42
    assert type(result) is int
    assert verificaciones == [("ClaveCorrecta!", "hash-simulado")]


def test_5r_servicio_rechaza_password_invalido(monkeypatch):
    monkeypatch.setattr(service_module, "verify_password", lambda *_: False)
    assert AuthService.autenticar_credenciales_tenant(
        DBFalsa([usuario_ficticio()]), "demo@example.com", "incorrecta"
    ) is None


def test_5r_servicio_no_verifica_password_si_usuario_ausente(monkeypatch):
    def prohibido(*args):
        raise AssertionError("No verificar un hash ausente")
    monkeypatch.setattr(service_module, "verify_password", prohibido)
    assert AuthService.autenticar_credenciales_tenant(
        DBFalsa(), "demo@example.com", "x"
    ) is None


def test_5r_login_saas_exitoso_separa_hash_de_respuesta(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    llamadas = []
    monkeypatch.setattr(
        AuthService, "autenticar_credenciales_tenant",
        lambda db, correo, password: llamadas.append((correo, password)) or 42,
    )
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_membresia_vigente",
        lambda db, **kw: llamadas.append(kw) or membresia_ficticia(),
    )
    respuesta = login_tenant(datos=login_request(), db=DBFalsa())
    assert leer_token_empresa(respuesta.access_token) == (42, 15, "cliente")
    assert respuesta.empresa_id == 15
    assert respuesta.rol == "CLIENTE"
    assert respuesta.empresa_slug == "empresa-demo"
    assert set(respuesta.model_dump()) == {
        "access_token", "token_type", "empresa_id", "empresa_slug",
        "empresa_nombre", "rol", "contexto",
    }
    assert "password_hash" not in repr(respuesta)
    assert llamadas == [
        ("demo@example.com", "ClaveCorrecta!"),
        {"usuario_id": 42, "empresa_slug": "empresa-demo", "contexto": "cliente"},
    ]


def test_5r_login_credenciales_incorrectas_no_consulta_membresia(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(AuthService, "autenticar_credenciales_tenant", lambda *_: None)
    def indebido(*args, **kwargs):
        raise AssertionError("No consultar membresía sin autenticación")
    monkeypatch.setattr(TenantAuthRepository, "obtener_membresia_vigente", indebido)
    with pytest.raises(HTTPException) as exc:
        login_tenant(datos=login_request(), db=DBFalsa())
    assert exc.value.status_code == 401
    assert exc.value.detail == "Credenciales invalidas o empresa no autorizada"


def test_5r_login_membresia_revocada_devuelve_mismo_401(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(AuthService, "autenticar_credenciales_tenant", lambda *_: 42)
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_membresia_vigente", lambda db, **kw: None
    )
    with pytest.raises(HTTPException) as exc:
        login_tenant(datos=login_request(), db=DBFalsa())
    assert exc.value.status_code == 401
    assert exc.value.detail == "Credenciales invalidas o empresa no autorizada"


def test_5r_login_error_sql_bootstrap_no_expone_detalles(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    def error_sql(*_):
        raise SQLAlchemyError("SQL privado ficticio")
    monkeypatch.setattr(AuthService, "autenticar_credenciales_tenant", error_sql)
    with pytest.raises(HTTPException) as exc:
        login_tenant(datos=login_request(), db=DBFalsa())
    assert exc.value.status_code == 503
    assert "SQL privado" not in str(exc.value.detail)


def test_5r_login_error_sql_membresia_no_expone_detalles(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(AuthService, "autenticar_credenciales_tenant", lambda *_: 42)
    def error_sql(*_, **kwargs):
        raise SQLAlchemyError("Otro error privado ficticio")
    monkeypatch.setattr(TenantAuthRepository, "obtener_membresia_vigente", error_sql)
    with pytest.raises(HTTPException) as exc:
        login_tenant(datos=login_request(), db=DBFalsa())
    assert exc.value.status_code == 503
    assert "privado" not in str(exc.value.detail)


def test_5r_login_flag_apagado_no_consulta_bootstrap(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    def indebido(*args):
        raise AssertionError("SaaS debe detenerse antes de leer credenciales")
    monkeypatch.setattr(AuthService, "autenticar_credenciales_tenant", indebido)
    with pytest.raises(HTTPException) as exc:
        login_tenant(datos=login_request(), db=DBFalsa())
    assert exc.value.status_code == 503


def test_5r_login_no_acepta_empresa_id_rol_ni_usuario_id_externos():
    for nombre in ("empresa_id", "usuario_id", "rol"):
        datos = login_request().model_dump()
        datos[nombre] = 99
        with pytest.raises(ValidationError):
            TenantLoginRequest(**datos)


def test_5r_contrato_legacy_permanece_con_orm_y_sin_llamadas_sp():
    repo = (
        ROOT / "app/modules/autenticacion_seguridad/repositories/repository.py"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "app/modules/autenticacion_seguridad/services/service.py"
    ).read_text(encoding="utf-8")
    gate = (ROOT / "app/core/saas_route_gate.py").read_text(encoding="utf-8")
    assert "joinedload(Usuario.rol)" in repo  # login legacy no alterado
    assert "def autenticar_credenciales(" in service
    assert "def autenticar_credenciales_tenant(" in service
    assert '("POST", "/tenant/reservas")' not in gate
    assert "CALL public.sp_" not in service
