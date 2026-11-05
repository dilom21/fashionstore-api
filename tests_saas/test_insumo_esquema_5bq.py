"""5BQ / ETAPA H - Pruebas del validador del insumo historico (B1).

Estas pruebas NO necesitan PostgreSQL, red ni archivos del origen. Usan
fixtures SINTETICOS para comprobar que el validador:

* acepta un dump schema-only de PG 17 y rechaza datos, versiones distintas y
  archivos que no son pg_dump;
* no confunde el cuerpo de una funcion que contiene un INSERT con datos
  exportados (por eso quita los bloques dollar-quoted antes de escanear);
* valida el contrato exacto que consume ``87_comparar_catalogo_historico_5bp``;
* rechaza catalogo con BOM (87 lo lee con utf-8 y fallaria), con aridad
  incorrecta, con firma incoherente y con apariencia de laboratorio local;
* clasifica el informe de 87 separando diferencias de plataforma de las de
  aplicacion.

Un catalogo sintetico que "cuadre" con 41/27/24/1 no es evidencia del esquema
real: solo demuestra que la herramienta no se equivoca al aceptarlo o
rechazarlo. B1 depende del archivo autorizado, no de estas pruebas.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAAS = ROOT / "database" / "saas"


def _cargar(nombre: str, alias: str):
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, SAAS / nombre)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[alias] = modulo
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    return modulo


validador = _cargar("94_validar_insumo_esquema_5bq.py", "validador_5bq")


# ---------------------------------------------------------------------------
# Fixtures sinteticas
# ---------------------------------------------------------------------------

DUMP_VALIDO = """--
-- PostgreSQL database dump
--

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.7

SET statement_timeout = 0;
SET client_encoding = 'UTF8';

CREATE TABLE public.cliente (
    id bigint NOT NULL,
    correo text NOT NULL
);

CREATE FUNCTION public.fn_bitacora() RETURNS trigger
    LANGUAGE plpgsql
    AS $function$
BEGIN
INSERT INTO public.bitacora (evento) VALUES (TG_OP);
    RETURN NEW;
END
$function$;

CREATE TRIGGER trg_bitacora BEFORE INSERT ON public.cliente
    FOR EACH ROW EXECUTE FUNCTION public.fn_bitacora();

CREATE VIEW public.vw_cliente AS SELECT id FROM public.cliente;
"""


def _escribir(ruta: Path, texto: str, bom: bool = False) -> Path:
    datos = texto.encode("utf-8")
    if bom:
        datos = b"\xef\xbb\xbf" + datos
    ruta.write_bytes(datos)
    return ruta


def _catalogo_sintetico(tablas: int = 41, triggers: int = 27,
                        rutinas: int = 24, vistas: int = 1,
                        base: str = "postgres",
                        version: str = "PostgreSQL 17.6") -> dict:
    objetos = {
        "tablas": [["public", f"t_{i:02d}", False, False] for i in range(tablas)],
        "vistas": [["public", f"v_{i:02d}", "<no definido>"] for i in range(vistas)],
        "columnas_empresa_id": [["public", "t_00"]],
        "rutinas": [
            ["public", f"fn_{i:02d}", "funcion", False, "postgres",
             "<sin search_path>", ""]
            for i in range(rutinas)
        ],
        "triggers": [
            ["public", f"t_{i % max(tablas, 1):02d}", f"trg_{i:02d}", "fn_00",
             False, "O"]
            for i in range(triggers)
        ],
        "politicas": [],
        "propietarios": [["public", f"t_{i:02d}", "postgres"]
                         for i in range(tablas)],
        "roles": [["postgres", True, True, True, True]],
        "extensiones": [["plpgsql", "1.0", "pg_catalog"]],
        "privilegios_rutinas": [],
    }
    return {
        "meta": {
            "version_servidor": version,
            "base": base,
            "usuario": "postgres",
            "capturado_utc": "2026-10-31T15:00:00+00:00",
            "procedimiento": "prueba sintetica",
        },
        "objetos": objetos,
        "conteos": {
            "tablas_public": tablas,
            "triggers_public": triggers,
            "rutinas_public": rutinas,
            "vistas_public": vistas,
        },
    }


# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------


def test_dump_schema_only_pg17_es_apto(tmp_path):
    ruta = _escribir(tmp_path / "esquema.sql", DUMP_VALIDO)
    resultado = validador.validar_dump(ruta)

    assert resultado["apto"] is True, resultado["errores"]
    assert resultado["datos"]["version_origen"] == "17.6"
    assert resultado["datos"]["version_pg_dump"] == "17.7"
    assert resultado["datos"]["conteos_heuristicos"]["create_table"] == 1
    assert resultado["datos"]["conteos_heuristicos"]["create_trigger"] == 1
    assert resultado["datos"]["conteos_heuristicos"]["create_vista"] == 1


def test_insert_dentro_de_cuerpo_de_funcion_no_es_dato(tmp_path):
    """El cuerpo dollar-quoted contiene 'INSERT INTO' al inicio de linea."""
    ruta = _escribir(tmp_path / "esquema.sql", DUMP_VALIDO)
    resultado = validador.validar_dump(ruta)

    assert resultado["apto"] is True, resultado["errores"]


def test_dump_con_copy_from_stdin_es_rechazado(tmp_path):
    texto = DUMP_VALIDO + "\nCOPY public.cliente (id, correo) FROM stdin;\n1\ta@b.test\n\\.\n"
    ruta = _escribir(tmp_path / "con_datos.sql", texto)
    resultado = validador.validar_dump(ruta)

    assert resultado["apto"] is False
    assert any("sentencias de datos" in e for e in resultado["errores"])


def test_dump_version_18_es_rechazado(tmp_path):
    texto = DUMP_VALIDO.replace("version 17.6", "version 18.2")
    ruta = _escribir(tmp_path / "pg18.sql", texto)
    resultado = validador.validar_dump(ruta)

    assert resultado["apto"] is False
    assert any("PostgreSQL 18.2" in e for e in resultado["errores"])


def test_dump_utf16_es_rechazado(tmp_path):
    ruta = tmp_path / "utf16.sql"
    ruta.write_bytes(DUMP_VALIDO.encode("utf-16"))
    with pytest.raises(validador.InsumoInvalido, match="UTF-16"):
        validador.validar_dump(ruta)


def test_archivo_que_no_es_pg_dump_es_rechazado(tmp_path):
    ruta = _escribir(tmp_path / "otro.sql", "CREATE TABLE x (id int);\n")
    resultado = validador.validar_dump(ruta)

    assert resultado["apto"] is False
    assert any("cabecera de pg_dump" in e for e in resultado["errores"])


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------


def test_catalogo_coherente_con_referencia_es_apto(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json",
                     json.dumps(_catalogo_sintetico()))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is True, resultado["errores"]
    assert resultado["hallazgos_vs_referencia"] == []
    assert resultado["datos"]["firma_calculada"]


def test_catalogo_con_conteos_distintos_genera_hallazgos(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json",
                     json.dumps(_catalogo_sintetico(tablas=40, triggers=14, rutinas=12)))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is True
    assert len(resultado["hallazgos_vs_referencia"]) == 3


def test_catalogo_con_aridad_incorrecta_es_rechazado(tmp_path):
    catalogo = _catalogo_sintetico()
    catalogo["objetos"]["tablas"][0] = ["public", "t_00"]  # faltan columnas
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(catalogo))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is False
    assert any("aridad fija" in e for e in resultado["errores"])


def test_catalogo_con_firma_incoherente_es_rechazado(tmp_path):
    catalogo = _catalogo_sintetico()
    catalogo["_firma"] = "0" * 32
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(catalogo))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is False
    assert any("_firma declarada" in e for e in resultado["errores"])


def test_catalogo_con_bom_es_rechazado_porque_87_falla(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json",
                     json.dumps(_catalogo_sintetico()), bom=True)
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is False
    assert any("BOM UTF-8" in e for e in resultado["errores"])


def test_catalogo_de_laboratorio_es_rechazado(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(
        _catalogo_sintetico(base="fashionstore_lab_pg17_5bp")))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is False
    assert any("laboratorio local" in e for e in resultado["errores"])


def test_catalogo_de_version_no_17_es_rechazado(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(
        _catalogo_sintetico(version="PostgreSQL 18.2")))
    resultado = validador.validar_catalogo(ruta)

    assert resultado["apto"] is False
    assert any("17.x" in e for e in resultado["errores"])


def test_firma_rechazada_detecta_autocomparacion(tmp_path):
    catalogo = _catalogo_sintetico()
    firma = validador.firma_catalogo(catalogo["objetos"])
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(catalogo))
    resultado = validador.validar_catalogo(ruta, firma_rechazada=firma)

    assert resultado["apto"] is False
    assert any("autocomparacion" in e for e in resultado["errores"])


def test_salida_de_psql_con_encabezado_es_rechazada(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.txt",
                     "catalogo\n{...}\n(1 fila)\n")
    with pytest.raises(validador.InsumoInvalido, match="psql"):
        validador.validar_catalogo(ruta)


# ---------------------------------------------------------------------------
# Informe de 87 y plan de comparacion
# ---------------------------------------------------------------------------


def test_clasificacion_separa_plataforma_de_aplicacion(tmp_path):
    informe = {
        "veredicto": "CON DIFERENCIAS",
        "resultado": {
            "distintos": [
                {"objeto": "rutina:public.sp_crear_reserva_desde_carrito()",
                 "clase": "rutina", "atributos": ["search_path"]},
            ],
            "solo_origen": [
                "rol:authenticated",
                "rol:service_role",
                "extension:pgcrypto",
                "tabla:public.bitacora",
                "trigger:public.cliente.trg_faltante",
            ],
            "solo_destino": [
                "rol:fs_saas_runtime",
                "tabla:saas.empresa",
            ],
        },
    }
    ruta = _escribir(tmp_path / "informe.json", json.dumps(informe))
    clasificado = validador.clasificar_informe(ruta)

    detalle = clasificado["detalle"]
    assert detalle["solo_origen"]["plataforma"] == [
        "extension:pgcrypto", "rol:authenticated", "rol:service_role"]
    assert detalle["solo_origen"]["aplicacion"] == [
        "tabla:public.bitacora", "trigger:public.cliente.trg_faltante"]
    assert detalle["solo_destino"]["plataforma"] == []
    assert detalle["atributos_distintos"]["aplicacion"] == [
        "rutina:public.sp_crear_reserva_desde_carrito()"]
    # 2 de solo_origen + 2 de solo_destino + 1 atributo distinto
    assert clasificado["diferencias_de_aplicacion"] == 5
    assert clasificado["diferencias_de_plataforma"] == 3


def test_plan_de_comparacion_incluye_dump_y_salida(tmp_path):
    comando = validador.plan_comparacion(
        tmp_path / "catalogo.json", tmp_path / "esquema.sql")[0]

    assert "87_comparar_catalogo_historico_5bp.py" in comando
    assert "--origen-dump" in comando
    assert "--origen-catalogo" in comando


def test_main_devuelve_5_si_el_insumo_no_es_apto(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(
        _catalogo_sintetico(base="fashionstore_lab_5bp")))

    assert validador.main(["--catalogo", str(ruta)]) == 5


def test_main_devuelve_4_con_discrepancias(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(
        _catalogo_sintetico(tablas=40)))

    assert validador.main(["--catalogo", str(ruta)]) == 4


def test_main_devuelve_0_con_insumo_coherente(tmp_path):
    ruta = _escribir(tmp_path / "catalogo.json", json.dumps(_catalogo_sintetico()))

    assert validador.main(["--catalogo", str(ruta)]) == 0
