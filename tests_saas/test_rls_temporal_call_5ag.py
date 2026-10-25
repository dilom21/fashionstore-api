"""5AG: pruebas estaticas del runner con grants/policies temporales.
No conecta ni modifica PostgreSQL.
"""
from pathlib import Path

LAB=(Path(__file__).resolve().parents[1] /
     "database/saas/41_pruebas_rls_temporales_llamadas_5ag.py")
SRC=LAB.read_text(encoding="utf-8")


def test_5ag_usa_solo_clúster_independiente():
    assert 'HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"' in SRC
    assert 'c:/si2_parcial1/fashionstore-rls-lab-5v-data' in SRC
    assert '"servidor lab incorrecto"' in SRC
    assert "port=5432" not in SRC and "PORT = 5432" not in SRC


def test_5ag_preflight_valida_baseline_y_roles():
    assert "!= " not in SRC or "rolbypassrls" in SRC
    for fragment in ("== 46","== 48","== 35","== 2",
                     "NOT rolsuper AND NOT rolbypassrls",
                     "fixture"):
        assert fragment in SRC


def test_5ag_policies_solo_temporales():
    assert "CREATE POLICY fs_lab_5ag_reserva_insert" in SRC
    assert "CREATE POLICY fs_lab_5ag_inventario_update" in SRC
    assert "CREATE POLICY fs_lab_5ag_bitacora_insert" in SRC
    assert "WITH CHECK" in SRC and "USING" in SRC


def test_5ag_no_da_privilegios_amplios():
    assert "GRANT INSERT (empresa_id,cliente_id,sucursal_id,fecha_atencion)" in SRC
    assert "GRANT UPDATE (stock_reservado)" in SRC
    assert "GRANT ALL" not in SRC
    assert "BYPASSRLS" not in SRC


def test_5ag_limpieza_simetrica():
    for name in ("fs_lab_5ag_reserva_insert",
                 "fs_lab_5ag_inventario_update",
                 "fs_lab_5ag_bitacora_insert"):
        assert "CREATE POLICY "+name in SRC
        assert "DROP POLICY "+name in SRC
    assert "REVOKE INSERT (empresa_id,cliente_id,sucursal_id,fecha_atencion)" in SRC
    assert "REVOKE UPDATE (stock_reservado)" in SRC
    assert "REVOKE INSERT ON public.bitacora" in SRC


def test_5ag_rollback_pruebas_dml():
    assert 'conn.execute("BEGIN")' in SRC
    assert 'conn.execute("ROLLBACK")' in SRC
    assert "SQLSTATE" in SRC
    assert 'code=="42501"' in SRC


def test_5ag_4_call_legacy_y_no_sp_tenant():
    assert 'NAMES = (' in SRC
    for name in ("sp_cancelar_reserva","sp_crear_reserva",
                 "sp_crear_reserva_desde_carrito",
                 "sp_finalizar_reserva_sin_compra"):
        assert name in SRC
    assert 'f"CALL public.{name}(' in SRC
    assert "CREATE PROCEDURE" not in SRC


def test_5ag_password_no_versionado_y_pendientes():
    assert 'getpass(' in SRC
    assert "A08 llamada autenticada" in SRC
    assert "A11 rollback de SP" in SRC
    assert "A13 concurrencia de SP" in SRC
    assert "DELETE FROM public" not in SRC
