"""Contrato estático del paquete CU11; no abre conexiones ni ejecuta SQL."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "database/saas/preparacion_pg17/reservas"
INSTALL = (DIR / "080_instalar_reservas_saas_PROPUESTA_NO_EJECUTAR.sql").read_text("utf-8")
VERIFY = (DIR / "081_verificar_reservas_saas.sql").read_text("utf-8")
REVERT = (DIR / "082_revertir_reservas_saas_CONTROLADO.sql").read_text("utf-8")


def test_080_conserva_guarda_y_transaccion_unica():
    assert "NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA" in INSTALL
    assert INSTALL.count("BEGIN;") == 1
    assert INSTALL.count("COMMIT;") == 1


def test_firmas_coinciden_con_fastapi():
    assert "sp_crear_reserva_tenant(bigint,bigint,bigint,timestamptz,text,bigint)" in INSTALL
    assert "sp_cancelar_reserva_tenant(bigint,bigint,bigint,text)" in INSTALL
    assert "sp_finalizar_reserva_sin_compra_tenant(bigint,bigint,bigint,text)" in INSTALL


def test_security_definer_restringido_y_sin_public_execute():
    assert INSTALL.count("SECURITY DEFINER") == 4
    assert INSTALL.count("SET search_path TO pg_catalog, pg_temp") == 3
    assert "SET search_path=pg_catalog,pg_temp" in INSTALL
    assert INSTALL.count("FROM PUBLIC;") >= 4
    assert "TO fashionstore_api;" in INSTALL


def test_acl_se_concede_antes_de_transferir_ownership():
    grant = INSTALL.index("GRANT EXECUTE ON PROCEDURE public.sp_crear_reserva_tenant")
    owner = INSTALL.index("ALTER PROCEDURE public.sp_crear_reserva_tenant")
    assert grant < owner
    grant_actor = INSTALL.index("GRANT EXECUTE ON FUNCTION public.fs_verificar_actor_reserva")
    owner_actor = INSTALL.index("ALTER FUNCTION public.fs_verificar_actor_reserva")
    assert grant_actor < owner_actor


def test_no_concede_dml_ni_bypassrls():
    assert "GRANT INSERT" not in INSTALL
    assert "GRANT UPDATE" not in INSTALL
    assert "GRANT DELETE" not in INSTALL
    assert "BYPASSRLS" not in INSTALL


def test_locks_en_orden_y_sin_commit_interno():
    assert "ORDER BY dc.inventario_id" in INSTALL
    assert INSTALL.count("ORDER BY dr.inventario_id") == 2
    assert INSTALL.count("FOR UPDATE OF i") == 3
    assert INSTALL.count("COMMIT;") == 1


def test_postflight_revisa_owners_acl_y_membresia_temporal():
    for fragment in (
        "fashionstore_sp_owner", "fashionstore_context_owner",
        "aclexplode", "m.set_option", "grantor.rolname=current_user",
    ):
        assert fragment in VERIFY
    assert "p_sp_tenant_movimiento_inventario" in VERIFY
    assert "CU11_POSTFLIGHT=:cu11_estado" in VERIFY
    assert "RAISE EXCEPTION 'CU11_POSTFLIGHT=FAIL'" in VERIFY


def test_reversion_cambia_rol_y_no_toca_datos_ni_tablas():
    assert "SET ROLE fashionstore_sp_owner" in REVERT
    assert "SET ROLE fashionstore_context_owner" in REVERT
    assert REVERT.count("RESET ROLE") == 2
    assert "DROP TABLE" not in REVERT
    assert "DELETE FROM" not in REVERT
    assert "UPDATE public." not in REVERT
    assert "body_md5=" in REVERT
    assert "secuencias CU11 avanzaron" in REVERT
    assert "'movimiento_inventario'" in REVERT and "'p_sp_tenant_'||t.tabla" in REVERT


def test_preflight_exige_policies_y_marcador_de_instalacion():
    assert "Policies RLS CU11 ausentes o incompatibles" in INSTALL
    assert "p_sp_tenant_movimiento_inventario" in INSTALL
    assert "fashionstore.cu11.080.v1" in INSTALL
    assert "reserva_seq=" in INSTALL
    assert "movimiento_seq=" in INSTALL

