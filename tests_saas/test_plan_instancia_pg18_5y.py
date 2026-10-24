"""B3/5Y: 14 pruebas OFFLINE de plan de clúster nativo no ejecutable.

No inicia servicios, no abre puertos, no consulta DB, no modifica sistema.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "database/saas/41_b3_p5y_instancia_nativa_NO_EJECUTAR.json"
SCRIPT = ROOT / "database/saas/29_validar_plan_instancia_nativa_5y.py"
DOC = ROOT / "database/saas/42_b3_p5y_autorizacion_instancia_nativa_pg18.md"
V = runpy.run_path(str(SCRIPT))
ERRORES = V["errores_plan_5y"]


def plan():
    return json.loads(PLAN.read_text(encoding="utf-8"))


def test_5y_estado_bloqueado_y_contrato_valido():
    p = plan()
    assert ERRORES(p, ROOT) == []
    assert p["estado"] == "PLAN_NO_EJECUTAR"


def test_5y_cli_solo_muestra_plan_sin_ejecutar(capsys):
    assert V["main"]() == 0
    s = capsys.readouterr().out
    assert "10 compuertas, 14 pruebas A/B futuras" in s
    assert "5Y BLOQUEADO" in s
    assert "7 bloqueantes abiertos" in s


def test_5y_no_autoriza_crear_instancia_por_defecto():
    p = plan()
    p["ejecucion_autorizada"] = True
    assert any("permiso anticipado" in e for e in ERRORES(p, ROOT))


def test_5y_no_autoriza_sql_sin_consentimiento_separado():
    p = plan()
    p["operaciones_sql_autorizadas"] = True
    assert any("permiso anticipado" in e for e in ERRORES(p, ROOT))


def test_5y_rechaza_puerto_original_o_destino_alterado():
    p = plan()
    p["destino"]["puerto_propuesto"] = 5432
    assert any("destino no autorizado" in e for e in ERRORES(p, ROOT))


def test_5y_rechaza_destino_fingido_inicializado():
    p = plan()
    p["destino"]["cluster_inicializado"] = True
    p["destino"]["system_identifier_destino"] = 99999
    assert any("destino real" in e for e in ERRORES(p, ROOT))


def test_5y_protege_id_real_y_pgdata_del_origen():
    p = plan()
    p["origen_protegido"]["system_identifier_fuente_en_repo"] = 123456
    assert any("filtracion" in e for e in ERRORES(p, ROOT))


def test_5y_rechaza_auth_trust_o_md5():
    p = plan()
    p["seguridad_propuesta"]["autenticacion"] = "trust"
    assert any("autenticacion/escucha" in e for e in ERRORES(p, ROOT))
    p = plan()
    p["seguridad_propuesta"]["prohibir_md5"] = False
    assert any("restricciones" in e for e in ERRORES(p, ROOT))


def test_5y_prohibe_escucha_de_red_publica():
    p = plan()
    p["seguridad_propuesta"]["listen_addresses"] = "*"
    assert any("autenticacion/escucha" in e for e in ERRORES(p, ROOT))


def test_5y_no_permita_secreto_en_argumentos():
    p = plan()
    p["seguridad_propuesta"]["sin_contrasena_en_argumentos_o_repo"] = False
    assert any("restricciones" in e for e in ERRORES(p, ROOT))


def test_5y_rechaza_faltante_o_desorden_de_compuertas():
    p = plan()
    p["compuertas"] = p["compuertas"][:-1]
    assert any("compuertas" in e for e in ERRORES(p, ROOT))
    p = plan()
    p["compuertas"][0]["id"] = "G07"
    assert any("desordenadas" in e for e in ERRORES(p, ROOT))


def test_5y_no_marca_instalacion_completada_sin_evidencia():
    p = plan()
    p["compuertas"][5]["cumplida"] = True
    assert any("compuerta marcada" in e for e in ERRORES(p, ROOT))


def test_5y_no_finge_rls_a_b_ni_cierra_bloqueantes():
    p = plan()
    p["validacion_rls"]["casos_reales_ejecutados"] = 14
    p["validacion_rls"]["bloqueantes_pendientes"] = 0
    assert any("aislamiento RLS" in e for e in ERRORES(p, ROOT))


def test_5y_offline_no_ejecuta_programas_y_documenta_permiso():
    s = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "psycopg", "create_engine(",
                  "SessionLocal(", "os.system(", "Popen("):
        assert token not in s
    d = DOC.read_text(encoding="utf-8")
    for marker in ("588 passed", "574 passed", "SAAS_AUTH_ENABLED=false",
                   "14 pruebas A/B", "7 bloqueantes", "autorización explícita",
                   "system_identifier", "Bloque 3 4/7"):
        assert marker in d
