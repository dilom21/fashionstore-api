"""CU13 SaaS: venta presencial y pago tenant sin conexiones externas."""
import os
from datetime import datetime, timezone
from decimal import Decimal
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
from app.modules.pagos.api import tenant_router as pagos_router
from app.modules.pagos.repositories.tenant_repository import PagoTenantRepository
from app.modules.pagos.schemas.schemas import (
    MetodoPago,
    RegistrarPagoPresencialRequest,
)
from app.modules.pagos.services import tenant_service as pago_servicio
from app.modules.roles.services.service import RolService
from app.modules.ventas.api import tenant_presencial_router
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.schemas.schemas import (
    RegistrarVentaPresencialRequest,
    VentaPresencialItemRequest,
)
from app.modules.ventas.services import tenant_presencial_service as venta_servicio


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def scalar(self, *_a, **_k):
        return None


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


def venta(estado="PENDIENTE", sucursal_id=5, canal="PRESENCIAL", reserva_id=None):
    return SimpleNamespace(
        id=900,
        cliente_id=31,
        empleado_id=11,
        sucursal_id=sucursal_id,
        reserva_id=reserva_id,
        canal=canal,
        estado=estado,
        total=Decimal("120.00"),
        fecha_hora=datetime.now(timezone.utc),
        detalles=[],
        sucursal=SimpleNamespace(nombre="Sucursal"),
    )


def test_venta_rechaza_cliente(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    cliente = contexto("CLIENTE")
    object.__setattr__(cliente, "contexto", "cliente")
    with pytest.raises(venta_servicio.VentaTenantRolNoAutorizadoError):
        venta_servicio.VentaTenantPresencialService.registrar(
            FakeDB(),
            cliente,
            RegistrarVentaPresencialRequest(
                items=[VentaPresencialItemRequest(inventario_id=1, cantidad=1)]
            ),
        )


def test_venta_desde_reserva_ajena_es_403(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        venta_servicio.ReservaTenantGestionRepository,
        "obtener_por_id",
        lambda *_a, **_k: SimpleNamespace(
            id=77, estado="CONFIRMADA", sucursal_id=6
        ),
    )
    with pytest.raises(venta_servicio.VentaTenantScopeError):
        venta_servicio.VentaTenantPresencialService.registrar(
            db,
            contexto("ENCARGADO_SUCURSAL"),
            RegistrarVentaPresencialRequest(
                reserva_id=77,
                items=[VentaPresencialItemRequest(inventario_id=1, cantidad=1)],
            ),
        )


def test_venta_directa_llama_sp_con_empresa_del_jwt(monkeypatch):
    db = FakeDB()
    llamadas = []
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        venta_servicio,
        "crear_venta_transaccional_tenant",
        lambda _db, **kwargs: llamadas.append(kwargs) or 900,
    )
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        lambda *_a, **_k: venta(),
    )
    monkeypatch.setattr(
        venta_servicio, "_venta_response", lambda _db, _v: "RESPUESTA"
    )

    resultado = venta_servicio.VentaTenantPresencialService.registrar(
        db,
        contexto("ENCARGADO_SUCURSAL", empresa_id=7),
        RegistrarVentaPresencialRequest(
            cliente_id=None,
            items=[VentaPresencialItemRequest(inventario_id=100, cantidad=2)],
        ),
    )

    assert resultado == "RESPUESTA"
    assert llamadas[0]["tenant"].empresa_id == 7
    assert llamadas[0]["tenant"].usuario_id == 10
    assert llamadas[0]["items"] == [(100, 2)]
    assert db.commits == 1


def test_pago_duplicado_no_llama_sp(monkeypatch):
    db = FakeDB()
    llamadas = []
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        lambda *_a, **_k: venta(),
    )
    monkeypatch.setattr(
        PagoTenantRepository,
        "obtener_pago_aprobado",
        lambda *_a, **_k: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        pago_servicio,
        "registrar_pago_transaccional_tenant",
        lambda _db, **kwargs: llamadas.append(kwargs) or 1,
    )
    with pytest.raises(pago_servicio.PagoTenantYaAprobadoError):
        pago_servicio.PagoTenantPresencialService.registrar(
            db,
            contexto("CAJERO"),
            RegistrarPagoPresencialRequest(
                venta_id=900, metodo=MetodoPago.EFECTIVO
            ),
        )
    assert llamadas == []
    assert db.commits == 0


def test_pago_otra_sucursal_es_403(monkeypatch):
    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        lambda *_a, **_k: venta(sucursal_id=6),
    )
    with pytest.raises(pago_servicio.PagoTenantScopeError):
        pago_servicio.PagoTenantPresencialService.registrar(
            FakeDB(),
            contexto("CAJERO"),
            RegistrarPagoPresencialRequest(
                venta_id=900, metodo=MetodoPago.EFECTIVO
            ),
        )


def test_pago_valido_compromete_y_libera_reserva(monkeypatch):
    db = FakeDB()
    llamadas = []
    lecturas = {"n": 0}

    def obtener_venta(*_a, **_k):
        lecturas["n"] += 1
        if lecturas["n"] == 1:
            return venta(estado="PENDIENTE", reserva_id=77)
        return venta(estado="COMPLETADA", reserva_id=77)

    monkeypatch.setattr(
        TenantAuthRepository,
        "obtener_perfil_personal",
        lambda *_a, **_k: perfil(5),
    )
    monkeypatch.setattr(
        VentaTenantRepository,
        "obtener_por_id",
        obtener_venta,
    )
    monkeypatch.setattr(
        PagoTenantRepository,
        "obtener_pago_aprobado",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        PagoTenantRepository,
        "obtener_pago",
        lambda *_a, **_k: SimpleNamespace(
            id=501,
            metodo="EFECTIVO",
            estado="APROBADO",
            monto=Decimal("120.00"),
            fecha_hora=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        PagoTenantRepository,
        "obtener_reserva",
        lambda *_a, **_k: SimpleNamespace(estado="ATENDIDA"),
    )
    monkeypatch.setattr(
        pago_servicio,
        "registrar_pago_transaccional_tenant",
        lambda _db, **kwargs: llamadas.append(kwargs) or 501,
    )

    respuesta = pago_servicio.PagoTenantPresencialService.registrar(
        db,
        contexto("CAJERO"),
        RegistrarPagoPresencialRequest(
            venta_id=900, metodo=MetodoPago.EFECTIVO
        ),
    )

    assert respuesta.pago_id == 501
    assert respuesta.estado_venta == "COMPLETADA"
    assert respuesta.estado_reserva == "ATENDIDA"
    assert llamadas == [
        {
            "tenant": contexto("CAJERO"),
            "venta_id": 900,
            "metodo": "EFECTIVO",
        }
    ]
    assert db.commits == 1


class _Orig:
    def __init__(self, sqlstate, mensaje):
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=None)
        self._mensaje = mensaje

    def __str__(self):
        return self._mensaje


def _dbapi(sqlstate, mensaje):
    return SimpleNamespace(orig=_Orig(sqlstate, mensaje))


def test_traduccion_stock_duplicado_y_metodo():
    assert isinstance(
        venta_servicio._traducir_error_bd(
            _dbapi("P0001", "Stock insuficiente")
        ),
        venta_servicio.VentaTenantStockError,
    )
    assert isinstance(
        venta_servicio._traducir_error_bd(
            _dbapi("P0001", "La reserva ya tiene una venta")
        ),
        venta_servicio.VentaTenantDuplicadaError,
    )
    assert isinstance(
        pago_servicio._traducir_error_bd(
            _dbapi("P0001", "La venta ya tiene un pago aprobado")
        ),
        pago_servicio.PagoTenantYaAprobadoError,
    )
    assert isinstance(
        pago_servicio._traducir_error_bd(
            _dbapi("22023", "Metodo de pago no permitido")
        ),
        pago_servicio.PagoTenantMetodoError,
    )


@pytest.fixture()
def app_router(monkeypatch):
    app = FastAPI()
    app.include_router(tenant_presencial_router.router)
    app.include_router(pagos_router.router)
    fake_db = FakeDB()
    ctx = contexto("CAJERO")

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_tenant_context] = lambda: ctx
    monkeypatch.setattr(RolService, "tiene_permiso", lambda *_a, **_k: True)
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    with TestClient(app) as cliente:
        yield cliente, fake_db


def _venta_dict():
    ahora = datetime.now(timezone.utc).isoformat()
    return {
        "venta_id": 900,
        "cliente_id": None,
        "empleado_id": 11,
        "sucursal_id": 5,
        "sucursal_nombre": "Sucursal",
        "reserva_id": None,
        "canal": "PRESENCIAL",
        "estado": "PENDIENTE",
        "fecha_hora": ahora,
        "total": "120.00",
        "items": [],
        "cantidad_total_unidades": 0,
    }


def test_router_venta_y_pago_usan_tenant(app_router, monkeypatch):
    cliente, _db = app_router
    llamadas = []
    monkeypatch.setattr(
        tenant_presencial_router.VentaTenantPresencialService,
        "registrar",
        lambda _db, tenant, _datos: llamadas.append(
            ("venta", tenant.empresa_id, tenant.usuario_id)
        ) or _venta_dict(),
    )
    monkeypatch.setattr(
        pagos_router.PagoTenantPresencialService,
        "registrar",
        lambda _db, tenant, datos: llamadas.append(
            ("pago", tenant.empresa_id, tenant.usuario_id, datos.venta_id)
        )
        or {
            "pago_id": 501,
            "venta_id": 900,
            "metodo": "EFECTIVO",
            "estado_pago": "APROBADO",
            "monto": "120.00",
            "fecha": datetime.now(timezone.utc).isoformat(),
            "estado_venta": "COMPLETADA",
            "reserva_id": None,
            "estado_reserva": None,
        },
    )

    venta_resp = cliente.post(
        "/tenant/ventas/presencial",
        json={"items": [{"inventario_id": 100, "cantidad": 1}]},
    )
    assert venta_resp.status_code == 201, venta_resp.text
    pago_resp = cliente.post(
        "/tenant/pagos/presencial",
        json={"venta_id": 900, "metodo": "EFECTIVO"},
    )
    assert pago_resp.status_code == 201, pago_resp.text
    assert llamadas == [("venta", 1, 10), ("pago", 1, 10, 900)]


def test_router_flag_apagado_cierra_cu13(app_router, monkeypatch):
    cliente, _db = app_router
    monkeypatch.setattr(settings, "saas_ventas_enabled", False)
    assert (
        cliente.post(
            "/tenant/ventas/presencial",
            json={"items": [{"inventario_id": 1, "cantidad": 1}]},
        ).status_code
        == 503
    )
    assert (
        cliente.post(
            "/tenant/pagos/presencial",
            json={"venta_id": 1, "metodo": "EFECTIVO"},
        ).status_code
        == 503
    )


def test_gate_cu13_solo_abre_rutas_exactas(monkeypatch):
    monkeypatch.setattr(settings, "saas_ventas_enabled", True)
    assert _ruta_saas_permitida("POST", "/tenant/ventas/presencial")
    assert _ruta_saas_permitida("GET", "/tenant/ventas/900")
    assert _ruta_saas_permitida("POST", "/tenant/pagos/presencial")
    assert not _ruta_saas_permitida("POST", "/tenant/ventas")
    assert not _ruta_saas_permitida("GET", "/tenant/ventas/0")
    assert not _ruta_saas_permitida("PATCH", "/tenant/pagos/presencial")


class _Resultado:
    def __init__(self, fila):
        self.fila = fila

    def mappings(self):
        return self

    def one_or_none(self):
        return self.fila


class _PreflightDB:
    def __init__(self, principal, spmeta, actormeta, actor_ok=True):
        self.principal = principal
        self.spmeta = spmeta
        self.actormeta = actormeta
        self.actor_ok = actor_ok

    def execute(self, sentencia, params=None):
        texto = str(sentencia)
        if "session_user AS principal" in texto:
            return _Resultado(self.principal)
        if "p.prokind='p'" in texto and "p.proname=:procedimiento" in texto:
            return _Resultado(self.spmeta)
        if "fs_verificar_actor_reserva(bigint,bigint)" in texto:
            return _Resultado(self.actormeta)
        raise AssertionError(f"consulta inesperada: {texto[:80]}")

    def scalar(self, _sentencia, _params=None):
        return self.actor_ok


def _principal(**_cambios):
    fila = {
        "principal": "api_login",
        "restringido": True,
        "rls_empresas": True,
        "rls_ventas": True,
        "rls_inventario": True,
        "rls_reservas": True,
        "empresas_visibles": 1,
        "empresa_esperada_visible": 1,
        "planes_venta_vigentes": 1,
        "procedimientos_autorizados": 0,
    }
    fila.update(_cambios)
    return fila


def _spmeta(**_cambios):
    fila = {
        "propietario": "fashionstore_sp_owner",
        "security_definer": True,
        "path_seguro": True,
        "puede_ejecutar": True,
        "propietario_restringido": True,
        "owner_sin_ddl": True,
        "llamador_ajeno_al_propietario": True,
        "llamador_sin_ddl": True,
        "publico_revocado": True,
    }
    fila.update(_cambios)
    return fila


def _actormeta(**_cambios):
    fila = {
        "propietario": "fashionstore_context_owner",
        "security_definer": True,
        "path_seguro": True,
        "puede_ejecutar": True,
        "propietario_restringido": True,
        "llamador_sin_ddl": True,
        "llamador_ajeno_al_propietario": True,
        "publico_revocado": True,
    }
    fila.update(_cambios)
    return fila


def test_preflight_venta_falla_cerrado(monkeypatch):
    from app.modules.ventas.services import tenant_transaccional

    monkeypatch.setattr(settings, "saas_sp_sql_owner", "fashionstore_sp_owner")
    monkeypatch.setattr(
        settings, "saas_actor_sql_owner", "fashionstore_context_owner"
    )
    with pytest.raises(ValueError):
        tenant_transaccional.verificar_principal_ventas(
            _PreflightDB(_principal(), _spmeta(), _actormeta()),
            contexto("CAJERO"),
            "sp_legacy_prohibido",
        )
    for cambio in (
        {"rls_ventas": False},
        {"rls_reservas": False},
        {"empresas_visibles": 2},
        {"planes_venta_vigentes": 0},
        {"restringido": False},
    ):
        with pytest.raises(tenant_transaccional.VentaTenantDeshabilitada):
            tenant_transaccional.verificar_principal_ventas(
                _PreflightDB(_principal(**cambio), _spmeta(), _actormeta()),
                contexto("CAJERO"),
                "sp_crear_venta_presencial_tenant",
            )


def test_preflight_venta_valido_usa_owner_exacto(monkeypatch):
    from app.modules.ventas.services import tenant_transaccional

    monkeypatch.setattr(settings, "saas_sp_sql_owner", "fashionstore_sp_owner")
    monkeypatch.setattr(
        settings, "saas_actor_sql_owner", "fashionstore_context_owner"
    )
    principal = tenant_transaccional.verificar_principal_ventas(
        _PreflightDB(_principal(), _spmeta(), _actormeta()),
        contexto("CAJERO"),
        "sp_registrar_pago_presencial_tenant",
    )
    assert principal == "api_login"
    with pytest.raises(tenant_transaccional.VentaTenantDeshabilitada):
        tenant_transaccional.verificar_principal_ventas(
            _PreflightDB(_principal(), _spmeta(propietario="otro"), _actormeta()),
            contexto("CAJERO"),
            "sp_registrar_pago_presencial_tenant",
        )
