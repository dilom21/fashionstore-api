"""5AZ: guardrails offline de HTTP/JWT + 5AX SQL real, SIN PostgreSQL."""
import ast
from pathlib import Path

P=(Path(__file__).resolve().parents[1]/
   "database/saas/59_http_jwt_sp_definer_rollback_5az.py")
S=P.read_text(encoding="utf-8")


def test_5az_sintaxis_python():
    ast.parse(S)


def test_5az_preflight_cluster_fijo_y_rollback_obligatorio():
    assert "58_sp_owner_nologin_sin_dml_directo_rollback_5ax.py" in S
    assert "51_http_jwt_postgresql_rollback_5ap.py" in S
    assert 'HOST,PORT,DB=X["HOST"],X["PORT"],X["DB"]' in S
    assert 'PREFLIGHT(c)' in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'c.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "saas_5az_import_inerte.db" in S
    assert "NO validar ni instalar en puerto 5432" in S


def test_5az_fastapi_jwt_reales_sin_mockear_dependencia_seguridad():
    assert "api.add_middleware(BloqueoRutasLegacySaaS)" in S
    assert "api.include_router(auth_router)" in S
    assert "api.include_router(reserva_router)" in S
    assert "api.dependency_overrides[get_db]=laboratorio" in S
    assert "dependency_overrides[get_current_tenant_context]" not in S
    assert "emitir_token_empresa(" in S
    assert "with TestClient(api) as http" in S
    assert 'http.get("/auth/tenant/me"' in S


def test_5az_jwt_invalido_ausente_cruzado_e_inyectado():
    assert '401,"sin JWT"' in S
    assert '401,"JWT falsificado"' in S
    assert '403,"JWT A intentando empresa B"' in S
    assert '422,"JSON intenta inyectar actor"' in S
    assert '"CALL public.sp_" in s' in S
    assert 'sesion' not in S.lower() or "SESSION AUTHORIZATION" in S


def test_5az_dos_usuarios_mismo_tenant_falla_cerrado_antes_de_call():
    assert "sql A1" in S.lower() or "SQL A1" in S
    assert 'denied.status_code==503' in S
    assert "before==after" in S
    assert "5AZ ACTOR DENEGADO PASS" in S
    assert "stock_reservado" in S


def test_5az_doble_flag_y_duenos_solo_durante_testclient():
    assert "settings.saas_auth_enabled=True" in S
    assert "settings.saas_reservas_enabled=True" in S
    assert "settings.saas_actor_sql_owner=VERIFIER_OWNER" in S
    assert "settings.saas_sp_sql_owner=OWNER" in S
    assert "settings.saas_actor_sql_owner,settings.saas_sp_sql_owner" in S
    assert "api.dependency_overrides.clear()" in S
    assert "ROLES=X[\"ROLES\"]" in S
    assert "entrar(c,A1)" in S
    assert "entrar(c,B1)" in S


def test_5az_cinco_call_validos_por_http_jwt_y_sin_commit():
    assert 'http.post("/tenant/reservas"' in S
    assert "finalizar-sin-compra" in S
    assert "/cancelar" in S
    assert "5AZ CU16 PASS" in S
    assert "5AZ CU17 PASS" in S
    assert "5AZ CU18 PASS" in S
    assert "db.commits_bloqueados==5" in S
    assert "SessionRollback=LAB[" in S
    assert "CU16" in S and "CU17" in S and "CU18" in S


def test_5az_reutiliza_acl_rls_5ax_y_audita_dml_directo():
    for fragment in (
        'X["preparar_writer"](c)',
        'X["copiar_rls_a_writer"](c)',
        'X["retirar_dml_directo_de_actores"](c)',
        'X["instalar_sp_como_definer"](c)',
        'X["auditar_dml_sin_privilegios"](c,ids,reserva2)',
        'X["auditar_reserva_ajena_sin_grant"](c,ids,reserva2)',
    ):
        assert fragment in S
    assert "==161" in S
    assert "0 SP comerciales, 41 policies" in S
    assert "flags OFF" in S


def test_5az_no_confunde_sesion_simulada_con_login_real():
    assert "SESSION AUTHORIZATION" in S
    assert "son SIMULADAS" in S
    assert "no logins reales" in S
    assert "PENDIENTE: login PG no-superuser REAL" in S
    assert "NO COMMIT" in S
