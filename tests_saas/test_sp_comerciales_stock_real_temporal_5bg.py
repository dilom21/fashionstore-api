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
    assert 'c.execute("SET ROLE' not in S
    assert 'text("SET ROLE' not in S


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
    assert "initdb,pgctl=postgres_bin()" in S
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



def test_5bg_acl_reales_public_execute_revocado_y_owner_nologin():
    assert "aclexplode(p.proacl)" in S
    assert "acl.grantee=0 AND acl.privilege_type='EXECUTE'" in S
    assert "AS publico_revocado" in S
    assert "r.rolcanlogin,r.rolsuper,r.rolbypassrls" in S
    assert "meta[3] is True" in S
    assert "meta[4] is False" in S
    assert "meta[5] is False" in S
    assert "meta[6] is False" in S
    assert 'has_schema_privilege(%s,\'public\',\'CREATE\')' in S
    assert "ACL PUBLIC revocada y search_path seguro" in S


def test_5bg_b_fixture_nueve_parametros_iguales_a_columnas():
    # Evitar fallo 5BE: comprobar INSERT parametrizado con aridad correcta.
    import re
    sql=re.search(r'INSERT INTO public\.reserva\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)',
                  S,re.S)
    assert sql is not None
    assert len(sql.group(1).split(","))==9
    assert sql.group(2).count("%s")==9
    assert '(300,2,201,2,99,ts,ts+timedelta(days=2),"CONFIRMADA","B ficticia")' in S



def test_5bg_crea_tablas_por_separado_y_valida_catalogo():
    assert 'for ddl in MINIMAL_SCHEMA.split(";"):' in S
    assert "if ddl.strip():" in S
    assert "admin.execute(ddl)" in S
    assert "c.relkind='r'" in S
    assert "==27" in S
    assert "25 tablas parciales + 2 de 5BC" in S



def test_5bg_overload_bigint_esperado_no_convierte_a_integer():
    # 5BC usa fs_5bc_verificar(integer,integer), pero los borradores
    # CU16–CU18 reciben bigint. PostgreSQL rechazó su mezcla con 42883.
    assert "def instalar_verificador_bigint(c):" in S
    assert "p_empresa_id bigint,p_usuario_id bigint" in S
    assert "public.fs_5bc_verificar(bigint,bigint)" in S
    assert "m.empresa_id::bigint=p_empresa_id" in S
    assert "m.usuario_id::bigint=p_usuario_id" in S
    assert "p_empresa_id::integer" not in S
    assert "p_usuario_id::integer" not in S
    assert "instalar_verificador_bigint(admin)" in S
    assert S.index("instalar_verificador_bigint(admin)") < S.index(
        "instalar_sp_originales(admin,fuentes)"
    )


def test_5bg_bigint_titular_no_login_y_public_exec_revocado():
    assert "REVOKE ALL ON FUNCTION {} FROM PUBLIC" in S
    assert 'sql.Identifier(VERIFIER)' in S
    assert "GRANT EXECUTE ON FUNCTION {} TO {}" in S
    assert "aclexplode(p.proacl)" in S
    assert "meta==(True,VERIFIER,2,True,True,False,False,False)" in S
    assert '"5BG VERIFICADOR BIGINT PASS' in S
    assert "verificador conservó CREATE" in S


def test_5bg_bigint_real_se_valida_desde_los_tres_logins():
    assert "role_check(c,role,company)" in S
    assert "persona={ROLES[0]:101,ROLES[1]:102,ROLES[2]:201}[role]" in S
    assert 'SELECT public.fs_5bc_verificar(%s::bigint,%s::bigint)' in S
    assert "verificador bigint no reconoce al principal propio" in S
    assert "verificador bigint permitió otro usuario del mismo tenant" in S
    assert 'getattr(e.diag,"message_primary",None)' in S


def test_5bg_cargador_sql_real_inyecta_guardia_una_vez_sin_pg():
    # Ejecutar la transformación real sobre los tres borradores originales
    # SIN importar app ni abrir conexiones; prueba funcional offline.
    tree=ast.parse(S)
    fn=next(n for n in tree.body
            if isinstance(n,ast.FunctionDef)
            and n.name=="cargar_sql_real")
    module=ast.Module(body=[fn],type_ignores=[])
    def check(ok,msg):
        if not ok:
            raise RuntimeError(msg)
    sigs={
      "sp_crear_reserva_tenant":("12_sp_crear_reserva_tenant.sql.txt",
        "bigint,bigint,bigint,timestamptz,text,bigint"),
      "sp_cancelar_reserva_tenant":("13_sp_cancelar_reserva_tenant.sql.txt",
        "bigint,bigint,bigint,text"),
      "sp_finalizar_reserva_sin_compra_tenant":(
        "14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
        "bigint,bigint,bigint,text"),
    }
    env={"HERE":DIR.parent,"PROCEDURES":sigs,"require":check,"re":re}
    exec(compile(module,str(P),"exec"),env)
    sources=env["cargar_sql_real"]()
    assert set(sources)==set(ORIG)
    for name,source in sources.items():
        original=(DIR/ORIG[name]).read_text(encoding="utf-8")
        assert source!=original
        assert source.count("fs_5bc_verificar(p_empresa_id,p_usuario_id)")==1
        assert source.index("fs_5bc_verificar(p_empresa_id,p_usuario_id)") < (
            source.index("IF p_empresa_id IS NULL")
        )
        assert source.count("CREATE OR REPLACE PROCEDURE")==1
