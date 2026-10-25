"""5BH: controles puros de inventario de fuentes, 0 conexiones PostgreSQL."""
import ast
from collections import Counter
from pathlib import Path
import re
import runpy

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"database/saas/67_auditar_fuentes_esquema_sin_conexion_5bh.py"
S=P.read_text(encoding="utf-8")
MODULE=runpy.run_path(str(P))


def test_5bh_sintaxis_python():
    ast.parse(S)


def test_5bh_sin_importacion_db_network_driver:
    tree=ast.parse(S)
    imported=[]
    for node in tree.body:
        if isinstance(node,ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom):
            imported.append(node.module)
    assert set(imported)=={
        "__future__","collections","pathlib","re","subprocess","sys"
    }
    for forbidden in ("psycopg","sqlalchemy","httpx","requests",
                      "socket","supabase"):
        assert forbidden not in imported


def test_5bh_no_conecta_ningun_puerto_pg():
    assert "NO establece conexiones a 5432, 55432, 55433" in S
    assert '["git","-C",str(ROOT),"ls-files","-z"]' in S
    assert "psycopg.connect(" not in S
    assert "create_engine(" not in S
    assert "postgres_bin(" not in S
    assert "pg_ctl" not in S
    assert "psql " not in S


def test_5bh_no_carga_env_o_archivo_de_credenciales():
    assert ".env" in S  # sólo cita que NO se carga
    assert "load_dotenv(" not in S
    assert "dotenv_values(" not in S
    assert "os.getenv(" not in S
    assert "read_text(encoding=" in S  # únicamente en SQL/py versionado
    assert 'print("  "+name+' in S
    assert "print(source" not in S
    assert "print(raw" not in S


def test_5bh_categoriza_fuentes_sql_y_generadores_py():
    f=MODULE["categorizar"]
    assert f("database/schema.sql")=="resto-versionado"
    assert f("migrations/0001.sql")=="resto-versionado"
    assert f("app/models/tables.py")=="resto-versionado"
    assert f("database/saas/borradores/12_sp.sql.txt")=="saas-ensayo"
    assert f("tests_saas/test_foo.py")=="saas-ensayo"
    assert f(".env")=="no-fuente"
    assert f("README.md")=="no-fuente"


def test_5bh_excluye_virtualenv_cache_build_node_modules():
    f=MODULE["categorizar"]
    assert f(".venv/lib/postgres.sql")=="no-fuente"
    assert f("node_modules/foo/create.sql")=="no-fuente"
    assert f("dist/schema.sql")=="no-fuente"
    assert f("app/__pycache__/file.py")=="no-fuente"


def test_5bh_reconoce_ddl_basico_sin_ejecutar_sql():
    patterns=MODULE["PATTERNS"]
    assert patterns["tablas"].search("CREATE TABLE public.empresa")
    assert patterns["fk"].search("FOREIGN KEY (empresa_id)")
    assert patterns["fk"].search("REFERENCES empresa(id)")
    assert patterns["rls"].search("CREATE POLICY fs_tenant_select")
    assert patterns["rls"].search("ENABLE ROW LEVEL SECURITY")
    assert patterns["triggers"].search("CREATE TRIGGER audit_reserva")
    assert patterns["funciones"].search("CREATE OR REPLACE FUNCTION my_func")
    assert patterns["procedimientos"].search("CREATE PROCEDURE public.sp_crear")


def test_5bh_totaliza_categorias_independientes():
    count=MODULE["summary"]([
        ("database/schema.sql","resto-versionado",{"tablas":3,"fk":5}),
        ("database/saas/borradores/12.sql.txt","saas-ensayo",
         {"procedimientos":1}),
    ])
    assert count["resto-versionado"]["tablas"]==3
    assert count["resto-versionado"]["fk"]==5
    assert count["resto-versionado"]["procedimientos"]==0
    assert count["saas-ensayo"]["procedimientos"]==1
    assert count["saas-ensayo"]["tablas"]==0


def test_5bh_no_afirma_schema_completo_por_encontrar_create_table():
    assert "NO basta una coincidencia textual de CREATE TABLE" in S
    assert 'totals["resto-versionado"][k]==0' in S
    assert "NO están validadas" in S
    assert "sin fuente versionada fuera de ensayos" in S


def test_5bh_no_imprime_sql_enteros_ni_secretos():
    assert 'print("  "+name+" ["+kinds+"]")' in S
    assert "No ejecuta, migra ni copia" in S
    assert "no imprime texto del SQL" in S
    assert "print(source)" not in S


def test_5bh_falla_si_repositorio_incorrecto_y_limita_archivos():
    assert "66_sp_comerciales_saas_stock_real_temporal_5bg.py" in S
    assert 'ensure(bool(rels),' in S
    assert "full.stat().st_size>MAX_BYTES" in S
    assert "full.resolve().is_relative_to(ROOT.resolve())" in S
    assert 'MAX_BYTES=2_000_000' in S


def test_5bh_no_es_migracion_ni_habilita_saas():
    assert "5BH AUDITORÍA LOCAL PASS" in S
    assert "5BH FALTA EVIDENCIA" in S
    assert "5BH PENDIENTE" in S
    assert "48 FK" in S
    assert "47 tablas" in S
    assert "flags SaaS OFF" in S
    assert "no tocados" in S
