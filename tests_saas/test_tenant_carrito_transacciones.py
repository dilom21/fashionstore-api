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
from sqlalchemy import BigInteger, create_engine, insert, select, func, event
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
from app.modules.reservas.models.models import Reserva
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
        Reserva.__table__,
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


# B3/P5D: lecturas del carrito propio sin escribir ni tocar stock.

def test_listar_y_detallar_carrito_propio_con_totales_correctos(escenario_carrito):
    client, _, factory = escenario_carrito
    alta = _agregar(client, cantidad=3)
    assert alta.status_code == 200, alta.text
    cid = alta.json()["carrito_id"]
    lista = client.get("/tenant/carritos")
    assert lista.status_code == 200, lista.text
    assert lista.json()["total_carritos_activos"] == 1
    assert len(lista.json()["items"]) == 1
    resumen = lista.json()["items"][0]
    assert resumen["carrito_id"] == cid
    assert resumen["sucursal_nombre"] == "Sucursal Urban"
    assert resumen["cantidad_lineas"] == 1
    assert resumen["cantidad_unidades"] == 3
    assert Decimal(str(resumen["subtotal"])) == Decimal("180.00")
    detalle = client.get(f"/tenant/carritos/{cid}")
    assert detalle.status_code == 200, detalle.text
    body = detalle.json()
    assert body["estado"] == "ACTIVO"
    assert body["cantidad_total_unidades"] == 3
    assert Decimal(str(body["subtotal_carrito"])) == Decimal("180.00")
    linea = body["items"][0]
    assert linea["detalle_id"] == alta.json()["detalle_id"]
    assert linea["producto_nombre"] == "Camisa A"
    assert linea["sku"] == "A-111"
    assert linea["temporada_nombre"] == "Temporada A"
    assert linea["talla_nombre"] == "M"
    assert linea["color_nombre"] == "Negro"
    assert linea["stock_disponible"] == 8
    assert Decimal(str(linea["precio_unitario"])) == Decimal("60.00")
    assert Decimal(str(linea["subtotal_linea"])) == Decimal("180.00")
    assert "cliente_id" not in str(body)
    assert "empresa_id" not in str(body)
    assert _total(factory, DetalleCarrito.__table__, empresa_id=10) == 1


def test_listado_inicial_vacio_para_cliente_valido(escenario_carrito):
    client, _, _ = escenario_carrito
    respuesta = client.get("/tenant/carritos")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"items": [], "total_carritos_activos": 0}


def test_cambiar_tenant_cambia_carritos_y_detalles(escenario_carrito):
    client, identidad, _ = escenario_carrito
    a = _agregar(client, cantidad=2).json()
    identidad["empresa_id"], identidad["usuario_id"] = 20, 88
    b = _agregar(client, sucursal=2202, inventario=2222, cantidad=1).json()
    lista_b = client.get("/tenant/carritos")
    assert lista_b.status_code == 200
    assert [x["carrito_id"] for x in lista_b.json()["items"]] == [b["carrito_id"]]
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 404
    propio_b = client.get(f'/tenant/carritos/{b["carrito_id"]}')
    assert propio_b.status_code == 200, propio_b.text
    assert propio_b.json()["items"][0]["producto_nombre"] == "Camisa B"
    identidad["empresa_id"], identidad["usuario_id"] = 10, 99
    assert [x["carrito_id"] for x in client.get("/tenant/carritos").json()["items"]] == [a["carrito_id"]]
    assert client.get(f'/tenant/carritos/{b["carrito_id"]}').status_code == 404


def test_otro_cliente_misma_empresa_no_ve_carrito(escenario_carrito):
    client, identidad, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(insert(Cliente.__table__).values(
            id=1002, empresa_id=10, usuario_id=77,
            nombre="Otro", apellido="Usuario", estado=True,
        ))
    identidad["usuario_id"] = 77
    assert client.get("/tenant/carritos").json() == {
        "items": [], "total_carritos_activos": 0,
    }
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 404


def test_query_empresa_y_cliente_no_cambian_identidad(escenario_carrito):
    client, _, _ = escenario_carrito
    a = _agregar(client).json()
    lista = client.get("/tenant/carritos", params={
        "empresa_id": 20, "cliente_id": 2002,
    })
    assert [x["carrito_id"] for x in lista.json()["items"]] == [a["carrito_id"]]
    detalle = client.get(f'/tenant/carritos/{a["carrito_id"]}', params={
        "empresa_id": 20, "cliente_id": 2002,
    })
    assert detalle.status_code == 200
    assert detalle.json()["items"][0]["sku"] == "A-111"


@pytest.mark.parametrize("estado", ["CONVERTIDO", "ELIMINADO", "EXPIRADO"])
def test_carrito_no_activo_no_se_muestra_ni_reactiva(escenario_carrito, estado):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(
            Carrito.__table__.update().where(
                Carrito.__table__.c.id == a["carrito_id"]
            ).values(estado=estado)
        )
    assert client.get("/tenant/carritos").json()["items"] == []
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 409
    with factory() as db:
        assert db.scalar(select(Carrito.estado)) == estado


def test_carrito_con_dos_horas_vencidas_no_se_muta_por_get(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    antes = datetime.now(timezone.utc) - timedelta(hours=3)
    with factory.begin() as db:
        db.execute(
            Carrito.__table__.update().where(
                Carrito.__table__.c.id == a["carrito_id"]
            ).values(fecha_actualizacion=antes)
        )
    assert client.get("/tenant/carritos").json()["items"] == []
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 409
    with factory() as db:
        assert db.scalar(select(Carrito.estado)) == "ACTIVO"
        assert db.scalar(select(DetalleCarrito.cantidad)) == 2


def test_get_no_modifica_carrito_ni_inventario(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory() as db:
        antes = db.execute(
            select(Carrito.estado, Carrito.fecha_actualizacion)
            .where(Carrito.id == a["carrito_id"])
        ).one()
        stock = db.execute(
            select(Inventario.stock_actual, Inventario.stock_reservado)
            .where(Inventario.id == 1111)
        ).one()
    assert client.get("/tenant/carritos").status_code == 200
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 200
    with factory() as db:
        despues = db.execute(
            select(Carrito.estado, Carrito.fecha_actualizacion)
            .where(Carrito.id == a["carrito_id"])
        ).one()
        ahora_stock = db.execute(
            select(Inventario.stock_actual, Inventario.stock_reservado)
            .where(Inventario.id == 1111)
        ).one()
    assert antes == despues
    assert stock == ahora_stock == (10, 2)


def test_paginacion_contador_global_correcto(escenario_carrito):
    client, _, factory = escenario_carrito
    _agregar(client)
    ahora = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.execute(insert(Sucursal.__table__).values(
            id=1102, empresa_id=10, ciudad_id=1,
            nombre="Sucursal Urbana 2", direccion="Ficticia 2", estado=True,
        ))
        db.execute(insert(Carrito.__table__).values(
            id=7778, empresa_id=10, cliente_id=1001, sucursal_id=1102,
            fecha_creacion=ahora, fecha_actualizacion=ahora, estado="ACTIVO",
        ))
    pagina = client.get("/tenant/carritos", params={"limit": 1, "offset": 1})
    assert pagina.status_code == 200, pagina.text
    assert len(pagina.json()["items"]) == 1
    assert pagina.json()["total_carritos_activos"] == 2
    completo = client.get("/tenant/carritos")
    assert completo.json()["total_carritos_activos"] == 2
    assert len(completo.json()["items"]) == 2


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1},
    {"limit": "invalido"}, {"offset": "invalido"},
])
def test_paginacion_invalida_en_get(escenario_carrito, params):
    client, _, factory = escenario_carrito
    assert client.get("/tenant/carritos", params=params).status_code == 422
    assert _total(factory, Carrito.__table__) == 0


@pytest.mark.parametrize("tipo,valor", [
    ("inventario", 2222), ("variante", 222),
    ("temporada", 222), ("categoria", 222),
])
def test_lectura_falla_cerrada_por_relacion_empresarial_ajena(
    escenario_carrito, tipo, valor,
):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        if tipo == "inventario":
            db.execute(
                DetalleCarrito.__table__.update()
                .where(DetalleCarrito.__table__.c.id == a["detalle_id"])
                .values(inventario_id=valor)
            )
        elif tipo == "variante":
            db.execute(
                Inventario.__table__.update()
                .where(Inventario.__table__.c.id == 1111)
                .values(variante_producto_id=valor)
            )
        elif tipo == "temporada":
            db.execute(
                Inventario.__table__.update()
                .where(Inventario.__table__.c.id == 1111)
                .values(temporada_id=valor)
            )
        else:
            db.execute(
                Producto.__table__.update()
                .where(Producto.__table__.c.id == 111)
                .values(categoria_id=valor)
            )
    # SQLite de pruebas NO tiene las FK compuestas: forzar cruce deliberado.
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 409
    assert client.get("/tenant/carritos").status_code == 409


def test_linea_adulterada_otro_tenant_se_rechaza_completa(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(insert(DetalleCarrito.__table__).values(
            empresa_id=20, carrito_id=a["carrito_id"],
            inventario_id=2222, cantidad=1,
        ))
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 409
    assert client.get("/tenant/carritos").status_code == 409


def test_sucursal_del_carrito_cruzada_no_revela_detalles(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(
            Carrito.__table__.update()
            .where(Carrito.__table__.c.id == a["carrito_id"])
            .values(sucursal_id=2202)
        )
    assert client.get(f'/tenant/carritos/{a["carrito_id"]}').status_code == 404
    assert client.get("/tenant/carritos").json() == {
        "items": [], "total_carritos_activos": 0,
    }


def test_no_cliente_en_empresa_no_puede_listar_carrito(escenario_carrito):
    client, identidad, _ = escenario_carrito
    identidad["empresa_id"] = 20
    assert client.get("/tenant/carritos").status_code == 403
    assert client.get("/tenant/carritos/123").status_code == 403


def test_contexto_personal_no_puede_leer_carrito(escenario_carrito):
    client, identidad, _ = escenario_carrito
    identidad["contexto"] = "personal"
    assert client.get("/tenant/carritos").status_code == 403
    assert client.get("/tenant/carritos/123").status_code == 403


def test_sin_token_saas_no_puede_listar_carrito(escenario_carrito):
    client, _, factory = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    assert client.get("/tenant/carritos").status_code == 401
    assert client.get("/tenant/carritos/123").status_code == 401
    assert _total(factory, Carrito.__table__) == 0


def test_flag_apagado_deniega_lectura_nueva(escenario_carrito, monkeypatch):
    client, _, _ = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    assert client.get("/tenant/carritos").status_code == 503
    assert client.get("/tenant/carritos/123").status_code == 503


def test_gate_de_lectura_no_abre_subrutas_arbitrarias(escenario_carrito):
    client, _, _ = escenario_carrito
    for ruta in (
        "/tenant/carritos/items", "/tenant/carritos/01",
        "/tenant/carritos/0", "/tenant/carritos/1/items",
        "/tenant/carritos/1/extra", "/carritos",
    ):
        assert client.get(ruta).status_code == 503, ruta
    assert client.get("/tenant/carritos/99999").status_code == 404


def test_get_no_requiere_producto_actualmente_activo(escenario_carrito):
    # El CU15 original muestra un item historico incluso si se desactiva el producto.
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(Producto.__table__.update().where(
            Producto.__table__.c.id == 111
        ).values(estado=False))
    r = client.get(f'/tenant/carritos/{a["carrito_id"]}')
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["producto_nombre"] == "Camisa A"
    # La escritura PATCH sigue exigiendo que el producto este activo.
    assert client.patch(
        f'/tenant/carritos/{a["carrito_id"]}/items/{a["detalle_id"]}',
        json={"cantidad": 3},
    ).status_code == 404


# B3/P5E: prevalidar CU16 sin crear reserva ni tocar inventario.

def _prevalidar(client, carrito_id, fecha=None, **extra):
    if fecha is None:
        fecha = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    return client.post("/tenant/reservas/prevalidar", json={
        "carrito_id": carrito_id, "fecha_atencion": fecha, **extra,
    })


def test_reserva_prevalidacion_apta_no_crea_reserva_ni_cambia_stock(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _agregar(client, cantidad=3).json()
    r = _prevalidar(client, carrito["carrito_id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["carrito_id"] == carrito["carrito_id"]
    assert body["sucursal_id"] == 1101
    assert body["cantidad_lineas"] == 1
    assert body["cantidad_unidades"] == 3
    assert body["stock_verificado_preliminarmente"] is True
    assert body["reserva_creada"] is False
    assert body["requiere_verificacion_transaccional"] is True
    assert set(body) == {
        "carrito_id", "sucursal_id", "fecha_atencion",
        "cantidad_lineas", "cantidad_unidades",
        "stock_verificado_preliminarmente",
        "reserva_creada", "requiere_verificacion_transaccional",
    }
    assert _total(factory, Reserva.__table__) == 0
    with factory() as db:
        assert db.scalar(select(Carrito.estado)) == "ACTIVO"
        assert db.scalar(select(DetalleCarrito.cantidad)) == 3
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 1111)).one() == (10, 2)


def test_prevalidacion_repetida_no_convierte_carrito(escenario_carrito):
    client, _, factory = escenario_carrito
    carrito = _agregar(client).json()
    for _ in range(3):
        assert _prevalidar(client, carrito["carrito_id"]).status_code == 200
    assert _total(factory, Reserva.__table__) == 0
    with factory() as db:
        assert db.scalar(select(Carrito.estado)) == "ACTIVO"


def test_prevalidacion_distingue_empresas_sin_revelar_carritos(escenario_carrito):
    client, identidad, factory = escenario_carrito
    a = _agregar(client).json()
    identidad["empresa_id"], identidad["usuario_id"] = 20, 88
    b = _agregar(client, sucursal=2202, inventario=2222).json()
    assert _prevalidar(client, a["carrito_id"]).status_code == 404
    assert _prevalidar(client, b["carrito_id"]).status_code == 200
    identidad["empresa_id"], identidad["usuario_id"] = 10, 99
    assert _prevalidar(client, b["carrito_id"]).status_code == 404
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_otra_persona_de_misma_empresa_404(escenario_carrito):
    client, identidad, factory = escenario_carrito
    a = _agregar(client).json()
    with factory.begin() as db:
        db.execute(insert(Cliente.__table__).values(
            id=1002, empresa_id=10, usuario_id=77,
            nombre="Otro", apellido="Usuario", estado=True,
        ))
    identidad["usuario_id"] = 77
    assert _prevalidar(client, a["carrito_id"]).status_code == 404
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_carrito_sin_lineas_409(escenario_carrito):
    client, _, factory = escenario_carrito
    ahora = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.execute(insert(Carrito.__table__).values(
            id=8111, empresa_id=10, cliente_id=1001, sucursal_id=1101,
            fecha_creacion=ahora, fecha_actualizacion=ahora, estado="ACTIVO",
        ))
    r = _prevalidar(client, 8111)
    assert r.status_code == 409
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_reserva_ya_existente_409(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    hoy = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.execute(insert(Reserva.__table__).values(
            id=9111, empresa_id=10, cliente_id=1001,
            sucursal_id=1101, carrito_id=a["carrito_id"],
            fecha_reserva=hoy, fecha_atencion=hoy+timedelta(days=2),
            estado="PENDIENTE",
        ))
    assert _prevalidar(client, a["carrito_id"]).status_code == 409
    assert _total(factory, Reserva.__table__) == 1


@pytest.mark.parametrize("stock,esperado", [
    (2, 200), (9, 409), (8, 200), (0, 200),
])
def test_prevalidacion_stock_actual_y_reservado(
    escenario_carrito, stock, esperado,
):
    client, _, factory = escenario_carrito
    a = _agregar(client, cantidad=2).json()
    with factory.begin() as db:
        db.execute(Inventario.__table__.update().where(
            Inventario.__table__.c.id == 1111
        ).values(stock_reservado=stock))
    r = _prevalidar(client, a["carrito_id"])
    assert r.status_code == esperado, r.text
    assert _total(factory, Reserva.__table__) == 0


@pytest.mark.parametrize("entidad", [
    "sucursal", "variante", "producto", "categoria", "talla", "color",
])
def test_prevalidacion_prenda_o_sucursal_inactiva_409(escenario_carrito, entidad):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    tablas = {
        "sucursal": (Sucursal.__table__, 1101),
        "variante": (VarianteProducto.__table__, 111),
        "producto": (Producto.__table__, 111),
        "categoria": (Categoria.__table__, 111),
        "talla": (Talla.__table__, 1),
        "color": (Color.__table__, 1),
    }
    tabla, id_ = tablas[entidad]
    with factory.begin() as db:
        db.execute(tabla.update().where(tabla.c.id == id_).values(estado=False))
    r = _prevalidar(client, a["carrito_id"])
    assert r.status_code == 409, r.text
    assert _total(factory, Reserva.__table__) == 0


@pytest.mark.parametrize("cambio", [
    ("inventario_id", 2222), ("empresa_id", 20),
])
def test_prevalidacion_linea_inconsistente_409(escenario_carrito, cambio):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    campo, valor = cambio
    with factory.begin() as db:
        db.execute(DetalleCarrito.__table__.update().where(
            DetalleCarrito.__table__.c.id == a["detalle_id"]
        ).values(**{campo: valor}))
    assert _prevalidar(client, a["carrito_id"]).status_code == 409


@pytest.mark.parametrize("offset", [-3, -1, 0])
def test_prevalidacion_fecha_pasada_o_actual_422(escenario_carrito, offset):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    pasado = (datetime.now(timezone.utc) + timedelta(days=offset)).isoformat()
    assert _prevalidar(client, a["carrito_id"], fecha=pasado).status_code == 422
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_fecha_naive_tratada_como_utc(escenario_carrito):
    client, _, _ = escenario_carrito
    a = _agregar(client).json()
    fecha = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None)
    assert _prevalidar(client, a["carrito_id"], fecha=fecha.isoformat()).status_code == 200


@pytest.mark.parametrize("extra", [
    {"empresa_id": 20}, {"cliente_id": 2002},
    {"inventario_id": 2222}, {"cantidad": 50},
])
def test_prevalidacion_no_acepta_identidad_ni_stock_desde_json(escenario_carrito, extra):
    client, _, factory = escenario_carrito
    a = _agregar(client).json()
    assert _prevalidar(client, a["carrito_id"], **extra).status_code == 422
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_cliente_no_activo_403(escenario_carrito):
    client, identidad, factory = escenario_carrito
    identidad["empresa_id"] = 20
    assert _prevalidar(client, 1234).status_code == 403
    assert _total(factory, Reserva.__table__) == 0


def test_prevalidacion_contexto_personal_403(escenario_carrito):
    client, identidad, _ = escenario_carrito
    identidad["contexto"] = "personal"
    assert _prevalidar(client, 1234).status_code == 403


def test_prevalidacion_sin_token_401(escenario_carrito):
    client, _, _ = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    assert _prevalidar(client, 1234).status_code == 401


def test_prevalidacion_con_flag_inactivo_503(escenario_carrito, monkeypatch):
    client, _, _ = escenario_carrito
    app.dependency_overrides.pop(get_current_tenant_context)
    monkeypatch.setattr(settings, "saas_auth_enabled", False)
    assert _prevalidar(client, 1234).status_code == 503


def test_sigue_bloqueada_la_creacion_y_cancelacion_real(escenario_carrito):
    client, _, factory = escenario_carrito
    rutas = [
        ("POST", "/tenant/reservas"),
        ("POST", "/reservas"),
        ("PATCH", "/tenant/reservas/1/cancelar"),
        ("POST", "/tenant/reservas/prevalidar/crear"),
        ("GET", "/tenant/reservas/prevalidar"),
        ("POST", "/tenant/comercial/ventas"),
    ]
    for metodo, ruta in rutas:
        assert client.request(metodo, ruta, json={}).status_code == 503
    assert _total(factory, Reserva.__table__) == 0


# B3/P5H: orden CU15: carrito antes de inventario.
# SQLite ignora FOR UPDATE: estos tests SOLO comprueban el orden SQL emitido.

def _registrar_sql(engine, ejecutar):
    sentencias = []
    def antes(conn, cursor, statement, parameters, context, executemany):
        sentencias.append(" ".join(statement.upper().split()))
    event.listen(engine, "before_cursor_execute", antes)
    try:
        respuesta = ejecutar()
    finally:
        event.remove(engine, "before_cursor_execute", antes)
    return respuesta, sentencias


def _indices_acceso_carrito_y_stock(sentencias):
    carrito = next(
        (i for i, q in enumerate(sentencias)
         if " FROM CARRITO " in " " + q + " "
         or q.startswith("INSERT INTO CARRITO ")), None
    )
    inventario = next(
        (i for i, q in enumerate(sentencias)
         if " FROM INVENTARIO " in " " + q + " "), None
    )
    assert carrito is not None, "No se consulto/creo el carrito"
    assert inventario is not None, "No se comprobo inventario"
    return carrito, inventario


def test_5h_alta_carrito_antes_de_bloquear_inventario(escenario_carrito):
    client, _, factory = escenario_carrito
    res, sentencias = _registrar_sql(
        factory.kw["bind"], lambda: _agregar(client, cantidad=2)
    )
    assert res.status_code == 200, res.text
    carrito, inventario = _indices_acceso_carrito_y_stock(sentencias)
    assert carrito < inventario
    assert any(q.startswith("INSERT INTO CARRITO ") for q in sentencias)
    assert _total(factory, Carrito.__table__) == 1


def test_5h_carrito_existente_se_consulta_antes_del_stock(escenario_carrito):
    client, _, factory = escenario_carrito
    a = _agregar(client, cantidad=2)
    assert a.status_code == 200, a.text
    res, sentencias = _registrar_sql(
        factory.kw["bind"], lambda: _agregar(client, cantidad=3)
    )
    assert res.status_code == 200, res.text
    carrito, inventario = _indices_acceso_carrito_y_stock(sentencias)
    assert carrito < inventario
    assert not any(q.startswith("INSERT INTO CARRITO ") for q in sentencias)
    assert res.json()["cantidad"] == 5
    assert res.json()["carrito_id"] == a.json()["carrito_id"]
    with factory() as db:
        assert db.scalar(select(DetalleCarrito.cantidad)) == 5


def test_5h_inventario_inexistente_revierte_alta_carrito(escenario_carrito):
    client, _, factory = escenario_carrito
    res, sentencias = _registrar_sql(
        factory.kw["bind"],
        lambda: _agregar(client, inventario=99999, cantidad=1),
    )
    assert res.status_code == 404, res.text
    carrito, inventario = _indices_acceso_carrito_y_stock(sentencias)
    assert carrito < inventario
    assert _total(factory, Carrito.__table__) == 0
    assert _total(factory, DetalleCarrito.__table__) == 0


def test_5h_stock_insuficiente_revierte_alta_y_sin_movimiento(
    escenario_carrito,
):
    client, _, factory = escenario_carrito
    res, sentencias = _registrar_sql(
        factory.kw["bind"],
        lambda: _agregar(client, cantidad=9),  # 10 - 2 = 8 disponibles
    )
    assert res.status_code == 409, res.text
    carrito, inventario = _indices_acceso_carrito_y_stock(sentencias)
    assert carrito < inventario
    assert _total(factory, Carrito.__table__) == 0
    assert _total(factory, DetalleCarrito.__table__) == 0
    with factory() as db:
        assert db.execute(select(
            Inventario.stock_actual, Inventario.stock_reservado
        ).where(Inventario.id == 1111)).one() == (10, 2)
