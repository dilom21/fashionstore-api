"""B3/P5B: primera transaccion de carrito cliente SaaS en SQLite en memoria.

NO se usan Supabase, PostgreSQL local ni procedimientos de reservas, pagos,
ventas o inventario. Se simulan JWT/membresia con identidad tenant ficticia,
pero se comprueba el cliente REALMENTE enlazado en tablas del test.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, insert, select, func
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.main import app
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Ciudad, Sucursal


@compiles(BigInteger, "sqlite")
def _bigint_pk_autogenerada(tipo, compilador, **kw):
    # Solo para la BD SQLite temporal de este proceso pytest.
    return "INTEGER"


def _total(factory, tabla, **filtros):
    with factory() as db:
        consulta = select(func.count()).select_from(tabla)
        for nombre, valor in filtros.items():
            consulta = consulta.where(tabla.c[nombre] == valor)
        return int(db.scalar(consulta))


@pytest.fixture()
def escenario_carrito(monkeypatch):
    motor = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(motor, tables=[
        Ciudad.__table__, Sucursal.__table__, Cliente.__table__,
        Categoria.__table__, Producto.__table__, Talla.__table__,
        Color.__table__, Temporada.__table__, VarianteProducto.__table__,
        Inventario.__table__, Carrito.__table__, DetalleCarrito.__table__,
    ])
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    with motor.begin() as c:
        c.execute(insert(Ciudad.__table__), [
            {"id": 1, "nombre": "Ciudad Prueba", "estado": True},
        ])
        c.execute(insert(Cliente.__table__), [
            {"id": 1001, "empresa_id": 10, "usuario_id": 99, "nombre": "A",
             "apellido": "Demo", "estado": True},
            {"id": 2002, "empresa_id": 20, "usuario_id": 88, "nombre": "B",
             "apellido": "Demo", "estado": True},
        ])
        c.execute(insert(Sucursal.__table__), [
            {"id": 1101, "empresa_id": 10, "ciudad_id": 1,
             "nombre": "Sucursal Urban", "direccion": "Ficticia", "estado": True},
            {"id": 2202, "empresa_id": 20, "ciudad_id": 1,
             "nombre": "Sucursal Vanter", "direccion": "Ficticia", "estado": True},
        ])
        c.execute(insert(Categoria.__table__), [
            {"id": 111, "empresa_id": 10, "nombre": "Moda A", "estado": True},
            {"id": 222, "empresa_id": 20, "nombre": "Moda B", "estado": True},
        ])
        c.execute(insert(Producto.__table__), [
            {"id": 111, "empresa_id": 10, "categoria_id": 111,
             "nombre": "Camisa A", "precio": Decimal("60.00"), "estado": True},
            {"id": 222, "empresa_id": 20, "categoria_id": 222,
             "nombre": "Camisa B", "precio": Decimal("70.00"), "estado": True},
        ])
        c.execute(insert(Talla.__table__), [
            {"id": 1, "nombre": "M", "estado": True},
        ])
        c.execute(insert(Color.__table__), [
            {"id": 1, "nombre": "Negro", "estado": True},
        ])
        c.execute(insert(Temporada.__table__), [
            {"id": 111, "empresa_id": 10, "nombre": "Temporada A",
             "fecha_inicio": ahora.date(), "fecha_fin": ahora.date(), "estado": True},
            {"id": 222, "empresa_id": 20, "nombre": "Temporada B",
             "fecha_inicio": ahora.date(), "fecha_fin": ahora.date(), "estado": True},
        ])
        c.execute(insert(VarianteProducto.__table__), [
            {"id": 111, "empresa_id": 10, "producto_id": 111,
             "talla_id": 1, "color_id": 1, "sku": "A-111", "estado": True},
            {"id": 222, "empresa_id": 20, "producto_id": 222,
             "talla_id": 1, "color_id": 1, "sku": "B-222", "estado": True},
        ])
        c.execute(insert(Inventario.__table__), [
            {"id": 1111, "empresa_id": 10, "sucursal_id": 1101,
             "variante_producto_id": 111, "temporada_id": 111,
             "stock_actual": 10, "stock_reservado": 2,
             "fecha_actualizacion": ahora},
            {"id": 2222, "empresa_id": 20, "sucursal_id": 2202,
             "variante_producto_id": 222, "temporada_id": 222,
             "stock_actual": 7, "stock_reservado": 0,
             "fecha_actualizacion": ahora},
        ])

    factory = sessionmaker(bind=motor, expire_on_commit=False)
    identidad = {"empresa_id": 10, "usuario_id": 99, "contexto": "cliente"}
    monkeypatch.setattr(settings, "saas_auth_enabled", True)

    def db_ficticia():
        with factory() as db:
            yield db

    def tenant_ficticio():
        return TenantContext(
            usuario_id=identidad["usuario_id"],
            empresa_id=identidad["empresa_id"],
            empresa_slug="urbanwear" if identidad["empresa_id"] == 10 else "vanter-men",
            empresa_nombre="Empresa Demo",
            rol_id=1, rol_nombre="CLIENTE",
            contexto=identidad["contexto"], plan_codigo="BASICO",
            permite_reportes_ia=False,
        )

    app.dependency_overrides[get_db] = db_ficticia
    app.dependency_overrides[get_current_tenant_context] = tenant_ficticio
    try:
        with TestClient(app) as client:
            yield client, identidad, factory
    finally:
        app.dependency_overrides.clear()
        motor.dispose()


def _agregar(client, sucursal=1101, inventario=1111, cantidad=2):
    return client.post("/tenant/carritos/items", json={
        "sucursal_id": sucursal, "inventario_id": inventario,
        "cantidad": cantidad,
    })


def test_crea_carrito_cliente_y_detalle_en_empresa_propia(escenario_carrito):
    client, _, factory = escenario_carrito
    res = _agregar(client)
    assert res.status_code == 200, res.text
    assert res.json()["cantidad"] == 2
    assert res.json()["estado"] == "ACTIVO"
    assert _total(factory, Carrito.__table__, empresa_id=10, cliente_id=1001) == 1
    assert _total(factory, DetalleCarrito.__table__, empresa_id=10) == 1
    with factory() as db:
        registro = db.execute(
            select(Carrito.empresa_id, Carrito.cliente_id, Carrito.sucursal_id)
        ).one()
    assert registro == (10, 1001, 1101)


def test_reutiliza_carrito_y_acumula_cantidad(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client, cantidad=2)
    b = _agregar(client, cantidad=3)
    assert a.status_code == b.status_code == 200
    assert a.json()["carrito_id"] == b.json()["carrito_id"]
    assert a.json()["detalle_id"] == b.json()["detalle_id"]
    assert b.json()["cantidad"] == 5
    assert _total(factory, Carrito.__table__, empresa_id=10) == 1
    assert _total(factory, DetalleCarrito.__table__, empresa_id=10) == 1


def test_agregar_al_carrito_no_altera_stock_actual_ni_reservado(escenario_carrito):
    client, _, factory = escenario_carrito
    assert _agregar(client, cantidad=6).status_code == 200
    with factory() as db:
        stock = db.execute(
            select(Inventario.stock_actual, Inventario.stock_reservado)
            .where(Inventario.id == 1111)
        ).one()
    assert stock == (10, 2)


@pytest.mark.parametrize("sucursal,inventario", [
    (1101, 2222),   # inventario ajeno
    (2202, 2222),   # ambos de empresa ajena
    (2202, 1111),   # sucursal ajena con inventario propio
    (1101, 9999),   # inventario inexistente
])
def test_sucursal_e_inventario_ajenos_devuelven_404(
    escenario_carrito, sucursal, inventario,
):
    client, _, factory = escenario_carrito
    resp = _agregar(client, sucursal=sucursal, inventario=inventario)
    assert resp.status_code == 404
    assert _total(factory, Carrito.__table__) == 0
    assert _total(factory, DetalleCarrito.__table__) == 0


def test_stock_insuficiente_hace_rollback_de_carrito_nuevo(escenario_carrito):
    client, _, factory = escenario_carrito
    assert _agregar(client, cantidad=9).status_code == 409
    assert _total(factory, Carrito.__table__) == 0
    assert _total(factory, DetalleCarrito.__table__) == 0


def test_stock_insuficiente_no_incrementa_linea_existente(escenario_carrito):
    client, _, factory = escenario_carrito
    r = _agregar(client, cantidad=7)
    assert r.status_code == 200
    assert _agregar(client, cantidad=2).status_code == 409
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 7


def test_falta_perfil_de_cliente_de_esa_empresa_403(escenario_carrito):
    client, identidad, factory = escenario_carrito
    identidad["empresa_id"] = 20  # usuario 99 no es cliente de Vanter
    assert _agregar(client, sucursal=2202, inventario=2222).status_code == 403
    assert _total(factory, Carrito.__table__) == 0


def test_usuario_con_perfil_vigente_en_empresa_b_puede_agregar_solo_ahi(escenario_carrito):
    client, identidad, factory = escenario_carrito
    identidad["empresa_id"] = 20
    identidad["usuario_id"] = 88
    r = _agregar(client, sucursal=2202, inventario=2222, cantidad=2)
    assert r.status_code == 200, r.text
    assert _total(factory, Carrito.__table__, empresa_id=20, cliente_id=2002) == 1
    assert _total(factory, Carrito.__table__, empresa_id=10) == 0


def test_contexto_personal_no_puede_utilizar_carrito_cliente(escenario_carrito):
    client, identidad, factory = escenario_carrito
    identidad["contexto"] = "personal"
    assert _agregar(client).status_code == 403
    assert _total(factory, Carrito.__table__) == 0


@pytest.mark.parametrize("campo", ["empresa_id", "cliente_id", "estado"])
def test_suplantacion_de_propietario_desde_json_rechazada(escenario_carrito, campo):
    client, _, factory = escenario_carrito
    datos = {"sucursal_id": 1101, "inventario_id": 1111, "cantidad": 2, campo: 20}
    assert client.post("/tenant/carritos/items", json=datos).status_code == 422
    assert _total(factory, Carrito.__table__) == 0


@pytest.mark.parametrize("valor", [0, -1, 1001, "invalido"])
def test_cantidad_invalida_rechazada(escenario_carrito, valor):
    client, _, factory = escenario_carrito
    datos = {"sucursal_id": 1101, "inventario_id": 1111, "cantidad": valor}
    assert client.post("/tenant/carritos/items", json=datos).status_code == 422
    assert _total(factory, Carrito.__table__) == 0


def test_carrito_vencido_se_expira_y_se_crea_otro(escenario_carrito):
    client, _, factory = escenario_carrito
    pasado = datetime.now(timezone.utc) - timedelta(hours=3)
    with factory.begin() as db:
        db.execute(insert(Carrito.__table__).values(
            id=7777, empresa_id=10, cliente_id=1001,
            sucursal_id=1101, fecha_creacion=pasado,
            fecha_actualizacion=pasado, estado="ACTIVO",
        ))
    r = _agregar(client)
    assert r.status_code == 200, r.text
    assert r.json()["carrito_id"] != 7777
    with factory() as db:
        assert db.scalar(select(Carrito.estado).where(Carrito.id == 7777)) == "EXPIRADO"
    assert _total(factory, Carrito.__table__, empresa_id=10) == 2
    assert _total(factory, Carrito.__table__, empresa_id=10, estado="ACTIVO") == 1


def test_no_acepta_variante_de_empresa_ajena_aunque_inventario_diga_urbanwear(
    escenario_carrito,
):
    client, _, factory = escenario_carrito
    # SQLite no replica la FK compuesta PostgreSQL; insert malicioso de prueba.
    with factory.begin() as db:
        db.execute(insert(Inventario.__table__).values(
            id=3333, empresa_id=10, sucursal_id=1101,
            variante_producto_id=222, temporada_id=111,
            stock_actual=10, stock_reservado=0,
            fecha_actualizacion=datetime.now(timezone.utc),
        ))
    assert _agregar(client, inventario=3333).status_code == 404
    assert _total(factory, Carrito.__table__) == 0


def test_modo_saas_apagado_impide_la_ruta_nueva(escenario_carrito, monkeypatch):
    client, _, factory = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    assert _agregar(client).status_code == 503
    assert _total(factory, Carrito.__table__) == 0


def test_falta_token_devuelve_401_sin_crear_carrito(escenario_carrito):
    client, _, factory = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    assert _agregar(client).status_code == 401
    assert _total(factory, Carrito.__table__) == 0


def test_resto_del_flujo_comercial_sigue_bloqueado_503(escenario_carrito):
    client, _, factory = escenario_carrito
    for metodo, ruta in (
        ("POST", "/carritos/items"),
        ("POST", "/tenant/carritos/crear-reserva"),
        ("PUT", "/tenant/carritos/123/items/9"),
        ("POST", "/tenant/carritos/123"),
        ("DELETE", "/tenant/carritos/123/items"),
        ("POST", "/tenant/comercial/pagos"),
        ("POST", "/tenant/comercial/ventas"),
    ):
        assert client.request(metodo, ruta, json={}).status_code == 503
    assert _total(factory, Carrito.__table__) == 0


# B3/P5C: PATCH de cantidad y DELETE de linea/carrito, sin alterar stock.

def _carrito_creado(client):
    r = _agregar(client, cantidad=2)
    assert r.status_code == 200, r.text
    return r.json()


def test_patch_cantidad_reemplaza_y_no_suma(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    ruta = f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
    respuesta = client.patch(ruta, json={"cantidad": 5})
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["cantidad"] == 5
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 5
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 1111)).one() == (10, 2)


def test_patch_cantidad_maxima_disponible_o_rechazada(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    ruta = f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
    assert client.patch(ruta, json={"cantidad": 9}).status_code == 409
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2
    assert client.patch(ruta, json={"cantidad": 8}).status_code == 200
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 8


def test_delete_ultima_linea_elimina_carrito_logicamente(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    ruta = f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
    resp = client.delete(ruta)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "carrito_id": carrito["carrito_id"],
        "estado": "ELIMINADO",
        "cantidad_lineas": 0,
    }
    assert _total(factory, DetalleCarrito.__table__) == 0
    with factory() as db:
        assert db.scalar(select(Carrito.estado)) == "ELIMINADO"
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 1111)).one() == (10, 2)


def test_delete_carrito_logico_conserva_detalles(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    resp = client.delete(f'/tenant/carritos/{carrito["carrito_id"]}')
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "ELIMINADO"
    assert resp.json()["cantidad_lineas"] == 1
    assert _total(factory, Carrito.__table__) == 1
    assert _total(factory, DetalleCarrito.__table__) == 1
    assert client.delete(f'/tenant/carritos/{carrito["carrito_id"]}').status_code == 409


def test_delete_una_de_dos_lineas_mantiene_carrito_activo(escenario_carrito):
    client, _, factory = escenario_carrito
    with factory.begin() as db:
        db.execute(insert(VarianteProducto.__table__).values(
            id=112, empresa_id=10, producto_id=111,
            talla_id=1, color_id=1, sku="A-112", estado=True,
        ))
        db.execute(insert(Inventario.__table__).values(
            id=1112, empresa_id=10, sucursal_id=1101,
            variante_producto_id=112, temporada_id=111,
            stock_actual=5, stock_reservado=0,
            fecha_actualizacion=datetime.now(timezone.utc),
        ))
    primero = _carrito_creado(client)
    segundo = _agregar(client, inventario=1112, cantidad=1)
    assert segundo.status_code == 200, segundo.text
    assert primero["carrito_id"] == segundo.json()["carrito_id"]
    resp = client.delete(
        f'/tenant/carritos/{primero["carrito_id"]}/items/{primero["detalle_id"]}'
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "ACTIVO"
    assert resp.json()["cantidad_lineas"] == 1
    assert _total(factory, DetalleCarrito.__table__) == 1
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.inventario_id)) == 1112


@pytest.mark.parametrize("metodo", ["PATCH", "DELETE_LINEA", "DELETE_CARRITO"])
def test_mutaciones_no_alcanzan_carrito_otra_empresa(escenario_carrito, metodo):
    client, identidad, factory = escenario_carrito
    carrito_a = _carrito_creado(client)
    identidad["empresa_id"], identidad["usuario_id"] = 20, 88
    carrito_b = _agregar(client, sucursal=2202, inventario=2222, cantidad=2).json()
    identidad["empresa_id"], identidad["usuario_id"] = 10, 99
    if metodo == "PATCH":
        r = client.patch(
            f'/tenant/carritos/{carrito_b["carrito_id"]}/items/{carrito_b["detalle_id"]}',
            json={"cantidad": 5},
        )
    elif metodo == "DELETE_LINEA":
        r = client.delete(
            f'/tenant/carritos/{carrito_b["carrito_id"]}/items/{carrito_b["detalle_id"]}'
        )
    else:
        r = client.delete(f'/tenant/carritos/{carrito_b["carrito_id"]}')
    assert r.status_code == 404, r.text
    with factory() as db:
        assert db.scalar(
            select(DetalleCarrito.cantidad).where(
                DetalleCarrito.id == carrito_b["detalle_id"]
            )
        ) == 2
        assert db.scalar(
            select(Carrito.estado).where(Carrito.id == carrito_b["carrito_id"])
        ) == "ACTIVO"
    assert _total(factory, Carrito.__table__) == 2


def test_detalle_propio_de_otro_carrito_no_modificable(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    with factory.begin() as db:
        db.execute(insert(Carrito.__table__).values(
            id=8888, empresa_id=10, cliente_id=1001, sucursal_id=1101,
            fecha_creacion=datetime.now(timezone.utc),
            fecha_actualizacion=datetime.now(timezone.utc),
            estado="ACTIVO",
        ))
    ruta = f'/tenant/carritos/8888/items/{carrito["detalle_id"]}'
    assert client.patch(ruta, json={"cantidad": 4}).status_code == 404
    assert client.delete(ruta).status_code == 404
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2


@pytest.mark.parametrize("metodo", ["PATCH", "DELETE_LINEA", "DELETE_CARRITO"])
def test_carrito_caducado_o_convertido_no_se_reactiva(escenario_carrito, metodo):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    with factory.begin() as db:
        db.execute(
            Carrito.__table__.update().where(
                Carrito.__table__.c.id == carrito["carrito_id"]
            ).values(
                fecha_actualizacion=datetime.now(timezone.utc) - timedelta(hours=3),
            )
        )
    if metodo == "PATCH":
        r = client.patch(
            f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}',
            json={"cantidad": 4},
        )
    elif metodo == "DELETE_LINEA":
        r = client.delete(
            f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
        )
    else:
        r = client.delete(f'/tenant/carritos/{carrito["carrito_id"]}')
    assert r.status_code == 409, r.text
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2
    assert _total(factory, DetalleCarrito.__table__) == 1


@pytest.mark.parametrize("datos", [
    {}, {"cantidad": None}, {"cantidad": 0},
    {"cantidad": -2}, {"cantidad": 1001},
    {"cantidad": 4, "empresa_id": 20},
    {"cantidad": 4, "cliente_id": 2002},
])
def test_patch_cantidad_invalida_y_suplantacion(escenario_carrito, datos):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    r = client.patch(
        f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}',
        json=datos,
    )
    assert r.status_code == 422
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2


@pytest.mark.parametrize("metodo", ["PATCH", "DELETE"])
def test_contexto_personal_no_puede_editar_carrito(escenario_carrito, metodo):
    client, identidad, factory = escenario_carrito
    carrito = _carrito_creado(client)
    identidad["contexto"] = "personal"
    ruta = f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
    r = client.patch(ruta, json={"cantidad": 3}) if metodo == "PATCH" else client.delete(ruta)
    assert r.status_code == 403
    assert _total(factory, DetalleCarrito.__table__) == 1


def test_perfil_cliente_distinto_denegado_antes_de_editar(escenario_carrito):
    client, identidad, factory = escenario_carrito
    carrito = _carrito_creado(client)
    identidad["usuario_id"] = 88
    r = client.delete(f'/tenant/carritos/{carrito["carrito_id"]}')
    assert r.status_code == 403
    assert _total(factory, Carrito.__table__, estado="ACTIVO") == 1


def test_stock_de_variante_ajena_impide_patch(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    with factory.begin() as db:
        db.execute(
            Inventario.__table__.update().where(Inventario.__table__.c.id == 1111)
            .values(variante_producto_id=222)
        )
    r = client.patch(
        f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}',
        json={"cantidad": 4},
    )
    assert r.status_code == 404
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2


@pytest.mark.parametrize("metodo", ["PATCH", "DELETE"])
def test_flag_apagado_y_token_ausente_bloquean_mutacion(escenario_carrito, metodo, monkeypatch):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    app.dependency_overrides.pop(get_current_tenant_context)
    ruta = f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}'
    r = client.patch(ruta, json={"cantidad": 3}) if metodo == "PATCH" else client.delete(ruta)
    assert r.status_code == 401
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    r = client.patch(ruta, json={"cantidad": 3}) if metodo == "PATCH" else client.delete(ruta)
    assert r.status_code == 503
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2


def test_gate_restringe_metodos_y_urls_de_carrito(escenario_carrito):
    client, _, factory = escenario_carrito
    for metodo, ruta in (
        ("PUT", "/tenant/carritos/1/items/1"),
        ("POST", "/tenant/carritos/1/items/1"),
        ("GET", "/tenant/carritos/1/items"),
        ("PATCH", "/tenant/carritos/0/items/1"),
        ("DELETE", "/tenant/carritos/01"),
        ("DELETE", "/tenant/carritos/1/extra"),
        ("POST", "/tenant/reservas"),
        ("POST", "/tenant/comercial/ventas"),
    ):
        assert client.request(metodo, ruta).status_code == 503
    assert _total(factory, Carrito.__table__) == 0


def test_eliminar_y_editar_no_cambian_reservas_ni_movimientos(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _carrito_creado(client)
    assert client.patch(
        f'/tenant/carritos/{carrito["carrito_id"]}/items/{carrito["detalle_id"]}',
        json={"cantidad": 6},
    ).status_code == 200
    assert client.delete(f'/tenant/carritos/{carrito["carrito_id"]}').status_code == 200
    with factory() as db:
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 1111)).one() == (10, 2)
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 2222)).one() == (7, 0)
