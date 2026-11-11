"""Pruebas dirigidas CU19/CU20: contrato, roles, idempotencia y rutas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_route_gate import _ruta_saas_permitida
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.offline import service as offline_service
from app.modules.offline.repository import hash_payload
from app.modules.offline.schemas import (
    OperacionOfflineRequest, OperacionOfflineResponse,
)
from app.modules.offline.service import (
    OfflineConflicto, OfflineNoAutorizado, OfflineService,
)


def _tenant(**changes):
    values = dict(
        usuario_id=10, empresa_id=1, empresa_slug="empresa-a",
        empresa_nombre="A", rol_id=2, rol_nombre="CLIENTE",
        contexto="cliente", plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )
    values.update(changes)
    return TenantContext(**values)


def _reserva(**changes):
    values = dict(
        operacion_uuid=uuid4(), tipo="RESERVA", sucursal_id=1,
        lineas=[{"variante_id": 5, "cantidad": 1}],
        fecha_atencion=datetime.now(timezone.utc) + timedelta(days=2),
        observacion="offline",
    )
    values.update(changes)
    return OperacionOfflineRequest(**values)


def _venta(**changes):
    values = dict(
        operacion_uuid=uuid4(), tipo="VENTA_CONTADO", sucursal_id=1,
        lineas=[{"inventario_id": 5, "cantidad": 1}],
        efectivo_recibido=Decimal("100.00"),
    )
    values.update(changes)
    return OperacionOfflineRequest(**values)


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_contrato_rechaza_empresa_id_y_campos_cruzados():
    with pytest.raises(ValidationError):
        _reserva(empresa_id=99)
    with pytest.raises(ValidationError):
        _venta(fecha_atencion=datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(ValidationError):
        _reserva(efectivo_recibido=Decimal("10.00"))
    with pytest.raises(ValidationError):
        _reserva(cliente_id=1)
    with pytest.raises(ValidationError):
        _reserva(lineas=[
            {"variante_id": 5, "cantidad": 1},
            {"variante_id": 5, "cantidad": 2},
        ])
    with pytest.raises(ValidationError):
        _reserva(fecha_atencion=datetime.now(timezone.utc) - timedelta(days=1))
    with pytest.raises(ValidationError):
        _reserva(fecha_atencion=datetime.now())
    with pytest.raises(ValidationError):
        _reserva(lineas=[{"cantidad": 1}])
    with pytest.raises(ValidationError):
        _reserva(lineas=[{"variante_id": 9, "inventario_id": 5, "cantidad": 1}])


def test_cu19_cliente_guarda_variante_y_hash_distingue_identificador():
    datos = _reserva(lineas=[{"variante_id": 456, "cantidad": 2}])
    assert datos.lineas[0].variante_id == 456
    assert datos.lineas[0].inventario_id is None
    assert hash_payload(datos) != hash_payload(
        _reserva(lineas=[{"variante_id": 457, "cantidad": 2}])
    )


def test_resolucion_variante_rechaza_sucursal_o_stock_inexistente():
    class _Result:
        def first(self):
            return None

    class _DB:
        def execute(self, *args, **kwargs):
            return _Result()

    with pytest.raises(OfflineConflicto) as exc:
        offline_service._resolver_variantes_reserva(
            _DB(), empresa_id=1, sucursal_id=999,
            lineas=[SimpleNamespace(variante_id=456, cantidad=1)],
        )
    assert exc.value.codigo == "STOCK_O_INVENTARIO"


def test_hash_canonico_ignora_orden_de_lineas():
    fecha = datetime.now(timezone.utc) + timedelta(days=2)
    a = _reserva(fecha_atencion=fecha, lineas=[
        {"variante_id": 5, "cantidad": 1},
        {"variante_id": 3, "cantidad": 2},
    ])
    b = _reserva(fecha_atencion=fecha, lineas=[
        {"variante_id": 3, "cantidad": 2},
        {"variante_id": 5, "cantidad": 1},
    ])
    assert hash_payload(a) == hash_payload(b)
    c = _reserva(fecha_atencion=fecha, lineas=[
        {"variante_id": 5, "cantidad": 2},
        {"variante_id": 3, "cantidad": 2},
    ])
    assert hash_payload(a) != hash_payload(c)


def test_reserva_requiere_cliente_y_venta_rechaza_encargado(monkeypatch):
    with pytest.raises(OfflineNoAutorizado):
        offline_service._validar_actor(
            _tenant(contexto="personal", rol_nombre="ADMINISTRADOR"),
            _reserva(), None,
        )
    with pytest.raises(OfflineNoAutorizado):
        offline_service._validar_actor(
            _tenant(contexto="personal", rol_nombre="ENCARGADO_SUCURSAL"),
            _venta(), None,
        )


def test_cajero_debe_pertenecer_a_la_sucursal(monkeypatch):
    monkeypatch.setattr(
        offline_service.TenantAuthRepository, "obtener_perfil_personal",
        staticmethod(lambda *a, **k: SimpleNamespace(sucursal_id=2)),
    )
    with pytest.raises(OfflineNoAutorizado):
        offline_service._validar_actor(
            _tenant(contexto="personal", rol_nombre="CAJERO"), _venta(), None
        )
    monkeypatch.setattr(
        offline_service.TenantAuthRepository, "obtener_perfil_personal",
        staticmethod(lambda *a, **k: SimpleNamespace(sucursal_id=1)),
    )
    assert offline_service._validar_actor(
        _tenant(contexto="personal", rol_nombre="CAJERO"), _venta(), None
    ) is None


def test_administrador_no_requiere_empleado(monkeypatch):
    assert offline_service._validar_actor(
        _tenant(contexto="personal", rol_nombre="ADMINISTRADOR"), _venta(), None
    ) is None


def test_idempotencia_recupera_aceptada_sin_repetir(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(offline_service, "_validar_actor", lambda *a, **k: 1)
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "begin",
        staticmethod(lambda *a, **k: {
            "procesar": False, "estado": "ACEPTADA", "id_definitivo": 77,
            "resultado": {"venta_id": 77}, "error_codigo": None,
            "error_mensaje": None, "payload_hash": "x" * 64,
        }),
    )
    respuesta, codigo = OfflineService.ejecutar(db, _tenant(), _reserva())
    assert codigo == 200 and respuesta.estado == "ACEPTADA"
    assert respuesta.id_definitivo == 77
    assert db.rollbacks == 1 and db.commits == 0


def test_flujo_aceptado_confirma_ledger_y_comercial(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(offline_service, "_validar_actor", lambda *a, **k: 1)
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "begin",
        staticmethod(lambda *a, **k: {
            "procesar": True, "estado": "ENVIANDO", "id_definitivo": None,
            "resultado": None, "error_codigo": None, "error_mensaje": None,
            "payload_hash": "x" * 64,
        }),
    )
    monkeypatch.setattr(
        offline_service, "_reservar",
        lambda *a, **k: {"reserva_id": 55, "estado": "PENDIENTE"},
    )
    visto = {}
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "finish",
        staticmethod(lambda *a, **k: visto.update(k)),
    )
    respuesta, codigo = OfflineService.ejecutar(db, _tenant(), _reserva())
    assert codigo == 200 and respuesta.estado == "ACEPTADA"
    assert respuesta.id_definitivo == 55 and db.commits == 1
    assert visto["estado"] == "ACEPTADA" and visto["id_definitivo"] == 55


def test_fallo_comercial_registra_evidencia(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(offline_service, "_validar_actor", lambda *a, **k: 1)
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "begin",
        staticmethod(lambda *a, **k: {
            "procesar": True, "estado": "ENVIANDO", "id_definitivo": None,
            "resultado": None, "error_codigo": None, "error_mensaje": None,
            "payload_hash": "x" * 64,
        }),
    )
    monkeypatch.setattr(
        offline_service, "_reservar",
        lambda *a, **k: (_ for _ in ()).throw(
            OfflineConflicto("STOCK_O_INVENTARIO", "sin stock")
        ),
    )
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "rechazar",
        staticmethod(lambda *a, **k: {"estado": "CONFLICTO"}),
    )
    respuesta, codigo = OfflineService.ejecutar(db, _tenant(), _reserva())
    assert codigo == 409 and respuesta.estado == "CONFLICTO"
    assert db.rollbacks == 1 and db.commits == 1


def test_idempotencia_conflicto_y_reintento(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(offline_service, "_validar_actor", lambda *a, **k: 1)
    monkeypatch.setattr(
        offline_service.OfflineLedgerRepository, "begin",
        staticmethod(lambda *a, **k: {
            "procesar": False, "estado": "CONFLICTO", "id_definitivo": None,
            "resultado": None, "error_codigo": "UUID_PAYLOAD_MISMATCH",
            "error_mensaje": "otro contenido", "payload_hash": "x" * 64,
        }),
    )
    respuesta, codigo = OfflineService.ejecutar(db, _tenant(), _reserva())
    assert codigo == 409 and respuesta.estado == "CONFLICTO"
    assert respuesta.reintentable is False


def test_idempotency_key_debe_coincidir():
    datos = _reserva()
    respuesta, codigo = OfflineService.ejecutar(
        _FakeDB(), _tenant(), datos, idempotency_key=str(uuid4())
    )
    assert codigo == 422
    assert respuesta.error_codigo == "IDEMPOTENCY_KEY_INVALIDA"


def test_gate_offline_solo_con_flag(monkeypatch):
    datos = _reserva()
    ruta = f"/tenant/offline/operaciones/{datos.operacion_uuid}"
    monkeypatch.setattr(settings, "saas_offline_enabled", False)
    assert _ruta_saas_permitida("POST", "/tenant/offline/operaciones") is False
    assert _ruta_saas_permitida("GET", ruta) is False
    monkeypatch.setattr(settings, "saas_offline_enabled", True)
    assert _ruta_saas_permitida("POST", "/tenant/offline/operaciones") is True
    assert _ruta_saas_permitida("GET", ruta) is True
    assert _ruta_saas_permitida("POST", "/tenant/offline/otra") is False
    assert _ruta_saas_permitida("POST", "/tenant/reportes/generativo") is True
    assert _ruta_saas_permitida("POST", "/tenant/reportes/predicciones") is True
    assert _ruta_saas_permitida("POST", "/tenant/reportes/patrones") is True


def test_endpoint_offline_http_con_flag(monkeypatch):
    datos = _reserva()
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_offline_enabled", True)
    monkeypatch.setattr(
        OfflineService, "ejecutar",
        staticmethod(lambda *a, **k: (
            OperacionOfflineResponse(
                operacion_uuid=datos.operacion_uuid, tipo="RESERVA",
                estado="ACEPTADA", id_definitivo=12,
                resultado={"reserva_id": 12}, reintentable=False,
            ), 200,
        )),
    )
    app.dependency_overrides[get_current_tenant_context] = lambda: _tenant()
    app.dependency_overrides[get_db] = lambda: _FakeDB()
    try:
        response = TestClient(app).post(
            "/tenant/offline/operaciones",
            json=datos.model_dump(mode="json"),
        )
        assert response.status_code == 200
        assert response.json()["estado"] == "ACEPTADA"
        assert response.json()["id_definitivo"] == 12
    finally:
        app.dependency_overrides.clear()


def test_endpoint_offline_http_cerrado_sin_flag(monkeypatch):
    datos = _reserva()
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_offline_enabled", False)
    app.dependency_overrides[get_current_tenant_context] = lambda: _tenant()
    app.dependency_overrides[get_db] = lambda: _FakeDB()
    try:
        response = TestClient(app).post(
            "/tenant/offline/operaciones",
            json=datos.model_dump(mode="json"),
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()
