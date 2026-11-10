"""Contrato estático del paquete SQL CU12/CU13. No abre conexiones."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "database/saas/preparacion_pg17/cu12_cu13"
INSTALL = (DIR / "090_instalar_cu12_cu13_saas_PROPUESTA_NO_EJECUTAR.sql").read_text(
    "utf-8"
)
VERIFY = (DIR / "091_verificar_cu12_cu13_saas.sql").read_text("utf-8")
REVERT = (DIR / "092_revertir_cu12_cu13_CONTROLADO.sql").read_text("utf-8")


def test_090_conserva_guarda_y_transaccion_unica():
    assert "NO EJECUTAR EN SUPABASE SIN AUTORIZACION EXPRESA" in INSTALL
    assert INSTALL.count("BEGIN;") == 1
    assert INSTALL.count("COMMIT;") == 1


def test_firmas_coinciden_con_fastapi():
    assert "sp_confirmar_reserva_tenant(" in INSTALL
    assert "bigint,bigint,bigint)" in INSTALL
    assert "sp_crear_venta_presencial_tenant(" in INSTALL
    assert "bigint,bigint,bigint,bigint,jsonb,bigint" in INSTALL
    assert "sp_registrar_pago_presencial_tenant(" in INSTALL
    assert "bigint,bigint,bigint,text,bigint" in INSTALL


def test_preflight_no_sobrescribe_objetos_ni_policies_cu12_cu13():
    assert "actor_sucursal_id()') IS NOT NULL" in INSTALL
    assert "Policies CU12/CU13 ya existen" in INSTALL
    assert "Objetos CU12/CU13 ya existen" in INSTALL
    assert "p_api_select_staff_cliente" in INSTALL
    assert "p_sp_tenant_detalle_venta" in INSTALL


def test_reutiliza_verificador_cu11_y_no_llama_sp_legacy():
    assert INSTALL.count("public.fs_verificar_actor_reserva(") >= 3
    assert "sp_confirmar_venta(" not in INSTALL
    assert "sp_cancelar_reserva(" not in INSTALL


def test_security_definer_owner_nologin_y_sin_public():
    assert INSTALL.count("SECURITY DEFINER") == 4
    assert "SET search_path TO pg_catalog, pg_temp" in INSTALL
    assert "ALTER PROCEDURE public.sp_confirmar_reserva_tenant" in INSTALL
    assert "OWNER TO fashionstore_sp_owner" in INSTALL
    assert "ALTER FUNCTION fashionstore_private.actor_sucursal_id()" in INSTALL
    assert "OWNER TO fashionstore_context_owner" in INSTALL
    assert INSTALL.count("FROM PUBLIC;") >= 4


def test_ownership_concede_y_revoca_create_temporal():
    assert "GRANT CREATE ON SCHEMA fashionstore_private TO fashionstore_context_owner" in INSTALL
    assert "REVOKE CREATE ON SCHEMA fashionstore_private" in INSTALL
    assert "GRANT CREATE ON SCHEMA public TO fashionstore_sp_owner" in INSTALL
    assert "REVOKE CREATE ON SCHEMA public FROM fashionstore_sp_owner" in INSTALL
    assert "cu12_cu13_creates_temporales" in INSTALL


def test_membresias_administrativas_se_fotografian_y_verifican():
    assert "cu12_cu13_membresias_previas" in INSTALL
    assert "cu12_cu13_set_temporal" in INSTALL
    assert "pg_temp.cu12_cu13_preparar_set" in INSTALL
    assert "pg_temp.cu12_cu13_restaurar_set" in INSTALL
    assert "admin_previo" in INSTALL
    assert "inherit_previo" in INSTALL
    assert "set_previo" in INSTALL
    assert "Membresia preexistente alterada" in INSTALL
    assert "Membresia temporal no revocada" in INSTALL
    assert "FROM supabase_admin" not in INSTALL
    assert "TO supabase_admin" not in INSTALL


def test_set_temporal_restaura_con_grant_y_no_revoca_otro_otorgante():
    assert "GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET TRUE" in INSTALL
    assert "GRANT %I TO CURRENT_USER WITH %s INHERIT %s, SET %s" in INSTALL
    assert "REVOKE %I FROM CURRENT_USER GRANTED BY CURRENT_USER" in INSTALL
    assert "v.existente" in INSTALL
    # El unico REVOKE de membresia es para una fila creada por CURRENT_USER
    # en esta transaccion; nunca para supabase_admin.
    assert "GRANTED BY supabase_admin" not in INSTALL
    assert "FROM CURRENT_USER GRANTED BY CURRENT_USER" in INSTALL


def test_administrador_sin_empleado_conserva_alcance_empresarial():
    assert INSTALL.count(
        "IF v_rol<>'ADMINISTRADOR' AND v_empleado_id IS NULL"
    ) == 2
    assert "IF v_rol<>'ADMINISTRADOR' AND NOT EXISTS (" in INSTALL
    assert "em.sucursal_id=v_reserva.sucursal_id" in INSTALL


def test_no_concede_dml_a_la_api_ni_bypassrls():
    assert "GRANT INSERT,UPDATE ON public.venta TO fashionstore_sp_owner" in INSTALL
    assert "GRANT INSERT,UPDATE ON public.venta TO fashionstore_api" not in INSTALL
    assert "GRANT INSERT ON public.pago TO fashionstore_api" not in INSTALL
    assert "BYPASSRLS" not in INSTALL
    assert "GRANT ALL" not in INSTALL


def test_filtra_empresa_y_sucursal_en_todas_las_escrituras():
    assert INSTALL.count("empresa_id=p_empresa_id") >= 8
    assert "v_sucursal_id<>v_sucursal_empleado" in INSTALL
    assert "empresa_id=v_venta.empresa_id" not in INSTALL  # IDs globales, no join dudoso
    assert "FOR UPDATE OF ve" in INSTALL
    assert "FOR UPDATE OF re" in INSTALL
    assert "FOR UPDATE OF i" in INSTALL
    assert "stock_actual>=v_detalle.cantidad" in INSTALL


def test_pago_y_venta_son_operaciones_separadas():
    assert "estado='PENDIENTE'" in INSTALL
    assert "INSERT INTO public.pago" in INSTALL
    assert "estado='COMPLETADA'" in INSTALL
    assert "estado='ATENDIDA'" in INSTALL
    assert "COMMIT;" not in INSTALL[
        INSTALL.index("sp_crear_venta_presencial_tenant(") :
        INSTALL.index("sp_registrar_pago_presencial_tenant(")
    ]


def test_postflight_revisa_firmas_acl_y_policies():
    for fragmento in (
        "CU12_CU13_POSTFLIGHT=OK",
        "fashionstore_sp_owner",
        "p_api_select_staff_cliente",
        "p_api_select_staff_reserva",
        "p_api_select_staff_venta",
        "p_sp_tenant_detalle_venta",
        "p_sp_tenant_pago",
        "p_sp_tenant_venta",
        "aclexplode",
        "CREATE residual",
        "membresia admin ausente",
        "SET temporal residual",
        "CU11 perdido",
        "RLS/FORCE incompleto",
    ):
        assert fragmento in VERIFY
    assert "fashionstore.cu12_cu13.090.v2" in VERIFY


def test_reversion_no_toca_datos_ni_cu11():
    assert "DROP TABLE" not in REVERT
    assert "DELETE FROM" not in REVERT
    assert "UPDATE public." not in REVERT
    assert "sp_cancelar_reserva_tenant" not in REVERT
    assert "sp_finalizar_reserva_sin_compra_tenant" not in REVERT
    assert "SET ROLE fashionstore_sp_owner" in REVERT
    assert "SET ROLE fashionstore_context_owner" in REVERT
    assert REVERT.count("RESET ROLE") == 2
