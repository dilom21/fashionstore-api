"""Bloque 3/Paso 5: GET comerciales aislados con dos tenants FICTICIOS.

Nunca invoca procedimientos PostgreSQL, pagos reales, Supabase ni BD local.
El contexto simulado sustituye solo la identidad en las pruebas de acceso;
la validacion JWT real se prueba en test_auth_tenant_unit.py.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.core.tenant_commercial import TenantComercialRepository
from app.main import app

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito
from app.modules.compras.models.models import OrdenCompra
from app.modules.devoluciones.models.models import Devolucion
from app.modules.pagos.models.models import Pago
from app.modules.proveedores.models.models import Proveedor
from app.modules.reservas.models.models import Reserva
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.ventas.models.models import Venta


RECURSOS = ("carritos", "reservas", "ventas", "pagos", "compras", "devoluciones")


@pytest.fixture()
def escenario_comercial(monkeypatch):
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tablas = [
        Ciudad.__table__, Sucursal.__table__, Cliente.__table__,
        Proveedor.__table__, Carrito.__table__, Reserva.__table__,
        Venta.__table__, Pago.__table__, OrdenCompra.__table__,
        Devolucion.__table__,
    ]
    Base.metadata.create_all(motor, tables=tablas)
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    con_fecha = {"fecha_hora": ahora}
    with motor.begin() as conn:
        conn.execute(insert(Ciudad.__table__), [
            {"id": 1, "nombre": "Ciudad Ficticia", "estado": True},
        ])
        conn.execute(insert(Sucursal.__table__), [
            {"id": 1101, "empresa_id": 10, "ciudad_id": 1,
             "nombre": "Sucursal A", "direccion": "Ficticia A", "estado": True},
            {"id": 2202, "empresa_id": 20, "ciudad_id": 1,
             "nombre": "Sucursal B", "direccion": "Ficticia B", "estado": True},
        ])
        conn.execute(insert(Cliente.__table__), [
            {"id": 1101, "empresa_id": 10, "usuario_id": None,
             "nombre": "Cliente A", "apellido": "Fake", "estado": True},
            {"id": 2202, "empresa_id": 20, "usuario_id": None,
             "nombre": "Cliente B", "apellido": "Fake", "estado": True},
        ])
        conn.execute(insert(Proveedor.__table__), [
            {"id": 1101, "empresa_id": 10, "razon_social": "Prov A", "estado": True},
            {"id": 2202, "empresa_id": 20, "razon_social": "Prov B", "estado": True},
        ])
        conn.execute(insert(Carrito.__table__), [
            {"id": 1101, "empresa_id": 10, "cliente_id": 1101, "sucursal_id": 1101,
             "fecha_creacion": ahora, "fecha_actualizacion": ahora, "estado": "ACTIVO"},
            {"id": 2202, "empresa_id": 20, "cliente_id": 2202, "sucursal_id": 2202,
             "fecha_creacion": ahora, "fecha_actualizacion": ahora, "estado": "ACTIVO"},
        ])
        conn.execute(insert(Reserva.__table__), [
            {"id": 1101, "empresa_id": 10, "cliente_id": 1101, "sucursal_id": 1101,
             "carrito_id": 1101, "fecha_reserva": ahora,
             "fecha_atencion": ahora + timedelta(hours=2), "estado": "PENDIENTE"},
            {"id": 2202, "empresa_id": 20, "cliente_id": 2202, "sucursal_id": 2202,
             "carrito_id": 2202, "fecha_reserva": ahora,
             "fecha_atencion": ahora + timedelta(hours=2), "estado": "PENDIENTE"},
        ])
        conn.execute(insert(Venta.__table__), [
            {"id": 1101, "empresa_id": 10, "cliente_id": 1101, "sucursal_id": 1101,
             "carrito_id": 1101, "fecha_hora": ahora, "canal": "WEB",
             "estado": "PENDIENTE", "total": Decimal("100.00")},
            {"id": 2202, "empresa_id": 20, "cliente_id": 2202, "sucursal_id": 2202,
             "carrito_id": 2202, "fecha_hora": ahora, "canal": "WEB",
             "estado": "PENDIENTE", "total": Decimal("250.00")},
        ])
        conn.execute(insert(Pago.__table__), [
            {"id": 1101, "empresa_id": 10, "venta_id": 1101,
             "fecha_hora": ahora, "monto": Decimal("100.00"),
             "metodo": "QR", "estado": "PENDIENTE"},
            {"id": 2202, "empresa_id": 20, "venta_id": 2202,
             "fecha_hora": ahora, "monto": Decimal("250.00"),
             "metodo": "QR", "estado": "PENDIENTE"},
        ])
        conn.execute(insert(OrdenCompra.__table__), [
            {"id": 1101, "empresa_id": 10, "proveedor_id": 1101,
             "sucursal_id": 1101, "fecha_orden": ahora, "estado": "BORRADOR"},
            {"id": 2202, "empresa_id": 20, "proveedor_id": 2202,
             "sucursal_id": 2202, "fecha_orden": ahora, "estado": "BORRADOR"},
        ])
        conn.execute(insert(Devolucion.__table__), [
            {"id": 1101, "empresa_id": 10, "venta_id": 1101,
             "fecha_hora": ahora, "motivo": "Ficticio", "estado": "SOLICITADA"},
            {"id": 2202, "empresa_id": 20, "venta_id": 2202,
             "fecha_hora": ahora, "motivo": "Ficticio", "estado": "SOLICITADA"},
        ])

    fabrica = sessionmaker(bind=motor, expire_on_commit=False)
    actual = {"empresa_id": 10, "rol": "ADMINISTRADOR", "contexto": "personal"}
    monkeypatch.setattr(settings, "saas_auth_enabled", True)

    def bd_ficticia():
        with fabrica() as db:
            yield db

    def identidad_ficticia():
        return TenantContext(
            usuario_id=99, empresa_id=actual["empresa_id"],
            empresa_slug="urbanwear" if actual["empresa_id"] == 10 else "vanter-men",
            empresa_nombre="Empresa Ficticia",
            rol_id=1, rol_nombre=actual["rol"],
            contexto=actual["contexto"],
            plan_codigo="PROFESIONAL", permite_reportes_ia=True,
        )

    app.dependency_overrides[get_db] = bd_ficticia
    app.dependency_overrides[get_current_tenant_context] = identidad_ficticia
    try:
        with TestClient(app) as cliente:
            yield cliente, actual, fabrica
    finally:
        app.dependency_overrides.clear()
        motor.dispose()


@pytest.mark.parametrize("tipo", RECURSOS)
def test_listado_admin_solo_su_empresa(escenario_comercial, tipo):
    client, _, _ = escenario_comercial
    respuesta = client.get(f"/tenant/comercial/{tipo}")
    assert respuesta.status_code == 200, respuesta.text
    assert len(respuesta.json()) == 1
    assert respuesta.json()[0]["id"] == 1101
    assert set(respuesta.json()[0]) == {"id", "estado"}


@pytest.mark.parametrize("tipo", RECURSOS)
def test_id_de_empresa_ajena_no_existe_para_el_tenant(escenario_comercial, tipo):
    client, _, _ = escenario_comercial
    r = client.get(f"/tenant/comercial/{tipo}/2202")
    assert r.status_code == 404
    assert r.json() == {"detail": "Recurso no encontrado"}


@pytest.mark.parametrize("tipo", RECURSOS)
def test_id_propio_se_lee_sin_datos_sensibles(escenario_comercial, tipo):
    client, _, _ = escenario_comercial
    r = client.get(f"/tenant/comercial/{tipo}/1101")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"id", "estado"}
    assert r.json()["id"] == 1101


@pytest.mark.parametrize("tipo", RECURSOS)
def test_al_cambiar_tenant_cambian_lista_y_propiedad(escenario_comercial, tipo):
    client, actual, _ = escenario_comercial
    actual["empresa_id"] = 20
    assert [x["id"] for x in client.get(f"/tenant/comercial/{tipo}").json()] == [2202]
    assert client.get(f"/tenant/comercial/{tipo}/1101").status_code == 404
    assert client.get(f"/tenant/comercial/{tipo}/2202").status_code == 200


@pytest.mark.parametrize("tipo", RECURSOS)
def test_query_empresa_id_no_suplanta_tenant(escenario_comercial, tipo):
    client, _, _ = escenario_comercial
    respuesta = client.get(f"/tenant/comercial/{tipo}", params={"empresa_id": 20})
    assert [x["id"] for x in respuesta.json()] == [1101]


def test_no_admin_o_contexto_cliente_denegado(escenario_comercial):
    client, actual, _ = escenario_comercial
    actual["rol"] = "CAJERO"
    assert client.get("/tenant/comercial/pagos").status_code == 403
    actual["rol"] = "ADMINISTRADOR"
    actual["contexto"] = "cliente"
    assert client.get("/tenant/comercial/ventas").status_code == 403


def test_solo_get_en_allowlist_y_otros_tipos_bloqueados(escenario_comercial):
    client, _, _ = escenario_comercial
    for metodo in ("POST", "PATCH", "PUT", "DELETE"):
        assert client.request(
            metodo, "/tenant/comercial/ventas/1101", json={}
        ).status_code == 503
    for ruta in (
        "/tenant/comercial/otros",
        "/tenant/comercial/pagos/0",
        "/tenant/comercial/pagos/01",
        "/tenant/comercial/pagos/1101/editar",
        "/auth/personal/login",
    ):
        assert client.get(ruta).status_code == 503


def test_paginacion_y_salida_minima(escenario_comercial):
    client, _, _ = escenario_comercial
    assert client.get("/tenant/comercial/pagos", params={"limit": 0}).status_code == 422
    assert client.get("/tenant/comercial/pagos", params={"limit": 101}).status_code == 422
    assert client.get("/tenant/comercial/pagos", params={"offset": -1}).status_code == 422
    assert client.get("/tenant/comercial/pagos", params={"offset": 1}).json() == []


def test_repositorio_rechaza_empresa_invalida(escenario_comercial):
    _, _, fabrica = escenario_comercial
    with fabrica() as db:
        for v in (-1, 0, None, "10", True):
            with pytest.raises(ValueError):
                TenantComercialRepository.listar(
                    db, empresa_id=v, tipo="ventas",
                )
        with pytest.raises(ValueError):
            TenantComercialRepository.obtener(
                db, empresa_id=10, tipo="invalido", recurso_id=1101,
            )


def test_parte_falsa_de_un_pago_impide_filtrar_por_padre_ajeno(escenario_comercial):
    # SQLite del test no reproduce las 48 FK compuestas de PostgreSQL,
    # asi que aqui se simula expresamente la fila maliciosa/huerfana.
    client, _, fabrica = escenario_comercial
    with fabrica.begin() as db:
        db.execute(insert(Pago.__table__).values(
            id=3303, empresa_id=10, venta_id=2202,
            fecha_hora=datetime.now(timezone.utc),
            monto=Decimal("9.00"), metodo="QR", estado="PENDIENTE",
        ))
    assert client.get("/tenant/comercial/pagos/3303").status_code == 404
    assert [x["id"] for x in client.get("/tenant/comercial/pagos").json()] == [1101]


def test_falta_token_devuelve_401(escenario_comercial):
    client, _, _ = escenario_comercial
    app.dependency_overrides.pop(get_current_tenant_context)
    assert client.get("/tenant/comercial/carritos").status_code == 401


def test_flag_apagado_devuelve_503(escenario_comercial, monkeypatch):
    client, _, _ = escenario_comercial
    app.dependency_overrides.pop(get_current_tenant_context)
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    assert client.get("/tenant/comercial/pagos").status_code == 503
