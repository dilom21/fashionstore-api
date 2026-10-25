"""5BI: regresiones sin SQL, DB, Supabase ni archivos comerciales."""
import ast
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"database/saas/68_dependencias_esquema_historico_offline_5bi.py"
S=P.read_text(encoding="utf-8")
LAB=runpy.run_path(str(P))


def test_5bi_sintaxis_e_imports_solo_stdlib():
    tree=ast.parse(S)
    actual=[]
    for node in tree.body:
        if isinstance(node,ast.Import):
            actual.extend(alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom):
            actual.append(node.module)
    assert set(actual)=={"__future__","pathlib","re","sys"}


def test_5bi_no_importa_app_ni_conecta_db():
    assert "no conecta" in S
    assert "PostgreSQL 5432/55432/55433" in S
    assert "psycopg.connect(" not in S
    assert "create_engine(" not in S
    assert "supabase.Client" not in S
    assert "subprocess.run(" not in S
    assert "runpy.run_path(" not in S
    assert "os.environ" not in S
    assert "load_dotenv(" not in S
    assert "DATABASE_URL" not in S


def test_5bi_rutas_son_archivos_fijos_de_migracion():
    files=LAB["MIGRATIONS"]
    assert len(files)==4
    assert all(x.startswith("supabase/migrations/") for x in files)
    assert all(x.endswith(".sql") for x in files)
    assert LAB["BITACORA"]=="SQL_Correccion_Bitacora_IP.sql"


def test_5bi_solo_lee_fuentes_known_no_backups():
    assert 'for rel in (*MIGRATIONS,BITACORA):' in S
    assert "path.resolve().is_relative_to(ROOT.resolve())" in S
    assert "path.stat().st_size<500_000" in S
    assert 'path.read_text(encoding="utf-8")' in S
    assert ".backup" not in S
    assert ".dump" not in S


def test_5bi_tablas_saas_base_exigidas_exactamente_cinco():
    assert LAB["BASE_TABLES"]==(
        "empresa","plan_saas","suscripcion",
        "usuario_empresa","empresa_configuracion"
    )
    assert 'to_regclass(\'public.usuario\')' in S
    assert 'to_regclass(\'public.rol\')' in S
    assert 'CREATE TABLE public.usuario (' in S


def test_5bi_verifica_guardas_db_y_conteos_46():
    assert "v_tablas_public <> 46" in S
    assert "v_total <> 46" in S
    assert "v_n <> 46" in S
    assert "current_database\\(\\)" in S
    assert "EXPECTED_DB=" in S


def test_5bi_verifica_27_triggers_48_fk():
    assert "v_trigger_count <> 27" in S
    assert "NOT t.tgisinternal) <> 27" in S
    assert "fk_tenant_%" in S
    assert '"<> 48"' in S
    assert "CREATE TRIGGER trg_bitacora_completar_ip" in S


def test_5bi_ejecuta_analisis_real_sobre_fuentes_sin_sql():
    # Solo lectura local de cinco archivos versionados.
    got=LAB["verificar_dependencias"](LAB["leer_rutas"]())
    assert got["base_saas"]==5
    assert got["total_documentado_migraciones"]==46
    assert got["legacy_preexistente_estimado"]==41
    assert got["triggers_heredados_exigidos"]==27
    assert got["fk_tenant_esperadas_paso6"]==48
    assert got["fuentes_legado_ddl_completo"] is False


def test_5bi_reporta_bloqueo_aunque_auditoria_apruebe():
    assert "5BI AUDITORÍA PASS" in S
    assert "5BI RECONSTRUCCIÓN BLOQUEADA" in S
    assert "5BI DISCREPANCIA" in S
    assert "47 tablas" in S
    assert "46" in S
    assert "no hay DDL heredado completo" in S


def test_5bi_no_hace_claim_produccion_lista():
    assert "No ejecuta SQL" in S
    assert "no se leyeron backups ni secretos" in S
    assert "SaaS OFF" in S
    assert "Supabase intactos" in S
    assert 'if __name__=="__main__":' in S
