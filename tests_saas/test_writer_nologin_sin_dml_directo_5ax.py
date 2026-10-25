"""5AX: controles estáticos, no conectan a PostgreSQL ni leen .env."""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/58_sp_owner_nologin_sin_dml_directo_rollback_5ax.py")
S=P.read_text(encoding="utf-8")


def test_5ax_sintaxis_python():
    ast.parse(S)


def test_5ax_solo_pg18_lab_con_preflight_y_rollback():
    assert "57_restringir_reserva_rls_actor_rollback_5aw.py" in S
    assert 'HOST,PORT,DB=X["HOST"],X["PORT"],X["DB"]' in S
    assert "PREFLIGHT(c)" in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'c.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "0 COMMIT" in S
    assert "no activa flags" in S.lower() or "flags SaaS OFF" in S


def test_5ax_no_login_no_bypass_y_sin_create():
    assert 'WRITER="fashionstore_5ax_sp_writer"' in S
    assert "NOLOGIN NOINHERIT" in S
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in S
    assert "NOBYPASSRLS" in S
    assert "GRANT USAGE ON SCHEMA public" in S
    assert "GRANT CREATE ON SCHEMA public" in S
    assert "REVOKE CREATE ON SCHEMA public" in S
    assert "has_schema_privilege(%s,'public','CREATE')" in S
    assert "has_database_privilege(%s,current_database(),'CREATE')" in S
    assert "pg_has_role(%s::name,%s::name,'MEMBER')" in S


def test_5ax_rls_para_writer_no_hace_bypass():
    assert "copiar_rls_a_writer(c)" in S
    assert "len(src)==54" in S
    assert "fs_lab_5at_" in S and "fs_lab_5aw_" in S
    assert "AS {permissive} FOR {cmd} TO {WRITER}" in S
    assert "==161" in S
    assert "session_user" in S
    assert "NOT" in S and "BYPASSRLS" in S


def test_5ax_sps_definer_con_guardia_y_owner_limitado():
    assert len([x for x in ("sp_crear_reserva_tenant",
                           "sp_cancelar_reserva_tenant",
                           "sp_finalizar_reserva_sin_compra_tenant")
                if x in S]) == 3
    assert "ALTER PROCEDURE public.{proc}({signature})" in S
    assert "SECURITY DEFINER" in S
    assert "OWNER TO {WRITER}" in S
    assert "SET search_path = pg_catalog, pg_temp" in S
    assert "p.prosecdef" in S
    assert "p.proconfig @>" in S
    assert "public.fs_verificar_actor_reserva(bigint,bigint)" in S
    assert "GRANT EXECUTE ON FUNCTION" in S
    assert 'M["usar_unico_verificador_en_tres_sp"](c)' in S


def test_5ax_revoca_dml_tablas_y_columnas():
    assert 'TABLES=("reserva","detalle_reserva","movimiento_inventario","bitacora"' in S
    assert '"inventario","carrito","usuario_empresa","suscripcion")' in S
    assert "REVOKE INSERT,UPDATE,DELETE ON public.{table}" in S
    assert "REVOKE INSERT ({cols}), UPDATE ({cols})" in S
    assert "has_column_privilege(%s,a.attrelid,a.attnum,'INSERT')" in S
    assert "has_column_privilege(%s,a.attrelid,a.attnum,'UPDATE')" in S
    assert "count==0" in S
    assert "GRANT USAGE ON SEQUENCE" in S


def test_5ax_sql_directo_debe_ser_rechazado_con_42501():
    assert 'def auditar_dml_sin_privilegios' in S
    assert "SAVEPOINT sp_5ax_dml" in S
    assert "ROLLBACK TO SAVEPOINT sp_5ax_dml" in S
    assert "RELEASE SAVEPOINT sp_5ax_dml" in S
    assert 'ENSURE(code=="42501"' in S
    assert "5AX BRECHA: SQL DML directo todavía permitido" in S
    assert "UPDATE public.reserva" in S
    assert "UPDATE public.inventario" in S
    assert "UPDATE public.carrito" in S


def test_5ax_flujos_positivos_y_aislamiento_5aw():
    assert 'M["verificar_5au_y_llamadas"](c,ids,a2,carrito2)' in S
    assert 'auditar_reserva_ajena_sin_grant(c,ids,res_a2)' in S
    assert "5AX RLS PASS: A1 no puede leer reserva ajena A2" in S
    assert "retirar_dml_directo_de_actores(c)" in S
    assert "instalar_sp_como_definer(c)" in S
    assert "5AX OK LAB" in S
    assert "seguridad de DEFINER/policies completa" in S


def test_5ax_postflight_no_persisten_roles_mapa_ni_policies():
    assert "([WRITER,VERIFIER_OWNER,*ROLES],)" in S
    assert "to_regclass('public.fs_lab_actor_sql_5ar') IS NULL" in S
    assert '==41,"41 policies originales no restauradas"' in S
    assert "0 SP" in S
    assert "3 reservas y 4 carritos" in S



def test_5ax_security_definer_no_permita_actor_ajeno_ni_compartido():
    assert "auditar_guardia_definer(c,ids,a2,carrito2)" in S
    assert "M[\"LAB\"][\"CALL_CREATE\"]" in S
    assert 'reason=="Actor SQL no coincide con usuario de la empresa"' in S
    assert "has_function_privilege(" in S
    assert "5AX IDENTIDAD PASS" in S
    assert "5AX BRECHA: SP DEFINER aceptó actor ajeno" in S



def test_5ax_roles_heredados_a_b_no_retienen_dml_directo():
    assert 'SHARED=("fashionstore_5ab_a","fashionstore_5ab_b")' in S
    assert "TARGETS=ROLES+SHARED" in S
    assert 'targets=", ".join(TARGETS)' in S
    assert "for role in TARGETS:" in S
    assert "(SHARED[0],\"UPDATE public.inventario \"" in S
    assert "5 roles (A1,A2,B1,A,B)" in S
