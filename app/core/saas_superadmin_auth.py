"""Autenticación web del superadministrador SaaS (opt-in).

Un JWT tenant (aunque tenga rol ADMINISTRADOR) NUNCA concede alcance
global. Este guard usa clave aleatoria de >=32 caracteres suministrada
fuera del repositorio y su hash SHA256 en la configuración. No hay
credencial por defecto, login público, alta automática ni bypass debug.

Solo para entornos aislados con HTTPS y principal SQL restringido:
no habilitar en producción hasta integrar RBAC, audit log, rotación
y rate limiting. No tocar PG comercial ni Supabase.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_platform_db
from app.core.security import verify_password

# __Host- solo es válido con Secure; local HTTP usa un nombre equivalente sin
# prefijo para que el navegador no descarte la cookie.
COOKIE = "__Host-fashionstore_platform_session" if settings.saas_superadmin_cookie_secure else "fashionstore_platform_session"
CSRF_COOKIE = "fashionstore_platform_csrf"
_login_attempts: dict[str, list[float]] = {}

class SuperadminLogin(BaseModel):
    usuario: str = Field(min_length=5, max_length=150,
                         validation_alias=AliasChoices("usuario", "correo"))
    password: str = Field(min_length=8, max_length=128)

    @property
    def correo(self) -> str:
        return self.usuario

def _enabled() -> None:
    if not settings.saas_auth_enabled or not settings.saas_superadmin_enabled:
        raise HTTPException(503, "Administración SaaS no habilitada")

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _session(token: str | None, db: Session) -> dict:
    _enabled()
    if not token:
        raise HTTPException(401, "Sesión de plataforma requerida")
    row = db.execute(text("""
        SELECT id, principal, csrf_token, created_at, expires_at, revoked_at
        FROM platform_admin_sessions
        WHERE token_hash = :token_hash
          AND revoked_at IS NULL
          AND expires_at > CURRENT_TIMESTAMP
          AND created_at + (:absolute || ' hours')::interval > CURRENT_TIMESTAMP
    """), {"token_hash": _token_hash(token), "absolute": settings.saas_superadmin_absolute_hours}).mappings().first()
    if not row:
        raise HTTPException(401, "Sesión de plataforma inválida")
    db.execute(text("UPDATE platform_admin_sessions SET last_seen = CURRENT_TIMESTAMP WHERE id = :id"), {"id": row["id"]})
    db.commit()
    return {"id": row["id"], "sub": row["principal"], "scope": "platform", "csrf": row["csrf_token"]}

def _check_origin(request: Request) -> None:
    allowed = {x.strip() for x in settings.saas_superadmin_allowed_origins.split(",") if x.strip()}
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin or (referer.split('/', 3)[0] + '//' + referer.split('/', 3)[2] if referer and '://' in referer else None)
    if not candidate or candidate not in allowed:
        raise HTTPException(403, "Origen no permitido")

def get_platform_session(request: Request, db: Session = Depends(get_platform_db), token: Annotated[str | None, Cookie(alias=COOKIE)] = None,
                         csrf: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
                         x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None) -> dict:
    payload = _session(token, db)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_origin(request)
        if not csrf or not x_csrf_token or not secrets.compare_digest(csrf, x_csrf_token) or csrf != payload["csrf"]:
            raise HTTPException(403, "CSRF inválido")
    return payload

def platform_or_service_key(request: Request, db: Session = Depends(get_platform_db), x_saas_admin_key: str | None = Header(default=None)) -> bool:
    # Browser session and CLI key remain separate authentication channels.
    if request.cookies.get(COOKIE):
        get_platform_session(request, db=db)
        return True
    return _verify_service_key(x_saas_admin_key)

def _verify_service_key(x_saas_admin_key: str | None) -> bool:
    _enabled()
    configured = settings.saas_superadmin_key_sha256
    if not isinstance(configured, str) or re.fullmatch(r"[0-9a-fA-F]{64}", configured) is None:
        raise HTTPException(503, "Credencial superadmin no configurada")
    if not x_saas_admin_key or len(x_saas_admin_key) < 32:
        raise HTTPException(401, "Credencial inválida")
    supplied = hashlib.sha256(x_saas_admin_key.encode()).hexdigest()
    if not secrets.compare_digest(supplied, configured.lower()):
        raise HTTPException(401, "Credencial inválida")
    return True


def get_superadmin_saas(
    request: Request = None,
    x_saas_admin_key: str | None = Header(default=None),
) -> bool:
    # Compatibilidad con pruebas/unitarios existentes que invocan el guard
    # directamente con la clave de servicio como único argumento.
    if isinstance(request, str):
        if not settings.saas_auth_enabled or not settings.saas_superadmin_enabled:
            raise HTTPException(503, "Administración SaaS no habilitada")
        configured = settings.saas_superadmin_key_sha256
        if not isinstance(configured, str) or re.fullmatch(r"[0-9a-fA-F]{64}", configured) is None:
            raise HTTPException(503, "Credencial superadmin no configurada")
        supplied = hashlib.sha256(request.encode("utf-8")).hexdigest()
        if len(request) < 32 or not secrets.compare_digest(supplied, configured.lower()):
            raise HTTPException(401, "Credencial inválida")
        return True
    if request is None:
        if not settings.saas_auth_enabled or not settings.saas_superadmin_enabled:
            raise HTTPException(503, "Administración SaaS no habilitada")
        configured = settings.saas_superadmin_key_sha256
        if not isinstance(configured, str) or re.fullmatch(r"[0-9a-fA-F]{64}", configured) is None:
            raise HTTPException(503, "Credencial superadmin no configurada")
        candidate = x_saas_admin_key
        if not candidate or len(candidate) < 32:
            raise HTTPException(401, "Credencial inválida")
        supplied = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(supplied, configured.lower()):
            raise HTTPException(401, "Credencial inválida")
        return True
    if request.cookies.get(COOKIE):
        db = next(get_platform_db())
        try:
            get_platform_session(
                request,
                db=db,
                token=request.cookies.get(COOKIE),
                csrf=request.cookies.get(CSRF_COOKIE),
                x_csrf_token=request.headers.get("X-CSRF-Token"),
            )
        finally:
            db.close()
        return True
    return _verify_service_key(x_saas_admin_key)

def login_platform(data: SuperadminLogin, request: Request, response: Response, db: Session) -> dict:
    _enabled()
    _check_origin(request)
    now = time.time(); ip = request.client.host if request.client else "unknown"
    attempts = [x for x in _login_attempts.get(ip, []) if now - x < 60]
    if len(attempts) >= settings.saas_superadmin_rate_limit_per_minute:
        raise HTTPException(429, "Demasiados intentos. Intente nuevamente más tarde")
    attempts.append(now); _login_attempts[ip] = attempts
    valid = (settings.saas_superadmin_email and settings.saas_superadmin_password_hash
             and secrets.compare_digest(data.correo.lower(), settings.saas_superadmin_email.lower())
             and verify_password(data.password, settings.saas_superadmin_password_hash))
    if not valid:
        raise HTTPException(401, "Credenciales inválidas")
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    try:
        db.execute(text("""
            INSERT INTO platform_admin_sessions
              (token_hash, principal, csrf_token, created_at, last_seen, expires_at)
            VALUES (:token_hash, :principal, :csrf_token, CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + (:ttl || ' minutes')::interval)
        """), {"token_hash": _token_hash(token), "principal": data.correo.lower(),
               "csrf_token": csrf, "ttl": settings.saas_superadmin_idle_minutes})
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sesión de plataforma no disponible") from exc
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.saas_superadmin_cookie_secure,
                        samesite="lax", max_age=settings.saas_superadmin_idle_minutes * 60, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.saas_superadmin_cookie_secure,
                        samesite="lax", max_age=settings.saas_superadmin_idle_minutes * 60, path="/")
    return {"autenticado": True, "usuario": data.correo.lower(), "rol": "SUPERADMIN"}

def revoke_platform_session(session: dict, response: Response, db: Session) -> None:
    db.execute(text("UPDATE platform_admin_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = :id"), {"id": session["id"]})
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
