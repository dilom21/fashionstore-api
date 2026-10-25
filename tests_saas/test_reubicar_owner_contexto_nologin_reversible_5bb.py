"""5BB: guardrails offline para owner NOLOGIN de contexto SQL.

No importa el arnés PG ni abre conexiones o cambia credenciales.
"""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/61_reubicar_funcion_contexto_owner_nologin_5bb.py")
S=P.read_text(encoding="utf-8")


def test_5bb_sintaxis_python():
    ast.parse(S)


def test_5bb_solo_lab_preflight_y_rollback_obligatorio():
    assert "60_auditar_acl_todas_tablas_roles_login_rollback_5ba.py" in S
    assert 'HOST,PORT,DB=BA["HOST"],BA["PORT"],BA["DB"]' in S
    assert "PREFLIGHT(c)" in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'c.execute("COMMIT")' not in S
    assert "Sin migraciones permanentes" in S
    assert "ni Supabase" in S
    assert "41 policies" in S


def test_5bb_cambia_solo_funcion_auxiliar_y_restringe_owner():
    assert "public.fs_lab_empresa_actor_5at()" in S
    assert 'OWNER TO {VERIFIER_OWNER}' in S
    assert 'GRANT CREATE ON SCHEMA public TO {VERIFIER_OWNER}' in S
    assert 'REVOKE CREATE ON SCHEMA public FROM {VERIFIER_OWNER}' in S
    assert "NOT rolcanlogin AND NOT rolsuper" in S
    assert "NOT rolbypassrls" in S
    assert "NOT rolcreaterole" in S
    assert "NOT rolinherit" in S
    assert "has_database_privilege(%s,current_database(),'CREATE')" in S
    assert "has_table_privilege(" in S


def test_5bb_cinco_rutinas_y_owners_separados():
    assert '"fs_verificar_actor_reserva"' in S
    assert '"fs_lab_empresa_actor_5at"' in S
    assert "len(rows)==5" in S
    assert 'expected_owner=WRITER if name in X["PROCEDURES"] else VERIFIER_OWNER' in S
    assert "p.prosecdef" in S
    assert "pg_get_userbyid(p.proowner)" in S
    assert "acl.grantee=0 AND acl.privilege_type='EXECUTE'" in S
    assert "public_revoked is True" in S
    assert "pg_has_role(%s::name,%s::name,'MEMBER')" in S
    assert "actor {actor} puede leer el mapa privado" in S


def test_5bb_guc_no_puede_suplantar_tenant():
    assert 'X["A1"],ids["a_empresa"],ids["b_empresa"]' in S
    assert 'X["A2"],ids["a_empresa"],ids["b_empresa"]' in S
    assert 'X["B1"],ids["b_empresa"],ids["a_empresa"]' in S
    assert "set_config('app.empresa_id',%s,true)" in S
    assert "SELECT public.fs_lab_empresa_actor_5at()" in S
    assert "SELECT count(*) FROM public.empresa" in S
    assert "5BB RLS PASS" in S


def test_5bb_operaciones_validas_y_suplantaciones_rechazadas():
    assert 'X["M"]["verificar_5au_y_llamadas"](c,ids,a2,carrito_a2)' in S
    assert 'X["auditar_guardia_definer"](c,ids,a2,carrito_a2)' in S
    assert 'X["auditar_reserva_ajena_sin_grant"](c,ids,reserva_a2)' in S
    assert 'X["auditar_dml_sin_privilegios"](c,ids,reserva_a2)' in S
    assert "settings.saas_sp_sql_owner=WRITER" in S
    assert "settings.saas_sp_sql_owner=old_sp" in S
    assert "5BB SP PASS" in S


def test_5bb_auditoria_acl_general_no_omitada():
    assert 'BA["auditar_tablas"](c)' in S
    assert "ENSURE(not riesgos" in S
    assert 'BA["auditar_login"](c)' in S
    assert "ACL global contiene privilegios DML residuales" in S
    assert "sin superusuario" in S


def test_5bb_postflight_ningun_rol_o_policy_persistente():
    assert '([WRITER,VERIFIER_OWNER,*ROLES],)' in S
    assert "to_regclass('public.fs_lab_actor_sql_5ar') IS NULL" in S
    assert '==41,"las políticas originales no regresaron"' in S
    assert "5BB ROLLBACK VERIFICADO" in S
    assert "3 reservas, 4 carritos originales" in S


def test_5bb_no_confunde_restriccion_owner_con_login_real():
    assert "PASSWORD" not in S  # no se crea credencial/login
    assert "cluster" in S
    assert "desechable" in S
    assert "flags SaaS OFF" in S
