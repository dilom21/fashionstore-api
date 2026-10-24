"""Bloque 3 / Paso 2: validacion del mapeo ORM sin conectar a una BD.

El esquema efectivo de FK compuestas lo administra PostgreSQL mediante SQL
versionado; estas pruebas solo verifican metadata SQLAlchemy y que el campo
empresa_id exista en cada clase mapeada.
"""
import importlib
import os

# Igual que los tests SaaS existentes: no depender de secretos ni de Supabase.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from sqlalchemy import BigInteger, UniqueConstraint, inspect

from app.core.database import Base
from app.core.tenant_models import EmpresaAuditoriaMixin, EmpresaScopedMixin


MODULOS_MODELOS = (
    "autenticacion_seguridad",
    "carrito",
    "catalogo",
    "compras",
    "devoluciones",
    "inventario",
    "pagos",
    "promociones",
    "proveedores",
    "reservas",
    "sucursales",
    "temporadas_colecciones",
    "ventas",
    "vestidor_virtual",
)

TABLAS_EMPRESA = frozenset(
    (
        "bitacora", "sucursal", "empleado", "cliente", "categoria",
        "producto", "variante_producto", "recurso_producto", "temporada",
        "coleccion", "producto_coleccion", "promocion", "promocion_producto",
        "proveedor", "proveedor_producto", "orden_compra",
        "detalle_orden_compra", "inventario", "movimiento_inventario",
        "carrito", "detalle_carrito", "reserva", "detalle_reserva", "venta",
        "detalle_venta", "pago", "devolucion", "detalle_devolucion",
        "configuracion_vestidor_ar", "sesion_vestidor_ar", "prueba_vestidor_ar",
    )
)

TABLAS_GLOBALES = frozenset(
    (
        "accion", "ciudad", "color", "funcion", "modulo", "rol",
        "rol_funcion", "talla", "token_recuperacion", "usuario",
    )
)


def _importar_modelos():
    for modulo in MODULOS_MODELOS:
        importlib.import_module(f"app.modules.{modulo}.models.models")


def _conjuntos_unique(tabla):
    return {
        tuple(col.name for col in constraint.columns)
        for constraint in tabla.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_31_tablas_empresa_y_10_globales_identificadas():
    _importar_modelos()
    assert len(TABLAS_EMPRESA) == 31
    assert len(TABLAS_GLOBALES) == 10
    assert not (TABLAS_EMPRESA & TABLAS_GLOBALES)
    assert TABLAS_EMPRESA.issubset(Base.metadata.tables)


@pytest.mark.parametrize("tabla", sorted(TABLAS_EMPRESA))
def test_empresa_id_bigint_sin_default_ni_pk(tabla):
    _importar_modelos()
    columna = Base.metadata.tables[tabla].c.empresa_id
    assert isinstance(columna.type, BigInteger)
    assert columna.nullable is (tabla == "bitacora")
    assert not columna.primary_key
    assert columna.default is None
    assert columna.server_default is None


def test_modelos_globales_conservan_identidad_compartida():
    _importar_modelos()
    for nombre in TABLAS_GLOBALES:
        if nombre in Base.metadata.tables:
            assert "empresa_id" not in Base.metadata.tables[nombre].c


@pytest.mark.parametrize(
    "tabla,unicidades",
    (
        ("categoria", (("empresa_id", "nombre"),)),
        ("temporada", (("empresa_id", "nombre"),)),
        ("variante_producto", (("empresa_id", "sku"),)),
        ("sucursal", (("empresa_id", "ciudad_id", "nombre"),)),
        ("proveedor", (("empresa_id", "nit"),)),
        ("cliente", (
            ("empresa_id", "usuario_id"),
            ("empresa_id", "ci"),
        )),
        ("empleado", (
            ("empresa_id", "usuario_id"),
            ("empresa_id", "ci"),
        )),
    ),
)
def test_unicidad_pertenece_a_empresa(tabla, unicidades):
    _importar_modelos()
    definiciones = _conjuntos_unique(Base.metadata.tables[tabla])
    for columnas in unicidades:
        assert columnas in definiciones, (tabla, columnas)
        assert (columnas[-1],) not in definiciones, (tabla, columnas)


def test_bitacora_es_unica_tabla_empresarial_con_empresa_opcional():
    _importar_modelos()
    nullables = {
        t for t in TABLAS_EMPRESA if Base.metadata.tables[t].c.empresa_id.nullable
    }
    assert nullables == {"bitacora"}


def test_dos_mappers_orden_compra_reutilizan_misma_tabla():
    _importar_modelos()
    from app.modules.proveedores.models.models import OrdenCompra as OrdenProveedor
    from app.modules.compras.models.models import OrdenCompra as OrdenCompras

    assert inspect(OrdenProveedor).local_table is inspect(OrdenCompras).local_table
    assert "empresa_id" in inspect(OrdenCompras).local_table.c


def test_herencia_mixins_correcta_y_sin_fk_duplicada_en_metadata():
    _importar_modelos()
    from app.modules.catalogo.models.models import Producto
    from app.modules.autenticacion_seguridad.models.models import Bitacora

    assert issubclass(Producto, EmpresaScopedMixin)
    assert issubclass(Bitacora, EmpresaAuditoriaMixin)
    # En Paso 2 solo se agrega la columna: las FK cruzadas validadas en
    # PostgreSQL no deben duplicarse aqui antes de migrar las relaciones ORM.
    assert not Base.metadata.tables["producto"].c.empresa_id.foreign_keys
