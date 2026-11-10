"""Pruebas locales del contrato CU10 sin abrir una conexion PostgreSQL."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.modules.catalogo.repositories.public_repository import PublicCatalogoRepository
from app.modules.catalogo.schemas.public_schemas import PublicProductoDetalleResponse


def _db(rows=None, scalar=None):
    db = MagicMock()
    db.execute.return_value = rows or []
    db.scalar.return_value = scalar
    return db


def test_listado_filtros_combinados_usa_funcion_y_parametros_enlazados():
    db = _db(rows=[({"id": 7, "nombre": "Camisa"},)])
    result = PublicCatalogoRepository.listar_productos(
        db,
        empresa_slug="vanter-men",
        buscar="cam",
        categoria_id=2,
        talla_id=3,
        color_id=4,
        temporada_id=5,
        coleccion_id=6,
        sucursal_id=7,
        disponibilidad="CON_STOCK",
        limit=24,
        offset=0,
    )
    assert result == [{"id": 7, "nombre": "Camisa"}]
    sql = str(db.execute.call_args.args[0])
    assert "catalogo_publico_filtrado" in sql


def test_detalle_contrato_incluye_promociones_stock_y_recursos():
    payload = {
        "id": 7,
        "nombre": "Camisa",
        "descripcion": None,
        "precio": "100.00",
        "categoria": {"id": 2, "nombre": "Camisas"},
        "imagen_principal_url": None,
        "promociones_vigentes": [{
            "id": 9, "nombre": "Oferta", "tipo_descuento": "PORCENTAJE",
            "valor_descuento": "10",
        }],
        "precio_definitivo": "90.00",
        "precio_calculo_pendiente": False,
        "variantes": [{
            "id": 8, "sku": "CAM-01", "estado": True,
            "talla": {"id": 3, "nombre": "M"},
            "color": {"id": 4, "nombre": "Negro"},
        }],
        "disponibilidad": [{
            "variante_id": 8, "sucursal_id": 1, "sucursal": "Central",
            "sku": "CAM-01", "talla": "M", "color": "Negro",
            "temporada": "2026", "stock_disponible": 5,
        }],
        "recursos": [{
            "id": 10, "tipo": "IMAGEN", "url": "https://example.test/a.jpg",
            "es_principal": True,
        }],
    }
    db = _db(scalar=payload)
    result = PublicCatalogoRepository.detalle_producto(
        db, empresa_slug="vanter-men", producto_id=7
    )
    dto = PublicProductoDetalleResponse.model_validate(result)
    assert dto.id == 7
    assert dto.promociones_vigentes[0].valor_descuento == 10
    assert dto.disponibilidad[0].stock_disponible == 5


def test_producto_inactivo_o_slug_inexistente_se_representa_como_vacio_o_null():
    db = _db(rows=[], scalar=None)
    assert PublicCatalogoRepository.listar_productos(db, empresa_slug="inexistente") == []
    assert PublicCatalogoRepository.detalle_producto(
        db, empresa_slug="inexistente", producto_id=999
    ) is None


def test_filtros_y_solicitud_anonima_no_requieren_tablas_en_backend():
    db = _db(scalar=None)
    assert PublicCatalogoRepository.filtros(db, empresa_slug="vanter-men") == {
        "categorias": [], "tallas": [], "colores": [], "temporadas": [],
        "colecciones": [], "sucursales": [],
    }


@pytest.fixture()
def cliente_publico(monkeypatch):
    db = MagicMock()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_http_catalogo_publico_cerrado_por_defecto(cliente_publico, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_catalog_enabled", False)
    assert cliente_publico.get("/catalogo/public/vanter-men/productos").status_code == 503
    assert cliente_publico.get("/catalogo/public/vanter-men/filtros").status_code == 503
    assert cliente_publico.get("/productos").status_code == 503


def test_http_catalogo_publico_no_exige_jwt_y_legacy_sigue_cerrado(
    cliente_publico, monkeypatch
):
    monkeypatch.setattr(settings, "saas_public_catalog_enabled", True)
    monkeypatch.setattr(
        PublicCatalogoRepository,
        "listar_productos",
        classmethod(lambda cls, db, **kwargs: []),
    )
    respuesta = cliente_publico.get("/catalogo/public/vanter-men/productos")
    assert respuesta.status_code == 200
    assert respuesta.json() == []
    assert cliente_publico.get("/productos").status_code == 503


def test_http_catalogo_publico_propaga_filtros_validados(cliente_publico, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_catalog_enabled", True)
    recibidos = {}

    def listar(cls, db, **kwargs):
        recibidos.update(kwargs)
        return []

    monkeypatch.setattr(PublicCatalogoRepository, "listar_productos", classmethod(listar))
    respuesta = cliente_publico.get(
        "/catalogo/public/vanter-men/productos",
        params={
            "buscar": "camisa",
            "categoria_id": 2,
            "talla_id": 3,
            "color_id": 4,
            "temporada_id": 5,
            "coleccion_id": 6,
            "sucursal_id": 7,
            "disponibilidad": "con_stock",
            "limit": 12,
            "offset": 4,
        },
    )
    assert respuesta.status_code == 200
    assert recibidos == {
        "empresa_slug": "vanter-men",
        "buscar": "camisa",
        "categoria_id": 2,
        "talla_id": 3,
        "color_id": 4,
        "temporada_id": 5,
        "coleccion_id": 6,
        "sucursal_id": 7,
        "disponibilidad": "CON_STOCK",
        "limit": 12,
        "offset": 4,
    }


def test_http_catalogo_publico_rechaza_parametros_invalidos(cliente_publico, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_catalog_enabled", True)
    assert cliente_publico.get(
        "/catalogo/public/vanter-men/productos", params={"categoria_id": 0}
    ).status_code == 422
    assert cliente_publico.get(
        "/catalogo/public/vanter-men/productos", params={"disponibilidad": "OTRO"}
    ).status_code == 422
    assert cliente_publico.get(
        "/catalogo/public/vanter-men/productos", params={"limit": 101}
    ).status_code == 422


def test_http_catalogo_publico_detalle_inexistente_y_error_sql_sanitizado(
    cliente_publico, monkeypatch
):
    monkeypatch.setattr(settings, "saas_public_catalog_enabled", True)
    monkeypatch.setattr(
        PublicCatalogoRepository,
        "detalle_producto",
        classmethod(lambda cls, db, **kwargs: None),
    )
    assert cliente_publico.get(
        "/catalogo/public/vanter-men/productos/999"
    ).status_code == 404

    def fallo_sql(cls, db, **kwargs):
        raise OperationalError("consulta", {}, Exception("detalle privado"))

    monkeypatch.setattr(
        PublicCatalogoRepository, "listar_productos", classmethod(fallo_sql)
    )
    respuesta = cliente_publico.get("/catalogo/public/vanter-men/productos")
    assert respuesta.status_code == 503
    assert respuesta.json() == {"detail": "Catalogo publico no disponible"}
