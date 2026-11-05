"""5BP / ETAPA G - Pruebas adversariales del pipeline y de las herramientas.

Estas pruebas NO necesitan PostgreSQL ni red: verifican que las herramientas
fallan CERRADO antes de tocar nada. Son las que impiden que un tercero aplique
por accidente las migraciones demo, un manifiesto sin hashes, una fase huerfana o
un destino protegido.

Cubre:
* ``86_preflight_migracion_pg17_5bp.py``: manifiesto, checksums, orden,
  clasificacion, firmas demo, puertos/base prohibidos, plan determinista.
* ``87_comparar_catalogo_historico_5bp.py``: sin catalogo autorizado no hay
  veredicto; comparacion por objeto; solo loopback.
* ``88_ensayo_backup_restore_5bp.py``: guardas de destino y detector de
  diferencias de inventario.
* ``90_laboratorio_pg17_5bp.py``: guardas del laboratorio.
* ``app/core/saas_invitation_rate_limit.py``: identificadores no reversibles y
  confianza de proxy.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

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


@pytest.fixture(scope="module")
def preflight():
    return _cargar("86_preflight_migracion_pg17_5bp.py", "preflight_5bp_test")


@pytest.fixture(scope="module")
def comparador():
    return _cargar("87_comparar_catalogo_historico_5bp.py", "comparador_5bp_test")


@pytest.fixture(scope="module")
def respaldo():
    return _cargar("88_ensayo_backup_restore_5bp.py", "respaldo_5bp_test")


@pytest.fixture(scope="module")
def laboratorio():
    return _cargar("90_laboratorio_pg17_5bp.py", "lab_5bp_test")


@pytest.fixture()
def manifiesto(preflight):
    return json.loads(json.dumps(preflight.cargar_manifiesto()))


# ---------------------------------------------------------------------------
# Manifiesto y pipeline
# ---------------------------------------------------------------------------


def test_manifiesto_real_valida(preflight):
    resultado = preflight.validar_manifiesto(preflight.cargar_manifiesto())
    assert resultado["ok"], resultado["errores"]
    assert resultado["clasificacion"]["sin_clasificar"] == []


def test_manifiesto_no_es_aplicable_a_produccion(preflight):
    datos = preflight.cargar_manifiesto()
    assert datos["estado"] == "CANDIDATO_LAB"
    assert datos["aplicable_a_produccion"] is False
    assert datos["aplicable_a_staging_real"] is False
    assert datos["motivos_no_produccion"], "Debe declarar por que no es producible"


def test_demo_excluida_del_plan(preflight, manifiesto):
    demo = "supabase/migrations/20261023000400_cuatro_tenants_demo_local.sql"
    plan = preflight.planificar(manifiesto, solo_laboratorio=True)
    archivos = [a for fase in plan for a in fase["archivos"]]
    assert demo not in archivos
    exclusiones = {e["archivo"]: e for e in manifiesto["exclusiones"]}
    assert exclusiones[demo]["categoria"] == "datos_demo"
    assert "produccion" in exclusiones[demo]["prohibido_en"]


def test_rechaza_demo_si_se_agrega_una_fase(preflight, manifiesto):
    demo = "supabase/migrations/20261023000400_cuatro_tenants_demo_local.sql"
    manifiesto["fases"].append({
        "id": "F99-demo", "orden": 999, "operacion": "sql", "tipo": "sql",
        "archivos": [demo], "requisitos": ["F13-invitaciones-01000"],
    })
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("demo prohibido" in e for e in resultado["errores"])


def test_rechaza_fase_sin_checksum(preflight, manifiesto):
    manifiesto["checksums"].pop("supabase/migrations/20261030001000_saas_invitaciones_rate_limit_5bp.sql")
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("Sin SHA-256" in e for e in resultado["errores"])


def test_rechaza_checksum_divergente(preflight, manifiesto):
    clave = "supabase/migrations/20261030000100_saas_core_portable.sql"
    manifiesto["checksums"][clave] = "0" * 64
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("Checksum divergente" in e for e in resultado["errores"])


def test_rechaza_requisito_huerfano(preflight, manifiesto):
    manifiesto["fases"][3]["requisitos"] = ["F-INEXISTENTE"]
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("huerfano" in e for e in resultado["errores"])


def test_rechaza_orden_no_monotonico(preflight, manifiesto):
    manifiesto["fases"][2]["orden"] = 5
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("orden ascendente" in e or "no es anterior" in e
               for e in resultado["errores"])


def test_rechaza_motor_menor_a_17(preflight, manifiesto):
    manifiesto["motor_esperado"]["version_minima"] = 16
    resultado = preflight.validar_manifiesto(manifiesto)
    assert not resultado["ok"]
    assert any("PostgreSQL 17" in e for e in resultado["errores"])


def test_clasificacion_detecta_migracion_sin_clasificar(preflight, manifiesto, tmp_path,
                                                        monkeypatch):
    destino = tmp_path / "migrations"
    destino.mkdir()
    for archivo in preflight.archivos_referenciados(manifiesto):
        if Path(archivo).parent.name == "migrations":
            (destino / Path(archivo).name).write_text("-- vacio\n", encoding="utf-8")
    for archivo in ("20261023000100_saas_base_tables.sql",
                    "20261023000200_vanter_tenancy_historica.sql",
                    "20261023000300_integridad_tenant_local.sql",
                    "20261023000400_cuatro_tenants_demo_local.sql"):
        (destino / archivo).write_text("-- vacio\n", encoding="utf-8")
    (destino / "20990101000000_nueva_sin_revisar.sql").write_text(
        "-- nueva\n", encoding="utf-8")
    monkeypatch.setattr(preflight, "DIR_MIGRACIONES", destino)
    clasificacion = preflight.clasificar_migraciones(manifiesto)
    assert clasificacion["sin_clasificar"] == ["20990101000000_nueva_sin_revisar.sql"]
    resultado = preflight.validar_manifiesto(manifiesto, exigir_checksums=False)
    assert not resultado["ok"]
    assert any("sin clasificar" in e for e in resultado["errores"])


def test_rechaza_firma_demo_en_archivo_incluido(preflight, manifiesto, tmp_path,
                                                monkeypatch):
    destino = tmp_path / "migrations"
    destino.mkdir()
    monkeypatch.setattr(preflight, "DIR_MIGRACIONES", destino)
    ruta = tmp_path / "fase_demo.sql"
    ruta.write_text("-- cuatro_tenants_demo\nSELECT 1;\n", encoding="utf-8")
    manifiesto["fases"][-1]["archivos"] = [str(ruta)]
    resultado = preflight.validar_manifiesto(manifiesto, exigir_checksums=False)
    assert not resultado["ok"]
    assert any("firma demo" in e for e in resultado["errores"])


def test_plan_es_determinista_y_ordenado(preflight, manifiesto):
    uno = preflight.planificar(manifiesto)
    otro = preflight.planificar(manifiesto)
    assert uno == otro
    ordenes = [fase["orden"] for fase in uno]
    assert ordenes == sorted(ordenes)
    assert len(uno) == len(manifiesto["fases"])
    solo_prod = preflight.planificar(manifiesto, solo_laboratorio=False)
    assert len(solo_prod) < len(uno)
    assert all(not fase["solo_laboratorio"] for fase in solo_prod)


# ---------------------------------------------------------------------------
# Guardas de destino
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("puerto", [5432, 55443, 55444, 55445, 55442])
def test_rechaza_puertos_protegidos(preflight, puerto):
    with pytest.raises(preflight.MigracionError):
        preflight.validar_destino("127.0.0.1", puerto, "fashionstore_lab_x")


def test_rechaza_host_no_loopback(preflight):
    with pytest.raises(preflight.MigracionError):
        preflight.validar_destino("10.0.0.5", 55446, "fashionstore_lab_x")


@pytest.mark.parametrize("base", ["postgres", "produccion", "fashionstore"])
def test_rechaza_base_sin_marcador(preflight, base):
    with pytest.raises(preflight.MigracionError):
        preflight.validar_destino("127.0.0.1", 55446, base)


def test_acepta_laboratorio_valido(preflight):
    preflight.validar_destino("127.0.0.1", 55446, "fashionstore_lab_pg17_5bp")


def test_aplicar_sin_confirmacion_falla(preflight, monkeypatch, capsys):
    monkeypatch.setenv("FASHIONSTORE_LAB_5BP_DB_PASSWORD", "x")
    codigo = preflight.main(["--aplicar-lab", "--base", "fashionstore_lab_x"])
    assert codigo == 2
    assert "confirm-desechable" in capsys.readouterr().err


def test_aplicar_sin_password_falla(preflight, monkeypatch, capsys):
    monkeypatch.delenv("FASHIONSTORE_LAB_5BP_DB_PASSWORD", raising=False)
    codigo = preflight.main(["--aplicar-lab", "--base", "fashionstore_lab_x",
                             "--confirm-desechable"])
    assert codigo == 2
    assert "FASHIONSTORE_LAB_5BP_DB_PASSWORD" in capsys.readouterr().err


def test_plan_sin_conexion_sale_cero(preflight, capsys):
    assert preflight.main(["--plan"]) == 0
    salida = capsys.readouterr().out
    assert "VALIDACION ESTATICA: OK" in salida


# ---------------------------------------------------------------------------
# Comparador de catalogo
# ---------------------------------------------------------------------------


def test_comparador_sin_origen_no_declara_veredicto(comparador, capsys):
    codigo = comparador.main(["--json"])
    assert codigo == 3
    salida = json.loads(capsys.readouterr().out)
    assert salida["estado"] == "BLOQUEADO POR EVIDENCIA NO DISPONIBLE"
    assert salida["bloqueo"] == "B1"


def test_comparador_rechaza_origen_inexistente(comparador, tmp_path, capsys):
    codigo = comparador.main(["--origen-catalogo", str(tmp_path / "no_existe.json")])
    assert codigo == 3
    assert json.loads(capsys.readouterr().out)["estado"].startswith("BLOQUEADO")


def test_comparador_solo_loopback(comparador):
    with pytest.raises(comparador.ComparacionError):
        comparador._solo_loopback("postgresql://u:p@db.produccion.local:5432/x")


def _catalogo(tablas, rutinas, filas):
    return {
        "meta": {"version_servidor": "PostgreSQL 17.7"},
        "objetos": {
            "tablas": tablas,
            "rutinas": rutinas,
            "triggers": [],
            "politicas": [],
            "propietarios": [],
            "roles": [],
            "extensiones": [],
            "vistas": [],
            "columnas_empresa_id": [],
            "privilegios_rutinas": [],
        },
        "conteos": filas,
    }


def test_comparador_detecta_objetos_y_atributos(comparador):
    origen = _catalogo(
        [["public", "usuario", True, True]],
        [["public", "fn_x", "funcion", True, "owner_hist", "search_path=public",
          "integer"]],
        {"tablas_public": 41})
    destino = _catalogo(
        [["public", "usuario", True, False]],
        [["public", "fn_x", "funcion", True, "saas_owner", "<sin search_path>",
          "integer"],
         ["public", "fn_extra", "funcion", False, "postgres", "<sin>", ""]],
        {"tablas_public": 48})
    resultado = comparador.comparar(origen, destino)
    assert resultado["sin_diferencias"] is False
    assert resultado["resumen"]["solo_destino"] == 1
    assert resultado["resumen"]["atributos_distintos"] == 2
    assert resultado["solo_destino"] == ["rutina:public.fn_extra()"]
    atributos = {d["objeto"]: d["atributos"] for d in resultado["distintos"]}
    assert "force_rls" in atributos["tabla:public.usuario"]
    assert set(atributos["rutina:public.fn_x(integer)"]) == {"propietario",
                                                            "search_path"}
    # Un conteo igual no puede ocultar objetos distintos.
    iguales = comparador.comparar(origen, origen)
    assert iguales["sin_diferencias"] is True


# ---------------------------------------------------------------------------
# Respaldo y restauracion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("puerto", [5432, 55445, 55443])
def test_respaldo_rechaza_puertos_protegidos(respaldo, puerto):
    with pytest.raises(respaldo.RespaldoError):
        respaldo.validar_destino(puerto, "fashionstore_lab_x")


def test_respaldo_rechaza_origen_sin_marcador(respaldo):
    with pytest.raises(respaldo.RespaldoError):
        respaldo.validar_destino(55446, "fashionstore")


def test_respaldo_acepta_laboratorio(respaldo):
    respaldo.validar_destino(55446, "fashionstore_lab_pg17_5bp")


def test_detector_de_diferencias(respaldo):
    base = {
        "tablas": ["public.a", "public.b"], "triggers": ["t1"],
        "rutinas": [{"firma": "f()", "security_definer": True,
                     "propietario": "o", "search_path": "pg_catalog"}],
        "politicas": ["p1"], "secuencias": ["s1"], "extensiones": ["plpgsql:1.0"],
        "filas": {"public.a": 1, "public.b": 2},
        "propietarios_tablas": {"public.a": "postgres"},
    }
    roto = json.loads(json.dumps(base))
    roto["tablas"] = ["public.a"]
    roto["filas"] = {"public.a": 1}
    diferencias = respaldo._diferencias(base, roto)
    assert any(d["conjunto"] == "tablas" and d["tipo"] == "faltan"
               for d in diferencias)
    assert any(d["conjunto"] == "filas" for d in diferencias)

    alterado = json.loads(json.dumps(base))
    alterado["rutinas"][0]["propietario"] = "otro"
    assert any(d["conjunto"] == "rutinas" and d["tipo"] == "atributo_distinto"
               for d in respaldo._diferencias(base, alterado))

    assert respaldo._diferencias(base, base) == []


# ---------------------------------------------------------------------------
# Laboratorio PostgreSQL 17
# ---------------------------------------------------------------------------


def test_laboratorio_puerto_fijo(laboratorio):
    assert laboratorio.PUERTO_LAB == 55446
    assert laboratorio.PUERTO_LAB not in laboratorio.PUERTOS_PROHIBIDOS
    assert laboratorio.BASE_LAB == "fashionstore_lab_pg17_5bp"


def test_laboratorio_rechaza_pg18(tmp_path, monkeypatch, laboratorio):
    falso = tmp_path / "bin"
    falso.mkdir()
    for nombre in ("postgres", "pg_dump"):
        script = falso / f"{nombre}.exe"
        script.write_text("", encoding="utf-8")
    monkeypatch.setattr(laboratorio, "_candidatos_bin",
                        lambda: [falso])
    monkeypatch.setattr(
        laboratorio.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": "postgres (PostgreSQL) 18.6",
                                       "returncode": 0})())
    with pytest.raises(laboratorio.LabError):
        laboratorio.version_pg17()


# ---------------------------------------------------------------------------
# B9 - identificadores del rate limit
# ---------------------------------------------------------------------------


def _request(host: str, cabeceras: dict | None = None) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/auth/tenant/invitaciones/aceptar",
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (cabeceras or {}).items()],
        "client": (host, 12345),
    })


def test_huellas_no_reversibles_y_estables():
    from app.core.saas_invitation_rate_limit import huella_ip, huella_token

    token = "token-de-prueba-de-32-caracteres-minimo"
    assert huella_token(token) == hashlib.sha256(
        token.encode()).hexdigest()[:16]
    assert len(huella_token(token)) == 16
    assert huella_ip("127.0.0.1") == huella_ip("127.0.0.1")
    assert huella_ip("127.0.0.1") != huella_ip("127.0.0.2")
    assert len(huella_ip("127.0.0.1")) == 32
    assert "127.0.0.1" not in huella_ip("127.0.0.1")


def test_xff_solo_de_proxy_confiable(monkeypatch):
    from app.core.config import settings
    from app.core.saas_invitation_rate_limit import _ip_cliente

    monkeypatch.setattr(settings, "saas_invitation_trusted_proxies", "10.0.0.1")
    # Cliente arbitrario: el encabezado se ignora.
    arbitrario = _request("203.0.113.9", {"x-forwarded-for": "1.2.3.4"})
    assert _ip_cliente(arbitrario) == "203.0.113.9"
    # Proxy listado: se acepta el primer salto.
    proxy = _request("10.0.0.1", {"x-forwarded-for": "1.2.3.4, 10.0.0.9"})
    assert _ip_cliente(proxy) == "1.2.3.4"
