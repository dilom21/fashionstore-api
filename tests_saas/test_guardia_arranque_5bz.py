"""5BZ: guardia fail-closed de arranque (sin red, sin PostgreSQL real).

El incidente 5BY fue un `uvicorn` que heredo `DATABASE_URL` de produccion: el
staging "local" habria trabajado contra un DSN ajeno. Estos casos fijan la
matriz adversarial de `app.core.launch_guard.validar_arranque` usando SOLO
hosts y credenciales ficticias (`*.invalid`, `10.255.255.1`).

Cero sockets: cada caso adverso se ejecuta con `socket.socket.connect`,
`socket.socket.connect_ex`, `socket.create_connection` y `socket.getaddrinfo`
instrumentados. Si la guardia intentara dialogar con la red, el test fallaria
con `AssertionError("conexion de red")` en lugar de `ArranqueInseguro`.

Nota: `socket.socket` es un tipo C, asi que `connect` no se puede reemplazar en
la clase existente; se sustituye la clase completa por una subclase que
sobrescribe `connect`/`connect_ex` y se parchean las funciones del modulo.
`create_connection` resuelve `socket` por el nombre global del modulo, por lo
que tambien queda cubierta por la subclase.
"""
from __future__ import annotations

import contextlib
import os
import socket

import pytest

from app.core.launch_guard import (
    ArranqueInseguro,
    analizar_dsn,
    dsn_publico,
    validar_arranque,
)

# DSN sinteticos: el unico destino aceptado en modo estricto.
DSN_SINTETICO = (
    "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
    "127.0.0.1:55460/fashionstore_lab_5bx_staging"
)
DSN_SINTETICO_ADMIN = (
    "postgresql+psycopg://fs_saas_admin:clave_ficticia@"
    "127.0.0.1:55460/fashionstore_lab_5bx_staging"
)
DSN_REMOTO_DOMINIO = (
    "postgresql://usuario_ficticio:clave_ficticia@"
    "db.remoto-ficticio.invalid:5432/fashionstore_lab_5bx_staging"
)
DSN_REMOTO_IP = (
    "postgresql+psycopg://usuario_ficticio:clave_ficticia@"
    "10.255.255.1:5432/fashionstore_lab_5bx_staging"
)
DSN_SQLITE = "sqlite+pysqlite:///./saas_auth_unit_test.db"
DSN_COMPARTIDO = (
    "postgresql+psycopg://usuario_ficticio:clave_ficticia@"
    "db.compartido-ficticio.invalid:5432/postgres"
)
ENTORNO_COMPARTIDO = {
    "SAAS_SHARED_DB_DEV_ENABLED": "true",
    "SAAS_SHARED_DB_HOST": "db.compartido-ficticio.invalid",
    "SAAS_SHARED_DB_PORT": "5432",
    "SAAS_SHARED_DB_NAME": "postgres",
}

ESTRICTO = {"SAAS_LOCAL_ONLY": "1"}
LOCAL = {"SAAS_LOCAL_ONLY": "0"}


@contextlib.contextmanager
def sin_red(monkeypatch: pytest.MonkeyPatch):
    """Instrumenta sockets; cualquier intento de red lanza AssertionError."""
    registro = {"sockets": 0}
    original = socket.socket

    class SocketBloqueado(original):  # type: ignore[misc, valid-type]
        def connect(self, *args, **kwargs):
            registro["sockets"] += 1
            raise AssertionError("conexion de red")

        def connect_ex(self, *args, **kwargs):
            registro["sockets"] += 1
            raise AssertionError("conexion de red")

        def sendto(self, *args, **kwargs):
            registro["sockets"] += 1
            raise AssertionError("conexion de red")

    def bloqueo(*args, **kwargs):
        registro["sockets"] += 1
        raise AssertionError("conexion de red")

    with monkeypatch.context() as mp:
        mp.setattr(socket, "socket", SocketBloqueado)
        mp.setattr(socket, "create_connection", bloqueo)
        mp.setattr(socket, "getaddrinfo", bloqueo)
        yield registro


# (nombre, app_env, database_url, saas_admin_database_url, entorno, motivo)
CASOS_ADVERSOS = [
    (
        "dsn_remoto_dominio",
        "development",
        DSN_REMOTO_DOMINIO,
        None,
        dict(ESTRICTO),
        "host",
    ),
    (
        "dsn_remoto_ip",
        "development",
        DSN_REMOTO_IP,
        None,
        dict(ESTRICTO),
        "host",
    ),
    (
        "admin_remoto",
        "development",
        DSN_SINTETICO,
        DSN_REMOTO_DOMINIO,
        dict(ESTRICTO),
        "SAAS_ADMIN_DATABASE_URL",
    ),
    (
        "puerto_no_autorizado",
        "development",
        "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "127.0.0.1:5432/fashionstore_lab_5bx_staging",
        None,
        dict(ESTRICTO),
        "puerto",
    ),
    (
        "base_fuera_de_prefijo",
        "development",
        "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "127.0.0.1:55460/base_ajena",
        None,
        dict(ESTRICTO),
        "prefijo",
    ),
    (
        "parametro_host_en_query",
        "development",
        DSN_SINTETICO + "?host=10.255.255.1",
        None,
        dict(ESTRICTO),
        "parametro",
    ),
    (
        "pghost_definido",
        "development",
        DSN_SINTETICO,
        None,
        {**ESTRICTO, "PGHOST": "10.255.255.1"},
        "PGHOST",
    ),
    (
        "sqlite_en_estricto",
        "development",
        DSN_SQLITE,
        None,
        dict(ESTRICTO),
        "sqlite",
    ),
    (
        "localhost_literal",
        "development",
        "postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
        "localhost:55460/fashionstore_lab_5bx_staging",
        None,
        dict(ESTRICTO),
        "host",
    ),
    (
        "incidente_5by",
        "development",
        DSN_REMOTO_DOMINIO,
        None,
        dict(LOCAL),
        "host",
    ),
]


@pytest.mark.parametrize(
    "nombre, app_env, database_url, admin_url, entorno, motivo",
    CASOS_ADVERSOS,
    ids=[caso[0] for caso in CASOS_ADVERSOS],
)
def test_matriz_adversa_cero_sockets(
    monkeypatch, nombre, app_env, database_url, admin_url, entorno, motivo
):
    with sin_red(monkeypatch) as registro:
        with pytest.raises(ArranqueInseguro) as excinfo:
            validar_arranque(
                app_env=app_env,
                database_url=database_url,
                saas_admin_database_url=admin_url,
                entorno=entorno,
            )
        assert registro["sockets"] == 0, f"{nombre}: la guardia abrio sockets"

    mensaje = str(excinfo.value)
    assert mensaje.startswith("Arranque rechazado:"), mensaje
    assert motivo.lower() in mensaje.lower(), (nombre, mensaje)
    assert "clave_ficticia" not in mensaje
    assert "usuario_ficticio" not in mensaje
    assert DSN_REMOTO_DOMINIO not in mensaje
    assert DSN_REMOTO_IP not in mensaje


def test_acepta_dsn_sintetico_en_estricto(monkeypatch):
    with sin_red(monkeypatch) as registro:
        validar_arranque(
            app_env="staging-5bx-local",
            database_url=DSN_SINTETICO,
            saas_admin_database_url=DSN_SINTETICO_ADMIN,
            entorno=dict(ESTRICTO),
        )
    assert registro["sockets"] == 0


def test_acepta_sqlite_en_modo_local_implicito(monkeypatch):
    with sin_red(monkeypatch) as registro:
        validar_arranque(
            app_env="development",
            database_url=DSN_SQLITE,
            saas_admin_database_url=None,
            entorno=dict(LOCAL),
        )
    assert registro["sockets"] == 0


def test_sqlite_en_estricto_menciona_la_regla():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="development",
            database_url=DSN_SQLITE,
            saas_admin_database_url=None,
            entorno=dict(ESTRICTO),
        )
    assert "sqlite" in str(excinfo.value)
    assert "SAAS_LOCAL_ONLY" in str(excinfo.value)


def test_desplegado_sin_allowlist_explicita_es_rechazado():
    # 5C0 supersede el 5BZ "modo desplegado acepta cualquier remoto": ahora un
    # despliegue exige identidad exacta declarada en SAAS_DEPLOY_*.
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="production",
            database_url=DSN_REMOTO_DOMINIO,
            saas_admin_database_url=DSN_REMOTO_DOMINIO,
            entorno={},
        )
    assert "SAAS_DEPLOY" in str(excinfo.value)


def test_bypass_legacy_no_permite_remoto_en_local():
    with pytest.raises(ArranqueInseguro):
        validar_arranque(
            app_env="development",
            database_url=DSN_REMOTO_IP,
            saas_admin_database_url=None,
            entorno={"SAAS_ALLOW_REMOTE_DB": "true"},
        )


def test_desarrollo_compartido_autorizado_para_los_tres_pools():
    for nombre in ("DATABASE_URL", "DATABASE_BOOTSTRAP_URL", "DATABASE_PLATFORM_URL"):
        validar_arranque(
            app_env="development",
            database_url=DSN_COMPARTIDO if nombre == "DATABASE_URL" else DSN_SINTETICO,
            database_bootstrap_url=DSN_COMPARTIDO if nombre == "DATABASE_BOOTSTRAP_URL" else None,
            database_platform_url=DSN_COMPARTIDO if nombre == "DATABASE_PLATFORM_URL" else None,
            saas_admin_database_url=None,
            entorno=ENTORNO_COMPARTIDO,
        )
    validar_arranque(
        app_env="development",
        database_url=DSN_COMPARTIDO.replace(":5432/", "/"),
        saas_admin_database_url=None,
        entorno=ENTORNO_COMPARTIDO,
    )


@pytest.mark.parametrize(
    "cambio, motivo",
    [
        ({"SAAS_SHARED_DB_HOST": "otro.invalid"}, "host"),
        ({"SAAS_SHARED_DB_PORT": "5433"}, "host"),
        ({"SAAS_SHARED_DB_NAME": "otra_base"}, "host"),
        ({"SAAS_SHARED_DB_DEV_ENABLED": "false"}, "host"),
    ],
)
def test_desarrollo_compartido_rechaza_identidad_incorrecta(cambio, motivo):
    entorno = {**ENTORNO_COMPARTIDO, **cambio}
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="development",
            database_url=DSN_COMPARTIDO,
            saas_admin_database_url=None,
            entorno=entorno,
        )
    assert motivo in str(excinfo.value).lower()


def test_postgresql_sin_puerto_usa_5432_y_puerto_explicito_se_preserva():
    assert analizar_dsn("postgresql://u:p@db.example.invalid/postgres")["puerto"] == 5432
    assert analizar_dsn("postgresql://u:p@db.example.invalid:6543/postgres")["puerto"] == 6543


def test_entorno_del_proceso_sobrescribe_el_env(monkeypatch):
    # Pydantic Settings aplica la precedencia entorno > archivo .env. Solo se
    # inspecciona host/puerto/base; nunca se muestra el DSN ni sus credenciales.
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class ProbeSettings(BaseSettings):
        database_url: str

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

    heredada = "postgresql+psycopg://u:p@pooler.example.invalid:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", heredada)
    ajustes = ProbeSettings()
    datos = analizar_dsn(ajustes.database_url)
    assert (datos["host"], datos["puerto"], datos["base"]) == (
        "pooler.example.invalid",
        5432,
        "postgres",
    )


def test_puerto_allowlist_configurable(monkeypatch):
    with sin_red(monkeypatch):
        validar_arranque(
            app_env="development",
            database_url="postgresql+psycopg://fs_saas_runtime:clave_ficticia@"
            "127.0.0.1:65432/fashionstore_lab_5bx_staging",
            saas_admin_database_url=None,
            entorno={
                "SAAS_LOCAL_ONLY": "1",
                "SAAS_LOCAL_ALLOWED_PORTS": "55460, 65432",
            },
        )


def test_analizar_dsn_no_devuelve_contrasena():
    datos = analizar_dsn(DSN_REMOTO_DOMINIO)
    assert datos["esquema"] == "postgresql"
    assert datos["host"] == "db.remoto-ficticio.invalid"
    assert datos["puerto"] == 5432
    assert datos["base"] == "fashionstore_lab_5bx_staging"
    assert datos["usuario"] == "usuario_ficticio"
    assert "clave_ficticia" not in str(datos)

    publico = dsn_publico(DSN_REMOTO_DOMINIO)
    assert publico == (
        "postgresql://db.remoto-ficticio.invalid:5432/"
        "fashionstore_lab_5bx_staging"
    )
    assert "usuario_ficticio" not in publico


@pytest.mark.parametrize(
    "dsn, familia",
    [
        ("postgresql://u:p@127.0.0.1:55460/fashionstore_lab_x", "postgresql"),
        ("postgres://u:p@127.0.0.1:55460/fashionstore_lab_x", "postgres"),
        (
            "postgresql+psycopg://u:p@127.0.0.1:55460/fashionstore_lab_x",
            "postgresql+psycopg",
        ),
        (
            "postgresql+psycopg2://u:p@127.0.0.1:55460/fashionstore_lab_x",
            "postgresql+psycopg2",
        ),
    ],
)
def test_analizar_dsn_soporta_los_esquemas_documentados(dsn, familia):
    datos = analizar_dsn(dsn)
    assert datos["esquema"] == familia
    assert datos["host"] == "127.0.0.1"
    assert datos["puerto"] == 55460


def test_dsn_sin_host_es_rechazado():
    with pytest.raises(ArranqueInseguro) as excinfo:
        validar_arranque(
            app_env="development",
            database_url="postgresql:///fashionstore_lab_5bx_staging",
            saas_admin_database_url=None,
            entorno=dict(ESTRICTO),
        )
    assert "host" in str(excinfo.value)


def test_no_importa_config_para_evitar_ciclo():
    """La guardia debe poder usarse sin resolver settings ni crear engine."""
    from pathlib import Path

    import app.core.launch_guard as guardia

    origen = Path(guardia.__file__).read_text(encoding="utf-8")
    assert "from app.core.config" not in origen
    assert "import app.core.config" not in origen
