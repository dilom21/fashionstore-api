"""5M: 12 controles de regresión STATIC-ONLY de identidad y límites RLS.

No conecta a PostgreSQL ni importa app.core.database (que crea engine).
No ejecuta ningún .sql.txt ni habilita AUTH SaaS.
"""
from pathlib import Path
import runpy
import re

ROOT = Path(__file__).resolve().parents[1]
REVISOR = ROOT / "database/saas/20_revision_estatica_contrato_rls_5m.py"
INFORME = ROOT / "database/saas/28_b3_p5m_contrato_identidad_politicas_rls_no_ejecutable.md"
CFG = ROOT / "app/core/config.py"
GATE = ROOT / "app/core/saas_route_gate.py"
DRAFTS = [
    ROOT / "database/saas/borradores/12_sp_crear_reserva_tenant.sql.txt",
    ROOT / "database/saas/borradores/13_sp_cancelar_reserva_tenant.sql.txt",
    ROOT / "database/saas/borradores/14_sp_finalizar_reserva_sin_compra_tenant.sql.txt",
]


def herramienta():
    return runpy.run_path(str(REVISOR))


def test_5m_revisor_solo_fuentes_no_conexiones_sql():
    s = REVISOR.read_text(encoding="utf-8")
    assert "read_text(encoding=" in s
    assert not re.search(r"(?m)^\s*(?:import|from)\s+(?:sqlalchemy|psycopg|requests|httpx|subprocess)\b", s)
    assert ".env" not in s.split("RUTAS = {")[1].split("}")[0]
    assert "create_engine(" not in s
    assert "connect(" not in s


def test_5m_revisor_localiza_fuentes_reales():
    d = herramienta()["RUTAS"]
    for clave in ("dependencias", "contexto", "transacciones", "login",
                  "repositorio_auth", "gate", "config",
                  "sp_crear", "sp_cancelar", "sp_finalizar"):
        assert clave in d
        assert (ROOT / d[clave]).is_file()


def test_5m_informacion_de_guarda_estructurada():
    r = herramienta()["evaluar"](ROOT)
    assert set(r) == {"integridad", "pendientes_de_diseno", "evidencia", "advertencia"}
    assert all(isinstance(v, bool) for v in r["integridad"].values())
    assert all(isinstance(v, bool) for v in r["pendientes_de_diseno"].values())


def test_5m_gates_previos_deben_seguir_intactos():
    r = herramienta()["evaluar"](ROOT)
    assert r["integridad"]["flag_default_inactivo"]
    assert r["integridad"]["rutas_transaccionales_reserva_bloqueadas"]
    assert "saas_auth_enabled: bool = False" in CFG.read_text(encoding="utf-8")


def test_5m_login_membresia_auditable_sin_suponer_rls():
    r = herramienta()["evaluar"](ROOT)
    assert r["integridad"]["login_token_y_membresia"]
    assert r["integridad"]["contexto_empresa_posterior_membresia"]
    assert "bootstrap_login_sin_politicas_definidas" in r["pendientes_de_diseno"]
    assert "consulta_login_previa_al_tenant" in r["evidencia"]


def test_5m_documenta_actor_ip_sin_tratarlos_como_identidad():
    d = INFORME.read_text(encoding="utf-8")
    for clave in ("app.usuario_id", "app.empresa_id", "app.ip", "no autentican",
                  "backend", "BYPASSRLS"):
        assert clave in d


def test_5m_revisor_detecta_riesgos_actor_en_transacciones():
    r = herramienta()["evaluar"](ROOT)
    assert "actor_auditoria_no_automatizado_en_dependencia_saas" in r["pendientes_de_diseno"]
    assert "actor_no_reaplicado_automaticamente_tras_nueva_transaccion" in r["pendientes_de_diseno"]
    assert "GUC" in r["advertencia"]


def test_5m_tres_sp_permancen_inactivos():
    r = herramienta()["evaluar"](ROOT)
    assert r["integridad"]["tres_sp_aun_borradores_security_invoker"]
    assert r["evidencia"]["cantidad_sp_inactivos"] == 3
    for path in DRAFTS:
        s = path.read_text(encoding="utf-8")
        assert "SECURITY INVOKER" in s
        assert "IN p_empresa_id bigint" in s
        assert "IN p_usuario_id bigint" in s


def test_5m_suplantacion_de_p_usuario_id_explicitada():
    d = INFORME.read_text(encoding="utf-8")
    assert "p_usuario_id" in d
    assert "p_empresa_id" in d
    assert "suplantación" in d or "suplantado" in d
    assert "no autentican" in d


def test_5m_doc_separa_bootstrap_de_operacion_comercial():
    d = INFORME.read_text(encoding="utf-8")
    for s in ("Etapa A: autenticación controlada", "Etapa B: operaciones empresariales",
              "NO superusuario", "WITH CHECK", "USING", "bitacora"):
        assert s in d


def test_5m_doc_sin_sql_ejecutable_y_flag_bloqueado():
    d = INFORME.read_text(encoding="utf-8")
    for s in ("NO EJECUTABLE", "SAAS_AUTH_ENABLED=false", "412 passed",
              "CREATE ROLE", "CREATE POLICY", "RLS NO", "NO modificar"):
        assert s in d
    assert not re.search(r"(?m)^\s*CREATE\s+(?:POLICY|ROLE)", d)
    g = GATE.read_text(encoding="utf-8")
    assert '("POST", "/tenant/reservas")' not in g


def test_5m_main_solo_imprime_y_no_habilita_nada(capsys):
    m = herramienta()
    assert m["main"]() == 0
    salida = capsys.readouterr().out
    assert "REVISION ESTATICA LOCAL" in salida
    assert "5M ANALISIS DE CODIGO COMPLETADO" in salida
    assert "RLS REAL TODAVIA NO VALIDADO" in salida
