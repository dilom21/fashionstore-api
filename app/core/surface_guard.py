"""5C1: separacion de superficies legacy y SaaS en un mismo proceso.

Problema que resuelve
---------------------
``SAAS_AUTH_ENABLED`` era una bandera global: al activarla, el middleware
``BloqueoRutasLegacySaaS`` devolvia 503 a TODA ruta heredada y
``get_current_user`` rechazaba cualquier JWT del primer parcial. El resultado
era binario: o legacy o SaaS, nunca ambos en el mismo despliegue.

Este modulo separa las dos decisiones:

* ``SAAS_AUTH_ENABLED``       -> superficie tenant (``/auth/tenant``, ``/tenant``).
* ``LEGACY_SURFACE_ENABLED``  -> superficie heredada (todo lo demas).

Las cuatro combinaciones posibles se resuelven aqui, en el arranque, antes de
que exista ningun engine. La regla que protege el aislamiento es explicita:

    si las dos superficies conviven en el mismo proceso, la superficie legacy
    DEBE tener un destino de datos fisicamente distinto del destino tenant.

Sin esa separacion, la unica forma de que el legacy siga funcionando contra una
base multiempresa es usar un principal privilegiado (``BYPASSRLS``), que veria
las filas de todas las empresas. Eso es precisamente lo que no se permite: la
guardia aborta el arranque con un mensaje que nombra las variables faltantes o
la contradiccion, y nunca imprime DSN, usuario ni contrasena.

Limites:
  * Solo lee configuracion y entorno; no abre sockets ni conexiones.
  * No importa ``app.core.config`` (evita el ciclo: config lo llama).
"""
from __future__ import annotations

import os
from typing import Mapping

from app.core.launch_guard import (
    ArranqueInseguro,
    HOSTS_LOOPBACK,
    analizar_dsn,
    es_verdad,
    modo_arranque,
    MODO_DESPLEGADO,
    validar_arranque,
)

SURFACE_LEGACY = "legacy"
SURFACE_TENANT = "tenant"

# Prefijos que identifican la superficie tenant. Se comparan con el prefijo
# completo (barra incluida) para que `/tenants-legacy` no cuente como tenant.
PREFIJOS_TENANT = ("/tenant/", "/auth/tenant/", "/saas/")
PREFIJOS_TENANT_EXACTOS = ("/tenant", "/auth/tenant", "/saas")

# Identidad exigida al destino legacy en modo desplegado. Se mantiene separada
# de SAAS_DEPLOY_DB_* para que ambos destinos se declaren por separado.
VARIABLES_LEGACY_DESPLEGUE = (
    "LEGACY_DEPLOY_DB_HOST",
    "LEGACY_DEPLOY_DB_PORT",
    "LEGACY_DEPLOY_DB_NAME",
    "LEGACY_DEPLOY_DB_USER",
)


def superficie_de_ruta(ruta: str) -> str:
    """Superficie a la que pertenece una ruta HTTP.

    Solo la superficie tenant se deduce por prefijo; todo lo demas es legacy.
    Es deliberadamente simple: el gate de rutas sigue siendo una allowlist
    exacta y esta funcion no autoriza nada por si sola.
    """
    limpia = (ruta or "").split("?", 1)[0]
    if limpia in PREFIJOS_TENANT_EXACTOS:
        return SURFACE_TENANT
    if limpia.startswith(PREFIJOS_TENANT):
        return SURFACE_TENANT
    return SURFACE_LEGACY


def destinos_distintos(dsn_a: str, dsn_b: str) -> bool:
    """``True`` si dos DSN apuntan a bases/instancias diferentes.

    Se comparan host, puerto y nombre de base. Dos DSN sobre la MISMA base se
    consideran el mismo destino aunque cambien de usuario: el aislamiento no
    depende del rol cuando el legacy necesita leer y escribir el esquema
    multiempresa.
    """
    a = analizar_dsn(dsn_a)
    b = analizar_dsn(dsn_b)
    return (
        (a["host"] or "").strip().lower(),
        a["puerto"],
        (a["base"] or "").strip().lower(),
    ) != (
        (b["host"] or "").strip().lower(),
        b["puerto"],
        (b["base"] or "").strip().lower(),
    )


def superficies_activas(
    *, saas_auth_enabled: bool, legacy_surface_enabled: bool
) -> tuple[str, ...]:
    """Lista ordenada de superficies habilitadas."""
    activas: list[str] = []
    if legacy_surface_enabled:
        activas.append(SURFACE_LEGACY)
    if saas_auth_enabled:
        activas.append(SURFACE_TENANT)
    return tuple(activas)


def validar_coexistencia(
    *,
    saas_auth_enabled: bool,
    legacy_surface_enabled: bool,
    database_url: str,
    legacy_database_url: str | None,
    app_env: str,
    entorno: Mapping[str, str] | None = None,
) -> None:
    """Aborta si la combinacion de superficies no es segura.

    Se invoca desde ``app.core.config.get_settings`` despues de
    ``validar_arranque`` y antes de construir cualquier engine.
    """
    ambiente = os.environ if entorno is None else entorno
    legacy_dsn = (legacy_database_url or "").strip()

    if not saas_auth_enabled and not legacy_surface_enabled:
        raise ArranqueInseguro(
            "Arranque rechazado: no hay ninguna superficie habilitada; "
            "active LEGACY_SURFACE_ENABLED o SAAS_AUTH_ENABLED"
        )

    # Modo corte (solo SaaS) y modo legacy puro: LEGACY_DATABASE_URL no aplica.
    if not (saas_auth_enabled and legacy_surface_enabled):
        if legacy_dsn:
            raise ArranqueInseguro(
                "Arranque rechazado: LEGACY_DATABASE_URL solo se admite con "
                "SAAS_AUTH_ENABLED=true y LEGACY_SURFACE_ENABLED=true"
            )
        return

    # Coexistencia: se exige un destino legacy propio y distinto.
    if not legacy_dsn:
        raise ArranqueInseguro(
            "Arranque rechazado: SAAS_AUTH_ENABLED y LEGACY_SURFACE_ENABLED "
            "estan activos y falta LEGACY_DATABASE_URL. Servir el contrato "
            "legacy contra la base multiempresa requiere un destino de datos "
            "separado; sin el, el aislamiento no puede garantizarse"
        )

    if not destinos_distintos(database_url, legacy_dsn):
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL apunta al mismo destino "
            "que DATABASE_URL. La coexistencia exige bases distintas: el "
            "contrato legacy no filtra por empresa"
        )

    if modo_arranque(app_env, ambiente) == MODO_DESPLEGADO:
        _validar_legacy_desplegado(legacy_dsn, ambiente)
    else:
        # En local se reutilizan las mismas reglas que DATABASE_URL
        # (loopback, puerto permitido y prefijo de base desechable).
        validar_arranque(
            app_env=app_env,
            database_url=legacy_dsn,
            saas_admin_database_url=None,
            entorno=ambiente,
        )


def _validar_legacy_desplegado(
    dsn: str, ambiente: Mapping[str, str]
) -> None:
    """Identidad exacta del destino legacy en staging/produccion.

    No se admite loopback ni un host de la lista de denegacion, y las cuatro
    variables LEGACY_DEPLOY_DB_* deben coincidir con el DSN declarado.
    """
    if es_verdad(ambiente.get("SAAS_ALLOW_REMOTE_DB")):
        raise ArranqueInseguro(
            "Arranque rechazado: SAAS_ALLOW_REMOTE_DB no se admite en modo "
            "desplegado"
        )

    faltantes = [
        clave
        for clave in VARIABLES_LEGACY_DESPLEGUE
        if not (ambiente.get(clave) or "").strip()
    ]
    if faltantes:
        raise ArranqueInseguro(
            "Arranque rechazado: faltan variables de identidad del destino "
            "legacy (" + ", ".join(sorted(faltantes)) + ")"
        )

    datos = analizar_dsn(dsn)
    host = (datos["host"] or "").strip().lower()
    if not host:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL no declara host"
        )
    if host in HOSTS_LOOPBACK:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL usa loopback y el "
            "despliegue exige un destino remoto declarado"
        )

    denegados = {
        parte.strip().lower()
        for parte in (ambiente.get("SAAS_DEPLOY_DENY_HOSTS") or "").split(",")
        if parte.strip()
    }
    if host in denegados:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL apunta a un host denegado"
        )

    host_esperado = (
        ambiente.get("LEGACY_DEPLOY_DB_HOST") or ""
    ).strip().lower()
    if host != host_esperado:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL no coincide con "
            "LEGACY_DEPLOY_DB_HOST"
        )

    try:
        puerto_esperado = int(ambiente.get("LEGACY_DEPLOY_DB_PORT") or "5432")
    except ValueError as exc:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DEPLOY_DB_PORT no es numerico"
        ) from exc
    if datos["puerto"] != puerto_esperado:
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL no coincide con "
            "LEGACY_DEPLOY_DB_PORT"
        )

    if (datos["base"] or "") != (
        ambiente.get("LEGACY_DEPLOY_DB_NAME") or ""
    ).strip():
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL no coincide con "
            "LEGACY_DEPLOY_DB_NAME"
        )

    if (datos["usuario"] or "") != (
        ambiente.get("LEGACY_DEPLOY_DB_USER") or ""
    ).strip():
        raise ArranqueInseguro(
            "Arranque rechazado: LEGACY_DATABASE_URL no coincide con "
            "LEGACY_DEPLOY_DB_USER"
        )
