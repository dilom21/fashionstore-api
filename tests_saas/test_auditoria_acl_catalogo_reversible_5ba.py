"""5BA: controles estáticos, sin conexiones PostgreSQL ni cambios del .env."""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/60_auditar_acl_todas_tablas_roles_login_rollback_5ba.py")
S=P.read_text(encoding="utf-8")


def test_5ba_sintaxis_python():
    ast.parse(S)


def test_5ba_preflight_solo_lab_y_rollback_final():
    assert "59_http_jwt_sp_definer_rollback_5az.py" in S
    assert "PREFLIGHT(c)" in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'c.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "Nunca usar en 5432 ni Supabase" in S
    assert "41," in S or "41 policies" in S


def test_5ba_reutiliza_el_modelo_protegido_5ax():
    for fragment in (
        'X["M"]["INSTALAR_CU16"](c,fuentes)',
        'X["M"]["PROTEGER_CU17_18"](c,fuentes)',
        'X["M"]["ACL_RLS"](c)',
        'X["M"]["instalar_verificador_5av"](c)',
        'X["M"]["usar_unico_verificador_en_tres_sp"](c)',
        'X["X"]["preparar_restriccion_individual"](c)',
        'X["preparar_writer"](c)',
        'X["copiar_rls_a_writer"](c)',
        'X["retirar_dml_directo_de_actores"](c)',
        'X["instalar_sp_como_definer"](c)',
    ):
        assert fragment in S
    assert "==161" in S


def test_5ba_catalogo_completo_y_dml_efectivo():
    assert "FROM pg_catalog.pg_class cl" in S
    assert "ns.nspname='public'" in S
    assert "len(rows)>=46" in S
    assert "for role in ACTORS:" in S
    assert "has_table_privilege(%s,%s::oid,%s)" in S
    assert "has_any_column_privilege(%s,%s::oid,%s)" in S
    assert "TRUNCATE" in S and "TRIGGER" in S
    assert "INSERT" in S and "UPDATE" in S and "DELETE" in S
    assert "REFERENCES" in S


def test_5ba_8_tablas_y_mapa_criticos_sin_dml():
    assert 'esenciales=[x for x in riesgos if x[1] in TABLES]' in S
    assert '"usuario","cliente","empleado","empresa"' in S
    assert '"usuario_empresa","fs_lab_actor_sql_5ar"' in S
    assert "ENSURE(not esenciales," in S
    assert "ENSURE(not sensibles," in S
    assert "5BA BRECHA" in S
    assert "5BA ALERTA" in S


def test_5ba_rutinas_no_exponen_exec_publico():
    assert '"fs_verificar_actor_reserva"' in S
    assert '"fs_lab_empresa_actor_5at"' in S
    assert "p.proacl IS NOT NULL AND NOT EXISTS" in S
    assert "acl.grantee=0 AND acl.privilege_type='EXECUTE'" in S
    assert "bool(public_revoked)" in S
    assert "bool(path_ok)" in S
    assert "bool(definer) and bool(limited)" in S
    assert "fs_lab_empresa_actor_5at" in S
    assert "5BA PENDIENTE" in S


def test_5ba_roles_no_heredan_owner_y_sin_create():
    assert "pg_has_role(%s::name,%s::name,'MEMBER')" in S
    assert 'for owner in (WRITER,VERIFIER_OWNER):' in S
    assert "rolpassword IS NULL" in S
    assert "rolcanlogin" in S
    assert "has_schema_privilege(%s,'public','CREATE')" in S
    assert "PASSWORD NULL" in S
    assert "5BA LOGIN PENDIENTE" in S


def test_5ba_resguarda_0_roles_0_mapas_y_41_policies():
    assert "([WRITER,VERIFIER_OWNER,*ROLES],)" in S
    assert "to_regclass('public.fs_lab_actor_sql_5ar') IS NULL" in S
    assert "==41" in S
    assert "5BA ROLLBACK VERIFICADO" in S
    assert "0 SP comerciales" in S


def test_5ba_no_confunde_catalogo_seguro_con_login_real():
    assert "ninguna conexión nueva" in S
    assert "creados sin COMMIT" in S
    assert "BLOQUEO ACTUAL" in S
    assert "flags OFF" in S
    assert "riscos=auditar_tablas(c)" in S
