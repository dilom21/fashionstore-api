"""P5F: contrato ESTATICO de revision, sin conectarse a PostgreSQL.

NO valida la seguridad de los SP reales. Solo garantiza que el utilitario
de extraccion no modifica ni llama funciones y que reservas SaaS continuan
cerradas. La revision del cuerpo SQL exige el fichero LOCAL del estudiante.
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
SQL = BASE / "database" / "saas" / "11_extraer_definiciones_reserva_solo_lectura.sql"
PS1 = BASE / "database" / "saas" / "11_extraer_definiciones_reserva_solo_lectura.ps1"
GUIA = BASE / "database" / "saas" / "20_b3_p5f_diseno_sp_reserva_tenant_y_revision.md"


def test_sql_extrae_en_transaccion_read_only_con_rollback():
    contenido = SQL.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION READ ONLY;" in contenido
    assert re.search(r"(?m)^ROLLBACK;$", contenido)
    assert not re.search(r"(?im)^\s*(CALL|INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|COMMIT)\s+", contenido)


def test_sql_solo_observa_pg_get_functiondef():
    contenido = SQL.read_text(encoding="utf-8")
    assert "pg_get_functiondef(p.oid)" in contenido
    assert "pg_proc" in contenido
    assert "pg_namespace" in contenido
    assert "prosecdef" in contenido
    assert "proconfig" in contenido
    assert "aclexplode" in contenido


@pytest.mark.parametrize("nombre", [
    "sp_crear_reserva_desde_carrito",
    "sp_cancelar_reserva",
    "sp_finalizar_reserva_sin_compra",
])
def test_extraccion_limita_los_tres_sp_originales(nombre):
    contenido = SQL.read_text(encoding="utf-8")
    assert contenido.count("'" + nombre + "'") >= 2


def test_sql_resguarda_base_y_48_fk_y_5_tenants():
    contenido = SQL.read_text(encoding="utf-8")
    assert "fashionstore_restore_20261023_193554_594" in contenido
    assert "host(inet_server_addr()) <> '127.0.0.1'" in contenido
    assert "v_fks <> 48" in contenido
    assert "(SELECT count(*) FROM public.empresa) <> 5" in contenido
    assert "v_procs <> 3" in contenido


def test_ps1_es_restricto_a_local_y_no_copia_fuera_de_temp():
    contenido = PS1.read_text(encoding="utf-8")
    assert "$hostLocal = '127.0.0.1'" in contenido
    assert "$puerto = 5432" in contenido
    assert "$bd = 'fashionstore_restore_20261023_193554_594'" in contenido
    assert "$env:TEMP" in contenido
    assert "Join-Path $env:TEMP" in contenido
    assert "Out-File" in contenido
    assert not re.search(r"(?i)\bgit\s+(add|push|commit)", contenido)


def test_ps1_fuerza_read_only_y_no_persiste_clave():
    contenido = PS1.read_text(encoding="utf-8")
    assert "default_transaction_read_only=on" in contenido
    assert "--no-password" in contenido
    assert "Read-Host 'Contrasena PostgreSQL LOCAL" in contenido
    assert "ZeroFreeBSTR" in contenido
    assert "Remove-Item Env:PGPASSWORD" in contenido
    assert "Remove-Item Env:PGOPTIONS" in contenido
    assert "EXTRACCION LOCAL SUPERADA" in contenido


def test_ps1_falla_cerrado_si_definicion_incompleta():
    contenido = PS1.read_text(encoding="utf-8")
    for nombre in [
        "sp_crear_reserva_desde_carrito",
        "sp_cancelar_reserva",
        "sp_finalizar_reserva_sin_compra",
    ]:
        assert nombre in contenido
    assert "ROLLBACK" in contenido
    assert "Extraccion incompleta" in contenido
    assert "Remove-Item -LiteralPath $salida" in contenido


@pytest.mark.parametrize("metodo,ruta", [
    ("POST", "/tenant/reservas"),
    ("POST", "/reservas"),
    ("PATCH", "/tenant/reservas/1/cancelar"),
    ("POST", "/tenant/reservas/1/finalizar"),
    ("POST", "/tenant/reservas/crear"),
    ("GET", "/tenant/reservas/1"),
    ("DELETE", "/tenant/reservas/1"),
])
def test_operaciones_reales_reserva_permanecen_bloqueadas(metodo, ruta):
    assert _ruta_saas_permitida(metodo, ruta) is False


def test_prevalidacion_de_lectura_previa_sigue_permitida():
    assert _ruta_saas_permitida("POST", "/tenant/reservas/prevalidar") is True
    assert _ruta_saas_permitida("GET", "/tenant/carritos") is True


def test_guia_5f_no_considera_seguro_el_sp_legacy():
    contenido = GUIA.read_text(encoding="utf-8")
    assert "NO están autorizados para SaaS" in contenido
    assert "SECURITY INVOKER" in contenido
    assert "BYPASSRLS" in contenido
    assert "no ejecutar" in contenido.lower()
