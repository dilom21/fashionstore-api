"""5BZ / T02: contratos tenant de LECTURA sobre SQLite efimero (offline).

Cubre los endpoints nuevos que evitan que la UI escriba IDs a mano:

  * `GET /tenant/catalogo/{tallas,colores,ciudades}`  (diccionarios globales)
  * `GET /tenant/catalogo/{proveedores,temporadas,variantes}` (por empresa)
  * `GET /tenant/inventario` (nuevo modulo tenant, stock por sucursal)
  * `GET /tenant/devoluciones/ventas/{venta_id}/lineas`

Todas las empresas, usuarios y recursos de este archivo son FICTICIOS y solo
existen en SQLite en memoria: no hay conexion a Supabase ni a PostgreSQL.
La dependencia `get_current_tenant_context` se sustituye por un tenant validado
FICTICIO para aislar la bateria del login (ya probado en `tests_saas`).

Lo que exige PostgreSQL real (RLS forzado, `saas.tiene_permiso_tenant`,
SPs y principal restringido) NO se simula aqui: la cobertura en vivo esta en
`database/saas/122_contratos_tenant_5bz.py`.
"""
import ast
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

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
from app.main import app
from app.modules.autenticacion_seguridad.models.models import (
    Accion, Funcion, Modulo, Rol, RolFuncion,
)
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.devoluciones.models.models import Devolucion, DetalleDevolucion
from app.modules.inventario.models.models import Inventario
from app.modules.proveedores.models.models import Proveedor
from app.modules.sucursales.models.models import Ciudad, Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta

ROOT = Path(__file__).resolve().parents[1]
API_MEMBRESIAS = ROOT / "app/modules/usuarios/api/tenant_membership_router.py"

EMPRESA_A = 100
EMPRESA_B = 200

# Rol 1 ADMINISTRADOR, rol 2 CAJERO, rol 3 ENCARGADO_SUCURSAL.
ROL_ADMIN, ROL_CAJERO, ROL_ENCARGADO = 1, 2, 3

FUNCIONES = ("CATALOGO", "COMPRAS", "SUCURSALES", "VENTAS", "PERSONAL")
ACCIONES = ("CONSULTAR", "ADMINISTRAR", "GESTIONAR")

# Matriz nominal FICTICIA equivalente a la 5BO para el camino SQLite.
MATRIZ = {
    ROL_ADMIN: {
        ("CATALOGO", "CONSULTAR"), ("COMPRAS", "CONSULTAR"),
        ("SUCURSALES", "CONSULTAR"), ("VENTAS", "CONSULTAR"),
        ("PERSONAL", "CONSULTAR"), ("PERSONAL", "ADMINISTRAR"),
    },
    ROL_CAJERO: {
        ("CATALOGO", "CONSULTAR"), ("SUCURSALES", "CONSULTAR"),
        ("VENTAS", "CONSULTAR"),
    },
    ROL_ENCARGADO: {
        ("CATALOGO", "CONSULTAR"), ("COMPRAS", "CONSULTAR"),
        ("SUCURSALES", "CONSULTAR"), ("VENTAS", "CONSULTAR"),
        ("PERSONAL", "CONSULTAR"),
    },
}


def tenant_ficticio(
    empresa_id: int = EMPRESA_A, *, rol_id: int = ROL_ADMIN,
    rol_nombre: str = "ADMINISTRADOR", contexto: str = "personal",
) -> TenantContext:
    return TenantContext(
        usuario_id=99,
        empresa_id=empresa_id,
        empresa_slug="empresa-a" if empresa_id == EMPRESA_A else "empresa-b",
        empresa_nombre="Empresa A" if empresa_id == EMPRESA_A else "Empresa B",
        rol_id=rol_id,
        rol_nombre=rol_nombre,
        contexto=contexto,
        plan_codigo="PROFESIONAL",
        permite_reportes_ia=True,
    )


def _sembrar_matriz(conn) -> None:
    """Matriz RBAC ficticia para el camino SQLite de `tiene_permiso`."""
    conn.execute(insert(Rol.__table__), [
        {"id": 1, "nombre": "ADMINISTRADOR", "descripcion": None, "estado": True},
        {"id": 2, "nombre": "CAJERO", "descripcion": None, "estado": True},
        {"id": 3, "nombre": "ENCARGADO_SUCURSAL", "descripcion": None,
         "estado": True},
        {"id": 4, "nombre": "CLIENTE", "descripcion": None, "estado": True},
    ])
    funcion_id: dict[str, int] = {}
    for indice, nombre in enumerate(FUNCIONES, start=1):
        conn.execute(insert(Modulo.__table__), [{
            "id": indice, "nombre": f"MOD_{nombre}", "descripcion": None,
            "estado": True,
        }])
        conn.execute(insert(Funcion.__table__), [{
            "id": indice, "modulo_id": indice, "nombre": nombre,
            "descripcion": None, "estado": True,
        }])
        funcion_id[nombre] = indice
    accion_id = {}
    for indice, nombre in enumerate(ACCIONES, start=1):
        conn.execute(insert(Accion.__table__), [{
            "id": indice, "nombre": nombre, "descripcion": None,
            "estado": True,
        }])
        accion_id[nombre] = indice
    filas = [
        {
            "rol_id": rol, "funcion_id": funcion_id[funcion],
            "accion_id": accion_id[accion], "descripcion": None,
        }
        for rol, permisos in MATRIZ.items()
        for funcion, accion in sorted(permisos)
    ]
    conn.execute(insert(RolFuncion.__table__), filas)


def _sembrar_catalogo(conn) -> None:
    conn.execute(insert(Talla.__table__), [
        {"id": 1, "nombre": "M", "estado": True},
        {"id": 2, "nombre": "S", "estado": False},
    ])
    conn.execute(insert(Color.__table__), [
        {"id": 1, "nombre": "Negro", "estado": True},
        {"id": 2, "nombre": "Azul", "estado": False},
    ])
    conn.execute(insert(Ciudad.__table__), [
        {"id": 1, "nombre": "Ciudad Alfa", "estado": True},
        {"id": 2, "nombre": "Ciudad Beta", "estado": True},
    ])
    conn.execute(insert(Categoria.__table__), [
        {"id": 101, "empresa_id": EMPRESA_A, "nombre": "Urbana",
         "descripcion": None, "estado": True},
        {"id": 201, "empresa_id": EMPRESA_B, "nombre": "Clasica",
         "descripcion": None, "estado": True},
    ])
    conn.execute(insert(Producto.__table__), [
        {"id": 1101, "empresa_id": EMPRESA_A, "categoria_id": 101,
         "nombre": "Polera A", "descripcion": None,
         "precio": Decimal("50.00"), "estado": True},
        {"id": 1102, "empresa_id": EMPRESA_A, "categoria_id": 101,
         "nombre": "Camisa A", "descripcion": None,
         "precio": Decimal("80.00"), "estado": True},
        {"id": 1201, "empresa_id": EMPRESA_B, "categoria_id": 201,
         "nombre": "Producto B", "descripcion": None,
         "precio": Decimal("70.00"), "estado": True},
    ])
    conn.execute(insert(VarianteProducto.__table__), [
        {"id": 2101, "empresa_id": EMPRESA_A, "producto_id": 1101,
         "talla_id": 1, "color_id": 1, "sku": "A-001", "estado": True},
        {"id": 2102, "empresa_id": EMPRESA_A, "producto_id": 1102,
         "talla_id": 1, "color_id": 1, "sku": "A-002", "estado": True},
        {"id": 2201, "empresa_id": EMPRESA_B, "producto_id": 1201,
         "talla_id": 1, "color_id": 1, "sku": "B-777", "estado": True},
    ])
    conn.execute(insert(Proveedor.__table__), [
        {"id": 3101, "empresa_id": EMPRESA_A, "razon_social": "Proveedor Alfa",
         "nit": None, "correo": None, "telefono": None, "direccion": None,
         "estado": True},
        {"id": 3102, "empresa_id": EMPRESA_A, "razon_social": "Beta Textil",
         "nit": None, "correo": None, "telefono": None, "direccion": None,
         "estado": False},
    ])
    conn.execute(insert(Temporada.__table__), [
        {"id": 4101, "empresa_id": EMPRESA_A, "nombre": "2026",
         "fecha_inicio": date(2026, 1, 1), "fecha_fin": date(2026, 12, 31),
         "estado": True},
        {"id": 4201, "empresa_id": EMPRESA_B, "nombre": "Otono B",
         "fecha_inicio": date(2026, 3, 1), "fecha_fin": date(2026, 6, 30),
         "estado": True},
    ])
    conn.execute(insert(Sucursal.__table__), [
        {"id": 5101, "empresa_id": EMPRESA_A, "ciudad_id": 1,
         "nombre": "Sucursal A", "direccion": "Calle A", "telefono": None,
         "estado": True},
        {"id": 5201, "empresa_id": EMPRESA_B, "ciudad_id": 2,
         "nombre": "Sucursal B", "direccion": "Calle B", "telefono": None,
         "estado": True},
    ])
    conn.execute(insert(Inventario.__table__), [
        {"id": 6101, "empresa_id": EMPRESA_A, "sucursal_id": 5101,
         "variante_producto_id": 2101, "temporada_id": 4101,
         "stock_actual": 20, "stock_reservado": 2,
         "fecha_actualizacion": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"id": 6102, "empresa_id": EMPRESA_A, "sucursal_id": 5101,
         "variante_producto_id": 2102, "temporada_id": 4101,
         "stock_actual": 5, "stock_reservado": 0,
         "fecha_actualizacion": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"id": 6201, "empresa_id": EMPRESA_B, "sucursal_id": 5201,
         "variante_producto_id": 2201, "temporada_id": 4201,
         "stock_actual": 7, "stock_reservado": 1,
         "fecha_actualizacion": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ])


def _sembrar_ventas(conn) -> None:
    fecha = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    conn.execute(insert(Venta.__table__), [
        {"id": 7101, "empresa_id": EMPRESA_A, "cliente_id": None,
         "empleado_id": None, "sucursal_id": 5101, "reserva_id": None,
         "fecha_hora": fecha, "canal": "PRESENCIAL", "estado": "COMPLETADA",
         "total": Decimal("250.00"), "carrito_id": None},
        {"id": 7201, "empresa_id": EMPRESA_B, "cliente_id": None,
         "empleado_id": None, "sucursal_id": 5201, "reserva_id": None,
         "fecha_hora": fecha, "canal": "PRESENCIAL", "estado": "COMPLETADA",
         "total": Decimal("70.00"), "carrito_id": None},
    ])
    conn.execute(insert(DetalleVenta.__table__), [
        {"id": 8101, "empresa_id": EMPRESA_A, "venta_id": 7101,
         "inventario_id": 6101, "cantidad": 3,
         "precio_unitario": Decimal("50.00")},
        {"id": 8102, "empresa_id": EMPRESA_A, "venta_id": 7101,
         "inventario_id": 6102, "cantidad": 2,
         "precio_unitario": Decimal("50.00")},
        {"id": 8201, "empresa_id": EMPRESA_B, "venta_id": 7201,
         "inventario_id": 6201, "cantidad": 1,
         "precio_unitario": Decimal("70.00")},
    ])
    conn.execute(insert(Devolucion.__table__), [
        {"id": 9101, "empresa_id": EMPRESA_A, "venta_id": 7101,
         "fecha_hora": fecha, "motivo": "Talla incorrecta",
         "estado": "COMPLETADA", "observacion": None},
        {"id": 9102, "empresa_id": EMPRESA_A, "venta_id": 7101,
         "fecha_hora": fecha, "motivo": "Rechazada de prueba",
         "estado": "RECHAZADA", "observacion": None},
    ])
    # 1 unidad devuelta de 3 (COMPLETADA) y 5 en una RECHAZADA que NO cuenta.
    conn.execute(insert(DetalleDevolucion.__table__), [
        {"id": 9201, "empresa_id": EMPRESA_A, "devolucion_id": 9101,
         "detalle_venta_id": 8101, "cantidad": 1, "motivo": "Talla"},
        {"id": 9202, "empresa_id": EMPRESA_A, "devolucion_id": 9102,
         "detalle_venta_id": 8101, "cantidad": 5, "motivo": "Rechazada"},
    ])


@pytest.fixture()
def db_falsa():
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(motor)
    with motor.begin() as conn:
        _sembrar_matriz(conn)
        _sembrar_catalogo(conn)
        _sembrar_ventas(conn)
    SessionTest = sessionmaker(bind=motor, expire_on_commit=False)
    try:
        yield SessionTest
    finally:
        motor.dispose()


@pytest.fixture()
def cliente_tenant(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_devoluciones_enabled", True)
    actual = {"tenant": tenant_ficticio()}

    def obtener_db():
        with db_falsa() as db:
            yield db

    def obtener_tenant_ficticio():
        return actual["tenant"]

    app.dependency_overrides[get_db] = obtener_db
    app.dependency_overrides[get_current_tenant_context] = obtener_tenant_ficticio
    try:
        with TestClient(app) as client:
            yield client, actual
    finally:
        app.dependency_overrides.clear()


def _claves(elemento: dict) -> set[str]:
    return set(elemento.keys())


# ---------------------------------------------------------------------------
# 1. Diccionarios GLOBALES
# ---------------------------------------------------------------------------
def test_tallas_y_colores_devuelven_solo_activos_y_sin_empresa_id(
    cliente_tenant,
):
    client, _ = cliente_tenant
    tallas = client.get("/tenant/catalogo/tallas")
    colores = client.get("/tenant/catalogo/colores")
    assert tallas.status_code == colores.status_code == 200
    assert tallas.json() == [{"id": 1, "nombre": "M"}]
    assert colores.json() == [{"id": 1, "nombre": "Negro"}]
    # La talla/color inactivos no se ofrecen para construir formularios.
    assert [x["id"] for x in tallas.json()] == [1]
    assert _claves(tallas.json()[0]) == {"id", "nombre"}


def test_ciudades_ordenadas_por_nombre_y_sin_empresa_id(cliente_tenant):
    client, _ = cliente_tenant
    respuesta = client.get("/tenant/catalogo/ciudades")
    assert respuesta.status_code == 200
    assert respuesta.json() == [
        {"id": 1, "nombre": "Ciudad Alfa"},
        {"id": 2, "nombre": "Ciudad Beta"},
    ]


def test_diccionarios_globales_son_identicos_para_ambas_empresas(
    cliente_tenant,
):
    client, actual = cliente_tenant
    en_a = client.get("/tenant/catalogo/tallas").json()
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    en_b = client.get("/tenant/catalogo/tallas").json()
    # Los diccionarios no son datos de empresa: A y B ven las mismas filas.
    assert en_a == en_b == [{"id": 1, "nombre": "M"}]


# ---------------------------------------------------------------------------
# 2. Proveedores y temporadas: por empresa
# ---------------------------------------------------------------------------
def test_proveedores_se_filtran_por_empresa(cliente_tenant):
    client, actual = cliente_tenant
    en_a = client.get("/tenant/catalogo/proveedores")
    assert en_a.status_code == 200
    assert [x["id"] for x in en_a.json()] == [3102, 3101]
    assert _claves(en_a.json()[0]) == {"id", "razon_social", "estado"}
    assert {x["razon_social"] for x in en_a.json()} == {
        "Proveedor Alfa", "Beta Textil",
    }
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    en_b = client.get("/tenant/catalogo/proveedores")
    assert en_b.status_code == 200
    assert en_b.json() == []


def test_temporadas_se_filtran_por_empresa_con_fechas_iso(cliente_tenant):
    client, actual = cliente_tenant
    en_a = client.get("/tenant/catalogo/temporadas")
    assert en_a.status_code == 200
    assert en_a.json() == [{
        "id": 4101,
        "nombre": "2026",
        "fecha_inicio": "2026-01-01",
        "fecha_fin": "2026-12-31",
        "estado": True,
    }]
    assert "empresa_id" not in en_a.json()[0]
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    en_b = client.get("/tenant/catalogo/temporadas").json()
    assert [x["id"] for x in en_b] == [4201]
    assert "2026" not in [x["nombre"] for x in en_b]


# ---------------------------------------------------------------------------
# 3. Variantes de toda la empresa
# ---------------------------------------------------------------------------
def test_variantes_filtran_por_empresa_y_forma_exacta(cliente_tenant):
    client, actual = cliente_tenant
    en_a = client.get("/tenant/catalogo/variantes")
    assert en_a.status_code == 200
    assert [x["id"] for x in en_a.json()] == [2101, 2102]
    assert en_a.json()[0] == {
        "id": 2101,
        "producto_id": 1101,
        "producto_nombre": "Polera A",
        "sku": "A-001",
        "talla_id": 1,
        "talla": "M",
        "color_id": 1,
        "color": "Negro",
    }
    assert "empresa_id" not in en_a.json()[0]
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    en_b = client.get("/tenant/catalogo/variantes")
    assert en_b.status_code == 200
    assert [x["sku"] for x in en_b.json()] == ["B-777"]


def test_variantes_filtro_texto_es_tenant_y_case_insensitive(cliente_tenant):
    client, actual = cliente_tenant
    assert [x["id"] for x in
            client.get("/tenant/catalogo/variantes",
                       params={"texto": "a-00"}).json()] == [2101, 2102]
    assert [x["id"] for x in
            client.get("/tenant/catalogo/variantes",
                       params={"texto": "camisa"}).json()] == [2102]
    # El SKU de la empresa B no existe en A: nunca se filtra por texto global.
    assert client.get("/tenant/catalogo/variantes",
                      params={"texto": "B-777"}).json() == []
    assert client.get("/tenant/catalogo/variantes",
                      params={"producto_id": 1201}).json() == []
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    assert client.get("/tenant/catalogo/variantes",
                      params={"producto_id": 1201}).json()[0]["id"] == 2201


def test_variantes_valida_paginacion_y_texto(cliente_tenant):
    client, _ = cliente_tenant
    ruta = "/tenant/catalogo/variantes"
    assert client.get(ruta, params={"limit": 0}).status_code == 422
    assert client.get(ruta, params={"limit": 201}).status_code == 422
    assert client.get(ruta, params={"offset": -1}).status_code == 422
    assert client.get(ruta, params={"producto_id": 0}).status_code == 422
    assert client.get(ruta, params={"texto": "x" * 61}).status_code == 422
    assert client.get(ruta, params={"offset": 2}).json() == []


# ---------------------------------------------------------------------------
# 4. Inventario tenant por sucursal
# ---------------------------------------------------------------------------
def test_inventario_propio_calcula_stock_disponible(cliente_tenant):
    client, _ = cliente_tenant
    respuesta = client.get("/tenant/inventario")
    assert respuesta.status_code == 200
    filas = respuesta.json()
    assert [f["id"] for f in filas] == [6101, 6102]
    assert filas[0] == {
        "id": 6101,
        "sucursal_id": 5101,
        "sucursal_nombre": "Sucursal A",
        "variante_producto_id": 2101,
        "producto_id": 1101,
        "producto_nombre": "Polera A",
        "sku": "A-001",
        "talla_id": 1,
        "talla": "M",
        "color_id": 1,
        "color": "Negro",
        "temporada_id": 4101,
        "temporada_nombre": "2026",
        "stock_actual": 20,
        "stock_reservado": 2,
        "stock_disponible": 18,
    }
    assert "empresa_id" not in filas[0]


def test_inventario_aislado_por_empresa_y_sucursal(cliente_tenant):
    client, actual = cliente_tenant
    # Sucursal de la empresa B consultada desde A: lista vacia, sin 404.
    assert client.get("/tenant/inventario",
                      params={"sucursal_id": 5201}).json() == []
    assert [f["id"] for f in
            client.get("/tenant/inventario",
                       params={"sucursal_id": 5101}).json()] == [6101, 6102]
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    filas_b = client.get("/tenant/inventario").json()
    assert [f["id"] for f in filas_b] == [6201]
    assert filas_b[0]["stock_disponible"] == 6
    assert filas_b[0]["sku"] == "B-777"


def test_inventario_filtro_texto_y_paginacion(cliente_tenant):
    client, _ = cliente_tenant
    ruta = "/tenant/inventario"
    assert [f["id"] for f in
            client.get(ruta, params={"texto": "a-002"}).json()] == [6102]
    assert client.get(ruta, params={"texto": "B-777"}).json() == []
    assert client.get(ruta, params={"sucursal_id": 0}).status_code == 422
    assert client.get(ruta, params={"limit": 0}).status_code == 422
    assert client.get(ruta, params={"limit": 201}).status_code == 422
    assert client.get(ruta, params={"offset": -1}).status_code == 422


# ---------------------------------------------------------------------------
# 5. Lineas de venta para devolucion
# ---------------------------------------------------------------------------
def test_lineas_venta_propia_informa_cantidad_disponible(cliente_tenant):
    client, _ = cliente_tenant
    respuesta = client.get("/tenant/devoluciones/ventas/7101/lineas")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert _claves(cuerpo) == {
        "venta_id", "sucursal_id", "estado", "total", "lineas",
    }
    assert cuerpo["venta_id"] == 7101
    assert cuerpo["sucursal_id"] == 5101
    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["total"] == 250.0
    lineas = {x["detalle_venta_id"]: x for x in cuerpo["lineas"]}
    assert set(lineas) == {8101, 8102}
    assert lineas[8101] == {
        "detalle_venta_id": 8101,
        "inventario_id": 6101,
        "variante_producto_id": 2101,
        "sku": "A-001",
        "producto_nombre": "Polera A",
        "talla": "M",
        "color": "Negro",
        "cantidad": 3,
        "precio_unitario": 50.0,
        # 1 devuelta en COMPLETADA; las 5 de la RECHAZADA NO cuentan.
        "cantidad_devuelta": 1,
        "cantidad_disponible_devolver": 2,
    }
    assert lineas[8102]["cantidad_devuelta"] == 0
    assert lineas[8102]["cantidad_disponible_devolver"] == 2


def test_lineas_venta_ajena_o_inexistente_responde_404_sin_filtrar(
    cliente_tenant,
):
    client, _ = cliente_tenant
    for venta_id in (7201, 999999):
        respuesta = client.get(f"/tenant/devoluciones/ventas/{venta_id}/lineas")
        assert respuesta.status_code == 404, venta_id
        assert respuesta.json() == {"detail": "Recurso no encontrado"}
    # Un ID no positivo JAMAS entra en la allowlist del gate (misma regla que
    # el resto de rutas con `{id}`): responde 503 antes del router.
    for venta_id in (0, -1):
        respuesta = client.get(f"/tenant/devoluciones/ventas/{venta_id}/lineas")
        assert respuesta.status_code == 503, venta_id


def test_lineas_venta_de_la_propia_empresa_b(cliente_tenant):
    client, actual = cliente_tenant
    actual["tenant"] = tenant_ficticio(EMPRESA_B)
    cuerpo = client.get("/tenant/devoluciones/ventas/7201/lineas").json()
    assert cuerpo["sucursal_id"] == 5201
    assert [x["detalle_venta_id"] for x in cuerpo["lineas"]] == [8201]
    assert cuerpo["lineas"][0]["cantidad_disponible_devolver"] == 1


# ---------------------------------------------------------------------------
# 6. Autenticacion y contexto
# ---------------------------------------------------------------------------
RUTAS_PERSONAL = (
    "/tenant/catalogo/tallas",
    "/tenant/catalogo/colores",
    "/tenant/catalogo/ciudades",
    "/tenant/catalogo/proveedores",
    "/tenant/catalogo/temporadas",
    "/tenant/catalogo/variantes",
    "/tenant/inventario",
    "/tenant/devoluciones/ventas/7101/lineas",
)


@pytest.mark.parametrize("ruta", RUTAS_PERSONAL)
def test_sin_jwt_responde_401_y_no_consulta_tenant(
    monkeypatch, db_falsa, ruta
):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    # Sin el flag del modulo, el gate responderia 503 antes de la dependencia.
    monkeypatch.setattr(settings, "saas_devoluciones_enabled", True)

    def obtener_db():
        with db_falsa() as db:
            yield db

    app.dependency_overrides[get_db] = obtener_db
    try:
        with TestClient(app) as client:
            assert client.get(ruta).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_contexto_cliente_no_accede_a_rutas_de_personal(cliente_tenant):
    client, actual = cliente_tenant
    actual["tenant"] = tenant_ficticio(
        rol_id=4, rol_nombre="CLIENTE", contexto="cliente",
    )
    for ruta in RUTAS_PERSONAL:
        assert client.get(ruta).status_code == 403, ruta


def test_cajero_sin_permiso_no_consulta_personal(cliente_tenant):
    client, actual = cliente_tenant
    actual["tenant"] = tenant_ficticio(rol_id=ROL_CAJERO, rol_nombre="CAJERO")
    # CAJERO no tiene PERSONAL/* en la matriz: las lecturas siguen en 403.
    assert client.get("/tenant/empresa/usuarios").status_code == 403
    assert client.get("/tenant/empresa/roles").status_code == 403


def test_matriz_autoriza_consulta_de_encargado_y_niega_administracion(
    db_falsa,
):
    """La matriz ya concedia PERSONAL/CONSULTAR al ENCARGADO_SUCURSAL.

    Se evalua el verificador real (rama SQLite de `_tiene_permiso_tenant`) para
    dejar constancia de que la lectura procede de la matriz y NO del nombre de
    rol; el 200 HTTP con PostgreSQL/RLS se cubre en la bateria 122.
    """
    from app.core.tenant_dependencies import _tiene_permiso_tenant

    encargado = tenant_ficticio(
        rol_id=ROL_ENCARGADO, rol_nombre="ENCARGADO_SUCURSAL",
    )
    cajero = tenant_ficticio(rol_id=ROL_CAJERO, rol_nombre="CAJERO")
    with db_falsa() as db:
        assert _tiene_permiso_tenant(db, encargado, "PERSONAL", "CONSULTAR")
        assert not _tiene_permiso_tenant(db, encargado, "PERSONAL", "ADMINISTRAR")
        assert not _tiene_permiso_tenant(db, cajero, "PERSONAL", "CONSULTAR")


# ---------------------------------------------------------------------------
# 7. Gate de rutas en modo SaaS
# ---------------------------------------------------------------------------
def test_gate_permite_las_rutas_nuevas_en_modo_saas(monkeypatch, db_falsa):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    # 5C1: el 503 del legacy corresponde al modo corte, no a "SaaS activo".
    monkeypatch.setattr(settings, "legacy_surface_enabled", False)
    monkeypatch.setattr(settings, "saas_devoluciones_enabled", True)

    def obtener_db():
        with db_falsa() as db:
            yield db

    app.dependency_overrides[get_db] = obtener_db
    app.dependency_overrides[get_current_tenant_context] = (
        lambda: tenant_ficticio()
    )
    try:
        with TestClient(app) as client:
            for ruta in RUTAS_PERSONAL:
                # Ninguna ruta nueva debe caer en el 503 del gate.
                assert client.get(ruta).status_code == 200, ruta
            assert client.get("/productos").status_code == 503
            assert client.get("/tenant/inventario/").status_code == 503
            assert client.get("/tenant/catalogo/tallas/").status_code == 503
            assert client.get(
                "/tenant/inventario/../productos"
            ).status_code == 503
            assert client.get(
                "/tenant/devoluciones/ventas/0/lineas"
            ).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_gate_no_abre_lineas_de_venta_sin_flag(monkeypatch):
    monkeypatch.setattr(settings, "saas_auth_enabled", True)
    monkeypatch.setattr(settings, "saas_devoluciones_enabled", False)
    with TestClient(app) as client:
        respuesta = client.get("/tenant/devoluciones/ventas/7101/lineas")
        assert respuesta.status_code == 503
        assert "Ruta no disponible" in respuesta.json()["detail"]


# ---------------------------------------------------------------------------
# 8. Coherencia RBAC exigida en el router de membresias
# ---------------------------------------------------------------------------
def test_lecturas_de_personal_usan_la_matriz_nominal_no_el_nombre_de_rol():
    fuente = API_MEMBRESIAS.read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    dependencias: dict[str, str] = {}
    for nodo in arbol.body:
        if not isinstance(nodo, ast.FunctionDef):
            continue
        for argumento in nodo.args.args:
            nombre = argumento.arg
            if nombre == "admin" and argumento.annotation is not None:
                anotacion = argumento.annotation
                if isinstance(anotacion, ast.Name):
                    dependencias[nodo.name] = anotacion.id
    assert dependencias["usuarios_empresa"] == "ConsultaPersonal"
    assert dependencias["roles_empresa"] == "ConsultaPersonal"
    assert dependencias["permisos_empresa"] == "ConsultaPersonal"
    # La ESCRITURA no se relaja: alta y cambios siguen exigiendo lo suyo.
    assert dependencias["alta_personal_empresa"] == "GestorPersonal"
    assert dependencias["cambiar_sucursal_personal"] == "GestorPersonal"
    assert dependencias["actualizar_permisos_rol"] == "GestorMatriz"
    assert "PERSONAL\",\"CONSULTAR" in fuente.replace(" ", "")
