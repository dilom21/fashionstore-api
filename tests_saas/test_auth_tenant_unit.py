"""Pruebas SaaS unitarias aisladas (sin Supabase, SQL real ni datos de producción).

Ejecución: pytest -q tests_saas
"""
import os
from types import SimpleNamespace

# Las pruebas SaaS no deben depender de la BD de producción.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.core.tenant_context import TenantContext, emitir_token_empresa, leer_token_empresa
from app.core.tenant_dependencies import (
    comprobar_saas_habilitado,
    get_current_tenant_context,
    get_current_tenant_admin,
    require_tenant_permission,
)
from app.core.dependencies import get_current_user
from app.modules.autenticacion_seguridad.repositories.tenant_repository import TenantAuthRepository
from app.modules.autenticacion_seguridad.services.service import AuthService
from app.modules.autenticacion_seguridad.api.tenant_router import router as tenant_router
from app.modules.roles.services.service import RolService


def credenciales(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def token_empresa():
    return emitir_token_empresa(usuario_id=42, empresa_id=15, contexto="personal")


def token_legacy():
    return create_access_token({"sub": "42", "rol": "ADMINISTRADOR", "contexto": "personal"})


def membresia_demo():
    return {
        "empresa_id": 15, "empresa_slug": "vanter-men",
        "empresa_nombre": "Vanter Men", "rol_id": 3, "rol_nombre": "CAJERO",
        "plan_codigo": "PROFESIONAL", "permite_reportes_ia": True,
    }


class FakeDB:
    def __init__(self, rows=()):
        self.info = {}
        self.rows = list(rows)
        self.statements = []

    def execute(self, statement, params):
        self.statements.append((str(statement), params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))


def test_token_de_empresa_firmado_y_sin_rol_global():
    token = token_empresa()
    assert leer_token_empresa(token) == (42, 15, "personal")
    claims = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["scope"] == "tenant"
    assert "rol" not in claims
    assert claims["exp"] and claims["jti"]


@pytest.mark.parametrize("payload", [
    {"sub": "42", "empresa_id": "15", "contexto": "personal"},
    {"scope": "tenant", "sub": "0", "empresa_id": "15", "contexto": "personal", "jti": "a"},
    {"scope": "tenant", "sub": "42", "empresa_id": "0", "contexto": "personal", "jti": "a"},
    {"scope": "tenant", "sub": "42", "empresa_id": "otro", "contexto": "personal", "jti": "a"},
    {"scope": "tenant", "sub": "42", "empresa_id": "15", "contexto": "otro", "jti": "a"},
    {"scope": "tenant", "sub": "42", "empresa_id": "15", "contexto": "personal"},
])
def test_token_invalido_rechazado(payload):
    assert leer_token_empresa(create_access_token(payload)) is None


def test_token_legacy_y_firma_alterada_rechazados_como_tenant():
    assert leer_token_empresa(token_legacy()) is None
    assert leer_token_empresa(token_empresa() + "modificado") is None


def test_feature_flag_saas_inactivo_retorna_503(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    with pytest.raises(HTTPException) as exc:
        comprobar_saas_habilitado()
    assert exc.value.status_code == 503


def test_token_empresa_no_reutilizable_en_dependencias_legacy(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials=credenciales(token_empresa()), db=FakeDB())
    assert exc.value.status_code == 401


def test_modo_saas_bloquea_autorizacion_legacy(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials=credenciales(token_legacy()), db=FakeDB())
    assert exc.value.status_code == 401


def test_auth_legacy_sigue_funcionando_mientras_flag_inactivo(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    user = SimpleNamespace(id=42, estado=True)
    monkeypatch.setattr(AuthService, "obtener_usuario_activo", lambda db, uid: user)
    assert get_current_user(credentials=credenciales(token_legacy()), db=FakeDB()) is user


def test_repositorio_filtra_usuario_empresa_y_contexto():
    db = FakeDB([membresia_demo()])
    result = TenantAuthRepository.obtener_membresia_vigente(
        db, usuario_id=42, empresa_slug="vanter-men", contexto="personal"
    )
    assert result["empresa_id"] == 15
    sql, params = db.statements[0]
    assert "usuario_empresa" in sql and "suscripcion" in sql
    assert "em.empresa_id = e.id" in sql and "su.empresa_id = e.id" in sql
    assert params["usuario_id"] == 42 and params["empresa_slug"] == "vanter-men"


def test_repositorio_no_escoge_una_membresia_ambigua():
    assert TenantAuthRepository.obtener_membresia_vigente(
        FakeDB([membresia_demo(), membresia_demo()]),
        usuario_id=42, empresa_slug="vanter-men", contexto="personal",
    ) is None


def test_contexto_empresarial_efectivo_tomado_de_bd(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_membresia_vigente",
        lambda db, **kw: membresia_demo(),
    )
    db = FakeDB()
    tenant = get_current_tenant_context(credentials=credenciales(token_empresa()), db=db)
    assert tenant.empresa_id == 15 and tenant.rol_nombre == "CAJERO"
    assert db.info["empresa_id"] == 15
    assert db.statements[-1][1]["empresa_id"] == "15"


def test_membresia_revocada_impide_operacion(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_membresia_vigente",
        lambda db, **kw: None,
    )
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        get_current_tenant_context(credentials=credenciales(token_empresa()), db=db)
    assert exc.value.status_code == 403
    assert "empresa_id" not in db.info


def test_permiso_obtiene_rol_de_membresia_no_global(monkeypatch):
    consultados = []
    monkeypatch.setattr(
        RolService, "tiene_permiso",
        lambda db, rol, funcion, accion: consultados.append((rol, funcion, accion)) or True,
    )
    tenant = TenantContext(
        usuario_id=42, empresa_id=15, empresa_slug="vanter-men",
        empresa_nombre="Vanter Men", rol_id=3, rol_nombre="CAJERO",
        contexto="personal", plan_codigo="PROFESIONAL", permite_reportes_ia=True,
    )
    assert require_tenant_permission("VENTAS", "CONSULTAR")(
        tenant=tenant, db=FakeDB()
    ) is tenant
    assert consultados == [(3, "VENTAS", "CONSULTAR")]
    with pytest.raises(HTTPException) as exc:
        get_current_tenant_admin(tenant=tenant)
    assert exc.value.status_code == 403


def test_endpoints_saas_inactivos_retorna_503_sin_leer_saas_bd(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    app = FastAPI()
    app.include_router(tenant_router)
    with TestClient(app) as client:
        result = client.post("/auth/tenant/login", json={
            "correo": "demo@example.com", "password": "xxx",
            "empresa_slug": "vanter-men", "contexto": "personal",
        })
        assert result.status_code == 503
        assert client.get("/auth/tenant/me").status_code == 503
