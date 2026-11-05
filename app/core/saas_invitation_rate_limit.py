"""5BP / Etapa G - B9: rate limit compartido del endpoint publico de invitaciones.

Contexto
--------
`POST /auth/tenant/invitaciones/aceptar` es la unica ruta publique que escribe en
la base (crea la contrasena Argon2 del invitado). El token es de un solo uso y
256 bits, por lo que la fuerza bruta del token es inviable; el riesgo real es la
inundacion del endpoint y la presion sobre Argon2/conexiones.

Que hace este modulo
--------------------
1. Calcula un identificador **no reversible** del origen (SHA-256 de la IP) y una
   **huella parcial** del token (primeros 16 hex del SHA-256 que la API ya calcula
   para localizar la invitacion).
2. Consulta `saas_admin.estado_invitacion(...)`, cuyos contadores viven en
   PostgreSQL: el estado es compartido entre procesos y replicas, no un
   diccionario en memoria.
3. Devuelve `429` con `Retry-After` cuando el origen o el token superan el
   limite, sin registrar el intento bloqueado (para no extender el bloqueo).
4. Registra `OK` solo cuando la aceptacion se completa y `FALLA` solo cuando el
   token es invalido/expirado/usado. Una politica de contrasena incumplida no
   consume cupo del token: es un error del cuerpo, no un intento de adivinar.

Por que no es un middleware ASGI
--------------------------------
El limite depende del **token**, que llega en el cuerpo. Un middleware tendria
que bufferizar el body de la peticion y reinyectarlo, lo que agrega superficie de
error y riesgo de doble lectura. La verificacion y el registro viven en el propio
endpoint, en el orden exacto en que ocurren los hechos.

Nunca se registra: token, su hash completo, correo, cuerpo del request ni la IP
en claro. El `Retry-After` se expresa en segundos.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException, Request, status

from app.core.config import settings

PREFLIJO_LOG = "5bp.invitacion"


def huella_ip(host: str) -> str:
    """SHA-256 de la IP con separacion de dominio. Nunca se guarda la IP."""
    return hashlib.sha256(f"5bp|invitacion|{host}".encode("utf-8")).hexdigest()[:32]


def huella_token(token: str) -> str:
    """Primeros 16 hex del SHA-256 del token (identificador no reversible)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _ip_cliente(request: Request) -> str:
    """IP del socket. Solo se confia en X-Forwarded-For de un proxy listado."""
    cliente = request.client.host if request.client else "desconocido"
    permitidos = {
        item.strip()
        for item in (settings.saas_invitation_trusted_proxies or "").split(",")
        if item.strip()
    }
    if cliente in permitidos:
        reenviado = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if reenviado:
            cliente = reenviado
    return cliente


def _sesion_admin():
    """Sesion corta del principal global. ``None`` si no esta configurado."""
    from sqlalchemy.orm import sessionmaker

    from app.core.saas_admin_db import motor_admin

    try:
        motor = motor_admin()
    except Exception:
        return None
    return sessionmaker(bind=motor, autoflush=False, autocommit=False,
                        expire_on_commit=False)()


def estado(ip_hash: str, token_huella: str) -> dict | None:
    from sqlalchemy import text

    sesion = _sesion_admin()
    if sesion is None:
        return None
    try:
        valor = sesion.execute(
            text(
                "SELECT saas_admin.estado_invitacion("
                "CAST(:ip AS text), CAST(:token AS text), "
                "CAST(:max_ip AS integer), CAST(:max_token AS integer), "
                "CAST(:ventana AS integer))"
            ),
            {
                "ip": ip_hash,
                "token": token_huella,
                "max_ip": int(settings.saas_invitation_rate_limit_ip),
                "max_token": int(settings.saas_invitation_rate_limit_token),
                "ventana": int(settings.saas_invitation_rate_limit_ventana_segundos),
            },
        ).scalar()
        sesion.commit()
        if isinstance(valor, dict):
            return valor
        return json.loads(valor or "{}")
    except Exception:
        sesion.rollback()
        return None
    finally:
        sesion.close()


def registrar(ip_hash: str, token_huella: str, resultado: str) -> bool:
    from sqlalchemy import text

    sesion = _sesion_admin()
    if sesion is None:
        return False
    try:
        valor = sesion.execute(
            text(
                "SELECT saas_admin.registrar_intento_invitacion("
                "CAST(:ip AS text), CAST(:token AS text), CAST(:res AS text))"
            ),
            {"ip": ip_hash, "token": token_huella, "res": resultado},
        ).scalar()
        sesion.commit()
        if isinstance(valor, dict):
            return bool(valor.get("registrado"))
        return bool(json.loads(valor or "{}").get("registrado"))
    except Exception:
        sesion.rollback()
        return False
    finally:
        sesion.close()


def verificar_limite(request: Request, token: str) -> tuple[str, str]:
    """Aplica el limite ANTES de tocar la base.

    Devuelve ``(ip_hash, token_huella)`` para que el endpoint pueda registrar el
    resultado exacto. Lanza 429 con ``Retry-After`` si el origen esta limitado y
    503 si el limitador no esta disponible (fail closed).
    """
    ip_hash = huella_ip(_ip_cliente(request))
    token_huella = huella_token(token)

    if not settings.saas_invitation_rate_limit_enabled:
        return ip_hash, token_huella

    if not settings.saas_admin_database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Proteccion de invitaciones no configurada",
        )

    datos = estado(ip_hash, token_huella)
    if datos is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Proteccion de invitaciones no disponible",
        )

    if datos.get("bloqueado"):
        espera = max(int(datos.get("reintentar_en") or 0), 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos de aceptacion de invitacion",
            headers={"Retry-After": str(espera)},
        )

    return ip_hash, token_huella


def registrar_exito(ip_hash: str, token_huella: str) -> None:
    registrar(ip_hash, token_huella, "OK")


def registrar_fallo(ip_hash: str, token_huella: str) -> None:
    registrar(ip_hash, token_huella, "FALLA")
