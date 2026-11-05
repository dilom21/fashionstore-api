"""5BR / Etapa I - Pruebas OFFLINE de contrato (no requieren PostgreSQL).

Cubren lo que puede verificarse sin cluster:
* el manifiesto 98 valida (fases, orden, checksums, clasificacion de migraciones);
* las migraciones nuevas 00550/01100 no desactivan RLS ni borran objetos;
* el runner 96 solo acepta loopback/puerto 55448 y rechaza SQL no confiable;
* el comparador 97 clasifica objetos originales vs reconstruccion vs migrado;
* la semilla usa exclusivamente los dominios CHECK del esquema ORIGINAL.

La evidencia de ejecucion real (restauracion, pipeline, A/B y HTTP) vive en
``tests_saas/test_http_ab_esquema_real_5br.py`` y en
``database/saas/99_ensayo_esquema_real_5br.py``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAAS = ROOT / "database" / "saas"
MIGRACIONES = ROOT / "supabase" / "migrations"
MANIFIESTO_5BR = SAAS / "98_manifiesto_migracion_esquema_real_5br.json"
MIGRACION_00550 = MIGRACIONES / "20261030000550_saas_compatibilidad_esquema_real_5br.sql"
MIGRACION_01100 = MIGRACIONES / "20261030001100_saas_acl_plataforma_esquema_real_5br.sql"
SEED_5BR = SAAS / "99_semilla_esquema_real_5br.sql"


def _cargar(relativa: str, alias: str):
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, ROOT / relativa)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def preflight():
    return _cargar("database/saas/86_preflight_migracion_pg17_5bp.py", "preflight_5br_off")


@pytest.fixture(scope="module")
def comparador():
    return _cargar("database/saas/97_comparar_esquema_real_5br.py", "comp_5br_off")


@pytest.fixture(scope="module")
def lab5br():
    return _cargar("database/saas/96_laboratorio_esquema_real_5br.py", "lab_5br_off")


# ---------------------------------------------------------------------------
# Manifiesto y pipeline
# ---------------------------------------------------------------------------


def test_manifiesto_5br_es_valido(preflight):
    manifiesto = json.loads(MANIFIESTO_5BR.read_text(encoding="utf-8"))
    validacion = preflight.validar_manifiesto(manifiesto, exigir_checksums=True)
    assert validacion["ok"], validacion["errores"]
    assert validacion["clasificacion"]["sin_clasificar"] == []


def test_manifiesto_no_incluye_la_reconstruccion_ni_los_seeds_demo(preflight):
    manifiesto = json.loads(MANIFIESTO_5BR.read_text(encoding="utf-8"))
    incluidos = {a for f in manifiesto["fases"] for a in f["archivos"]}
    assert "database/saas/75_esquema_historico_reconstruido_5bl.sql" not in incluidos
    demo = [a for a in incluidos if "20261023000400" in a]
    assert demo == []
    ids = [f["id"] for f in manifiesto["fases"]]
    assert "F00R-esquema-real-verificado" in ids
    assert "F02R-semilla-esquema-real" in ids
    assert not any(i.startswith(("F00-historico", "F01-historico")) for i in ids)


def test_fases_en_orden_y_con_requisitos_previos(preflight):
    manifiesto = json.loads(MANIFIESTO_5BR.read_text(encoding="utf-8"))
    fases = manifiesto["fases"]
    ordenes = [f["orden"] for f in fases]
    assert ordenes == sorted(ordenes)
    por_id = {f["id"]: f["orden"] for f in fases}
    for fase in fases:
        for requisito in fase.get("requisitos", []):
            assert por_id[requisito] < fase["orden"]


def test_migracion_de_compatibilidad_esta_en_el_manifiesto_con_checksum(preflight):
    manifiesto = json.loads(MANIFIESTO_5BR.read_text(encoding="utf-8"))
    relativa = "supabase/migrations/" + MIGRACION_00550.name
    refs = {a for f in manifiesto["fases"] for a in f["archivos"]}
    assert relativa in refs
    assert manifiesto["checksums"][relativa] == preflight.sha256_archivo(relativa)


def test_migracion_de_acl_esta_en_el_manifiesto_con_checksum(preflight):
    manifiesto = json.loads(MANIFIESTO_5BR.read_text(encoding="utf-8"))
    relativa = "supabase/migrations/" + MIGRACION_01100.name
    refs = {a for f in manifiesto["fases"] for a in f["archivos"]}
    assert relativa in refs
    assert manifiesto["checksums"][relativa] == preflight.sha256_archivo(relativa)


# ---------------------------------------------------------------------------
# Guardas de las migraciones nuevas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [MIGRACION_00550, MIGRACION_01100])
def test_migraciones_no_debilitan_rls_ni_borran_objetos(ruta):
    texto = ruta.read_text(encoding="utf-8").upper()
    assert "DISABLE ROW LEVEL SECURITY" not in texto
    assert "NO FORCE ROW LEVEL SECURITY" not in texto
    assert "DROP TABLE" not in texto
    assert "DROP FUNCTION" not in texto
    assert "DROP TRIGGER" not in texto
    assert "COMMIT;" in texto


def test_00550_recrea_solo_politicas_propias():
    texto = MIGRACION_00550.read_text(encoding="utf-8")
    assert "DROP POLICY IF EXISTS catalogo_lectura_saas" in texto
    # No elimina politicas ajenas: solo la que esta migracion define.
    assert len(re.findall(r"DROP POLICY IF EXISTS", texto)) == 1


def test_00550_declara_los_objetos_originales_y_los_diccionarios():
    texto = MIGRACION_00550.read_text(encoding="utf-8")
    for nombre in ("token_recuperacion", "trg_normalizar_sku",
                   "sp_ajustar_inventario", "catalogo_lectura_saas",
                   "compatibilidad_esquema_real", "validar_compatibilidad_esquema_real"):
        assert nombre in texto
    # Las listas nominales deben conservar los conteos del origen.
    assert "cardinality(v_tablas) <> 41" in texto
    assert "cardinality(v_triggers) <> 27" in texto
    assert "cardinality(v_rutinas) <> 24" in texto


def test_00550_revoca_roles_de_plataforma_sin_tocar_el_runtime():
    texto = MIGRACION_00550.read_text(encoding="utf-8")
    assert "anon" in texto and "authenticated" in texto and "service_role" in texto
    assert "REVOKE ALL ON TABLE" in texto
    assert "SECURITY DEFINER" in texto  # se rechazan DEFINER en trigger functions
    assert "fs_saas_runtime" not in re.sub(r"COALESCE\(NULLIF\(current_setting.*",
                                           "", texto, flags=re.S)[:0] or texto


def test_01100_revoca_default_privileges_heredados():
    texto = MIGRACION_01100.read_text(encoding="utf-8")
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE" in texto
    assert "REVOKE ALL ON %s FROM %I" in texto
    assert "ARRAY['TABLES', 'SEQUENCES', 'FUNCTIONS']" in texto
    for duenio in ("postgres", "supabase_admin"):
        assert duenio in texto
    assert "validar_acl_plataforma_5br" in texto


# ---------------------------------------------------------------------------
# Runner 96: guardas de destino e inspeccion del dump
# ---------------------------------------------------------------------------


def test_runner_5br_usa_puerto_exclusivo_y_no_otros_laboratorios(lab5br):
    assert lab5br.PUERTO_LAB == 55448
    assert lab5br.BASE_LAB == "fashionstore_lab_5br_esquema_real"
    for puerto in (5432, 55442, 55443, 55444, 55445, 55446, 55447):
        assert puerto in lab5br.PUERTOS_PROHIBIDOS
    assert 55448 not in lab5br.PUERTOS_PROHIBIDOS


def test_runner_5br_fija_el_hash_del_insumo(lab5br):
    assert lab5br.HASH_ORIGEN_ESPERADO == (
        "1cbe562bc3939db74354127658978e6dd13a82a205c29b6d455b2b2bca36f80e")
    assert lab5br.HASH_CATALOGO_ESPERADO == (
        "a18c9cb673036abe82664b7977ec53a68fab85e709933f34b4a6b039bc98aa23")
    assert lab5br.CONTEOS_ORIGEN == {"tablas": 41, "triggers": 27,
                                     "rutinas": 24, "vistas": 1}


def test_inspeccion_rechaza_sql_no_confiable(lab5br, tmp_path):
    sql = tmp_path / "peligroso.sql"
    sql.write_text("DROP DATABASE produccion;\nCREATE TABLE public.x (id int);\n",
                   encoding="utf-8")
    with pytest.raises(lab5br.LabError):
        lab5br.leer_sql_limpio(sql)


def test_inspeccion_rechaza_conteo_de_tablas_inesperado(lab5br, tmp_path):
    sql = tmp_path / "incompleto.sql"
    sql.write_text("CREATE TABLE public.x (id int);\n", encoding="utf-8")
    with pytest.raises(lab5br.LabError):
        lab5br.leer_sql_limpio(sql)


def test_inspeccion_quita_meta_comandos_psql(lab5br, tmp_path):
    cuerpo = "\\restrict abc\n" + "\n".join(
        f"CREATE TABLE public.t{i} (id bigint);" for i in range(41)) + "\n\\unrestrict abc\n"
    sql = tmp_path / "dump.sql"
    sql.write_text(cuerpo, encoding="utf-8")
    limpio, inspeccion = lab5br.leer_sql_limpio(sql)
    assert "\\restrict" not in limpio and "\\unrestrict" not in limpio
    assert inspeccion["create_table"] == 41
    assert inspeccion["sentencias_prohibidas"] == []


def test_verificar_insumo_detecta_hash_alterado(lab5br, tmp_path):
    archivo = tmp_path / "x.sql"
    archivo.write_text("-- nada\n", encoding="utf-8")
    with pytest.raises(lab5br.LabError):
        lab5br.verificar_insumo(archivo, "0" * 64)


# ---------------------------------------------------------------------------
# Comparador 97
# ---------------------------------------------------------------------------


def _catalogo(tablas=(), triggers=(), rutinas=(), vistas=()):
    return {
        "meta": {}, "conteos": {},
        "objetos": {
            "tablas": [["public", t, True, False] for t in tablas],
            "vistas": [["public", v, "true"] for v in vistas],
            "columnas_empresa_id": [],
            "rutinas": [["public", r, "funcion", False, "postgres",
                         "search_path=public, pg_temp", ""] for r in rutinas],
            "triggers": [["public", "t", n, "f", False, "O"] for n in triggers],
            "politicas": [], "propietarios": [], "roles": [], "extensiones": [],
            "privilegios_rutinas": [],
        },
    }


def test_comparador_identifica_la_tabla_ausente(comparador, monkeypatch):
    reconstruccion = {"tablas": {"usuario"}, "rutinas": {"fn_a"},
                      "triggers": {"trg_a"}, "vistas": {"vw"}}
    origen = _catalogo(tablas=["usuario", "token_recuperacion"],
                       triggers=["trg_a"], rutinas=["fn_a"], vistas=["vw"])
    resultado = comparador.comparar_reconstruccion(origen, reconstruccion)
    assert resultado["tablas_ausentes"] == ["token_recuperacion"]
    assert resultado["triggers_ausentes"] == []
    assert resultado["coincide"] is False


def test_comparador_detecta_search_path_distinto(comparador):
    origen = _catalogo(tablas=["usuario"], rutinas=["fn_registrar_bitacora"])
    lab = _catalogo(tablas=["usuario"], rutinas=["fn_registrar_bitacora"])
    lab["objetos"]["rutinas"][0][5] = "search_path=pg_catalog, public, pg_temp"
    resultado = comparador.comparar_migrado(origen, lab)
    assert resultado["compatible"] is True
    assert resultado["clasificacion_diferencias"] == {"ADAPTAR A TENANT": 1}
    assert resultado["cobertura_original"] == 1.0


def test_comparador_marca_riesgo_cuando_desaparece_un_objeto(comparador):
    origen = _catalogo(tablas=["usuario"], rutinas=["fn_original"])
    lab = _catalogo(tablas=["usuario"])
    resultado = comparador.comparar_migrado(origen, lab)
    assert resultado["compatible"] is False
    assert resultado["riesgos_sin_resolver"][0]["objeto"] == "rutina:fn_original"
    assert resultado["clasificacion_diferencias"] == {"RIESGO SIN RESOLVER": 1}


def test_comparador_no_confunde_conteos_iguales_con_objetos_iguales(comparador):
    origen = _catalogo(tablas=["usuario"], triggers=["trg_bitacora_usuario"])
    lab = _catalogo(tablas=["usuario"], triggers=["trg_bitacora_venta"])
    resultado = comparador.comparar_migrado(origen, lab)
    assert resultado["compatible"] is False
    assert len(resultado["riesgos_sin_resolver"]) == 1


# ---------------------------------------------------------------------------
# Semilla y dominios CHECK del esquema ORIGINAL
# ---------------------------------------------------------------------------


def _sql_sin_comentarios(ruta: Path) -> str:
    return "\n".join(line for line in ruta.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))


def test_semilla_usa_los_dominios_del_esquema_original():
    texto = _sql_sin_comentarios(SEED_5BR)
    # Valores prohibidos por los CHECK reales (los usaba la reconstruccion).
    assert "'TIENDA'" not in texto
    assert "'CONFIRMADO'" not in texto
    assert "'ENTRADA'," not in texto
    assert "'ACTIVA'," not in texto  # reserva/prueba usan PENDIENTE/INICIADA
    # Valores admitidos por el origen.
    assert "'PRESENCIAL'" in texto
    assert "'ENTRADA_COMPRA'" in texto
    assert "'PNG_2D'" in texto
    assert "'INICIADA'" in texto
    assert "'PENDIENTE'" in texto


def test_semilla_no_contiene_datos_personales_reales():
    texto = SEED_5BR.read_text(encoding="utf-8")
    assert "@lab5br.invalid" not in texto
    assert "@staging.invalid" in texto
    assert "DROP " not in texto.upper()
    assert "DELETE FROM" not in texto.upper()


def test_semilla_no_usa_columnas_que_el_esquema_original_no_tiene():
    texto = SEED_5BR.read_text(encoding="utf-8")
    # `reponer` lo agrega recien 20261030000700; la semilla historica corre antes.
    assert "reponer" not in texto
