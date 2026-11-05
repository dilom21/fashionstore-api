"""5BO / Etapa F: rate limiting y auditoría del plano superadministrador.

Por qué un middleware y no una dependencia de FastAPI
-----------------------------------------------------
La protección contra fuerza bruta debe actuar ANTES de verificar la clave y
debe registrar el resultado real de la petición (401/403/429/200). Una
dependencia solo ve el camino feliz o lanza excepción antes de poder auditar.
Este middleware ASGI envuelve la respuesta y con ello obtiene el código final
sin acoplarse a internals de FastAPI.

Los contadores viven en PostgreSQL (`saas_admin.intento_auth`), por lo que la
protección es COMPARTIDA entre procesos y réplicas: no es un diccionario en
memoria. Toda la escritura se hace a través de funciones `SECURITY DEFINER`
del propietario NOLOGIN `saas_admin_owner`; el principal
`saas_admin_runtime` sigue sin privilegios de tabla.

Nunca se registra: la clave en claro, su hash completo, JWT, DSN, cuerpos de
request ni IP sin hashear.
"""
from __future__ import annotations

import hashlib
import json
import re

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.saas_superadmin_auth import huella_clave

PREFIJO = "/saas/superadmin"
_EMPRESA_EN_RUTA = re.compile(r"/empresas/([1-9][0-9]*)")


def _cabecera(scope: Scope, nombre: bytes) -> str | None:
    for clave, valor in scope.get("headers", []):
        if clave.lower() == nombre:
            return valor.decode("latin-1")
    return None


def _ip_hash(scope: Scope) -> str:
    cliente = scope.get("client")
    host = cliente[0] if cliente else "desconocido"
    return hashlib.sha256(f"5bo|{host}".encode("utf-8")).hexdigest()[:32]


def _https_confiable(scope: Scope) -> bool:
    """HTTPS real o proxy listado. Nunca un X-Forwarded-Proto arbitrario."""
    if scope.get("scheme") == "https":
        return True
    cliente = scope.get("client")
    host = cliente[0] if cliente else ""
    permitidos = {
        item.strip()
        for item in (settings.saas_superadmin_trusted_proxies or "").split(",")
        if item.strip()
    }
    if host and host in permitidos:
        reenviado = (_cabecera(scope, b"x-forwarded-proto") or "").strip().lower()
        return reenviado == "https"
    return False


def _sesion_admin():
    """Sesión corta del principal global. ``None`` si no está configurado."""
    from app.core.saas_admin_db import get_saas_admin_db  # noqa: F401  (contrato)
    from app.core.saas_admin_db import motor_admin
    from sqlalchemy.orm import sessionmaker

    try:
        motor = motor_admin()
    except Exception:  # HTTPException 503 u otro: no romper la petición
        return None
    return sessionmaker(bind=motor, autoflush=False, autocommit=False,
                        expire_on_commit=False)()


def _estado_acceso(fingerprint: str, ip_hash: str) -> dict | None:
    from sqlalchemy import text

    sesion = _sesion_admin()
    if sesion is None:
        return None
    try:
        valor = sesion.execute(
            text(
                "SELECT saas_admin.estado_acceso("
                "CAST(:fp AS text), CAST(:ip AS text), "
                "CAST(:max_ip AS integer), CAST(:max_clave AS integer), "
                "CAST(:ventana AS integer))"
            ),
            {
                "fp": fingerprint,
                "ip": ip_hash,
                "max_ip": int(settings.saas_superadmin_rate_limit_ip),
                "max_clave": int(settings.saas_superadmin_rate_limit_clave),
                "ventana": int(settings.saas_superadmin_rate_limit_ventana_segundos),
            },
        ).scalar()
        sesion.commit()
        return valor if isinstance(valor, dict) else json.loads(valor or "{}")
    except Exception:
        sesion.rollback()
        return None
    finally:
        sesion.close()


def _registrar_intento(fingerprint: str, ip_hash: str, resultado: str) -> None:
    from sqlalchemy import text

    sesion = _sesion_admin()
    if sesion is None:
        return
    try:
        sesion.execute(
            text(
                "SELECT saas_admin.registrar_intento("
                "CAST(:fp AS text), CAST(:ip AS text), CAST(:res AS text))"
            ),
            {"fp": fingerprint, "ip": ip_hash, "res": resultado},
        )
        sesion.commit()
    except Exception:
        sesion.rollback()
    finally:
        sesion.close()


def _auditar(fingerprint: str, ip_hash: str, scope: Scope,
             codigo: int | None, resultado: str, detalle: str | None) -> None:
    from sqlalchemy import text

    sesion = _sesion_admin()
    if sesion is None:
        return
    ruta = str(scope.get("path", "/"))
    coincidencia = _EMPRESA_EN_RUTA.search(ruta)
    empresa_id = int(coincidencia.group(1)) if coincidencia else None
    try:
        sesion.execute(
            text(
                "SELECT saas_admin.auditar_operacion("
                "CAST(:fp AS text), CAST(:ip AS text), CAST(:metodo AS text), "
                "CAST(:ruta AS text), CAST(:codigo AS integer), "
                "CAST(:resultado AS text), CAST(:empresa AS bigint), "
                "CAST(:detalle AS text))"
            ),
            {
                "fp": fingerprint,
                "ip": ip_hash,
                "metodo": str(scope.get("method", "GET")).upper(),
                "ruta": ruta,
                "codigo": codigo,
                "resultado": resultado,
                "empresa": empresa_id,
                "detalle": detalle,
            },
        )
        sesion.commit()
    except Exception:
        sesion.rollback()
    finally:
        sesion.close()


class GuardiaSuperadminSaaS:
    """Rate limit compartido + auditoría global para `/saas/superadmin/*`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ruta = str(scope.get("path", ""))
        if not ruta.startswith(PREFIJO):
            await self.app(scope, receive, send)
            return
        if not (settings.saas_auth_enabled and settings.saas_superadmin_enabled):
            # Con el módulo apagado el router responde 503: no se toca la base.
            await self.app(scope, receive, send)
            return
        if str(scope.get("method", "")).upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        fingerprint = huella_clave(_cabecera(scope, b"x-saas-admin-key"))
        ip_hash = _ip_hash(scope)

        if settings.saas_superadmin_require_https and not _https_confiable(scope):
            await run_in_threadpool(
                _auditar, fingerprint, ip_hash, scope, 403, "DENEGADO",
                "requiere_https")
            respuesta = JSONResponse(
                status_code=403,
                content={"detail": "Se requiere HTTPS para administración global"},
            )
            await respuesta(scope, receive, send)
            return

        estado = await run_in_threadpool(_estado_acceso, fingerprint, ip_hash)
        if estado and estado.get("bloqueado"):
            await run_in_threadpool(
                _auditar, fingerprint, ip_hash, scope, 429, "LIMITADO",
                f"fallas_ip={estado.get('fallas_ip')};"
                f"fallas_clave={estado.get('fallas_clave')}")
            respuesta = JSONResponse(
                status_code=429,
                content={
                    "detail": "Demasiados intentos de autenticación administrativa"
                },
                headers={
                    "Retry-After": str(
                        int(settings.saas_superadmin_rate_limit_ventana_segundos))
                },
            )
            await respuesta(scope, receive, send)
            return

        codigo: int | None = None

        async def _enviar(mensaje: Message) -> None:
            nonlocal codigo
            if mensaje["type"] == "http.response.start":
                codigo = int(mensaje["status"])
                # Contabilidad ANTES de entregar la respuesta: una peticion
                # posterior del mismo cliente ya ve el intento y la auditoria
                # (elimina la carrera entre respuesta y bitacora).
                if codigo == 401:
                    await run_in_threadpool(
                        _registrar_intento, fingerprint, ip_hash, "FALLA")
                    await run_in_threadpool(
                        _auditar, fingerprint, ip_hash, scope, codigo,
                        "DENEGADO", "credencial_invalida")
                elif 200 <= codigo < 400:
                    await run_in_threadpool(
                        _registrar_intento, fingerprint, ip_hash, "OK")
                    await run_in_threadpool(
                        _auditar, fingerprint, ip_hash, scope, codigo, "OK", None)
                elif 400 <= codigo < 500:
                    # Rechazo de negocio (403/409/422): NO es un fallo de
                    # autenticacion ni una operacion exitosa. No altera el
                    # contador de fuerza bruta, que solo cuenta 401.
                    await run_in_threadpool(
                        _auditar, fingerprint, ip_hash, scope, codigo,
                        "DENEGADO", f"http_{codigo}")
                else:
                    await run_in_threadpool(
                        _auditar, fingerprint, ip_hash, scope, codigo,
                        "ERROR", "fallo_interno")
            await send(mensaje)

        try:
            await self.app(scope, receive, _enviar)
        except Exception:
            await run_in_threadpool(_auditar, fingerprint, ip_hash, scope,
                                    None, "ERROR", "excepcion")
            raise
