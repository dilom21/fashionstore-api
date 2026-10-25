"""5AN: pruebas estáticas del ensayo SQL reversible, sin usar PostgreSQL."""
import ast
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"database/saas/49_ensayo_sp_tenant_reversible_5an.py"
S=SCRIPT.read_text(encoding="utf-8")


def test_script_5an_sintaxis_python():
    ast.parse(S)


def test_5an_reutiliza_preflight_exactamente_5am():
    assert "48_compilar_sp_tenant_transaccion_rollback_5am.py" in S
    assert "preflight(c)" in S
    assert "preflight, sources, catalog" in S


def test_5an_persiste_nada_y_no_instala_en_servidor_original():
    assert "conn.execute" not in S or "COMMIT" not in S
    assert 'c.execute("BEGIN")' in S
    assert 'c.execute("ROLLBACK")' in S
    assert "finally:" in S
    assert 'conn.execute("COMMIT")' not in S
    assert 'c.execute("COMMIT")' not in S
    assert "fashionstore_restore_" not in S
    assert "supabase db push" not in S
    assert "DROP DATABASE" not in S
    assert "DROP ROLE" not in S


def test_5an_actores_5an_solo_sinteticos_y_sin_password_hash_real():
    assert "!FICTIONAL-NONLOGIN-5AN" in S
    assert "lab-a-cliente-5an@demo.invalid" in S
    assert "lab-b-admin-5an@demo.invalid" in S
    assert 'GRANT SELECT (id,estado) ON public.usuario' in S
    assert 'GRANT SELECT ON public.usuario' not in S
    assert "getpass(" in S


def test_5an_principal_autorizado_no_simulado_con_set_role():
    assert "SET LOCAL SESSION AUTHORIZATION" in S
    assert "SET LOCAL SESSION AUTHORIZATION DEFAULT" in S
    assert "SELECT session_user" in S
    assert "row_security_active('public.reserva'::regclass)" in S
    assert "row_security_active('public.inventario'::regclass)" in S
    assert "SET ROLE" not in S


def test_5an_tres_sp_y_rollback_completo():
    assert "sp_crear_reserva_tenant" in S
    assert "sp_cancelar_reserva_tenant" in S
    assert "sp_finalizar_reserva_sin_compra_tenant" in S
    assert "CALL_CREATE" in S
    assert "CALL_CHANGE" in S
    assert "CU16 PASS" in S and "CU17 PASS" in S and "CU18 PASS" in S
    assert "preflight(c)" in S.split('c.execute("ROLLBACK")')[-1]
    assert "5 empresas finales intactas" in S


def test_5an_rls_policy_temporal_y_planes_activos():
    assert "fs_lab_5an_plan_select" in S
    assert "fs_lab_5an_{t}_update" in S
    assert "permite_reservas,estado" in S
    assert "usuario_empresa" in S
    assert "fecha_vencimiento" in S
    assert "GRANT EXECUTE ON PROCEDURE" in S



def test_5an_grants_no_confunden_archivo_y_numero_de_argumentos():
    """Regresión del TypeError: las fichas 5AM son (archivo, aridad)."""
    import runpy

    module = runpy.run_path(str(SCRIPT))
    assert all(
        isinstance(metadata, tuple) and len(metadata) == 2
        for metadata in module["M"]["PROCEDURES"].values()
    )

    class DummyConn:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            return None

    fake = DummyConn()
    module["grant_minimal_lab"](fake)
    grants = [x for x in fake.calls if "GRANT EXECUTE ON PROCEDURE" in x]
    assert len(grants) == 3
    for name, signature in (
        ("sp_crear_reserva_tenant", "bigint,bigint,bigint,timestamptz,text,bigint"),
        ("sp_cancelar_reserva_tenant", "bigint,bigint,bigint,text"),
        ("sp_finalizar_reserva_sin_compra_tenant", "bigint,bigint,bigint,text"),
    ):
        assert any(
            f"public.{name}({signature})" in statement
            for statement in grants
        )
    policies = [x for x in fake.calls if "CREATE POLICY fs_lab_5an_" in x]
    assert len(policies) == 11
    assert any("fs_lab_5an_usuario_select" in p for p in policies)
    assert any("fs_lab_5an_rol_select" in p for p in policies)
    assert any("ue.empresa_id" in p and "session_user" in p for p in policies)
    assert any("ue.usuario_id = usuario.id" in p for p in policies)
    assert any("ue.rol_id = rol.id" in p for p in policies)



def test_5an_diagnostico_rls_exige_membresia_y_lock():
    """El diagnóstico distingue SELECT normal y SELECT FOR SHARE real."""
    import runpy
    import pytest

    m = runpy.run_path(str(SCRIPT))

    class Rows:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return (self.value,)

        def fetchall(self):
            return [] if self.value == 0 else [(71,)]

    class FakeDB:
        def __init__(self, lock_visible=True):
            self.lock_visible = lock_visible
            self.queries = []

        def execute(self, sql, values=None):
            self.queries.append((sql, values))
            if "FOR SHARE OF ue, ss" in sql and not self.lock_visible:
                return Rows(0)
            return Rows(1)

    ok = FakeDB()
    m["diagnosticar_membresia"](ok, empresa_id=3, usuario_id=7, rol="CLIENTE")
    assert len(ok.queries) == 8
    assert "FOR SHARE OF ue, ss" in ok.queries[-1][0]

    denied = FakeDB(lock_visible=False)
    with pytest.raises(RuntimeError, match="membresia no visible"):
        m["diagnosticar_membresia"](denied, 3, 7, "CLIENTE")



def test_5an_usuario_y_rol_tienen_rls_temporal_sin_hash():
    import runpy

    m = runpy.run_path(str(SCRIPT))
    class DummyConn:
        def __init__(self):
            self.calls = []
        def execute(self, sql):
            self.calls.append(sql)

    fake = DummyConn()
    m["grant_minimal_lab"](fake)
    sql = "\n".join(fake.calls)
    assert "ALTER TABLE public.usuario ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE public.rol ENABLE ROW LEVEL SECURITY" in sql
    assert "GRANT SELECT (id,estado) ON public.usuario" in sql
    assert "GRANT SELECT (id,nombre,estado) ON public.rol" in sql
    assert "GRANT SELECT ON public.usuario" not in sql
    assert "GRANT SELECT ON public.rol" not in sql
    assert "WITH CHECK" in sql
    assert "session_user" in sql
    assert 'ue.estado IS TRUE' in sql
    assert "CREATE POLICY fs_lab_5an_usuario_select" in sql
    assert "CREATE POLICY fs_lab_5an_rol_select" in sql


def test_5an_usuario_y_rol_ajenos_ocultos_antes_del_call():
    assert 'usuario B se filtro al principal A' in S
    assert 'usuario A se filtro al principal B' in S
    assert 'rol de B se filtro al principal A' in S
    assert 'rol de A se filtro al principal B' in S
    assert 'policies temporales no son exactamente once' in S
    assert 'c.execute("ROLLBACK")' in S



def test_5an_catalogo_y_permisos_globales_respetan_tenant():
    import runpy

    mod = runpy.run_path(str(SCRIPT))
    class DummyConn:
        def __init__(self): self.calls=[]
        def execute(self, sql): self.calls.append(sql)

    fake=DummyConn()
    mod["grant_minimal_lab"](fake)
    policy_sql = "\n".join(
        statement for statement in fake.calls
        if "CREATE POLICY fs_lab_5an_" in statement
    )
    for table in ("talla","color","rol_funcion","funcion","accion"):
        assert f"CREATE POLICY fs_lab_5an_{table}_select" in policy_sql
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in "\n".join(fake.calls)
    assert "variante_producto vp" in policy_sql
    assert "JOIN public.usuario_empresa ue" in policy_sql
    assert "ue.rol_id = rol_funcion.rol_id" in policy_sql
    assert "session_user" in policy_sql
    assert "catalogo A no visible por RLS" in S
    assert "permisos CU18 B no visibles por RLS" in S
    assert '==52' in S
