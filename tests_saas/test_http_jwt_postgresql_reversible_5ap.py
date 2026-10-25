"""5AP: verificaciones offline del enlace HTTP/JWT→SQL REAL reversible.

No importa 5AP, por lo que no construye engines ni lee .env. No se conecta
a PostgreSQL; la prueba dinámica se ejecuta solo manualmente en el LAB.
"""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"database/saas/51_http_jwt_postgresql_rollback_5ap.py"
CODE=SCRIPT.read_text(encoding="utf-8")


def test_5ap_python_compila():
    ast.parse(CODE)


def test_5ap_identidad_cluster_y_rollback_siempre():
    assert "49_ensayo_sp_tenant_reversible_5an.py" in CODE
    assert 'PREFLIGHT(conn)' in CODE
    assert 'conn.execute("BEGIN")' in CODE
    assert 'conn.execute("ROLLBACK")' in CODE
    assert "finally:" in CODE
    assert 'PREFLIGHT(conn)\n            print("5AP ROLLBACK VERIFICADO' in CODE
    assert 'conn.execute("COMMIT")' not in CODE
    assert "fashionstore_restore_" not in CODE
    assert "supabase db push" not in CODE


def test_5ap_jwt_y_deps_verdaderas_sin_omitir_membresia():
    assert "emitir_token_empresa(" in CODE
    assert "api.add_middleware(BloqueoRutasLegacySaaS)" in CODE
    assert "api.include_router(auth_router)" in CODE
    assert "api.include_router(reserva_router)" in CODE
    assert "api.dependency_overrides[get_db] = db_de_laboratorio" in CODE
    assert "dependency_overrides[get_current_tenant_context]" not in CODE
    assert "obtener_membresia_vigente" not in CODE
    assert "app.core.tenant_context" in CODE
    assert "Sesión" not in CODE


def test_5ap_no_consulta_http_bajo_superusuario():
    assert CODE.index("AS_ROLE(conn,A)") < CODE.index("with TestClient(api) as http")
    assert 'AS_ROLE(conn,B)' in CODE
    assert "SET LOCAL SESSION AUTHORIZATION" not in CODE
    assert "settings.saas_auth_enabled = True" in CODE
    assert "settings.saas_reservas_enabled = True" in CODE
    assert "settings.saas_auth_enabled,settings.saas_reservas_enabled = antes" in CODE
    assert "api.dependency_overrides.clear()" in CODE


def test_5ap_transacciones_no_persisten_ni_provisionan_sql():
    assert "self.commits_bloqueados += 1" in CODE
    assert "ENSURE(db.commits_bloqueados==3" in CODE
    assert 'src = CARGAR_SQL()' in CODE
    assert "ACL(conn)" in CODE
    assert "crear_segundo_cliente_a(conn,actores)" in CODE
    assert "password_hash,estado" in CODE
    assert "demo.invalid" in CODE
    assert '"!INUTILIZABLE-SOLO-5AP"' not in CODE
    assert "!INUTILIZABLE-SOLO-5AP" in CODE


def test_5ap_tres_http_y_dos_usuarios_misma_empresa():
    assert "dos JWT distintos, misma empresa A" in CODE
    assert "HTTP 201" in CODE
    assert "CU17 PASS" in CODE
    assert "CU18 PASS" in CODE
    assert '"usuario_id":actores["b_usuario"]' in CODE
    assert "suplantacion actor JSON" in CODE
    assert "JWT A con empresa B" in CODE
    assert "JWT + membresia segundo cliente A" in CODE
    assert "5AP PENDIENTE: establecer vínculo DB inviolable" in CODE


def test_5ap_ejecutable_directo_resuelve_app_desde_raiz():
    """`python database/saas/51_*.py` no agrega el root al sys.path."""
    assert 'REPO_ROOT = Path(__file__).resolve().parents[2]' in CODE
    assert 'sys.path.insert(0, str(REPO_ROOT))' in CODE
    assert '(REPO_ROOT / "app" / "core" / "config.py").is_file()' in CODE
    assert CODE.index('sys.path.insert(0, str(REPO_ROOT))') < CODE.index(
        'from app.core.config import settings'
    )
