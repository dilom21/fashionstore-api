"""5AD: pruebas estaticas, no conectan PostgreSQL ni generan informes."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"database"/"saas"
SQL=(BASE/"36_inventario_esquema_para_casos_ab_5ad_SOLO_LECTURA.sql").read_text(encoding="utf-8")
PS=(BASE/"37_generar_inventario_lab_5ad_SOLO_LECTURA.ps1").read_text(encoding="utf-8")

def test_5ad_sql_guarda_lab_identidad():
    assert "fashionstore_rls_lab_5u" in SQL
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in SQL
    assert "inet_server_port() <> 55432" in SQL
    assert "data_directory" in SQL
    assert "session_user <> 'postgres'" in SQL

def test_5ad_sql_readonly_y_rollback():
    assert "BEGIN TRANSACTION READ ONLY" in SQL
    assert "ROLLBACK;" in SQL
    for forbidden in ("CREATE ROLE ","INSERT INTO public.","DROP TABLE ","ALTER TABLE ","GRANT ALL"):
        assert forbidden not in SQL

def test_5ad_estructura_y_consistencia():
    for name in ("information_schema.columns","pg_get_constraintdef","pg_trigger",
                 "46","48","35","'empresa-lab-a'","'empresa-lab-b'"):
        assert name in SQL
    assert "password_hash" not in SQL

def test_5ad_solo_conteos_no_valores():
    assert "COUNT(*)::bigint AS total" in SQL
    assert "FROM public.carrito" in SQL
    assert "FROM public.inventario" in SQL
    assert "FROM public.reserva" in SQL
    assert "SELECT * FROM public.usuario" not in SQL

def test_5ad_wrapper_solo_lab_sin_password_en_archivo():
    assert "-p 55432" in PS
    assert "-p 5432" not in PS
    assert "PGPASSWORD',$null" in PS
    assert "default_transaction_read_only=on" in PS
    assert "fashionstore_5ad_estructura_lab_" in PS
    assert "-f $sql -o $file" in PS

def test_5ad_no_certifica_14_pruebas_ficticias():
    assert "para completar 14 casos A/B" in PS
    assert "no se instalaron nuevas policies ni datos ficticios" in PS
