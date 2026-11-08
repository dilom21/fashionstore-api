"""Pruebas aisladas de selector, registro bootstrap y CU05 SaaS."""
import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./saas_auth_unit_test.db")
os.environ.setdefault("SECRET_KEY", "clave-ficticia-exclusiva-para-test-saas")

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.api.tenant_router import (
    TenantClienteRegistroRequest,
    listar_empresas_publicas,
    registrar_cliente_tenant,
)
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    EmpresaPublicaTenant,
    RegistroClienteTenant,
    TenantAuthRepository,
)
from app.modules.autenticacion_seguridad.services.service import (
    AuthService,
    CorreoClienteDuplicadoError,
)
from app.modules.bitacora.api.tenant_router import _personal_autorizado
from app.modules.bitacora.services.service import BitacoraService


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def datos_registro(**cambios):
    datos = {
        "empresa_slug": "vanter-men",
        "correo": "cliente@example.com",
        "password": "Segura123!",
        "password_confirmacion": "Segura123!",
        "nombre": "Ana",
        "apellido": "Perez",
        "ci": "CI-100",
        "telefono": "70000000",
        "sexo": "FEMENINO",
        "fecha_nacimiento": date(1995, 1, 1),
    }
    datos.update(cambios)
    return TenantClienteRegistroRequest(**datos)


def tenant(*, contexto="personal", empresa_id=7):
    return TenantContext(
        usuario_id=2, empresa_id=empresa_id, empresa_slug="vanter-men",
        empresa_nombre="Vanter Men", rol_id=1, rol_nombre="ADMINISTRADOR",
        contexto=contexto, plan_codigo="PROFESIONAL", permite_reportes_ia=True,
    )


def test_selector_publico_proyecta_solo_slug_y_nombre(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository, "listar_empresas_publicas",
        lambda db: [EmpresaPublicaTenant("vanter-men", "Vanter Men")],
    )
    monkeypatch.setattr(
        "app.modules.autenticacion_seguridad.api.tenant_router.comprobar_saas_habilitado",
        lambda: None,
    )
    resultado = listar_empresas_publicas(FakeDB())
    assert resultado == [EmpresaPublicaTenant("vanter-men", "Vanter Men")]
    assert set(resultado[0].__dataclass_fields__) == {"slug", "nombre_comercial"}


def test_registro_valido_es_atomico_y_no_emite_token(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(
        TenantAuthRepository, "registrar_cliente_publico",
        lambda db, **kwargs: RegistroClienteTenant(10, 20, kwargs["empresa_slug"]),
    )
    resultado = AuthService.registrar_cliente_tenant(
        db, datos_registro(), empresa_slug="vanter-men"
    )
    assert (resultado.usuario_id, resultado.cliente_id) == (10, 20)
    assert db.commits == 1 and db.rollbacks == 0
    assert not hasattr(resultado, "access_token")


def test_registro_rechaza_password_insegura_antes_de_sql():
    with pytest.raises(Exception):
        AuthService.registrar_cliente_tenant(
            FakeDB(), datos_registro(password="123", password_confirmacion="123"),
            empresa_slug="vanter-men",
        )


def test_registro_no_acepta_rol_empresa_id_ni_estado_del_cliente():
    for campo in ("rol_id", "empresa_id", "estado"):
        with pytest.raises(ValidationError):
            datos_registro(**{campo: 1})


def test_correo_existente_se_rechaza_sin_vincular(monkeypatch):
    db = FakeDB()
    def duplicado(db, **kwargs):
        raise CorreoClienteDuplicadoError()
    monkeypatch.setattr(TenantAuthRepository, "registrar_cliente_publico", duplicado)
    with pytest.raises(CorreoClienteDuplicadoError):
        AuthService.registrar_cliente_tenant(db, datos_registro(), empresa_slug="otra")
    assert db.commits == 0 and db.rollbacks == 1


def test_cliente_no_puede_consultar_bitacora():
    with pytest.raises(HTTPException) as exc:
        _personal_autorizado(tenant(contexto="cliente"))
    assert exc.value.status_code == 403


def test_bitacora_propaga_empresa_a_listado_conteo(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        "app.modules.bitacora.services.service.BitacoraRepository.contar",
        lambda db, **kwargs: llamadas.append(("contar", kwargs["empresa_id"])) or 0,
    )
    monkeypatch.setattr(
        "app.modules.bitacora.services.service.BitacoraRepository.listar",
        lambda db, **kwargs: llamadas.append(("listar", kwargs["empresa_id"])) or [],
    )
    respuesta = BitacoraService.listar(FakeDB(), empresa_id=7)
    assert respuesta.total == 0
    assert llamadas == [("contar", 7), ("listar", 7)]


def test_bitacora_detalle_cruzado_no_se_sustituye(monkeypatch):
    capturado = {}
    def obtener(db, bitacora_id, *, empresa_id=None):
        capturado.update(id=bitacora_id, empresa_id=empresa_id)
        return None
    monkeypatch.setattr(
        "app.modules.bitacora.services.service.BitacoraRepository.obtener_por_id", obtener
    )
    assert BitacoraService.obtener(FakeDB(), 99, empresa_id=7) is None
    assert capturado == {"id": 99, "empresa_id": 7}


def test_propuesta_sql_permanece_bloqueada_y_sin_bypassrls():
    ruta = "database/saas/preparacion_pg17/080_bootstrap_empresas_registro_cliente_PROPUESTA_NO_EJECUTAR.sql"
    sql = open(ruta, encoding="utf-8").read()
    assert "GUARDA: PROPUESTA 080 NO AUTORIZADA" in sql
    assert "ALTER ROLE fashionstore_bootstrap_owner BYPASSRLS" not in sql
    assert "TO fashionstore_bootstrap;" in sql
    assert "FROM PUBLIC" in sql
