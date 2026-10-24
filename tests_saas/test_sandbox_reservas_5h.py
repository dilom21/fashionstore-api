"""B3/P5H: solo inspeccion del preparador de sandbox LOCAL.

Estos tests NO ejecutan PowerShell, pg_dump, createdb, pg_restore ni SP.
"""
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "database" / "saas" / "13_preparar_sandbox_pg_reservas_local.ps1"


def codigo():
    return SCRIPT.read_text(encoding="utf-8")


def test_5h_sandbox_requiere_doble_opt_in():
    s = codigo()
    assert "[switch]$CrearSandbox" in s
    assert "[switch]$ConfirmoCopiaSinDatos" in s
    assert "if (-not $CrearSandbox)" in s
    assert "if (-not $ConfirmoCopiaSinDatos)" in s


def test_5h_sandbox_defecto_no_accede_a_pg():
    s = codigo()
    assert s.index("if (-not $CrearSandbox)") < s.index("Read-Host")
    assert s.index("if (-not $CrearSandbox)") < s.index("& $pgdump")
    assert "return" in s[s.index("if (-not $CrearSandbox)"):s.index("if (-not $ConfirmoCopiaSinDatos)")]


def test_5h_sandbox_solo_origen_y_destino_fijos_locales():
    s = codigo()
    assert "$servidor = '127.0.0.1'" in s
    assert "$puerto = 5432" in s
    assert "$origen = 'fashionstore_restore_20261023_193554_594'" in s
    assert "$destino = 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "$destino -eq $origen" in s


def test_5h_sandbox_exporta_esquema_no_datos():
    s = codigo()
    assert "--schema-only" in s
    assert "--format=custom" in s
    assert "--file=$dump" in s
    assert "--data-only" not in s
    assert "pg_dump" in s
    assert "pg_restore" in s
    assert "--no-owner --no-acl" in s


def test_5h_sandbox_no_se_sobrescribe_y_es_independiente():
    s = codigo()
    assert "SELECT count(*) FROM pg_database WHERE datname=" in s
    assert "--template=template0" in s
    assert "--exit-on-error" in s
    assert "No sobrescribir" in s
    assert "ORIGEN_LOCAL_VALIDADO" in s


def test_5h_sandbox_lectura_del_origen_verificada():
    s = codigo()
    assert "default_transaction_read_only=on" in s
    assert "host(inet_server_addr())='127.0.0.1'" in s
    assert "(SELECT count(*) FROM public.empresa)=5" in s
    assert "(SELECT count(*) FROM public.reserva)=11" in s
    assert "convalidated)=48" in s


def test_5h_sandbox_no_copia_datos_y_valida_vacio():
    s = codigo()
    assert "(SELECT count(*) FROM public.empresa)" in s
    assert "(SELECT count(*) FROM public.cliente)" in s
    assert "(SELECT count(*) FROM public.reserva)" in s
    assert "(SELECT count(*) FROM public.inventario)" in s
    assert "Trim() -ne '0'" in s
    assert "SANDBOX LOCAL DE ESQUEMA PREPARADO" in s


def test_5h_sandbox_restaura_credenciales_y_no_ejecuta_procedimientos():
    s = codigo()
    assert "Read-Host" in s and "-AsSecureString" in s
    assert "ZeroFreeBSTR" in s
    assert "Remove-Item Env:PGPASSWORD" in s
    assert "Remove-Item Env:PGSSLMODE" in s
    assert "Remove-Item Env:PGOPTIONS" in s
    assert "NO ejecuta los archivos .sql.txt" in s
    assert "CALL public." not in s
