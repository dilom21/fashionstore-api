"""5BZ: guardia fail-closed de arranque contra un DSN remoto heredado.

Incidente 5BY: un proceso ``uvicorn`` lanzado desde la sesion del agente heredo
la variable ``DATABASE_URL`` de produccion. ``pydantic-settings`` da prioridad
al entorno sobre el archivo ``.env``, de modo que el servidor "local" de
staging habria quedado apuntando a un destino ajeno sin que ningun log lo
advirtiera: la configuracion se resuelve una sola vez, al importar
``app.core.config``, y desde ahi todo el proceso (engine, sesiones, tareas)
trabaja contra ese destino.

Principio fail-closed: ante la duda, no arrancar. Si el proceso declara un
entorno local (``SAAS_LOCAL_ONLY`` activo o ``APP_ENV`` de desarrollo/local) el
unico destino aceptable es un PostgreSQL loopback sintetico; cualquier otra
cosa aborta el arranque ANTES de que exista un engine o una conexion. Un
despliegue real (``production``, ``staging``, ...) no pasa por estas reglas.

Limites explicitos:
  * Este modulo solo usa la libreria estandar y NUNCA importa
    ``app.core.config``: la configuracion lo llama desde ``get_settings`` y un
    import inverso crearia un ciclo.
  * No abre sockets ni resuelve nombres: la decision se toma leyendo el DSN y
    el entorno, por lo que validar no tiene efectos de red.
  * Los mensajes nombran la variable y la regla incumplida, nunca el DSN, el
    usuario ni la contrasena.
"""
from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

# Valores que cuentan como "verdadero" al leer banderas del entorno.
VERDAD = frozenset({"1", "true", "yes", "si", "on"})

# APP_ENV que designan una instalacion local, ademas de las variantes que
# contienen "local" o "lab" (por ejemplo "staging-5bx-local").
APPS_LOCALES = frozenset({"development", "dev", "local", "testing", "test"})

FAMILIAS_POSTGRES = frozenset({"postgresql", "postgres"})
HOSTS_LOOPBACK = frozenset({"127.0.0.1", "::1"})

PUERTOS_LOCALES_DEFECTO = "55460"
PREFIJO_BASE_DEFECTO = "fashionstore_lab_"

# Parametros de query que libpq interpreta y que pueden redirigir la conexion
# por encima del host/puerto escritos en el DSN.
PARAMETROS_REDIRIGIBLES = frozenset(
    {"host", "hostaddr", "port", "dbname", "user", "service", "servicefile"}
)

# Variables de libpq/psycopg que suplen o alteran el destino del DSN.
VARIABLES_PG = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGOPTIONS",
)

MODO_ESTRICTO = "estricto"
MODO_LOCAL_IMPLICITO = "local_implicito"
MODO_DESPLEGADO = "desplegado"

# 5C0: un despliegue NO se autoriza con una bandera booleana. Exige identidad
# exacta del destino y del entorno; cualquier diferencia aborta el arranque.
VARIABLES_DESPLEGUE = (
    "SAAS_DEPLOY_ENV",
    "SAAS_DEPLOY_DB_HOST",
    "SAAS_DEPLOY_DB_PORT",
    "SAAS_DEPLOY_DB_NAME",
    "SAAS_DEPLOY_DB_USER",
)
VARIABLES_DESPLEGUE_OPCIONALES = (
    "SAAS_DEPLOY_ADMIN_USER",
    "SAAS_DEPLOY_DENY_HOSTS",
)
USUARIO_ADMIN_DEFECTO = "saas_admin_runtime"


class ArranqueInseguro(RuntimeError):
    """El entorno de arranque no cumple las reglas del modo detectado.

    Se lanza antes de construir engine o abrir conexiones. El mensaje nombra
    la variable y la regla, sin incluir el DSN ni credenciales.
    """


def analizar_dsn(dsn: str) -> dict:
    """Descompone un DSN en sus partes, sin exponer credenciales al llamador.

    Devuelve ``{esquema, host, puerto, base, usuario, parametros}``. Soporta
    ``postgresql://``, ``postgres://``, ``postgresql+psycopg://`` y
    ``postgresql+psycopg2://`` (el esquema se devuelve tal cual, con driver).
    Un DSN malformado devuelve todos los campos vacios en lugar de propagar la
    excepcion de ``urlsplit``: quien valida decide como tratarlo.
    """
    try:
        partes = urlsplit((dsn or "").strip())
        esquema = (partes.scheme or "").lower()
        host = partes.hostname
        usuario = partes.username
        base = unquote((partes.path or "").lstrip("/"))
        parametros = {
            clave.lower(): (valores[0] if valores else "")
            for clave, valores in parse_qs(
                partes.query, keep_blank_values=True
            ).items()
        }
    except ValueError:
        return {
            "esquema": "",
            "host": None,
            "puerto": None,
            "base": "",
            "usuario": None,
            "parametros": {},
        }
    try:
        puerto = partes.port
    except ValueError:
        # Puerto no numerico: se informa como ausente y la validacion lo
        # rechaza; nunca se propaga la excepcion de urlsplit al llamador.
        puerto = None
    return {
        "esquema": esquema,
        "host": host,
        "puerto": puerto,
        "base": base,
        "usuario": unquote(usuario) if usuario else None,
        "parametros": parametros,
    }


def dsn_publico(dsn: str) -> str:
    """``esquema://host:puerto/base`` sin usuario ni contrasena.

    Pensado para logs, reportes de evidencia y mensajes al operador.
    """
    datos = analizar_dsn(dsn)
    esquema = datos["esquema"] or "?"
    base = datos["base"] or ""
    if not datos["host"]:
        return f"{esquema}:///{base}"
    puerto = f":{datos['puerto']}" if datos["puerto"] else ""
    return f"{esquema}://{datos['host']}{puerto}/{base}"


def modo_arranque(app_env: str, entorno: Mapping[str, str] | None = None) -> str:
    """Modo efectivo de arranque: estricto, local implicito o desplegado."""
    ambiente = os.environ if entorno is None else entorno
    if es_verdad(ambiente.get("SAAS_LOCAL_ONLY")):
        return MODO_ESTRICTO
    normalizado = (app_env or "").strip().lower()
    if (
        normalizado in APPS_LOCALES
        or "local" in normalizado
        or "lab" in normalizado
    ):
        return MODO_LOCAL_IMPLICITO
    return MODO_DESPLEGADO


def es_verdad(valor: str | None) -> bool:
    """``True`` para las banderas textuales aceptadas (case-insensitive)."""
    return (valor or "").strip().lower() in VERDAD


def validar_arranque(
    *,
    app_env: str,
    database_url: str,
    saas_admin_database_url: str | None,
    entorno: Mapping[str, str] | None = None,
) -> None:
    """Aborta con ``ArranqueInseguro`` si el destino no es el permitido.

    Se invoca desde ``app.core.config.get_settings`` (por tanto al importar el
    modulo, antes de cualquier engine) y desde el lanzador 119 antes de crear
    el subproceso. No abre sockets ni conexiones: solo lee el DSN y el entorno.
    """
    ambiente = os.environ if entorno is None else entorno
    modo = modo_arranque(app_env, ambiente)
    if modo == MODO_DESPLEGADO:
        # 5C0: staging/produccion solo arrancan con identidad exacta declarada.
        _rechazar_variables_pg(ambiente)
        _validar_despliegue(
            "DATABASE_URL", database_url, app_env, ambiente, admin=False
        )
        if (saas_admin_database_url or "").strip():
            _validar_despliegue(
                "SAAS_ADMIN_DATABASE_URL",
                saas_admin_database_url,
                app_env,
                ambiente,
                admin=True,
            )
        return

    _rechazar_variables_pg(ambiente)
    if not (database_url or "").strip():
        raise ArranqueInseguro(
            "Arranque rechazado: DATABASE_URL no esta definida y el modo "
            "local exige un destino explicito"
        )
    _validar_dsn("DATABASE_URL", database_url, modo, ambiente)
    if (saas_admin_database_url or "").strip():
        _validar_dsn(
            "SAAS_ADMIN_DATABASE_URL",
            saas_admin_database_url,
            modo,
            ambiente,
        )


def _familia(esquema: str) -> str:
    """Backend sin driver: ``postgresql+psycopg`` -> ``postgresql``."""
    return (esquema or "").split("+", 1)[0].lower()


def _puertos_permitidos(ambiente: Mapping[str, str]) -> set[int]:
    crudo = ambiente.get("SAAS_LOCAL_ALLOWED_PORTS") or PUERTOS_LOCALES_DEFECTO
    permitidos = {
        int(parte) for parte in crudo.split(",") if parte.strip().isdigit()
    }
    if not permitidos:
        permitidos = {int(PUERTOS_LOCALES_DEFECTO)}
    return permitidos


def _rechazar_variables_pg(ambiente: Mapping[str, str]) -> None:
    for clave in VARIABLES_PG:
        if (ambiente.get(clave) or "").strip():
            raise ArranqueInseguro(
                f"Arranque rechazado: {clave} esta definida en el entorno y "
                "puede redirigir la conexion; el arranque local no la admite"
            )


def _rechazar_parametros(nombre: str, parametros: Mapping[str, str]) -> None:
    for clave in parametros:
        if clave in PARAMETROS_REDIRIGIBLES:
            raise ArranqueInseguro(
                f"Arranque rechazado: {nombre} incluye el parametro "
                f"'{clave}' en el query y puede redirigir la conexion"
            )


def _validar_dsn(
    nombre: str,
    dsn: str,
    modo: str,
    ambiente: Mapping[str, str],
) -> None:
    datos = analizar_dsn(dsn)
    familia = _familia(datos["esquema"])
    if not familia:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no declara un esquema de conexion "
            "reconocible"
        )

    _rechazar_parametros(nombre, datos["parametros"])

    if familia == "sqlite":
        if modo == MODO_ESTRICTO:
            raise ArranqueInseguro(
                f"Arranque rechazado: {nombre} usa sqlite y el modo estricto "
                "(SAAS_LOCAL_ONLY) exige PostgreSQL loopback"
            )
        # SQLite no tiene red: en modo local implicito es un destino seguro.
        return

    if familia not in FAMILIAS_POSTGRES:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} usa un esquema no soportado; se "
            "exige PostgreSQL (o sqlite en modo local implicito)"
        )

    host = (datos["host"] or "").strip()
    if not host:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no declara host y libpq podria "
            "tomarlo de una variable PG* del entorno"
        )

    loopback = host in HOSTS_LOOPBACK
    if modo == MODO_ESTRICTO:
        if not loopback:
            raise ArranqueInseguro(
                f"Arranque rechazado: {nombre} apunta a un host no autorizado "
                "(se exige loopback 127.0.0.1)"
            )
        if datos["puerto"] not in _puertos_permitidos(ambiente):
            raise ArranqueInseguro(
                f"Arranque rechazado: {nombre} usa un puerto no autorizado "
                "por SAAS_LOCAL_ALLOWED_PORTS"
            )
    elif not loopback and not es_verdad(ambiente.get("SAAS_ALLOW_REMOTE_DB")):
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} apunta a un host no loopback sin "
            "SAAS_ALLOW_REMOTE_DB (override explicito del operador)"
        )

    prefijo = ambiente.get("SAAS_LOCAL_DB_PREFIX") or PREFIJO_BASE_DEFECTO
    if not (datos["base"] or "").startswith(prefijo):
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} usa una base fuera del prefijo "
            "SAAS_LOCAL_DB_PREFIX"
        )


def _lista_hosts(valor: str | None) -> set[str]:
    return {
        parte.strip().lower()
        for parte in (valor or "").split(",")
        if parte.strip()
    }


def _validar_despliegue(
    nombre: str,
    dsn: str,
    app_env: str,
    ambiente: Mapping[str, str],
    *,
    admin: bool,
) -> None:
    """Valida identidad exacta del destino staging/produccion.

    5C0 reemplaza el "modo desplegado acepta cualquier remoto" por una lista
    blanca explicita: entorno, host, puerto, base y usuario deben coincidir con
    los valores declarados por el operador. No se admite
    ``SAAS_ALLOW_REMOTE_DB`` como atajo, ni destinos loopback, ni hosts de la
    lista de denegacion (legacy/produccion). Solo lee el DSN y el entorno; no
    abre sockets ni resuelve nombres.
    """
    if es_verdad(ambiente.get("SAAS_ALLOW_REMOTE_DB")):
        raise ArranqueInseguro(
            "Arranque rechazado: SAAS_ALLOW_REMOTE_DB no se admite en modo "
            "desplegado; declare SAAS_DEPLOY_DB_* con la identidad exacta"
        )

    esperado_env = (ambiente.get("SAAS_DEPLOY_ENV") or "").strip().lower()
    actual_env = (app_env or "").strip().lower()
    if not esperado_env:
        raise ArranqueInseguro(
            "Arranque rechazado: falta SAAS_DEPLOY_ENV; el despliegue exige "
            "identidad exacta y no una bandera booleana"
        )
    if esperado_env != actual_env:
        raise ArranqueInseguro(
            "Arranque rechazado: APP_ENV no coincide con SAAS_DEPLOY_ENV"
        )

    faltantes = [
        clave
        for clave in VARIABLES_DESPLEGUE
        if not (ambiente.get(clave) or "").strip()
    ]
    if faltantes:
        raise ArranqueInseguro(
            "Arranque rechazado: faltan variables de identidad de despliegue ("
            + ", ".join(sorted(faltantes))
            + ")"
        )

    if not (dsn or "").strip():
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no esta definida en modo desplegado"
        )

    datos = analizar_dsn(dsn)
    familia = _familia(datos["esquema"])
    if familia not in FAMILIAS_POSTGRES:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} debe usar PostgreSQL en despliegue"
        )
    _rechazar_parametros(nombre, datos["parametros"])

    host = (datos["host"] or "").strip().lower()
    if not host:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no declara host"
        )
    if host in HOSTS_LOOPBACK:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} usa loopback y el despliegue exige "
            "un destino remoto declarado"
        )

    host_permitido = (
        ambiente.get("SAAS_DEPLOY_DB_HOST") or ""
    ).strip().lower()
    denegados = _lista_hosts(ambiente.get("SAAS_DEPLOY_DENY_HOSTS"))
    if host_permitido in denegados:
        raise ArranqueInseguro(
            "Arranque rechazado: SAAS_DEPLOY_DB_HOST figura en "
            "SAAS_DEPLOY_DENY_HOSTS (configuracion contradictoria)"
        )
    if host in denegados:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} apunta a un host denegado "
            "(legacy/produccion)"
        )
    if host != host_permitido:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no coincide con "
            "SAAS_DEPLOY_DB_HOST"
        )

    try:
        puerto_esperado = int(ambiente.get("SAAS_DEPLOY_DB_PORT") or "5432")
    except ValueError as exc:
        raise ArranqueInseguro(
            "Arranque rechazado: SAAS_DEPLOY_DB_PORT no es numerico"
        ) from exc
    if datos["puerto"] != puerto_esperado:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no coincide con "
            "SAAS_DEPLOY_DB_PORT"
        )

    base_esperada = (ambiente.get("SAAS_DEPLOY_DB_NAME") or "").strip()
    if (datos["base"] or "") != base_esperada:
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} no coincide con "
            "SAAS_DEPLOY_DB_NAME"
        )

    usuario_esperado = (
        ambiente.get("SAAS_DEPLOY_ADMIN_USER") or USUARIO_ADMIN_DEFECTO
        if admin
        else ambiente.get("SAAS_DEPLOY_DB_USER")
    )
    if (datos["usuario"] or "") != (usuario_esperado or ""):
        raise ArranqueInseguro(
            f"Arranque rechazado: {nombre} usa un usuario distinto del "
            "declarado para su rol"
        )
