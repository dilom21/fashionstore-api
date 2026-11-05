"""5C0: allowlist exacta de identidad para arranque desplegado (sin red).

Sustituye el comportamiento 5BZ "modo desplegado acepta cualquier remoto" por
una identidad declarada: entorno, host, puerto, base y usuario deben coincidir.
Todos los hosts son ficticios (`*.invalid`); ningun caso abre sockets.
"""
from __future__ import annotations

import pytest

from app.core.launch_guard import ArranqueInseguro, validar_arranque

HOST_STAGING = "db.staging-ficticio.invalid"
HOST_LEGACY = "db.legacy-ficticio.invalid"
HOST_OTRO = "db.otro-ficticio.invalid"
BASE = "fashionstore_saas_staging"


def _dsn(host: str = HOST_STAGING, *, puerto: int = 5432,
         base: str = BASE, usuario: str = "fs_saas_runtime",
         extra: str = "") -> str:
    return (
        f"postgresql+psycopg://{usuario}:clave_ficticia@"
        f"{host}:{puerto}/{base}{extra}"
    )


def _entorno(**cambios: str) -> dict[str, str]:
    entorno = {
        "SAAS_DEPLOY_ENV": "staging",
        "SAAS_DEPLOY_DB_HOST": HOST_STAGING,
        "SAAS_DEPLOY_DB_PORT": "5432",
        "SAAS_DEPLOY_DB_NAME": BASE,
        "SAAS_DEPLOY_DB_USER": "fs_saas_runtime",
        "SAAS_DEPLOY_ADMIN_USER": "saas_admin_runtime",
        "SAAS_DEPLOY_DENY_HOSTS": HOST_LEGACY,
    }
    entorno.update(cambios)
    return entorno


def _admin_dsn(**cambios) -> str:
    parametros = dict(usuario="saas_admin_runtime")
    parametros.update(cambios)
    return _dsn(**parametros)


def test_staging_exacto_es_aceptado():
    validar_arranque(
        app_env="staging",
        database_url=_dsn(),
        saas_admin_database_url=_admin_dsn(),
        entorno=_entorno(),
    )


@pytest.mark.parametrize(
    "cambios,dsn,ruta,esperado",
    [
        ({}, _dsn(host=HOST_OTRO), "DATABASE_URL", "SAAS_DEPLOY_DB_HOST"),
        ({}, _dsn(host=HOST_LEGACY), "DATABASE_URL", "denegado"),
        ({}, _dsn(puerto=5433), "DATABASE_URL", "SAAS_DEPLOY_DB_PORT"),
        ({}, _dsn(base="fashionstore_prod"), "DATABASE_URL",
         "SAAS_DEPLOY_DB_NAME"),
        ({}, _dsn(usuario="postgres"), "DATABASE_URL", "usuario"),
        ({}, _dsn(host="127.0.0.1"), "DATABASE_URL", "loopback"),
        ({}, _dsn(extra="?host=10.255.255.1"), "DATABASE_URL",
         "redirigir"),
        ({"SAAS_DEPLOY_ENV": ""}, _dsn(), "DATABASE_URL", "SAAS_DEPLOY_ENV"),
        ({"SAAS_DEPLOY_DB_HOST": HOST_LEGACY}, _dsn(),
         "DATABASE_URL", "contradictoria"),
    ],
)
def test_configuracion_incorrecta_es_rechazada(cambios, dsn, ruta, esperado):
    entorno = _entorno(**cambios)
    database_url = dsn if ruta == "DATABASE_URL" else _dsn()
    admin = dsn if ruta == "SAAS_ADMIN_DATABASE_URL" else _admin_dsn()
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env=entorno.get("SAAS_DEPLOY_ENV", "staging"),
            database_url=database_url,
            saas_admin_database_url=admin,
            entorno=entorno,
        )
    assert esperado.lower() in str(excinfo.value).lower()


def test_admin_con_usuario_runtime_es_rechazado():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="staging",
            database_url=_dsn(),
            saas_admin_database_url=_dsn(),
            entorno=_entorno(),
        )
    assert "usuario distinto" in str(excinfo.value)


def test_admin_con_usuario_admin_es_aceptado():
    validar_arranque(
        app_env="staging",
        database_url=_dsn(),
        saas_admin_database_url=_admin_dsn(),
        entorno=_entorno(),
    )


def test_allow_remote_db_no_desbloquea_despliegue():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="staging",
            database_url=_dsn(),
            saas_admin_database_url=None,
            entorno=_entorno(SAAS_ALLOW_REMOTE_DB="true"),
        )
    assert "SAAS_ALLOW_REMOTE_DB" in str(excinfo.value)


def test_variable_pg_heredada_rechaza_despliegue():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="staging",
            database_url=_dsn(),
            saas_admin_database_url=None,
            entorno=_entorno(PGHOST=HOST_OTRO),
        )
    assert "PGHOST" in str(excinfo.value)


def test_app_env_distinto_de_deploy_env_es_rechazado():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="production",
            database_url=_dsn(),
            saas_admin_database_url=None,
            entorno=_entorno(),  # SAAS_DEPLOY_ENV=staging
        )
    assert "APP_ENV" in str(excinfo.value)


def test_sin_identidad_declarada_es_rechazado():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="staging",
            database_url=_dsn(),
            saas_admin_database_url=None,
            entorno={},
        )
    assert "SAAS_DEPLOY_ENV" in str(excinfo.value)
