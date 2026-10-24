"""B3/P5O: pruebas de constructores INERTES; NO ejecutan CALL ni PostgreSQL."""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import MappingProxyType

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest

from app.core.tenant_context import TenantContext
from app.modules.reservas.services.tenant_sp_calls_5o import (
    preparar_creacion_reserva,
    preparar_cancelacion_reserva,
    preparar_finalizacion_sin_compra,
)


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "app/modules/reservas/services/tenant_sp_calls_5o.py"
DRAFTS = [
    ROOT / "database/saas/borradores/12_sp_crear_reserva_tenant.sql.txt",
    ROOT / "database/saas/borradores/13_sp_cancelar_reserva_tenant.sql.txt",
    ROOT / "database/saas/borradores/14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
]


def ficticio(*, usuario=42, empresa=15, contexto="cliente", rol="CLIENTE"):
    return TenantContext(
        usuario_id=usuario, empresa_id=empresa, empresa_slug="empresa-demo",
        empresa_nombre="Empresa Demo", rol_id=3, rol_nombre=rol,
        contexto=contexto, plan_codigo="BASICO", permite_reportes_ia=False,
    )


def cita():
    return datetime(2030, 1, 1, 14, 30, tzinfo=timezone(timedelta(hours=-4)))


def test_5o_creacion_toma_actor_tenant_de_contexto():
    call = preparar_creacion_reserva(tenant=ficticio(), carrito_id=17, fecha_atencion=cita())
    assert call.parametros["empresa_id"] == 15
    assert call.parametros["usuario_id"] == 42
    assert call.parametros["carrito_id"] == 17
    assert call.parametros["reserva_id"] is None
    assert call.parametros["fecha_atencion"] == cita()


def test_5o_creacion_sql_parametrizado_y_nombre_exactos():
    call = preparar_creacion_reserva(tenant=ficticio(), carrito_id=1, fecha_atencion=cita())
    assert call.sentencia.text == (
        "CALL public.sp_crear_reserva_tenant("
        ":empresa_id, :carrito_id, :usuario_id, :fecha_atencion, "
        ":observacion, :reserva_id)"
    )
    assert set(call.parametros) == {
        "empresa_id", "carrito_id", "usuario_id", "fecha_atencion",
        "observacion", "reserva_id",
    }


def test_5o_creacion_rechaza_personal():
    with pytest.raises(PermissionError):
        preparar_creacion_reserva(
            tenant=ficticio(contexto="personal", rol="CAJERO"),
            carrito_id=2, fecha_atencion=cita(),
        )


def test_5o_creacion_rechaza_fecha_sin_zona_horaria():
    with pytest.raises(ValueError):
        preparar_creacion_reserva(
            tenant=ficticio(), carrito_id=2, fecha_atencion=datetime(2030, 1, 1)
        )


def test_5o_creacion_rechaza_ids_carrito_invalidos():
    for carrito in (0, -1, True, "123"):
        with pytest.raises(ValueError):
            preparar_creacion_reserva(tenant=ficticio(), carrito_id=carrito, fecha_atencion=cita())


def test_5o_cancelacion_cliente_toma_usuario_y_empresa_del_contexto():
    call = preparar_cancelacion_reserva(tenant=ficticio(usuario=23, empresa=78), reserva_id=91)
    assert dict(call.parametros) == {
        "empresa_id": 78, "reserva_id": 91,
        "usuario_id": 23, "observacion": None,
    }
    assert "CALL public.sp_cancelar_reserva_tenant(" in call.sentencia.text


def test_5o_cancelacion_personal_administrador():
    call = preparar_cancelacion_reserva(
        tenant=ficticio(contexto="personal", rol="ADMINISTRADOR"),
        reserva_id=31, observacion="Prueba académica",
    )
    assert call.parametros["observacion"] == "Prueba académica"


def test_5o_cancelacion_rechaza_personal_no_autorizado():
    for rol in ("CAJERO", "VISITANTE"):
        with pytest.raises(PermissionError):
            preparar_cancelacion_reserva(
                tenant=ficticio(contexto="personal", rol=rol), reserva_id=31,
            )


def test_5o_finalizacion_personal_cajero():
    call = preparar_finalizacion_sin_compra(
        tenant=ficticio(contexto="personal", rol="CAJERO"),
        reserva_id=52,
    )
    assert call.parametros["empresa_id"] == 15
    assert call.parametros["usuario_id"] == 42
    assert call.parametros["reserva_id"] == 52
    assert "CALL public.sp_finalizar_reserva_sin_compra_tenant(" in call.sentencia.text


def test_5o_finalizacion_rechaza_cliente():
    with pytest.raises(PermissionError):
        preparar_finalizacion_sin_compra(tenant=ficticio(), reserva_id=52)


def test_5o_constructor_rechaza_contexto_no_tenant():
    with pytest.raises(TypeError):
        preparar_cancelacion_reserva(tenant={"empresa_id": 1, "usuario_id": 2}, reserva_id=3)


def test_5o_constructor_rechaza_identidad_invalida():
    for empresa, usuario in ((0, 12), (15, False), (-1, 1)):
        with pytest.raises(ValueError):
            preparar_cancelacion_reserva(
                tenant=ficticio(usuario=usuario, empresa=empresa), reserva_id=33,
            )


def test_5o_parametros_inmutables_para_evitar_sustitucion_tardia():
    call = preparar_cancelacion_reserva(tenant=ficticio(), reserva_id=45)
    assert isinstance(call.parametros, MappingProxyType)
    with pytest.raises(TypeError):
        call.parametros["usuario_id"] = 99
    assert call.parametros["usuario_id"] == 42


def test_5o_rechaza_observaciones_que_no_sean_texto():
    with pytest.raises(TypeError):
        preparar_cancelacion_reserva(tenant=ficticio(), reserva_id=45, observacion=5)


def test_5o_sql_no_ejecuta_ni_crea_privilegios():
    src = BUILDER.read_text(encoding="utf-8")
    assert "Session.execute(" not in src
    assert "db.execute(" not in src
    assert "create_engine(" not in src
    assert "CREATE ROLE" not in src and "CREATE POLICY" not in src
    assert "set_config(" not in src


def test_5o_coincide_con_firmas_borradores_inertes():
    a, b, c = [p.read_text(encoding="utf-8") for p in DRAFTS]
    for s in (a, b, c):
        assert "SECURITY INVOKER" in s
        assert "IN p_empresa_id bigint" in s
        assert "IN p_usuario_id bigint" in s
    assert "IN p_carrito_id bigint" in a
    assert "IN p_fecha_atencion timestamptz" in a
    assert "INOUT p_reserva_id bigint" in a
    assert "IN p_reserva_id bigint" in b and "IN p_reserva_id bigint" in c
