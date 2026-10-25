"""5AO: JWT firmado real -> membresía revalidada -> CU16-18 HTTP.

Usa TestClient, token PyJWT REAL de la app y dependencia real
get_current_tenant_context. Reemplaza exclusivamente acceso a PostgreSQL
con fixtures falsas y los CALL transaccionales con simuladores.

NO acredita aislamiento SQL intraempresa ni sustituye RLS/ACL reales.
Las banderas se habilitan solo con monkeypatch dentro de cada test.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"
os.environ["SAAS_RESERVAS_ENABLED"] = "false"

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import BloqueoRutasLegacySaaS
from app.core.tenant_context import emitir_token_empresa
from app.modules.autenticacion_seguridad.repositories.tenant_repository import TenantAuthRepository
from app.modules.reservas.api import tenant_reserva_write_router as mod
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    ReservaTenantIntegridadError,
)

ACTORES = {
    (101, 3, "cliente"): ("elegancia-sur", "CLIENTE", 7),
    (102, 3, "cliente"): ("elegancia-sur", "CLIENTE", 7),
    (201, 4, "personal"): ("denim-house", "ADMINISTRADOR", 3),
    (301, 3, "personal"): ("elegancia-sur", "ADMINISTRADOR", 3),
}
CARRITO_DUENO = {11: (3, 101), 12: (3, 102), 21: (4, 201)}
RESERVA_DUENO = {111: (3, 101), 112: (3, 102), 211: (4, 201)}


class DummySession:
    def __init__(self):
        self.info = {}
        self.statements = []

    def execute(self, sql, params):
        # Solo dos set_config de AUDITORÍA ejecutados por la dependencia.
        self.statements.append((str(sql), dict(params)))
        assert "set_config(" in str(sql)
        return None


@pytest.fixture()
def sesion(monkeypatch):
    app = FastAPI()
    app.add_middleware(BloqueoRutasLegacySaaS)
    app.include_router(mod.router)

    db = DummySession()
    allowed = set(ACTORES)
    llamadas = []

    def db_fixture():
        yield db

    def membresia(_db, *, usuario_id, empresa_id=None,
                  empresa_slug=None, contexto):
        assert _db is db
        assert empresa_slug is None and empresa_id is not None
        key = (usuario_id, empresa_id, contexto)
        if key not in allowed:
            return None
        slug, rol, rol_id = ACTORES[key]
        return {
            "empresa_id": empresa_id,
            "empresa_slug": slug,
            "empresa_nombre": slug,
            "rol_id": rol_id,
            "rol_nombre": rol,
            "plan_codigo": "PROFESIONAL" if empresa_id == 3 else "BASICO",
            "permite_reportes_ia": empresa_id == 3,
        }

    def crear(_db, *, tenant, carrito_id, fecha_atencion, observacion):
        assert _db is db
        assert fecha_atencion.tzinfo is not None
        llamadas.append(("crear",tenant.usuario_id,tenant.empresa_id,carrito_id))
        if CARRITO_DUENO.get(carrito_id) != (tenant.empresa_id, tenant.usuario_id):
            raise ReservaTenantIntegridadError("Carrito ajeno")
        return carrito_id + 100

    def cancelar(_db, *, tenant, reserva_id, observacion):
        assert _db is db
        llamadas.append(("cancelar",tenant.usuario_id,tenant.empresa_id,reserva_id))
        if tenant.contexto == "cliente" and (
            RESERVA_DUENO.get(reserva_id) != (tenant.empresa_id,tenant.usuario_id)
        ):
            raise ReservaTenantIntegridadError("Reserva ajena")
        if RESERVA_DUENO.get(reserva_id, (None,None))[0] != tenant.empresa_id:
            raise ReservaTenantIntegridadError("Empresa ajena")
        return "CANCELADA"

    def finalizar(_db, *, tenant, reserva_id, observacion):
        assert _db is db
        llamadas.append(("finalizar",tenant.usuario_id,tenant.empresa_id,reserva_id))
        if RESERVA_DUENO.get(reserva_id, (None,None))[0] != tenant.empresa_id:
            raise ReservaTenantIntegridadError("Empresa ajena")
        return "ATENDIDA"

    monkeypatch.setattr(settings,"saas_auth_enabled",True)
    monkeypatch.setattr(settings,"saas_reservas_enabled",True)
    monkeypatch.setattr(TenantAuthRepository,"obtener_membresia_vigente",
                        staticmethod(membresia))
    monkeypatch.setattr(mod,"crear_reserva_transaccional_sin_exposicion_http",crear)
    monkeypatch.setattr(mod,"cancelar_reserva_transaccional_tenant",cancelar)
    monkeypatch.setattr(mod,"finalizar_reserva_sin_compra_transaccional_tenant",finalizar)
    app.dependency_overrides[get_db]=db_fixture

    try:
        with TestClient(app) as http:
            yield http, db, allowed, llamadas, monkeypatch
    finally:
        app.dependency_overrides.clear()


def token(usuario=101, empresa=3, contexto="cliente"):
    return emitir_token_empresa(
        usuario_id=usuario,empresa_id=empresa,contexto=contexto
    )


def headers(usuario=101, empresa=3, contexto="cliente"):
    return {"Authorization":f"Bearer {token(usuario,empresa,contexto)}"}


def datos(carrito=11,**kwargs):
    return {
        "carrito_id":carrito,
        "fecha_atencion":(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
        **kwargs
    }


def test_5ao_sin_token_401_ni_acceso_db(sesion):
    http,db,_,calls,_=sesion
    response=http.post("/tenant/reservas",json=datos())
    assert response.status_code==401
    assert not db.statements and not calls


@pytest.mark.parametrize("auth",[
    "Bearer x.y.z",
    "Bearer token-ilegible",
    "Basic usuario:pass",
])
def test_5ao_token_invalido_401(sesion,auth):
    http,db,_,calls,_=sesion
    assert http.post("/tenant/reservas",json=datos(),
                     headers={"Authorization":auth}).status_code==401
    assert not calls


def test_5ao_firma_falsa_401(sesion):
    http,_,_,calls,_=sesion
    fake=jwt.encode(
        {"sub":"101","scope":"tenant","empresa_id":"3","contexto":"cliente",
         "jti":"arbitrario","exp":datetime.now(timezone.utc)+timedelta(hours=1)},
        "FIRMA-NO-PERTENECE-AL-SERVIDOR",algorithm="HS256"
    )
    assert http.post("/tenant/reservas",json=datos(),
                     headers={"Authorization":f"Bearer {fake}"}).status_code==401
    assert not calls


def test_5ao_token_expirado_401(sesion):
    http,_,_,calls,_=sesion
    expired=jwt.encode(
        {"sub":"101","scope":"tenant","empresa_id":"3","contexto":"cliente",
         "jti":"expirado","exp":datetime.now(timezone.utc)-timedelta(minutes=1)},
        settings.secret_key,algorithm=settings.jwt_algorithm
    )
    assert http.post("/tenant/reservas",json=datos(),
                     headers={"Authorization":f"Bearer {expired}"}).status_code==401
    assert not calls


def test_5ao_cliente_firmado_recibe_su_empresa_y_usuario(sesion):
    http,db,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(),headers=headers())
    assert r.status_code==201,r.text
    assert r.json()=={"reserva_id":111,"carrito_id":11,"estado":"PENDIENTE"}
    assert calls==[("crear",101,3,11)]
    assert db.info=={"usuario_id":101,"empresa_id":3}
    assert len(db.statements)==2
    assert db.statements[0][1]["usuario_id"]=="101"
    assert db.statements[1][1]["empresa_id"]=="3"


def test_5ao_segundo_usuario_misma_empresa_actor_inmutable(sesion):
    http,db,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(carrito=12),
                headers=headers(102,3))
    assert r.status_code==201,r.text
    assert calls==[("crear",102,3,12)]
    assert db.info["usuario_id"]==102 and db.info["empresa_id"]==3


def test_5ao_no_permitir_carrito_de_otro_usuario_misma_empresa(sesion):
    http,db,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(carrito=12),
                headers=headers(101,3))
    assert r.status_code==409
    assert calls==[("crear",101,3,12)]


def test_5ao_no_permitir_reserva_ajena_misma_empresa(sesion):
    http,_,_,calls,_=sesion
    r=http.patch("/tenant/reservas/112/cancelar",json={},
                 headers=headers(101,3))
    assert r.status_code==409
    assert calls==[("cancelar",101,3,112)]


def test_5ao_no_permitir_recurso_otra_empresa(sesion):
    http,_,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(carrito=21),headers=headers())
    assert r.status_code==409
    assert calls==[("crear",101,3,21)]


@pytest.mark.parametrize("extra",[
    {"usuario_id":102},{"empresa_id":4},{"rol_nombre":"ADMINISTRADOR"},
])
def test_5ao_body_no_suplanta_claim_firmado(sesion,extra):
    http,_,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(**extra),headers=headers())
    assert r.status_code==422
    assert not calls


def test_5ao_membresia_revocada_403_antes_de_sp(sesion):
    http,db,allowed,calls,_=sesion
    allowed.remove((101,3,"cliente"))
    r=http.post("/tenant/reservas",json=datos(),headers=headers())
    assert r.status_code==403
    assert not calls
    assert "empresa_id" not in db.info


def test_5ao_claim_empresa_incorrecta_403(sesion):
    http,_,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(),
                headers=headers(101,4))
    assert r.status_code==403
    assert not calls


def test_5ao_contexto_personal_no_crea_reserva_cliente(sesion):
    http,_,_,calls,_=sesion
    r=http.post("/tenant/reservas",json=datos(),
                headers=headers(301,3,"personal"))
    assert r.status_code==403
    assert not calls


def test_5ao_cancelacion_firmada_perfil_cliente(sesion):
    http,_,_,calls,_=sesion
    r=http.patch("/tenant/reservas/111/cancelar",json={},
                 headers=headers())
    assert r.status_code==200,r.text
    assert r.json()=={"reserva_id":111,"estado":"CANCELADA"}
    assert calls==[("cancelar",101,3,111)]


def test_5ao_finalizacion_firmada_admin_b(sesion):
    http,_,_,calls,_=sesion
    r=http.patch("/tenant/reservas/211/finalizar-sin-compra",json={},
                 headers=headers(201,4,"personal"))
    assert r.status_code==200,r.text
    assert r.json()=={"reserva_id":211,"estado":"ATENDIDA"}
    assert calls==[("finalizar",201,4,211)]


def test_5ao_cliente_no_puede_finalizar(sesion):
    http,_,_,calls,_=sesion
    r=http.patch("/tenant/reservas/111/finalizar-sin-compra",json={},
                 headers=headers())
    assert r.status_code==403
    assert not calls


@pytest.mark.parametrize("s_auth,s_res",[ (False,True),(True,False),(False,False) ])
def test_5ao_doble_flag_falla_cerrado(sesion,s_auth,s_res):
    http,_,_,calls,mp=sesion
    mp.setattr(settings,"saas_auth_enabled",s_auth)
    mp.setattr(settings,"saas_reservas_enabled",s_res)
    r=http.post("/tenant/reservas",json=datos(),headers=headers())
    assert r.status_code==503
    assert not calls
