"""5Q: 16 pruebas OFFLINE de la matriz de acceso mínimo, sin PostgreSQL.

Las mutaciones intencionadamente inseguras deben ser RECHAZADAS por el
validador. No se ejecuta DDL, no se lee .env ni se instalan roles.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
RUTA = ROOT / "database/saas/32_b3_p5q_matriz_limites_roles_NO_APLICAR.json"
VALIDADOR = ROOT / "database/saas/23_validar_diseno_acceso_5q.py"
MODULO = runpy.run_path(str(VALIDADOR))
ERRORES = MODULO["errores_contrato"]


def matriz():
    return json.loads(RUTA.read_text(encoding="utf-8"))


def test_5q_documento_json_valido_y_borrador():
    m = matriz()
    assert m["formato"] == "fashionstore.b3.p5q.contrato-acceso.v1"
    assert m["estado"] == "BORRADOR_NO_APLICABLE"
    assert m["desplegable"] is False


def test_5q_validador_acepta_propuesta_segura_no_desplegable():
    assert ERRORES(matriz(), ROOT) == []


def test_5q_main_solo_muestra_advertencia_de_no_despliegue(capsys):
    assert MODULO["main"]() == 0
    salida = capsys.readouterr().out
    assert "5Q MATRIZ CONSISTENTE: 8 recursos, 7 bloqueantes sin resolver" in salida
    assert "5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO" in salida


def test_5q_roles_separados_aun_no_existen():
    m = matriz()
    roles = m["principales_futuros"]
    assert set(roles) == {"bootstrap", "comercial", "auditoria"}
    assert len({rol["nombre_descriptivo"] for rol in roles.values()}) == 3
    assert all(rol["existe_en_postgresql"] is False for rol in roles.values())
    assert all(rol["superuser"] is False and rol["bypassrls"] is False
               for rol in roles.values())


def test_5q_rechaza_manifiesto_marcado_desplegable():
    m = matriz()
    m["desplegable"] = True
    assert any("aplicable" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_uso_postgres_en_api():
    m = matriz()
    m["modelo_amenaza"]["cuenta_postgres_en_aplicacion"] = "PERMITIDA"
    assert any("superusuario" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_sql_directo_para_clientes():
    m = matriz()
    m["modelo_amenaza"]["acceso_sql_de_clientes_finales"] = "PERMITIDO"
    assert any("SQL externo" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_considerar_guc_una_identidad():
    m = matriz()
    m["modelo_amenaza"]["guc_es_identidad_autenticada"] = True
    assert any("GUC" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_select_hash_por_operaciones_comerciales():
    m = matriz()
    m["principales_futuros"]["comercial"]["puede_leer_password_hash"] = True
    assert any("hashes" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_bootstrap_con_select_general_usuario():
    m = matriz()
    m["principales_futuros"]["bootstrap"]["permiso_select_total_usuario"] = "PERMITIR"
    assert any("bootstrap" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_llamar_procedimientos_legacy():
    m = matriz()
    m["principales_futuros"]["comercial"]["puede_llamar_sp_legacy"] = True
    assert any("heredados" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_abrir_call_tenant_antes_de_rls():
    m = matriz()
    m["principales_futuros"]["comercial"]["llamada_sp_tenant"] = "HABILITADA"
    assert any("CALL SaaS" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_quitar_bloqueos_sin_evidencia():
    m = matriz()
    m["controles_bloqueantes"].pop()
    assert any("bloqueantes" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_marcar_bloqueo_resuelto_sin_pg():
    m = matriz()
    m["controles_bloqueantes"][0]["resuelto"] = True
    assert any("cerrado" in error for error in ERRORES(m, ROOT))


def test_5q_rechaza_matriz_con_permisos_amplios():
    m = matriz()
    m["matriz_politicas_futuras"][0]["comercial"] = "SELECT_TODOS"
    assert any("hash" in error or "permiso amplio" in error
               for error in ERRORES(m, ROOT))


def test_5q_sin_escrituras_sql_y_con_prohibiciones_conservadas():
    src = VALIDADOR.read_text(encoding="utf-8")
    m = matriz()
    assert "create_engine(" not in src
    assert "session.execute(" not in src
    assert "psycopg" not in src
    assert "subprocess" not in src
    assert set(m["operaciones_prohibidas_hasta_autorizacion"]) == {
        "CREATE ROLE", "CREATE POLICY", "GRANT", "REVOKE", "ALTER TABLE",
        "INSERT DE SEED", "CALL sp_*_tenant", "SAAS_AUTH_ENABLED=true",
    }
