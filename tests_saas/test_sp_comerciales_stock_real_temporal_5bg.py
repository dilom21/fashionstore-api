"""5BG: pruebas offline de fuentes SQL comerciales y guardrails de LAB."""
import ast
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"database/saas/66_sp_comerciales_saas_stock_real_temporal_5bg.py"
S=P.read_text(encoding="utf-8")
DIR=ROOT/"database/saas/borradores"
ORIG={
 "sp_crear_reserva_tenant":"12_sp_crear_reserva_tenant.sql.txt",
 "sp_cancelar_reserva_tenant":"13_sp_cancelar_reserva_tenant.sql.txt",
 "sp_finalizar_reserva_sin_compra_tenant":
   "14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
}


def test_5bg_compila_python():
    ast.parse(S)


def test_5bg_usa_tres_fuentes_reales_sin_copiar_cuerpo():
    assert set(ORIG)==set(re.findall(r'^    "(sp_\w+)":\(',S,re.M))
    for proc,file in ORIG.items():
        raw=(DIR/file).read_text(encoding="utf-8")
        assert raw.count(f"CREATE OR REPLACE PROCEDURE public.{proc}(")==1
        assert "SECURITY INVOKER" in raw
        assert "REVOKE ALL ON PROCEDURE public."+proc in raw
        assert file in S
    assert 'path=HERE/"borradores"/file' in S
    assert "out[proc]=edited" in S


def test_5bg_contexto_session_user_guarda_antes_de_negocio():
    assert 'r"(?m)^BEGIN\\s*$"' in S
    assert "public.fs_5bc_verificar(p_empresa_id,p_usuario_id)" in S
    assert 'USING ERRCODE=\'42501\'' in S
    assert 'edited.index("fs_5bc_verificar(") <' in S
    assert 'edited.index("IF p_empresa_id IS NULL")' in S


def test_5bg_nunca_usa_base_comercial_ni_lab_55432():
    assert "NUNCA toca el PostgreSQL" in S
    assert 'HOST,PORT=BD["HOST"],BD["PORT"]' in S
    assert 'require(port_free(),"55433 ocupado' in S
    assert "validate_cluster(admin,data)" in S
    assert 'tempfile.mkdtemp(prefix="fashionstore-5bg-real-sp-")' in S
    assert "fashionstore_restore_" not in S
    assert "DATABASE_URL" not in S
    assert 'NUNCA toca el PostgreSQL' in S


def test_5bg_ddl_ficticio_diferenciado_del_schema_real():
    assert "MINIMAL_SCHEMA=r" in S
    for table in ("empresa","usuario","usuario_empresa","plan_saas",
                  "suscripcion","cliente","empleado","sucursal","carrito",
                  "detalle_carrito","inventario","reserva","detalle_reserva",
                  "movimiento_inventario","venta","rol_funcion"):
        assert f"CREATE TABLE public.{table}(" in S
    assert "REPLICA PARCIAL DE PRUEBA" in S
    assert "48 FK ni triggers" in S


def test_5bg_owner_sp_nologin_path_y_public_revocado():
    assert "VERIFIER=BD[" in S
    assert "WRITER=BD[" in S
    assert "ALTER PROCEDURE {} OWNER TO {}" in S
    assert "ALTER PROCEDURE {} SECURITY DEFINER" in S
    assert "SET search_path=pg_catalog, pg_temp" in S
    assert "REVOKE ALL ON PROCEDURE public." in S
    assert "OWNER TO {}" in S
    assert "5BG SP REALES PASS" in S


def test_5bg_rls_derive_empresa_del_principal_sql_no_guc():
    assert "principal=session_user::name" in S
    assert "public.fs_5bg_empresa_actor()" in S
    assert "ENABLE ROW LEVEL SECURITY" in S
    assert "WITH CHECK (empresa_id=public.fs_5bg_empresa_actor())" in S
    assert "fs_5bg_empresa_actor() FROM PUBLIC" in S
    assert "grant_all" not in S.lower()
    assert "SET ROLE" not in S


def test_5bg_grants_minimos_y_denegacion_de_dml_directo():
    assert "GRANT SELECT ON public.{} TO {}" in S
    assert "GRANT UPDATE ({}) ON public.{} TO {}" in S
    assert "GRANT INSERT ON public.{} TO {}" in S
    assert "has_any_column_privilege(%s,%s,'INSERT')" in S
    assert "has_any_column_privilege(%s,%s,'UPDATE')" in S
    assert "5BG BRECHA: actor SQL tiene DML directo" in S


def test_5bg_datos_solo_ficticios_a1_a2_b1():
    assert '"empresa-lab-a"' in S
    assert '"empresa-lab-b"' in S
    assert "(1,101,1,True)" in S
    assert "(1,102,1,True)" in S
    assert "(2,201,2,True)" in S
    assert "c.execute(command,args)" in S
    assert "psycopg3.Connection no soporta ejecutemany" in S
    assert "5BG FIXTURES PASS" in S


def test_5bg_carrera_dos_logins_stock_unico():
    assert "ThreadPoolExecutor(max_workers=2)" in S
    assert "threading.Barrier(2)" in S
    assert "barrier.wait(timeout=12)" in S
    assert "with connect(role,passw[role]) as c:" in S
    assert "sp_crear_reserva_tenant(" in S
    assert 'sorted(x[2] for x in result)==["OK","SIN_STOCK"]' in S
    assert "WHERE id=1" in S
    assert "5BG CU16 CARRERA PASS" in S


def test_5bg_transaccion_fallida_no_crea_movimientos():
    assert 'one(admin,"SELECT count(*) FROM public.movimiento_inventario "' in S
    assert "WHERE empresa_id=1 AND tipo='RESERVA'" in S
    assert "CU16 no creó movimiento exactamente una vez" in S
    assert "CU16 insertó reserva incorrecta o duplicada" in S
    assert 'e.sqlstate in ("23514","23505")' in S


def test_5bg_cu17_ajeno_intraempresa_y_cross_tenant_negados():
    assert "sp_cancelar_reserva_tenant" in S
    assert '"P0002","42501"' in S
    assert "CU17 intraempresa debió denegar por dueño" in S
    assert 'e.sqlstate=="42501"' in S
    assert "5BG BRECHA: B1 canceló A1/A2" in S
    assert "5BG CU17 PASS" in S


def test_5bg_cu18_administrador_liberacion_unica():
    assert '"GESTIONAR_RESERVAS"' in S
    assert '"EJECUTAR"' in S
    assert '"CONFIRMADA","B ficticia"' in S
    assert "sp_finalizar_reserva_sin_compra_tenant" in S
    assert "CU18 doble finalización no bloqueada" in S
    assert 'final==[(1,"CANCELADA"),(2,"ATENDIDA")]' in S
    assert "5BG CU18 PASS" in S


def test_5bg_baseline_y_limpieza_del_cluster_solo_pg18():
    assert "pg_ctl" in S
    assert "initdb" in S
    assert '"--auth-host=scram-sha-256"' in S
    assert '"--auth-local=scram-sha-256"' in S
    assert '"-m","fast","stop"' in S
    assert 'if started or pid.exists():' in S
    assert "if not pid.exists() and port_free()" in S
    assert "shutil.rmtree(folder)" in S
    assert "5BG CLEANUP PASS" in S
    assert "5BG TEMP PASS" in S
    assert "5BG LIMPIEZA PENDIENTE" in S


def test_5bg_no_confunde_schema_ficticio_con_produccion():
    assert "DDL parcial sin 47 tablas/48 FK/triggers" in S
    assert "no es instalación productiva" in S
    assert "SaaS sigue OFF" in S
    assert "NO se importa respaldo comercial" in S
