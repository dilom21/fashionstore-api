"""5C1: separacion de superficies legacy/SaaS y guardia de coexistencia.

No abre sockets ni conexiones: la guardia solo lee configuracion y el DSN se
valida por texto. Los casos "desplegado" usan un entorno ficticio.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./saas_auth_unit_test.db")
os.environ.setdefault("SECRET_KEY", "clave-ficticia-exclusiva-para-test-saas")
os.environ.setdefault("SAAS_AUTH_ENABLED", "false")

import pytest

from app.core.launch_guard import ArranqueInseguro
from app.core.surface_guard import (
    SURFACE_LEGACY,
    SURFACE_TENANT,
    destinos_distintos,
    superficies_activas,
    superficie_de_ruta,
    validar_coexistencia,
)

DSN_SAAS = "postgresql+psycopg://saas:pw@127.0.0.1:55460/fashionstore_lab_saas"
DSN_LEGACY = "postgresql+psycopg://legacy:pw@127.0.0.1:55460/fashionstore_lab_legacy"
DSN_OTRA_BASE = "postgresql+psycopg://legacy:pw@127.0.0.1:55460/fashionstore_lab_otra"
DSN_FUERA_PREFIJO = "postgresql+psycopg://legacy:pw@127.0.0.1:55460/otra_base"

ENTORNO_DESPLEGUE = {
    "SAAS_DEPLOY_ENV": "production",
    "SAAS_DEPLOY_DB_HOST": "saas.db.example.com",
    "SAAS_DEPLOY_DB_PORT": "5432",
    "SAAS_DEPLOY_DB_NAME": "fashionstore",
    "SAAS_DEPLOY_DB_USER": "fs_saas_runtime",
    "SAAS_DEPLOY_DENY_HOSTS": "legacy-viejo.db.example.com",
    "LEGACY_DEPLOY_DB_HOST": "legacy.db.example.com",
    "LEGACY_DEPLOY_DB_PORT": "5432",
    "LEGACY_DEPLOY_DB_NAME": "fashionstore_legacy",
    "LEGACY_DEPLOY_DB_USER": "fs_legacy_runtime",
}
DSN_DESPLEGUE_SAAS = (
    "postgresql+psycopg://fs_saas_runtime:pw@saas.db.example.com:5432/fashionstore"
)
DSN_DESPLEGUE_LEGACY = (
    "postgresql+psycopg://fs_legacy_runtime:pw@legacy.db.example.com:5432/"
    "fashionstore_legacy"
)


@pytest.mark.parametrize(
    "ruta,esperada",
    [
        ("/tenant/inventario", SURFACE_TENANT),
        ("/tenant/catalogo/productos", SURFACE_TENANT),
        ("/tenant", SURFACE_TENANT),
        ("/auth/tenant/login", SURFACE_TENANT),
        ("/auth/tenant", SURFACE_TENANT),
        ("/saas/superadmin/empresas", SURFACE_TENANT),
        ("/saas", SURFACE_TENANT),
        ("/productos", SURFACE_LEGACY),
        ("/auth/personal/login", SURFACE_LEGACY),
        ("/health/db", SURFACE_LEGACY),
        ("/tenants-legacy", SURFACE_LEGACY),
        ("/tenantx", SURFACE_LEGACY),
        ("/", SURFACE_LEGACY),
        ("/tenant/inventario?x=1", SURFACE_TENANT),
    ],
)
def test_superficie_de_ruta_no_confunde_prefijos(ruta, esperada):
    assert superficie_de_ruta(ruta) == esperada


def test_destinos_distintos_compara_host_puerto_y_base():
    assert destinos_distintos(DSN_SAAS, DSN_LEGACY) is True
    assert destinos_distintos(DSN_SAAS, DSN_OTRA_BASE) is True
    assert destinos_distintos(DSN_SAAS, DSN_SAAS) is False
    # Misma base con distinto usuario: NO es un destino distinto.
    mismo_destino_otro_usuario = DSN_SAAS.replace("saas:pw", "postgres:pw")
    assert destinos_distintos(DSN_SAAS, mismo_destino_otro_usuario) is False
    # Cambiar solo el puerto si es otro destino.
    otro_puerto = DSN_SAAS.replace(":55460", ":55461")
    assert destinos_distintos(DSN_SAAS, otro_puerto) is True


def test_superficies_activas_respeta_orden():
    assert superficies_activas(
        saas_auth_enabled=False, legacy_surface_enabled=True
    ) == (SURFACE_LEGACY,)
    assert superficies_activas(
        saas_auth_enabled=True, legacy_surface_enabled=False
    ) == (SURFACE_TENANT,)
    assert superficies_activas(
        saas_auth_enabled=True, legacy_surface_enabled=True
    ) == (SURFACE_LEGACY, SURFACE_TENANT)
    assert (
        superficies_activas(
            saas_auth_enabled=False, legacy_surface_enabled=False
        )
        == ()
    )


def test_rechaza_arranque_sin_ninguna_superficie():
    with pytest.raises(ArranqueInseguro, match="ninguna superficie"):
        validar_coexistencia(
            saas_auth_enabled=False,
            legacy_surface_enabled=False,
            database_url=DSN_SAAS,
            legacy_database_url=None,
            app_env="development",
            entorno={},
        )


def test_rechaza_legacy_dsn_en_modo_corte():
    with pytest.raises(ArranqueInseguro, match="LEGACY_DATABASE_URL"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=False,
            database_url=DSN_SAAS,
            legacy_database_url=DSN_LEGACY,
            app_env="development",
            entorno={},
        )


def test_rechaza_coexistencia_sin_destino_legacy():
    with pytest.raises(ArranqueInseguro, match="falta LEGACY_DATABASE_URL"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_SAAS,
            legacy_database_url=None,
            app_env="development",
            entorno={},
        )


def test_rechaza_coexistencia_sobre_la_misma_base():
    # Mismo destino aunque cambie el usuario: el aislamiento no depende del rol.
    with pytest.raises(ArranqueInseguro, match="mismo destino"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_SAAS,
            legacy_database_url=DSN_SAAS.replace("saas:pw", "postgres:pw"),
            app_env="development",
            entorno={},
        )


def test_acepta_coexistencia_local_con_bases_distintas():
    validar_coexistencia(
        saas_auth_enabled=True,
        legacy_surface_enabled=True,
        database_url=DSN_SAAS,
        legacy_database_url=DSN_LEGACY,
        app_env="development",
        entorno={},
    )


def test_rechaza_legacy_local_fuera_del_prefijo_desechable():
    with pytest.raises(ArranqueInseguro, match="prefijo"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_SAAS,
            legacy_database_url=DSN_FUERA_PREFIJO,
            app_env="development",
            entorno={},
        )


def test_rechaza_legacy_local_en_puerto_no_autorizado():
    # El puerto solo se restringe en modo estricto (SAAS_LOCAL_ONLY), igual
    # que ya ocurre con DATABASE_URL.
    dsn = DSN_LEGACY.replace(":55460", ":5432")
    with pytest.raises(ArranqueInseguro, match="puerto"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_SAAS,
            legacy_database_url=dsn,
            app_env="development",
            entorno={"SAAS_LOCAL_ONLY": "1"},
        )


def test_modo_desplegado_exige_identidad_legacy_explicita():
    entorno = dict(ENTORNO_DESPLEGUE)
    for clave in (
        "LEGACY_DEPLOY_DB_HOST",
        "LEGACY_DEPLOY_DB_PORT",
        "LEGACY_DEPLOY_DB_NAME",
        "LEGACY_DEPLOY_DB_USER",
    ):
        recortado = dict(entorno)
        recortado.pop(clave)
        with pytest.raises(ArranqueInseguro, match="identidad del destino"):
            validar_coexistencia(
                saas_auth_enabled=True,
                legacy_surface_enabled=True,
                database_url=DSN_DESPLEGUE_SAAS,
                legacy_database_url=DSN_DESPLEGUE_LEGACY,
                app_env="production",
                entorno=recortado,
            )


def test_modo_desplegado_acepta_destinos_declarados():
    validar_coexistencia(
        saas_auth_enabled=True,
        legacy_surface_enabled=True,
        database_url=DSN_DESPLEGUE_SAAS,
        legacy_database_url=DSN_DESPLEGUE_LEGACY,
        app_env="production",
        entorno=ENTORNO_DESPLEGUE,
    )


def test_modo_desplegado_rechaza_legacy_en_host_denegado():
    entorno = dict(ENTORNO_DESPLEGUE)
    entorno["LEGACY_DEPLOY_DB_HOST"] = "legacy-viejo.db.example.com"
    dsn = DSN_DESPLEGUE_LEGACY.replace("legacy.db.example.com", "legacy-viejo.db.example.com")
    with pytest.raises(ArranqueInseguro, match="denegado"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_DESPLEGUE_SAAS,
            legacy_database_url=dsn,
            app_env="production",
            entorno=entorno,
        )


def test_modo_desplegado_rechaza_legacy_loopback():
    entorno = dict(ENTORNO_DESPLEGUE)
    entorno["LEGACY_DEPLOY_DB_HOST"] = "127.0.0.1"
    dsn = DSN_DESPLEGUE_LEGACY.replace("legacy.db.example.com", "127.0.0.1")
    with pytest.raises(ArranqueInseguro, match="loopback"):
        validar_coexistencia(
            saas_auth_enabled=True,
            legacy_surface_enabled=True,
            database_url=DSN_DESPLEGUE_SAAS,
            legacy_database_url=dsn,
            app_env="production",
            entorno=entorno,
        )


def test_modo_legacy_puro_no_exige_ni_admite_legacy_dsn():
    # SaaS apagado: el contrato sigue usando DATABASE_URL como siempre.
    validar_coexistencia(
        saas_auth_enabled=False,
        legacy_surface_enabled=True,
        database_url=DSN_SAAS,
        legacy_database_url=None,
        app_env="development",
        entorno={},
    )


# ---------------------------------------------------------------------------
# Enrutado de sesion por superficie
# ---------------------------------------------------------------------------


def test_fabrica_de_sesiones_usa_el_motor_legacy(monkeypatch):
    from app.core import database as modulo

    fabrica_falsa = object()
    monkeypatch.setattr(modulo, "SessionLegacyLocal", fabrica_falsa)

    assert modulo._fabrica_de_sesiones(SURFACE_LEGACY) is fabrica_falsa
    assert modulo._fabrica_de_sesiones(SURFACE_TENANT) is modulo.SessionLocal


def test_fabrica_de_sesiones_sin_dsn_legacy_comparte_engine_tenant(monkeypatch):
    from app.core import database as modulo

    monkeypatch.setattr(modulo, "SessionLegacyLocal", None)

    assert modulo._fabrica_de_sesiones(SURFACE_LEGACY) is modulo.SessionLocal
    assert modulo._fabrica_de_sesiones(SURFACE_TENANT) is modulo.SessionLocal
