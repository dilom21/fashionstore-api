"""5AC: revision estatica de verificador de roles A/B; NO conecta PostgreSQL."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "database/saas"
VERIFY = (P / "35_verificar_rls_existente_5ac_SOLO_LECTURA.ps1").read_text(encoding="utf-8")
OLD = (P / "33_aplicar_rls_principales_lab_5ab.ps1").read_text(encoding="utf-8")


def test_5ac_corrige_booleanos_textuales_sql():
    assert '$expected = "$name|true|$slug|false|0|true"' in OLD
    assert '$expected = "$role|$role|$db|127.0.0.1|55432|18|true|$ownSlug|0|false|0|true|true"' in VERIFY


def test_5ac_solo_lectura_sql_sin_reinstalacion():
    assert "default_transaction_read_only=on" in VERIFY
    assert "-f $script" not in VERIFY
    for statement in ("CREATE ROLE ", "ALTER TABLE ", "CREATE POLICY ",
                      "INSERT INTO ", "DELETE FROM ", "DROP DATABASE ",
                      "pg_restore", "pg_dump"):
        assert statement not in VERIFY


def test_5ac_entorno_unicamente_lab():
    assert "$db = 'fashionstore_rls_lab_5u'" in VERIFY
    assert "$data = 'c:/si2_parcial1/fashionstore-rls-lab-5v-data'" in VERIFY
    assert "host(inet_server_addr())" in VERIFY
    assert "-p 55432" in VERIFY
    assert "-p 5432" not in VERIFY


def test_5ac_autenticacion_real_diferente_por_empresa():
    assert "-U $role -d $db" in VERIFY
    assert "Check-Role $roleA 'empresa-lab-a' 'empresa-lab-b' $idB" in VERIFY
    assert "Check-Role $roleB 'empresa-lab-b' 'empresa-lab-a' $idA" in VERIFY
    assert "SET app.empresa_id = '$otherId'" in VERIFY


def test_5ac_identidad_de_cluster_y_roles():
    assert "35|2|2" in VERIFY
    assert "rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolinherit" in VERIFY
    assert "data_directory" in VERIFY
    assert "current_database()" in VERIFY


def test_5ac_verifica_hash_sp_y_ausencia_escritura():
    assert "has_column_privilege" in VERIFY
    assert "has_function_privilege" in VERIFY
    assert "has_table_privilege" in VERIFY
    assert "row_security_active('public.empresa'::regclass)" in VERIFY
    assert "public.bitacora'::regclass" in VERIFY


def test_5ac_credenciales_interactivas_sin_registro():
    assert "Read-Host" in VERIFY and "-AsSecureString" in VERIFY
    assert "ZeroFreeBSTR" in VERIFY
    assert "PGPASSWORD" in VERIFY
    assert "'-W'" not in VERIFY
    assert " -W -v " in VERIFY


def test_5ac_no_confunde_a_b_con_pruebas_completas():
    assert "5AC PENDIENTE: A01-A14" in VERIFY
    assert "5AC OK: verificaciones sin DDL/DML" in VERIFY
