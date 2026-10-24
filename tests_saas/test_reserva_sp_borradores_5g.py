"""B3/P5G: revision ESTATICA de borradores PL/pgSQL, NO SQL ejecutado.

Los 40 casos NO sustituyen validacion dinamica de triggers, permisos, RLS,
concurrencia o transacciones en un PostgreSQL aislado. Los archivos .sql.txt
NO son migraciones y NO deben ejecutarse en PostgreSQL restaurado ni Supabase.
"""
import os
import re
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./saas_auth_unit_test.db"
os.environ["SECRET_KEY"] = "clave-ficticia-exclusiva-para-test-saas"
os.environ["SAAS_AUTH_ENABLED"] = "false"

import pytest
from app.core.saas_route_gate import _ruta_saas_permitida

BASE = Path(__file__).resolve().parents[1]
BORRADORES = BASE / "database" / "saas" / "borradores"
NOMBRES = {
    "crear": ("12_sp_crear_reserva_tenant.sql.txt", "sp_crear_reserva_tenant"),
    "cancelar": ("13_sp_cancelar_reserva_tenant.sql.txt", "sp_cancelar_reserva_tenant"),
    "finalizar": (
        "14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
        "sp_finalizar_reserva_sin_compra_tenant",
    ),
}
TIPOS = tuple(NOMBRES)
SQL_PRECHECK = BASE / "database" / "saas" / "12_preflight_borradores_reservas_local.sql"
PS_PRECHECK = BASE / "database" / "saas" / "12_preflight_borradores_reservas_local.ps1"
GUIA = BASE / "database" / "saas" / "22_b3_p5g_borradores_sp_tenant.md"


def codigo(tipo: str) -> str:
    return (BORRADORES / NOMBRES[tipo][0]).read_text(encoding="utf-8")


@pytest.mark.parametrize("tipo", TIPOS)
def test_archivo_y_firma_nueva_sin_sobrescribir_legacy(tipo):
    archivo, nombre = NOMBRES[tipo]
    s = codigo(tipo)
    assert archivo.endswith(".sql.txt")
    assert ("CREATE OR REPLACE PROCEDURE public." + nombre + "(") in s
    assert not re.search(
        r"CREATE OR REPLACE PROCEDURE public.sp_(?:crear_reserva_desde_carrito"
        r"|cancelar_reserva\(|finalizar_reserva_sin_compra\()", s
    )


@pytest.mark.parametrize("tipo", TIPOS)
def test_borrador_marcado_no_ejecutar_y_fuera_de_migrations(tipo):
    s = codigo(tipo)
    assert "NO EJECUTAR" in s
    assert "supabase/migrations" not in str((BORRADORES / NOMBRES[tipo][0]).parent)
    assert s.count("CREATE OR REPLACE PROCEDURE") == 1


@pytest.mark.parametrize("tipo", TIPOS)
def test_security_invoker_y_search_path(tipo):
    s = codigo(tipo)
    assert "SECURITY INVOKER" in s
    assert "SET search_path TO public, pg_temp" in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_revoke_public_explicito(tipo):
    s = codigo(tipo)
    assert re.search(
        r"(?m)^REVOKE ALL ON PROCEDURE public\."
        + re.escape(NOMBRES[tipo][1])
        + r"\([^;]+\) FROM PUBLIC;",
        s,
    )
    assert not re.search(r"(?im)^\s*GRANT\b", s)


@pytest.mark.parametrize("tipo", TIPOS)
def test_membresia_suscripcion_rol_y_usuario_activos(tipo):
    s = codigo(tipo)
    for tabla in ("usuario_empresa", "usuario", "empresa", "rol", "suscripcion", "plan_saas"):
        assert "public." + tabla in s
    for expresion in (
        "ue.empresa_id=p_empresa_id", "ue.usuario_id=p_usuario_id",
        "ue.estado IS TRUE", "u.estado IS TRUE",
        "ss.estado='ACTIVA'", "pl.permite_reservas IS TRUE",
    ):
        assert expresion in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_dml_de_empresa_especifica_y_sin_inventarios_ajenos(tipo):
    s = codigo(tipo)
    assert "p_empresa_id bigint" in s
    assert "i.empresa_id=p_empresa_id" in s
    assert "empresa_id" in s
    assert "p_usuario_id bigint" in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_bloqueos_de_inventario_y_recurso_raiz(tipo):
    s = codigo(tipo)
    assert s.count("FOR UPDATE") >= 2
    assert "ORDER BY" in s and "inventario_id" in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_kardex_con_empresa_y_actor(tipo):
    s = codigo(tipo)
    assert "INSERT INTO public.movimiento_inventario" in s
    assert re.search(
        r"INSERT INTO public\.movimiento_inventario\s*\(\s*empresa_id", s
    )
    assert "p_empresa_id" in s and "p_usuario_id" in s
    assert "'RESERVA'" in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_stock_actual_nunca_se_modifica(tipo):
    s = codigo(tipo)
    assert "stock_reservado" in s
    assert not re.search(r"(?i)\bSET\s+stock_actual\s*=", s)
    assert "UPDATE public.inventario" in s


@pytest.mark.parametrize("tipo", TIPOS)
def test_no_tiene_control_interno_de_transaccion(tipo):
    s = codigo(tipo)
    assert not re.search(r"(?im)^\s*(COMMIT|ROLLBACK|BEGIN\s+TRANSACTION)\b", s)


def test_crear_valida_carrito_cliente_vigencia_y_cantidad():
    s = codigo("crear")
    assert "cl.usuario_id=p_usuario_id" in s
    assert "ca.empresa_id=p_empresa_id" in s
    assert "ca.estado='ACTIVO'" in s
    assert "interval '2 hours'" in s
    assert "v_total<>v_validas" in s
    assert "v_detalle.cantidad > v_inventario.stock_actual-v_inventario.stock_reservado" in s
    for tabla in ("reserva", "detalle_reserva"):
        assert "INSERT INTO public." + tabla in s
    assert "estado='CONVERTIDO'" in s


def test_cancelar_valida_propietario_personal_stock_y_venta():
    s = codigo("cancelar")
    assert "v_rol='CLIENTE'" in s
    assert "cl.usuario_id=p_usuario_id" in s
    assert "'ENCARGADO_SUCURSAL'" in s
    assert "f.nombre='GESTIONAR_RESERVAS'" in s
    assert "a.nombre='EJECUTAR'" in s
    assert "re.empresa_id=p_empresa_id" in s
    assert "ve.reserva_id=p_reserva_id" in s
    assert "'PENDIENTE','CONFIRMADA'" in s
    assert "estado='CANCELADA'" in s


def test_finalizar_limita_roles_sucursal_venta_y_estado():
    s = codigo("finalizar")
    assert "'ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO'" in s
    assert "em.sucursal_id=v_reserva.sucursal_id" in s
    assert "f.nombre='GESTIONAR_RESERVAS'" in s
    assert "a.nombre='EJECUTAR'" in s
    assert "ve.reserva_id=p_reserva_id" in s
    assert "v_reserva.estado<>'CONFIRMADA'" in s
    assert "estado='ATENDIDA'" in s


@pytest.mark.parametrize("metodo,ruta", [
    ("POST", "/tenant/reservas"),
    ("PATCH", "/tenant/reservas/1/cancelar"),
    ("POST", "/tenant/reservas/1/finalizar"),
    ("POST", "/tenant/reservas/crear"),
    ("POST", "/atencion-reservas/1/finalizar-sin-compra"),
])
def test_gate_reservas_reales_permanecen_bloqueadas(metodo, ruta):
    assert _ruta_saas_permitida(metodo, ruta) is False


def test_preflight_pg_5g_es_lectura_y_rollback():
    sql = SQL_PRECHECK.read_text(encoding="utf-8")
    ps = PS_PRECHECK.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION READ ONLY;" in sql
    assert re.search(r"(?m)^ROLLBACK;$", sql)
    assert "fashionstore_restore_20261023_193554_594" in sql
    assert "host(inet_server_addr()) <> '127.0.0.1'" in sql
    assert "v_fk<>48" in sql
    assert not re.search(r"(?im)^\s*(CALL|CREATE|ALTER|INSERT|UPDATE|DELETE|COMMIT)\s+", sql)
    assert "default_transaction_read_only=on" in ps
    assert "'127.0.0.1'" in ps
    assert "12_preflight_borradores_reservas_local.sql" in ps


def test_documento_delimita_borradores_e_integracion_pendiente():
    guia = GUIA.read_text(encoding="utf-8")
    assert "NO EJECUTAR" in guia
    assert "SECURITY INVOKER" in guia
    assert "BYPASSRLS" in guia
    assert "PostgreSQL aislado" in guia
    assert "320 passed" in guia
