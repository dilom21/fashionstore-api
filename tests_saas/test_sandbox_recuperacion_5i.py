"""5I: regresion ESTATICA tras pg_restore: public schema duplicado.

Estos tests no ejecutan PowerShell, pg_dump, pg_restore ni PostgreSQL.
La recuperacion exige opt-in explicito y verifica fuente/destino al ejecutarse.
"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1] / "database" / "saas"
CREAR = BASE / "13_preparar_sandbox_pg_reservas_local.ps1"
RECUPERAR = BASE / "16_recuperar_sandbox_parcial_local.ps1"
INSPECTOR = BASE / "15_inspeccionar_sandbox_parcial_local.sql"
INSPECTOR_PS = BASE / "15_inspeccionar_sandbox_parcial_local.ps1"
VERIFICADOR = BASE / "14_verificar_sandbox_pg_reservas_local.sql"


def test_5i_creador_corrige_public_existente_sin_drop():
    s = CREAR.read_text(encoding="utf-8")
    assert "pgrestore --list" in s
    assert "SCHEMA\\s+-\\s+public" in s
    assert "$entradaPublic.Count -ne 1" in s
    assert "$restaurar" in s
    assert "--use-list=$toc" in s
    assert not re.search(r"(?im)^\s*[^#\n]*?--clean\b", s)
    assert not re.search(r"(?im)^\s*DROP\s+(?:DATABASE|SCHEMA)", s)


def test_5i_creador_usa_pg_restore_atomico():
    s = CREAR.read_text(encoding="utf-8")
    assert "--single-transaction" in s
    assert "--exit-on-error" in s
    assert "--no-owner --no-acl" in s
    assert s.index("$entradaPublic.Count") < s.index("& $createdb")
    assert s.index("$toc") < s.index("& $createdb")


def test_5i_creador_limpia_lista_y_dump_temporales():
    s = CREAR.read_text(encoding="utf-8")
    assert "if (Test-Path -LiteralPath $toc)" in s
    assert "Remove-Item -LiteralPath $toc" in s
    assert "Remove-Item -LiteralPath $dump" in s
    assert "WriteAllLines" in s
    assert "UTF8Encoding($false)" in s


def test_5i_recuperador_requiere_doble_consentimiento():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert "[switch]$RecuperarSandboxParcial" in s
    assert "[switch]$ConfirmoSandboxVacio" in s
    assert "if (-not $RecuperarSandboxParcial)" in s
    assert "if (-not $ConfirmoSandboxVacio)" in s
    assert s.index("if (-not $RecuperarSandboxParcial)") < s.index("Read-Host")


def test_5i_recuperador_cierra_destino_y_origen():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert "$servidor = '127.0.0.1'" in s
    assert "$origen = 'fashionstore_restore_20261023_193554_594'" in s
    assert "$destino = 'fashionstore_saas_reservas_sandbox_5h'" in s
    assert "ORIGEN_LOCAL_VALIDO" in s
    assert "PARCIAL_PUBLIC_VACIO_AUTORIZADO" in s
    assert "SANDBOX NO VACIO" in s


def test_5i_recuperador_abort_si_hay_objetos_public():
    s = RECUPERAR.read_text(encoding="utf-8")
    for k in ("relaciones=0", "rutinas=0", "tipos=0", "extensiones=0", "public_existe=1"):
        assert k in s
    assert s.index("$estadoDestino =") < s.index("$pgdump --host=")


def test_5i_recuperador_no_destruye_ni_sobrescribe():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\\s*&\\s*\\$pgrestore[^\\n]*--clean(?:\\s|=)", s)
    assert not re.search(r"(?im)^\s*(DROP\s+(?:SCHEMA|DATABASE)|&\s*\$dropdb)\b", s)
    assert "Archivo temporal previo" in s
    assert "--no-owner --no-acl" in s
    assert "CALL public." not in s


def test_5i_recuperador_evade_schema_duplicado_y_datos():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert "TABLE DATA|MATERIALIZED VIEW DATA|BLOBS?" in s
    assert "omitidas.Count -ne 1" in s
    assert "SCHEMA\\s+-\\s+public" in s
    assert "--schema-only --schema=public" in s
    assert "--use-list=$toc" in s


def test_5i_recuperador_todo_o_nada_y_verificacion():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert "--single-transaction" in s
    assert "--exit-on-error" in s
    assert "default_transaction_read_only=on" in s
    assert "--file=$verificador" in s
    assert "SANDBOX NO APTO" in s
    assert s.index("--use-list=$toc") < s.index("--file=$verificador")
    assert s.index("--file=$verificador") < s.index("RECUPERACION SANDBOX LOCAL SUPERADA")


def test_5i_inspeccion_no_escribe():
    sql = INSPECTOR.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION READ ONLY;" in sql
    assert re.search(r"(?m)^ROLLBACK;$", sql)
    assert "current_database() <> 'fashionstore_saas_reservas_sandbox_5h'" in sql
    assert "host(inet_server_addr()) <> '127.0.0.1'" in sql
    assert not re.search(
        r"(?im)^\s*(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|CALL|COMMIT)\s+", sql
    )


def test_5i_inspector_rechaza_si_hay_objetos_public():
    sql = INSPECTOR.read_text(encoding="utf-8")
    for k in ("relaciones=0", "rutinas=0", "tipos=0", "extensiones=0"):
        assert k in sql
    assert "SANDBOX_PARCIAL_VACIO_APTO_PARA_RECUPERACION_CONTROLADA" in sql
    assert "NO_RECUPERAR" in sql
    ps = INSPECTOR_PS.read_text(encoding="utf-8")
    assert "default_transaction_read_only=on" in ps
    assert "15_inspeccionar_sandbox_parcial_local.sql" in ps


def test_5i_no_procedimientos_tenant_ni_rls_habilitado():
    s = RECUPERAR.read_text(encoding="utf-8")
    assert "sp_*_tenant" in s
    assert "NO habilitar SaaS" in s
    assert "No se borraran bases, esquemas ni tablas." in s
    assert "NO ejecuta sp_*_tenant" in s
    v = VERIFICADOR.read_text(encoding="utf-8")
    assert "v_sp<>0" in v
    assert "v_fk<>48" in v
