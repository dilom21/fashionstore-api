"""5AB: contrato estático de instalador SQL en clúster PostgreSQL desechable.

No conecta ni ejecuta operaciones SQL, no crea roles ni credenciales.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "database/saas/32_instalar_principales_policies_rls_5ab.sql").read_text(encoding="utf-8")
PS = (ROOT / "database/saas/33_aplicar_rls_principales_lab_5ab.ps1").read_text(encoding="utf-8")


def test_5ab_db_y_puerto_restringidos():
    assert "fashionstore_rls_lab_5u" in SQL and "55432" in SQL
    assert "current_database()" in SQL and "inet_server_port()" in SQL
    assert "data_directory" in SQL and "C:/SI2_Parcial1/fashionstore-rls-lab-5v-data" in SQL
    assert "c:/si2_parcial1/fashionstore-rls-lab-5v-data" in PS


def test_5ab_usa_dos_login_independientes():
    assert "CREATE ROLE fashionstore_5ab_a" in SQL
    assert "CREATE ROLE fashionstore_5ab_b" in SQL
    assert SQL.count("NOBYPASSRLS PASSWORD NULL") == 2
    assert SQL.count("NOCREATEROLE") == 2
    assert "NOINHERIT NOSUPERUSER" in SQL


def test_5ab_datos_solo_ficticios():
    assert "'empresa-lab-a'" in SQL and "'empresa-lab-b'" in SQL
    assert "INSERT INTO public.empresa" in SQL
    assert "Empresa de Prueba A (ficticia)" in SQL
    assert "INSERT INTO public.usuario" not in SQL
    assert "COPY public" not in SQL


def test_5ab_roles_no_heredan_permisos_peligrosos():
    assert "GRANT USAGE ON SCHEMA public" in SQL
    assert "GRANT SELECT ON public.empresa, public.carrito, public.inventario" in SQL
    assert "GRANT SELECT ON public.usuario" not in SQL
    assert "GRANT ALL" not in SQL
    assert "GRANT EXECUTE" not in SQL


def test_5ab_policies_sin_guc_como_identidad():
    assert "CASE session_user" in SQL
    assert "session_user='fashionstore_5ab_a'" in SQL
    assert "session_user='fashionstore_5ab_b'" in SQL
    assert "current_setting('app.empresa_id')" not in SQL
    assert "SECURITY DEFINER" not in SQL


def test_5ab_aplicacion_controlada_a_tablas_tenant():
    assert "a.attname='empresa_id'" in SQL
    assert "CREATE POLICY fs_lab_tenant_select" in SQL
    assert "FOR SELECT TO fashionstore_5ab_a, fashionstore_5ab_b" in SQL
    assert "WHERE schemaname='public'" in SQL
    assert "35" in SQL


def test_5ab_transaccion_y_sin_reintento_ciego():
    assert "BEGIN;" in SQL and "COMMIT;" in SQL
    assert "roles ya presentes. No repetir instalacion" in SQL
    assert "ya existen policies" in SQL
    assert "No repetir" in PS or "NO repetir" in PS


def test_5ab_passwords_interactivos():
    assert r"'\password fashionstore_5ab_a'" in PS
    assert r"'\password fashionstore_5ab_b'" in PS
    assert "Read-Host" in PS and "-AsSecureString" in PS
    assert "ZeroFreeBSTR" in PS
    assert "PASSWORD 'texto" not in SQL


def test_5ab_verifica_dos_sesiones_sql_reales():
    assert "Check-Role $roleA 'empresa-lab-a' $idB" in PS
    assert "Check-Role $roleB 'empresa-lab-b' $idA" in PS
    assert "SET app.empresa_id = '$otherId'" in PS
    assert "-U $name" in PS
    assert "row_security_active" in PS


def test_5ab_deniega_hashs_y_sp_legacy():
    assert "password_hash" in PS
    assert "has_column_privilege" in PS
    assert "has_function_privilege" in PS
    assert "sp_crear_reserva_desde_carrito" in PS


def test_5ab_no_declara_catorce_casos_reales():
    assert "NO CERTIFICA TODAVIA A01-A14" in PS
    assert "RLS SELECT EFECTIVO" in PS
    assert "INSERT/UPDATE comerciales continuan DENEGADAS" in SQL


def test_5ab_no_perturba_otro_cluster_o_supabase():
    for banned in ("5432 -U postgres", "supabase db", "DROP DATABASE",
                   "DROP ROLE", "rm -rf", "initdb", "pg_ctl", "pg_dump"):
        assert banned not in PS
    assert "fashionstore_restore_20261023_193554_594" not in SQL
