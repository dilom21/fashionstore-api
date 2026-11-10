"""CU12 SaaS: contratos, roles y alcance sin abrir conexiones externas."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./cu12_cu13_unit.db")
os.environ.setdefault("SECRET_KEY", "clave-ficticia-cu12-cu13")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    PerfilPersonalTenant,
    TenantAuthRepository,
)
from app.modules.reservas.api import tenant_atencion_router, tenant_gestion_router
from app.modules.reservas.repositories.tenant_gestion_repository import (
    ReservaTenantGestionRepository,
)
from app.modules.reservas.schemas.schemas import (
    PrepararVentaItemRequest,
    PrepararVentaRequest,
)
from app.modules.reservas.services import tenant_gestion_service as servicio
from app.modules.roles.services.service import RolService


class FakeDB:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value

    def scalar(self, *_args, **_kwargs):
        return self.scalar_value


def contexto(rol="ENCARGADO_SUCURSAL", *, empresa_id=1, usuario_id=10):
    return TenantContext(
        usuario_id=usuario_id,
        empresa_id=empresa_id,
        empresa_slug="empresa-a",
        empresa_nombre="Empresa A",
        rol_id=2,
        rol_nombre=rol,
        contexto="personal",
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


def perfil(sucursal_id=5):
    return PerfilPersonalTenant(
        correo="staff@example.test",
        empleado_id=11,
        nombre="Ana",
        apellido="Perez",
        sucursal_id=sucursal_id,
    )


def reserva(estado="PENDIENTE", sucursal_id=5, detalles=None, id_=91):
    ahora = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=id_,
        estado=estado,
        sucursal_id=sucursal_id,
        cliente_id=31,
        carrito_id=15,
        fecha_reserva=ahora,
        fecha_atencion=ahora,
        observacion=None,
        detalles=detalles or [],
        cliente=SimpleNamespace(
            nombre="Cliente", apellido="Demo", telefono=None
        ),
        sucursal=SimpleNamespace(nombre="Sucursal"),
    )


def test_gestion_encargado_usa_su_sucursal_sin_empresa_del_request(monkeypatch):
    db = FakeDB()
    llamadas = []
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        ReservaTenantGestionRepository,
        "listar",
        lambda _db, **kwargs: (llamadas.append(kwargs) or [], 0),
    )

    respuesta = servicio.ReservaTenantGestionService.listar_gestion(
        db, contexto("ENCARGADO_SUCURSAL")
    )

    assert respuesta.total == 0
    assert llamadas[0]["empresa_id"] == 1
    assert llamadas[0]["sucursal_id"] == 5


def test_encargado_no_amplia_alcance_por_query(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    with pytest.raises(servicio.ReservaTenantScopeError):
        servicio.ReservaTenantGestionService.listar_gestion(
            db, contexto("ENCARGADO_SUCURSAL"), sucursal_id=6
        )


def test_administrador_puede_filtrar_sucursal_de_su_empresa(monkeypatch):
    db = FakeDB(scalar_value=7)
    llamadas = []
    monkeypatch.setattr(
        ReservaTenantGestionRepository,
        "listar",
        lambda _db, **kwargs: (llamadas.append(kwargs) or [], 0),
    )
    servicio.ReservaTenantGestionService.listar_gestion(
        db, contexto("ADMINISTRADOR"), sucursal_id=7
    )
    assert llamadas[0]["empresa_id"] == 1
    assert llamadas[0]["sucursal_id"] == 7


def test_cliente_y_cajero_no_confirman(monkeypatch):
    db = FakeDB()
    with pytest.raises(servicio.ReservaTenantRolNoAutorizadoError):
        servicio.ReservaTenantGestionService.confirmar(
            db, contexto("CAJERO"), 91
        )
    cliente = contexto("CLIENTE")
    object.__setattr__(cliente, "contexto", "cliente")
    with pytest.raises(servicio.ReservaTenantRolNoAutorizadoError):
        servicio.ReservaTenantGestionService.confirmar(db, cliente, 91)


def test_confirmar_usa_sp_tenant_y_no_toca_empresa_del_body(monkeypatch):
    db = FakeDB()
    llamadas = []
    estados = {"veces": 0}

    def obtener(_db, **_kwargs):
        estados["veces"] += 1
        return reserva("PENDIENTE" if estados["veces"] == 1 else "CONFIRMADA")

    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        ReservaTenantGestionRepository, "obtener_por_id", obtener
    )
    monkeypatch.setattr(
        servicio,
        "confirmar_reserva_transaccional_tenant",
        lambda _db, **kwargs: llamadas.append(kwargs) or "CONFIRMADA",
    )
    monkeypatch.setattr(
        servicio, "_detalle_gestion_response", lambda _db, _r: "DETALLE"
    )

    resultado = servicio.ReservaTenantGestionService.confirmar(
        db, contexto("ENCARGADO_SUCURSAL", empresa_id=7), 91
    )

    assert resultado == "DETALLE"
    assert llamadas == [
        {"tenant": contexto("ENCARGADO_SUCURSAL", empresa_id=7), "reserva_id": 91}
    ]


def test_atencion_cajero_solo_confirmadas_y_prepara_venta(monkeypatch):
    db = FakeDB()
    detalle = SimpleNamespace(inventario_id=100, cantidad=2)
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        ReservaTenantGestionRepository,
        "obtener_por_id",
        lambda *_a, **_k: reserva("CONFIRMADA", 5, [detalle]),
    )
    respuesta = servicio.ReservaTenantGestionService.preparar_venta(
        db,
        contexto("CAJERO"),
        91,
        PrepararVentaRequest(
            items=[PrepararVentaItemRequest(inventario_id=100, cantidad_compra=1)]
        ),
    )
    assert respuesta.total_unidades_compra == 1
    assert respuesta.total_unidades_no_compra == 1


def test_detalle_atencion_rechaza_otra_sucursal(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        ReservaTenantGestionRepository,
        "obtener_por_id",
        lambda *_a, **_k: reserva("CONFIRMADA", 6),
    )
    with pytest.raises(servicio.ReservaTenantScopeError):
        servicio.ReservaTenantGestionService.obtener_atencion(
            db, contexto("CAJERO"), 91
        )


@pytest.fixture()
def app_router(monkeypatch):
    app = FastAPI()
    app.include_router(tenant_gestion_router.router)
    app.include_router(tenant_atencion_router.router)
    fake_db = FakeDB()
    ctx = contexto("ENCARGADO_SUCURSAL")

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_tenant_context] = lambda: ctx
    monkeypatch.setattr(RolService, "tiene_permiso", lambda *_a, **_k: True)
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_reservas_enabled", True)
    with TestClient(app) as cliente:
        yield cliente


def test_router_ignora_empresa_id_ajena_y_usa_jwt(app_router, monkeypatch):
    llamadas = []

    def listar(_db, tenant, **kwargs):
        llamadas.append((tenant, kwargs))
        return {"items": [], "total": 0, "limit": 20, "offset": 0}

    monkeypatch.setattr(
        tenant_gestion_router.ReservaTenantGestionService,
        "listar_gestion",
        listar,
    )
    respuesta = app_router.get(
        "/tenant/reservas-sucursal?empresa_id=999&sucursal_id=5"
    )
    assert respuesta.status_code == 200, respuesta.text
    tenant, _kwargs = llamadas[0]
    assert tenant.empresa_id == 1
    assert tenant.usuario_id == 10


def test_router_confirmar_llama_servicio_con_tenant(app_router, monkeypatch):
    llamadas = []
    ahora = datetime.now(timezone.utc).isoformat()

    def confirmar(_db, tenant, reserva_id):
        llamadas.append((tenant.empresa_id, tenant.usuario_id, reserva_id))
        return {
            "reserva_id": reserva_id,
            "carrito_id": 15,
            "cliente_id": 31,
            "cliente_nombre": "Cliente",
            "cliente_apellido": "Demo",
            "cliente_telefono": None,
            "sucursal_id": 5,
            "sucursal_nombre": "Sucursal",
            "fecha_reserva": ahora,
            "fecha_atencion": ahora,
            "estado": "CONFIRMADA",
            "observacion": None,
            "items": [],
            "cantidad_total_unidades": 0,
        }

    monkeypatch.setattr(
        tenant_gestion_router.ReservaTenantGestionService,
        "confirmar",
        confirmar,
    )
    respuesta = app_router.patch(
        "/tenant/reservas-sucursal/91/confirmar"
    )
    assert respuesta.status_code == 200, respuesta.text
    assert llamadas == [(1, 10, 91)]


def test_router_flag_apagado_cierra_cu12(app_router, monkeypatch):
    monkeypatch.setattr(settings, "saas_reservas_enabled", False)
    assert app_router.get("/tenant/reservas-sucursal").status_code == 503
    assert (
        app_router.get("/tenant/atencion-reservas").status_code == 503
    )


def test_gate_cu12_solo_abre_rutas_exactas(monkeypatch):
    monkeypatch.setattr(settings, "saas_reservas_enabled", True)
    assert _ruta_saas_permitida("GET", "/tenant/reservas-sucursal")
    assert _ruta_saas_permitida(
        "GET", "/tenant/reservas-sucursal/estadisticas"
    )
    assert _ruta_saas_permitida("GET", "/tenant/reservas-sucursal/1")
    assert _ruta_saas_permitida(
        "PATCH", "/tenant/reservas-sucursal/1/confirmar"
    )
    assert _ruta_saas_permitida(
        "PATCH", "/tenant/reservas-sucursal/1/cancelar"
    )
    assert _ruta_saas_permitida("GET", "/tenant/atencion-reservas")
    assert _ruta_saas_permitida("GET", "/tenant/atencion-reservas/1")
    assert _ruta_saas_permitida(
        "POST", "/tenant/atencion-reservas/1/preparar-venta"
    )
    assert not _ruta_saas_permitida("GET", "/tenant/reservas-sucursal/0")
    assert not _ruta_saas_permitida(
        "DELETE", "/tenant/reservas-sucursal/1"
    )
