"""5AA: verificaciones estáticas del script; NO conecta ni escribe en PostgreSQL."""
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] /
          "database/saas/31_importar_esquema_vacio_lab_5aa.ps1")
text = SCRIPT.read_text(encoding="utf-8")


def test_5aa_solo_puertos_locales_y_bases_fijas():
    assert "127.0.0.1" in text
    assert "Get-Scalar 5432 $sourceDb" in text
    assert "Get-Scalar 55432 'postgres'" in text
    assert "fashionstore_rls_lab_5u" in text
    assert "fashionstore_saas_reservas_sandbox_5h" in text


def test_5aa_origen_en_modo_readonly():
    assert "default_transaction_read_only=on" in text
    assert "[Environment]::SetEnvironmentVariable('PGOPTIONS', $null" in text


def test_5aa_exportacion_sin_filas():
    assert "--schema-only" in text
    assert "--schema=public" in text
    assert "--no-owner --no-acl" in text
    assert "TABLE DATA" in text
    assert "--data-only" not in text


def test_5aa_independencia_preflight():
    assert "system_identifier" in text
    assert "$dst[0] -eq $src[0]" in text
    assert "(Canon $dst[1]) -ne (Canon $targetData)" in text


def test_5aa_no_sobrescribe_db_existente():
    assert "if ($dst[4] -ne '0')" in text
    assert "YA existe; no sobrescribir" in text
    assert "DROP DATABASE" not in text


def test_5aa_restauracion_atomica_y_estructural():
    assert "--single-transaction --exit-on-error" in text
    assert "--use-list=$list" in text
    assert "46|48" in text


def test_5aa_cierre_de_acl_public_solo_en_lab():
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in text
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC" in text
    assert "REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA public FROM PUBLIC" in text


def test_5aa_secretos_y_limpieza_solo_temporal():
    assert "Read-Host" in text and "-AsSecureString" in text
    assert "ZeroFreeBSTR" in text
    assert "Remove-Item -LiteralPath $work" in text
    assert "DROP ROLE" not in text
