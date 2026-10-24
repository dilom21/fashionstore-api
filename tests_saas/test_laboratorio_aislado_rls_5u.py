"""B3/5U: 18 pruebas OFFLINE de la barrera contra clúster compartido.

No inicia Docker/initdb/pg_ctl, no accede a base, no crea roles ni datos.
Prueba escenarios ficticios y cambios inseguros de la propuesta únicamente.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json"
VALIDATOR = ROOT / "database/saas/25_validar_laboratorio_aislado_5u.py"
M = runpy.run_path(str(VALIDATOR))
ERRORES = M["errores_contrato_5u"]
IDENTIDAD = M["comparar_identidad_clusters_5u"]


def contrato():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_5u_contrato_valido_y_no_ejecutable():
    c = contrato()
    assert c["estado"] == "BORRADOR_NO_EJECUTAR"
    assert c["desplegable"] is False
    assert ERRORES(c, ROOT) == []


def test_5u_cli_reporta_pendiente_sin_prometer_aislamiento(capsys):
    assert M["main"]() == 0
    salida = capsys.readouterr().out
    assert "14 casos A/B, 2 empresas ficticias" in salida
    assert "system_identifier sin medir" in salida
    assert "RLS A/B NO CERTIFICADO" in salida


def test_5u_matriz_cubre_14_casos_unicos_y_dos_empresas():
    c = contrato()
    assert len(c["plan_casos"]) == 14
    assert len({fila["id"] for fila in c["plan_casos"]}) == 14
    assert c["tenants_ficticios"] == ["empresa-lab-a", "empresa-lab-b"]


def test_5u_prohibe_auto_autorizar_creacion():
    c = contrato()
    c["iniciar_laboratorio_autorizado"] = True
    assert any("habilitado" in e for e in ERRORES(c, ROOT))


def test_5u_prohibe_mover_origen_a_restaurada():
    c = contrato()
    c["origen"]["base"] = "fashionstore_restore_20261023_193554_594"
    assert any("origen" in e for e in ERRORES(c, ROOT))


def test_5u_rechaza_mismo_puerto_pg_local():
    c = contrato()
    c["destino"]["puerto_propuesto"] = 5432
    assert any("puerto 5432" in e for e in ERRORES(c, ROOT))


def test_5u_rechaza_exportar_datos_de_sandbox():
    c = contrato()
    c["origen"]["solo_esquema"] = False
    c["origen"]["filas_exportadas"] = True
    assert any("exporta datos" in e for e in ERRORES(c, ROOT))


def test_5u_no_puede_inventar_id_de_cluster():
    c = contrato()
    c["destino"]["system_identifier"] = 123456789
    assert any("identidad de destino" in e for e in ERRORES(c, ROOT))


def test_5u_comparar_clusters_acepta_solo_enteros_positivos_distintos():
    assert IDENTIDAD(123456789000, 987654321000) is True
    assert IDENTIDAD(123456789000, 123456789000) is False
    assert IDENTIDAD(None, 987654321000) is False
    assert IDENTIDAD(0, 987654321000) is False
    assert IDENTIDAD(-1, 987654321000) is False
    assert IDENTIDAD(True, 987654321000) is False
    assert IDENTIDAD("123456789000", 987654321000) is False


def test_5u_no_acepta_acls_policies_simuladas_como_implementadas():
    c = contrato()
    c["modelo_futuro"]["estado_acls_policies"] = "IMPLEMENTADA"
    assert any("policies ya activadas" in e for e in ERRORES(c, ROOT))


def test_5u_no_acepta_principales_sql_fingidos():
    c = contrato()
    c["modelo_futuro"]["principal_sql_empresa_a"] = "postgres"
    assert any("identidades SQL" in e for e in ERRORES(c, ROOT))


def test_5u_no_elimina_prueba_de_guc_falsificado():
    c = contrato()
    c["plan_casos"][6]["operacion"] = "Consulta de B con contexto confiable"
    assert any("suplantacion GUC" in e for e in ERRORES(c, ROOT))


def test_5u_no_permita_lectura_cruzada_empresa_b():
    c = contrato()
    c["plan_casos"][1]["esperado"] = "PERMITIR"
    assert any("resultado esperado alterado para A02" in e for e in ERRORES(c, ROOT))


def test_5u_no_permita_insert_ajeno_sin_with_check():
    c = contrato()
    c["plan_casos"][4]["esperado"] = "PERMITIR"
    assert any("resultado esperado alterado para A05" in e for e in ERRORES(c, ROOT))


def test_5u_no_habilita_lectura_hash_para_comercial():
    c = contrato()
    c["plan_casos"][8]["esperado"] = "PERMITIR"
    assert any("A09" in e for e in ERRORES(c, ROOT))


def test_5u_no_certifica_resultados_pg_nunca_ejecutados():
    c = contrato()
    c["evidencia"]["certificado_aislamiento"] = True
    c["evidencia"]["pruebas_pg_reales_ejecutadas"] = True
    c["evidencia"]["casos_reales_aprobados"] = 14
    assert any("evidencia PostgreSQL inventada" in e for e in ERRORES(c, ROOT))


def test_5u_no_remueve_prohibiciones_de_entorno():
    c = contrato()
    c["restricciones"]["saas_auth_enabled"] = True
    assert any("bloqueos de ambientes" in e for e in ERRORES(c, ROOT))


def test_5u_no_usa_red_ni_procesos_y_conserva_7_bloqueos():
    codigo = VALIDATOR.read_text(encoding="utf-8")
    for prohibido in (
        "subprocess", "socket", "psycopg", "create_engine(",
        "SessionLocal(", "os.system(", "Popen(",
    ):
        assert prohibido not in codigo
    c = contrato()
    assert c["baseline"]["bloqueantes_abiertos"] == 7
    assert c["evidencia"]["resultado_rls_a_b"] == "NO_EJECUTADO"
    assert "NO_CREADO" == c["modelo_futuro"]["principal_sql_empresa_b"]
