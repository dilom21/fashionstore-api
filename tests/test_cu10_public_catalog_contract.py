"""Pruebas locales del contrato CU10 sin abrir una conexion PostgreSQL."""

from unittest.mock import MagicMock

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
