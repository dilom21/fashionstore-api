"""5BJ: migraciones SaaS portables y arnes de staging desechable.

Controles sin PostgreSQL (siempre) y prueba de integracion opcional cuando
existe FASHIONSTORE_STAGING_DSN apuntando a una base descartable.
"""
from __future__ import annotations

import os
import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "supabase" / "migrations"
ARNES = ROOT / "database" / "saas" / "70_staging_saas_desde_cero_5bj.py"
PREFLIGHT = ROOT / "database" / "saas" / "71_preflight_migracion_saas_solo_lectura.sql"
POST = ROOT / "database" / "saas" / "72_verificacion_post_migracion_saas_solo_lectura.sql"

PORTABLES = (
    "20261030000100_saas_core_portable.sql",
    "20261030000200_saas_empresa_id_portable.sql",
    "20261030000300_saas_integridad_tenant_portable.sql",
)
LAB = (
    "20261023000100_saas_base_tables.sql",
    "20261023000200_vanter_tenancy_historica.sql",
    "20261023000300_integridad_tenant_local.sql",
    "20261023000400_cuatro_tenants_demo_local.sql",
)

MODULO = runpy.run_path(str(ARNES))
EMPRESAS = MODULO["EMPRESAS"]
BASE_SAAS = MODULO["BASE_SAAS"]
SEMILLA = MODULO["SEMILLA"]


def _sql(nombre: str) -> str:
    return (MIGRACIONES / nombre).read_text(encoding="utf-8")


def _sql_sin_comentarios(nombre: str) -> str:
    return "\n".join(
        linea for linea in _sql(nombre).splitlines()
        if not linea.lstrip().startswith("--")
    )


def test_5bj_existen_las_tres_migraciones_portables():
    for nombre in PORTABLES:
        assert (MIGRACIONES / nombre).is_file(), nombre
    assert list(PORTABLES) == MODULO["BLOQUE"]


def test_5bj_laboratorio_y_portables_son_archivos_distintos():
    for nombre in LAB:
        assert (MIGRACIONES / nombre).is_file()
    assert not set(LAB) & set(PORTABLES)
    # Las portables se ordenan despues de las del laboratorio.
    assert PORTABLES[0] > LAB[-1][:14]


def test_5bj_portables_no_dependen_del_nombre_de_base_ni_conteos_fijos():
    prohibidos = (
        "current_database()",
        "fashionstore_restore_20261023_193554_594",
        "v_tablas_public <>",
        "v_trigger_count",
        "v_total <>",
        "v_n <>",
    )
    for nombre in PORTABLES:
        texto = _sql_sin_comentarios(nombre)
        for patron in prohibidos:
            assert patron not in texto, f"{nombre} conserva {patron}"


def test_5bj_nucleo_es_idempotente_y_siembra_solo_dos_planes():
    texto = _sql(PORTABLES[0])
    assert texto.count("CREATE TABLE IF NOT EXISTS public.") == 5
    assert "ON CONFLICT (codigo) DO NOTHING" in texto
    assert "ENABLE ROW LEVEL SECURITY" in texto
    assert "'BASICO'" in texto and "'PROFESIONAL'" in texto
    assert "permite_reportes_ia" in texto
    # Nunca crea empresas, membresias ni suscripciones reales.
    assert "INSERT INTO public.empresa" not in texto
    assert "INSERT INTO public.usuario_empresa" not in texto
    assert "INSERT INTO public.suscripcion" not in texto


def test_5bj_tenencia_exige_tenant_explicito_y_retira_default():
    texto = _sql(PORTABLES[1])
    assert "saas.bootstrap_empresa_slug" in texto
    assert "ADD COLUMN empresa_id BIGINT" in texto
    assert "DROP DEFAULT" in texto
    for tabla in EMPRESAS:
        assert f"'{tabla}'" in texto, tabla
    # El tenant no puede quedar implicito en escrituras nuevas.
    assert "SET NOT NULL" in texto
    assert "saas.bootstrap_crear_membresias" in texto


def test_5bj_integridad_descubre_por_catalogo_y_no_por_nombres_fijos():
    texto = _sql(PORTABLES[2])
    assert "pg_catalog.pg_constraint" in texto
    assert "contype = 'f'" in texto
    assert "contype = 'u'" in texto
    assert "uq_tenant_" in texto and "fk_tenant_" in texto
    assert "v_huerfanos" in texto
    assert "RAISE EXCEPTION" in texto
    # No puede confiar en que el cliente aporte empresa_id para autorizar.
    assert "usuario_empresa_composite" in texto


def test_5bj_arnes_cubre_las_31_tablas_empresariales():
    assert len(EMPRESAS) == 31
    assert len(set(EMPRESAS)) == 31
    sembradas = {tabla for tabla, _ in SEMILLA}
    assert set(EMPRESAS) <= sembradas
    # Ademas de las 31 empresariales se siembran catalogos globales.
    assert sembradas - set(EMPRESAS) == {
        "rol", "ciudad", "color", "talla", "modulo", "accion", "funcion",
        "usuario",
    }
    assert BASE_SAAS == [
        "empresa", "plan_saas", "suscripcion", "usuario_empresa",
        "empresa_configuracion",
    ]


def test_5bj_arnes_rechaza_destinos_no_descartables():
    seguro = MODULO["_dsn_seguro"]
    error = MODULO["StagingError"]
    seguro("postgresql://postgres@127.0.0.1:55440/fashionstore_staging_x", False)
    with pytest.raises(error):
        seguro("postgresql://postgres@db.zyitwlxfkphthlvlmuej.supabase.co:5432/postgres", False)
    with pytest.raises(error):
        seguro("postgresql://postgres@127.0.0.1:5432/fashionstore", False)
    # Solo con autorizacion explicita deja de exigir el patron de nombre.
    seguro("postgresql://postgres@127.0.0.1:5432/fashionstore", True)


def test_5bj_scripts_de_migracion_son_solo_lectura():
    for ruta in (PREFLIGHT, POST):
        texto = ruta.read_text(encoding="utf-8")
        assert ruta.is_file()
        assert "SELECT" in texto
        for escritura in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ", "DROP ", "TRUNCATE"):
            assert escritura not in texto.upper(), (ruta.name, escritura)


def test_5bj_preflight_detecta_riesgos_antes_de_migrar():
    texto = PREFLIGHT.read_text(encoding="utf-8")
    for marcador in (
        "empresa_id",
        "fk_tenant",
        "uq_tenant",
        "relrowsecurity",
        "duplicados",
        "triggers",
    ):
        assert marcador.lower() in texto.lower(), marcador


@pytest.mark.skipif(
    not os.environ.get("FASHIONSTORE_STAGING_DSN"),
    reason="Requiere FASHIONSTORE_STAGING_DSN a una base descartable",
)
def test_5bj_integracion_staging_real():
    resultado = MODULO["run"](
        dsn=os.environ["FASHIONSTORE_STAGING_DSN"],
        slug="staging-historico",
        allow_remote=False,
        crear_membresias=True,
    )
    assert resultado["primera"]["ok"], resultado["primera"]["fallos"]
    assert resultado["primera"]["fk_tenant"] >= 40
    assert resultado["primera"]["uq_tenant"] >= 25
    assert resultado["primera"]["fk_cruzada_rechazada"] is True
    assert resultado["segunda"]["idempotente"] is True
