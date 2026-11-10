"""CU23 SaaS: comprobante tenant reutilizando el DTO de CU23."""
import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

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
from app.modules.pagos.repositories.tenant_repository import PagoTenantRepository
from app.modules.roles.services.service import RolService
from app.modules.ventas.api import tenant_presencial_router
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.services.comprobante_service import (
    ComprobanteNoAutorizadoError,
    ComprobanteVentaService,
    PagoAprobadoNoEncontradoError,
    VentaComprobanteNoEncontradaError,
    VentaNoCompletadaError,
)


class FakeDB:
    info = {}


def tenant(rol="CAJERO", contexto="personal", empresa_id=15):
    return TenantContext(
        usuario_id=42,
        empresa_id=empresa_id,
        empresa_slug="empresa-a",
        empresa_nombre="Empresa A",
        rol_id=4,
        rol_nombre=rol,
        contexto=contexto,
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=False,
    )


def perfil(sucursal_id=5):
    return PerfilPersonalTenant(
        correo="cajero@example.test",
        empleado_id=11,
        nombre="Carla",
        apellido="Paz",
        sucursal_id=sucursal_id,
    )


def pago_demo():
    return SimpleNamespace(
        id=336,
        fecha_hora=datetime(2026, 11, 9, tzinfo=timezone.utc),
        monto=Decimal("149.90"),
        metodo="EFECTIVO",
        estado="APROBADO",
        referencia_transaccion=None,
        pasarela=None,
    )


def venta_demo(estado="COMPLETADA", sucursal_id=5):
    ahora = datetime(2026, 11, 9, tzinfo=timezone.utc)
    variante = SimpleNamespace(
        id=4,
        sku="CAM-AZ-M",
        producto=SimpleNamespace(id=3, nombre="Camisa"),
        talla=SimpleNamespace(nombre="M"),
        color=SimpleNamespace(nombre="Azul"),
    )
    inventario = SimpleNamespace(
        id=8, variante_producto=variante,
    )
    detalle = SimpleNamespace(
        id=21,
        inventario_id=8,
        inventario=inventario,
        cantidad=1,
        precio_unitario=Decimal("149.90"),
    )
    return SimpleNamespace(
        id=689,
        cliente_id=None,
        empleado_id=None,
        sucursal_id=sucursal_id,
        reserva_id=650,
        carrito_id=None,
        fecha_hora=ahora,
        canal="PRESENCIAL",
        estado=estado,
        total=Decimal("149.90"),
        detalles=[detalle],
        sucursal=SimpleNamespace(
            id=sucursal_id,
            nombre="Central",
            direccion="Calle 1",
            telefono=None,
        ),
    )


@pytest.fixture(autouse=True)
def perfil_cajero(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda db, **kw: perfil(5),
    )


def test_cajero_comprobante_su_sucursal_reutiliza_dto(monkeypatch):
    llamadas = {}
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        lambda db, **kw: (llamadas.update({"venta": kw}) or venta_demo()),
    )
    monkeypatch.setattr(
        PagoTenantRepository,
        "obtener_pago_aprobado",
        lambda db, **kw: (llamadas.update({"pago": kw}) or pago_demo()),
    )
    respuesta = ComprobanteVentaService.generar_comprobante_tenant(
        FakeDB(), tenant("CAJERO", empresa_id=15), 689
    )
    assert respuesta.venta_id == 689
    assert respuesta.estado_venta == "COMPLETADA"
    assert respuesta.total == Decimal("149.90")
    assert respuesta.reserva_id == 650
    assert respuesta.pago.pago_id == 336
    assert respuesta.pago.estado == "APROBADO"
    assert respuesta.items[0].sku == "CAM-AZ-M"
    assert respuesta.sucursal.nombre == "Central"
    assert llamadas["venta"] == {"empresa_id": 15, "venta_id": 689}
    assert llamadas["pago"] == {"empresa_id": 15, "venta_id": 689}


def test_cajero_no_ve_venta_de_otra_sucursal(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(sucursal_id=6),
    )
    with pytest.raises(ComprobanteNoAutorizadoError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CAJERO"), 689
        )


def test_encargado_ve_su_sucursal(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(sucursal_id=5),
    )
    monkeypatch.setattr(
        PagoTenantRepository, "obtener_pago_aprobado",
        lambda db, **kw: pago_demo(),
    )
    respuesta = ComprobanteVentaService.generar_comprobante_tenant(
        FakeDB(), tenant("ENCARGADO_SUCURSAL"), 689
    )
    assert respuesta.venta_id == 689


def test_admin_ve_cualquier_sucursal_sin_empleado(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_perfil_personal", lambda *a, **k: None
    )
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(sucursal_id=9),
    )
    monkeypatch.setattr(
        PagoTenantRepository, "obtener_pago_aprobado",
        lambda db, **kw: pago_demo(),
    )
    respuesta = ComprobanteVentaService.generar_comprobante_tenant(
        FakeDB(), tenant("ADMINISTRADOR"), 689
    )
    assert respuesta.venta_id == 689


def test_cliente_no_usa_comprobante_personal(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(),
    )
    with pytest.raises(ComprobanteNoAutorizadoError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CLIENTE", contexto="cliente"), 689
        )


def test_cajero_sin_empleado_no_autorizado(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(),
    )
    monkeypatch.setattr(
        TenantAuthRepository, "obtener_perfil_personal", lambda *a, **k: None
    )
    with pytest.raises(ComprobanteNoAutorizadoError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CAJERO"), 689
        )


def test_venta_ajena_por_empresa_es_404(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        lambda db, **kw: None,
    )
    with pytest.raises(VentaComprobanteNoEncontradaError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CAJERO", empresa_id=15), 689
        )


def test_venta_no_completada_409(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(estado="PENDIENTE"),
    )
    with pytest.raises(VentaNoCompletadaError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CAJERO"), 689
        )


def test_venta_sin_pago_aprobado_409(monkeypatch):
    monkeypatch.setattr(
        VentaTenantRepository, "obtener_por_id",
        lambda db, **kw: venta_demo(),
    )
    monkeypatch.setattr(
        PagoTenantRepository, "obtener_pago_aprobado",
        lambda db, **kw: None,
    )
    with pytest.raises(PagoAprobadoNoEncontradoError):
        ComprobanteVentaService.generar_comprobante_tenant(
            FakeDB(), tenant("CAJERO"), 689
        )


@pytest.fixture()
def app_tenant(monkeypatch):
    app = FastAPI()
    app.include_router(tenant_presencial_router.router)
    fake_db = FakeDB()
    ctx = tenant("CAJERO")
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_tenant_context] = lambda: ctx
    monkeypatch.setattr(RolService, "tiene_permiso", lambda *_a, **_k: True)
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    with TestClient(app) as cliente:
        yield cliente


def _comprobante_dict():
    ahora = datetime(2026, 11, 9, tzinfo=timezone.utc).isoformat()
    return {
        "venta_id": 689,
        "fecha_hora": ahora,
        "canal": "PRESENCIAL",
        "estado_venta": "COMPLETADA",
        "total": "149.90",
        "carrito_id": None,
        "reserva_id": 650,
        "cliente": None,
        "empleado": None,
        "sucursal": {
            "id": 5,
            "nombre": "Central",
            "direccion": "Calle 1",
            "telefono": None,
        },
        "pago": {
            "pago_id": 336,
            "fecha_hora": ahora,
            "monto": "149.90",
            "metodo": "EFECTIVO",
            "estado": "APROBADO",
            "referencia_transaccion": None,
            "pasarela": None,
        },
        "items": [],
        "cantidad_total_unidades": 0,
    }


def test_router_comprobante_tenant_contrato(app_tenant, monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        tenant_presencial_router.ComprobanteVentaService,
        "generar_comprobante_tenant",
        lambda _db, tenant_ctx, venta_id: llamadas.append(
            (tenant_ctx.empresa_id, tenant_ctx.usuario_id, venta_id)
        ) or _comprobante_dict(),
    )
    respuesta = app_tenant.get("/tenant/ventas/689/comprobante")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["venta_id"] == 689
    assert respuesta.json()["pago"]["pago_id"] == 336
    assert respuesta.json()["reserva_id"] == 650
    assert llamadas == [(15, 42, 689)]


def test_router_comprobante_flag_apagado(app_tenant, monkeypatch):
    monkeypatch.setattr(settings, "saas_ventas_enabled", False)
    assert (
        app_tenant.get("/tenant/ventas/689/comprobante").status_code == 503
    )


def test_gate_comprobante_exacto(monkeypatch):
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    assert _ruta_saas_permitida("GET", "/tenant/ventas/689/comprobante")
    assert not _ruta_saas_permitida("GET", "/tenant/ventas/0/comprobante")
    assert not _ruta_saas_permitida(
        "GET", "/tenant/ventas/689/comprobante/extra"
    )
    assert not _ruta_saas_permitida(
        "POST", "/tenant/ventas/689/comprobante"
    )
