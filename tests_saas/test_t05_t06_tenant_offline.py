"""Offline T05/T06 isolation tests; no provider or remote database is used."""
import os

# This module imports app.main below. Always replace inherited DSNs before that
# import so an independently-run test can never inspect or use a remote DB.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./test_t05_t06_tenant_offline.db"
os.environ["SECRET_KEY"] = "offline-only-test-secret"
os.environ["SAAS_AUTH_ENABLED"] = "false"

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main as _main  # noqa: F401 - register the complete ORM graph
from app.core.database import Base
from app.core.tenant_context import TenantContext
from app.modules.asistencia_inteligente.api.tenant_router import recomendar_tenant
from app.modules.asistencia_inteligente.schemas.schemas import RecomendacionRequest
from app.modules.asistencia_inteligente.providers.base import AsistenteIAProvider
from app.modules.asistencia_inteligente.repositories.repository import AsistenciaRepository
from app.modules.catalogo.repositories.tenant_public_repository import TenantPublicRepository
from app.modules.catalogo.repositories.tenant_merchandising_repository import TenantMerchandisingRepository
from app.modules.catalogo.models.models import Temporada
from app.modules.temporadas_colecciones.models.models import Coleccion
from app.modules.temporadas_colecciones.schemas.schemas import TemporadaCreate, ColeccionCreate
from app.modules.reportes.services.tenant_reportes_service import TenantReportesService
from app.modules.reportes.api.tenant_router import FiltrosReporte, reporte_generativo
from app.modules.vestidor_virtual.schemas.schemas import IniciarPruebaRequest
from app.modules.vestidor_virtual.services.service import (
    ProductoNoEncontradoError, SesionNoEncontradaError, VestidorVirtualService,
)


class FakeReportProvider(AsistenteIAProvider):
    def __init__(self):
        self.user_payload = ""

    @property
    def nombre(self) -> str:
        return "fake"

    def generar(self, *, system: str, usuario: str) -> str:
        self.user_payload = usuario
        return '{"resumen":"Operación estable","alertas":["Revisar stock"]}'


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE empresa (id BIGINT PRIMARY KEY, slug VARCHAR UNIQUE NOT NULL, "
            "nombre_comercial VARCHAR NOT NULL, estado BOOLEAN NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO empresa VALUES (1,'alpha','Alpha',1),(2,'beta','Beta',1),"
            "(3,'inactive','Inactive',0)"
        ))
    session = Session(engine)
    now = datetime.now(timezone.utc)
    statements = [
        ("INSERT INTO ciudad(id,nombre,estado) VALUES (1,'La Paz',1)", {}),
        ("INSERT INTO sucursal(id,ciudad_id,nombre,direccion,telefono,estado,empresa_id) "
         "VALUES (1,1,'Alpha Centro','A','1',1,1),(2,1,'Beta Centro','B','2',1,2)", {}),
        ("INSERT INTO categoria(id,nombre,descripcion,estado,empresa_id) "
         "VALUES (1,'Alpha Cat',NULL,1,1),(2,'Beta Cat',NULL,1,2)", {}),
        ("INSERT INTO producto(id,categoria_id,nombre,descripcion,precio,estado,empresa_id) "
         "VALUES (1,1,'Alpha Shirt','Only Alpha',100,1,1),"
         "(2,2,'Beta Shirt','Only Beta',200,1,2),(3,1,'Hidden',NULL,10,0,1)", {}),
        ("INSERT INTO talla(id,nombre,estado) VALUES (1,'M',1)", {}),
        ("INSERT INTO color(id,nombre,estado) VALUES (1,'Blue',1)", {}),
        ("INSERT INTO temporada(id,nombre,fecha_inicio,fecha_fin,estado,empresa_id) "
         "VALUES (1,'Alpha Season','2026-01-01','2026-12-31',1,1),"
         "(2,'Beta Season','2026-01-01','2026-12-31',1,2)", {}),
        ("INSERT INTO variante_producto(id,producto_id,talla_id,color_id,sku,estado,empresa_id) "
         "VALUES (1,1,1,1,'A-1',1,1),(2,2,1,1,'B-1',1,2)", {}),
        ("INSERT INTO inventario(id,sucursal_id,variante_producto_id,temporada_id,stock_actual,stock_reservado,fecha_actualizacion,empresa_id) "
         "VALUES (1,1,1,1,8,2,:now,1),(2,2,2,2,40,0,:now,2)", {"now": now}),
        ("INSERT INTO recurso_producto(id,producto_id,color_id,tipo,url,es_principal,estado,empresa_id) "
         "VALUES (1,1,1,'IMAGEN','https://alpha/item.png',1,1,1),"
         "(2,2,1,'IMAGEN','https://beta/item.png',1,1,2)", {}),
        ("INSERT INTO configuracion_vestidor_ar(id,recurso_producto_id,zona_cuerpo,tipo_asset,factor_ancho,factor_alto,offset_x,offset_y,rotacion_offset,orden_capa,opacidad,estado,empresa_id) "
         "VALUES (1,1,'TORSO','PNG_2D',1,1,0,0,0,1,1,1,1),"
         "(2,2,'TORSO','PNG_2D',1,1,0,0,0,1,1,1,2)", {}),
        ("INSERT INTO cliente(id,usuario_id,nombre,apellido,telefono,sexo,ci,fecha_nacimiento,estado,empresa_id) "
         "VALUES (1,NULL,'A','Client',NULL,NULL,'A1',NULL,1,1),(2,NULL,'B','Client',NULL,NULL,'B1',NULL,1,2)", {}),
        ("INSERT INTO venta(id,cliente_id,empleado_id,sucursal_id,reserva_id,fecha_hora,canal,estado,total,carrito_id,empresa_id) "
         "VALUES (1,1,NULL,1,NULL,:now,'WEB','COMPLETADA',150,NULL,1),"
         "(2,2,NULL,2,NULL,:now,'WEB','COMPLETADA',900,NULL,2)", {"now": now}),
        ("INSERT INTO reserva(id,cliente_id,sucursal_id,carrito_id,fecha_reserva,fecha_atencion,estado,observacion,empresa_id) "
         "VALUES (1,1,1,NULL,:now,:later,'PENDIENTE',NULL,1),"
         "(2,2,2,NULL,:now,:later,'PENDIENTE',NULL,2)",
         {"now": now, "later": now + timedelta(days=1)}),
    ]
    for statement, params in statements:
        session.execute(text(statement), params)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_public_slug_and_catalog_are_isolated(db):
    assert TenantPublicRepository.resolver_empresa(db, "inactive") is None
    alpha = TenantPublicRepository.resolver_empresa(db, "alpha")
    assert alpha and alpha["id"] == 1
    items = TenantPublicRepository.listar_productos(
        db, 1, categoria_id=None, texto=None, limit=50, offset=0,
    )
    assert [item["nombre"] for item in items] == ["Alpha Shirt"]
    assert int(items[0]["stock_disponible"]) == 6
    assert "beta" not in str(items).lower()
    assert TenantPublicRepository.detalle_producto(db, 1, 2) is None


def test_assistance_candidates_never_cross_company(db):
    alpha = AsistenciaRepository.obtener_candidatos(
        db, talla=None, color=None, presupuesto_max=None,
        sucursal_id=None, sucursal=None, empresa_id=1,
    )
    assert [item.inventario_id for item in alpha] == [1]
    assert all("Beta" not in item.nombre for item in alpha)


def test_merchandising_writes_derive_company_and_reject_cross_company_parent(db):
    season = TenantMerchandisingRepository.crear_temporada(
        db, 1, TemporadaCreate(
            nombre="Alpha Drop", fecha_inicio=date(2026, 6, 1),
            fecha_fin=date(2026, 8, 1),
        ),
    )
    db.commit()
    assert season.empresa_id == 1
    assert TenantMerchandisingRepository.obtener(db, Temporada, 2, season.id) is None
    collection = TenantMerchandisingRepository.crear_coleccion(
        db, 1, ColeccionCreate(
            temporada_id=season.id, nombre="Alpha Capsule", descripcion=None,
        ),
    )
    db.commit()
    assert collection.empresa_id == 1
    assert TenantMerchandisingRepository.obtener(db, Coleccion, 2, collection.id) is None


def test_reports_are_aggregated_and_fake_provider_receives_no_customer_rows(db):
    summary = TenantReportesService.resumen(
        db, empresa_id=1, fecha_desde=date(2026, 1, 1),
        fecha_hasta=date(2026, 12, 31), sucursal_id=None,
    )
    assert summary["ventas_completadas"] == 1
    assert summary["total_vendido"] == Decimal("150")
    assert summary["stock_disponible"] == 6
    provider = FakeReportProvider()
    result = TenantReportesService.generar(summary, provider)
    assert result["resumen"] == "Operación estable"
    assert "Client" not in provider.user_payload
    assert "900" not in provider.user_payload


def test_virtual_fitting_room_enforces_company_for_catalog_and_sessions(db):
    with pytest.raises(ProductoNoEncontradoError):
        VestidorVirtualService.obtener_configuraciones(db, 2, empresa_id=1)
    response = VestidorVirtualService.obtener_configuraciones(db, 1, empresa_id=1)
    assert response.compatible is True
    session = VestidorVirtualService.crear_sesion(
        db, SimpleNamespace(id=1), empresa_id=1,
    )
    with pytest.raises(SesionNoEncontradaError):
        VestidorVirtualService.iniciar_prueba(
            db, SimpleNamespace(id=2), session.sesion_id,
            IniciarPruebaRequest(configuracion_id=2), empresa_id=2,
        )


def _tenant(*, allows_ai: bool, context: str = "personal") -> TenantContext:
    return TenantContext(
        usuario_id=1, empresa_id=1, empresa_slug="alpha",
        empresa_nombre="Alpha", rol_id=1,
        rol_nombre="ADMINISTRADOR" if context == "personal" else "CLIENTE",
        contexto=context, plan_codigo="PRO" if allows_ai else "BASIC",
        permite_reportes_ia=allows_ai,
    )


def test_basic_plan_blocks_generative_report_before_provider(db, monkeypatch):
    monkeypatch.setattr(
        TenantReportesService, "generar",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
    )
    with pytest.raises(HTTPException) as error:
        reporte_generativo(FiltrosReporte(), _tenant(allows_ai=False), db)
    assert error.value.status_code == 403


def test_basic_plan_blocks_intelligent_assistance_before_provider(db, monkeypatch):
    monkeypatch.setattr(
        "app.modules.asistencia_inteligente.api.tenant_router.AsistenciaInteligenteService.recomendar",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called"),
    )
    with pytest.raises(HTTPException) as error:
        recomendar_tenant(
            RecomendacionRequest(consulta="shirt"),
            _tenant(allows_ai=False, context="cliente"), db,
        )
    assert error.value.status_code == 403
