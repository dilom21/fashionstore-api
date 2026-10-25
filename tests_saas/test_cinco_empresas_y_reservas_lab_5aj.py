"""5AJ/5AK: pruebas offline de 5 empresas, dos laboratorios y arnes SQL.
NO conectan PostgreSQL; NO simulan resultado real de 3 casos pendientes.
"""
import ast
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"database"/"saas"
AGREEMENT=(BASE/"43_contrato_cinco_empresas_y_laboratorio_5aj.md").read_text(encoding="utf-8")
SQL=(BASE/"44_instalar_ensayo_reserva_sql_5aj_LAB.sql").read_text(encoding="utf-8")
RACE=(BASE/"45_ejecutar_pruebas_concurrentes_5ak_LAB.py").read_text(encoding="utf-8")


def test_5_empresas_fijas_planes_y_sucursales():
    for slug,plan,cantidad in (
        ("vanter-men","PROFESIONAL",4),
        ("urbanwear","BASICO",1),
        ("elegancia-sur","PROFESIONAL",3),
        ("denim-house","BASICO",2),
        ("sportline","PROFESIONAL",3),
    ):
        assert re.search(rf"\`{slug}\`\s*\|\s*\`{plan}\`\s*\|\s*{cantidad}",AGREEMENT)
    assert "**13**" in AGREEMENT and "**5 tenants**" in AGREEMENT


def test_no_recrear_4_tenants_ya_creados():
    assert "NO RECREAR" in AGREEMENT
    assert "05_crear_cuatro_tenants_demo_local.ps1" in AGREEMENT
    assert "fashionstore_restore_20261023_193554_594" in AGREEMENT


def test_lab_ab_no_reemplaza_cinco_tenants():
    assert "empresa-lab-a" in AGREEMENT and "empresa-lab-b" in AGREEMENT
    assert "No sustituyen" in AGREEMENT
    assert "SAAS_AUTH_ENABLED=false" in AGREEMENT


def test_sql_instalacion_exclusivamente_55432():
    assert "fashionstore_rls_lab_5u" in SQL
    assert "inet_server_port()<>55432" in SQL
    assert "host(inet_server_addr()) IS DISTINCT FROM '127.0.0.1'" in SQL
    assert "data_directory" in SQL
    assert "session_user<>'postgres'" in SQL
    assert "current_setting('server_version_num')::int/10000<>18" in SQL


def test_sql_transaccion_atomica_con_guardas_conteos():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "NO IDEMPOTENTE" in SQL
    for item in ("<> 46","<> 48","<> 35","<> 2"):
        assert item in SQL
    assert re.search(r"<>\\s*41", SQL)


def test_sql_reserva_seguridad_invoker_verdadera():
    assert "CREATE PROCEDURE public.sp_lab_reservar_5aj(" in SQL
    assert "SECURITY INVOKER" in SQL
    assert "REVOKE ALL ON PROCEDURE public.sp_lab_reservar_5aj(" in SQL
    assert "GRANT EXECUTE ON PROCEDURE public.sp_lab_reservar_5aj(" in SQL
    assert "CASE session_user" in SQL
    assert "p_actor_id IS DISTINCT FROM v_actor" in SQL
    assert "row_security_active('public.reserva'::regclass)" in SQL
    assert "row_security_active('public.inventario'::regclass)" in SQL
    assert "app.usuario_id" not in SQL


def test_sql_rls_dml_y_movimientos():
    for name in (
        "fs_lab_5aj_reserva_insert",
        "fs_lab_5aj_detalle_reserva_insert",
        "fs_lab_5aj_movimiento_insert",
        "fs_lab_5aj_bitacora_insert",
        "fs_lab_5aj_inventario_update",
        "fs_lab_5aj_carrito_update",
    ):
        assert "CREATE POLICY " + name in SQL
    assert "FOR UPDATE" in SQL
    assert "WITH CHECK" in SQL
    assert "stock_reservado=stock_reservado+v_det.cantidad" in SQL
    assert "INSERT INTO public.movimiento_inventario" in SQL


def test_sql_no_vuelve_a_tocar_base_original():
    for forbidden in (
        "fashionstore_restore_", "pg_dump", "DROP DATABASE",
        "DROP ROLE", "TRUNCATE", "BYPASSRLS", "supabase db push",
        "INSERT INTO public.empresa", "ALTER ROLE",
    ):
        assert forbidden not in SQL


def test_python_runner_sintaxis_y_laboratorio():
    ast.parse(RACE)
    assert 'HOST, PORT, DB = "127.0.0.1", 55432, "fashionstore_rls_lab_5u"' in RACE
    assert "default_transaction_read_only=on" in RACE
    assert "CALL public.sp_lab_reservar_5aj(" in RACE
    assert "ThreadPoolExecutor(max_workers=2)" in RACE
    assert "Barrier(2)" in RACE


def test_runner_8_11_13_con_evidencia_real_esperada():
    for case in ("A08: PASS_PRINCIPAL_SQL","A11: PASS_ROLLBACK","A13: PASS_LOCKS"):
        assert case in RACE
    assert "expected_exception(" in RACE
    assert 'b.execute("SELECT set_config(' in RACE
    assert 'expected_exception(' in RACE and '"42501"' in RACE and '"23514"' in RACE
    assert "len(success)==1 and len(rejected)==1" in RACE
    assert "count(*) FROM public.reserva" in RACE
    assert "count(*) FROM public.movimiento_inventario" in RACE


def test_no_confundir_laboratorio_con_despliegue_comercial():
    assert "NO ES el flujo de reservas de la aplicacion final" in RACE
    assert "FALTA: SP CU16 definitivo" in RACE
    assert "SAAS AUTH false" in RACE
    assert "NO ES la de produccion" in SQL
