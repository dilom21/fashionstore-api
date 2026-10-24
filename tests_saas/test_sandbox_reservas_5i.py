"""B3/P5I: contrato ESTATICO del sandbox local seguro.

No ejecuta comandos de shell, no conecta PostgreSQL y NO crea bases.
No certifica comportamiento de pg_restore ni reglas RLS con rol limitado.
"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1] / "database" / "saas"
CREAR = BASE / "13_preparar_sandbox_pg_reservas_local.ps1"
VERIFICAR = BASE / "14_verificar_sandbox_pg_reservas_local.sql"
VERIFICAR_PS = BASE / "14_verificar_sandbox_pg_reservas_local.ps1"


def test_5i_verificador_solo_lectura_y_rollback():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION READ ONLY;" in sql
    assert re.search(r"(?m)^ROLLBACK;$", sql)
    assert "SET LOCAL statement_timeout" in sql
    assert not re.search(
        r"(?im)^\s*(CREATE|CALL|INSERT|UPDATE|DELETE|ALTER|DROP|COMMIT)\s+", sql
    )


def test_5i_falla_si_no_es_sandbox_local_exacto():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in sql
    assert "host(inet_server_addr()) <> '127.0.0.1'" in sql


def test_5i_verifica_todas_las_tablas_public_y_no_solo_cuatro():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "FROM pg_class c JOIN pg_namespace n" in sql
    assert "c.relkind='r'" in sql
    assert "FOR v_rel IN" in sql
    assert "SELECT count(*) FROM %I.%I" in sql
    assert "IF v_conteo <> 0" in sql
    assert "IF v_tablas < 40" in sql


def test_5i_sandbox_tablas_criticas_presentes():
    sql = VERIFICAR.read_text(encoding="utf-8")
    for nombre in [
        "empresa", "usuario", "cliente", "carrito", "detalle_carrito",
        "reserva", "detalle_reserva", "inventario",
        "movimiento_inventario", "venta", "pago",
    ]:
        assert f"to_regclass('public.{nombre}')" in sql


def test_5i_sandbox_fk_compuestas_con_validez():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "co.convalidated" in sql
    assert "co.conname LIKE 'fk_tenant_%'" in sql
    assert "v_fk<>48" in sql


def test_5i_sandbox_rls_seis_tablas_no_se_confunde_con_seguridad():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "c.relrowsecurity" in sql
    assert "v_rls<>6" in sql
    assert "RLS NO ha sido probado con rol limitado." in sql


def test_5i_sandbox_debe_no_tener_sp_tenant_instalados():
    sql = VERIFICAR.read_text(encoding="utf-8")
    assert "v_sp<>0" in sql
    for nombre in [
        "sp_crear_reserva_tenant",
        "sp_cancelar_reserva_tenant",
        "sp_finalizar_reserva_sin_compra_tenant",
    ]:
        assert nombre in sql


def test_5i_preparador_solo_lee_fuente_sin_ejecutar_sp():
    ps = CREAR.read_text(encoding="utf-8")
    assert "default_transaction_read_only=on" in ps
    assert "--schema-only" in ps
    assert "--template=template0" in ps
    assert "--no-owner --no-acl" in ps
    assert "CALL public." not in ps


def test_5i_preparador_verifica_toc_antes_de_crear():
    ps = CREAR.read_text(encoding="utf-8")
    assert "$indice = & $pgrestore --list $dump" in ps
    assert "TABLE DATA|MATERIALIZED VIEW DATA|BLOBS?" in ps
    assert r"^\d+;" in ps  # patron regex efectivo, no doble slash
    assert r"^\\d+;" not in ps
    assert ps.index("$indice = & $pgrestore --list $dump") < ps.index("& $createdb")


def test_5i_verificador_integrado_en_preparador():
    ps = CREAR.read_text(encoding="utf-8")
    assert "14_verificar_sandbox_pg_reservas_local.sql" in ps
    assert "--file=$verificador" in ps
    assert "Sandbox NO APTO" in ps
    assert ps.index("--file=$verificador") < ps.index(
        "SANDBOX LOCAL DE ESQUEMA PREPARADO"
    )


def test_5i_sandbox_mantiene_doble_optin_y_no_sobrescribe():
    ps = CREAR.read_text(encoding="utf-8")
    assert "if (-not $CrearSandbox)" in ps
    assert "if (-not $ConfirmoCopiaSinDatos)" in ps
    assert "SELECT count(*) FROM pg_database WHERE datname=" in ps
    assert "No sobrescribir" in ps
    assert "$destino -eq $origen" in ps


def test_5i_verificador_powershell_solo_local_y_sin_dml():
    ps = VERIFICAR_PS.read_text(encoding="utf-8")
    assert "$servidor = '127.0.0.1'" in ps
    assert "$puerto = 5432" in ps
    assert "$base = 'fashionstore_saas_reservas_sandbox_5h'" in ps
    assert "default_transaction_read_only=on" in ps
    assert "--set=ON_ERROR_STOP=1 --file=$sql" in ps
    assert "SANDBOX LOCAL DE ESQUEMA VERIFICADO" in ps


def test_5i_no_hay_credenciales_ni_sql_borradores_en_scripts():
    ps = CREAR.read_text(encoding="utf-8")
    comprobar = VERIFICAR_PS.read_text(encoding="utf-8")
    for contenido in [ps, comprobar]:
        assert "Read-Host" in contenido
        assert "-AsSecureString" in contenido
        assert "ZeroFreeBSTR" in contenido
        assert "Remove-Item Env:PGPASSWORD" in contenido
        assert not re.search(r"(?im)^\s*(CALL|CREATE PROCEDURE|DROP DATABASE)\s+", contenido)
