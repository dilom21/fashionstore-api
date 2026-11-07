"""Dependencias tenant adaptadas para el laboratorio 5CA.

Reemplazan a app/core/tenant_dependencies.py SÓLO dentro del laboratorio:
la diferencia es que el token firmado se publica como app.actor_token antes de
la primera consulta y que el login usa un pool bootstrap separado.
El código original no se modifica.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tenant_context import TenantContext, leer_token_empresa
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    CredencialesBootstrapTenant,
    TenantAuthRepository,
)
from fastapi_lab.database_lab import get_bootstrap_db, get_db

_bearer = HTTPBearer(auto_error=False)


def _error_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail="Token empresarial invalido o expirado",
                         headers={"WWW-Authenticate": "Bearer"})


def publicar_token(db: Session, token: str) -> None:
    """Paso 1 del cambio: el JWT crudo viaja como GUC local de la transacción."""
    db.info["actor_token"] = token
    db.execute(text("SELECT set_config('app.actor_token', :token, true)"),
               {"token": token})


def get_current_tenant_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None,
                           Depends(_bearer)],
    db: Session = Depends(get_db),
) -> TenantContext:
    if not settings.saas_auth_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="La autenticacion multitenant no esta habilitada")
    if credentials is None:
        raise _error_token()
    claims = leer_token_empresa(credentials.credentials)
    if claims is None:
        raise _error_token()
    usuario_id, empresa_id, contexto = claims

    try:
        publicar_token(db, credentials.credentials)
        registro = TenantAuthRepository.obtener_membresia_vigente(
            db, usuario_id=usuario_id, empresa_id=empresa_id, contexto=contexto
        )
        if registro is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="No autorizado para la empresa solicitada")
        tenant = TenantContext(
            usuario_id=usuario_id,
            empresa_id=int(registro["empresa_id"]),
            empresa_slug=registro["empresa_slug"],
            empresa_nombre=registro["empresa_nombre"],
            rol_id=int(registro["rol_id"]),
            rol_nombre=registro["rol_nombre"],
            contexto=contexto,
            plan_codigo=registro["plan_codigo"],
            permite_reportes_ia=bool(registro["permite_reportes_ia"]),
        )
        # Sólo auditoría.
        db.info["usuario_id"] = tenant.usuario_id
        db.info["empresa_id"] = tenant.empresa_id
        return tenant
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        # HALLAZGO 5CA: si el token no tiene membresía vigente, actor_claims()
        # aborta la consulta con 42501 en lugar de devolver 0 filas. El
        # tenant_dependencies original lo convertiría en 503. Hay que mapearlo a
        # 403 para no confundir "sin permiso" con "esquema no disponible".
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == "42501":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado para la empresa solicitada") from exc
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="El esquema de autenticacion SaaS no esta "
                                   "disponible") from exc


def get_current_tenant_cliente(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
) -> TenantContext:
    if tenant.contexto != "cliente":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Se requiere un cliente de esta empresa")
    return tenant


def get_current_tenant_admin(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
) -> TenantContext:
    if tenant.contexto != "personal" or tenant.rol_nombre != "ADMINISTRADOR":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Se requiere ADMINISTRADOR de esta empresa")
    return tenant


def obtener_credenciales_bootstrap(
    db: Session, correo: str
) -> CredencialesBootstrapTenant | None:
    """Paso 4: el pool bootstrap sólo ejecuta la función estrecha."""
    normalizado = correo.strip().lower()
    if not normalizado:
        return None
    filas = db.execute(
        text("SELECT usuario_id, password_hash "
             "FROM fashionstore_private.bootstrap_credencial(:correo)"),
        {"correo": normalizado},
    ).mappings().all()
    if len(filas) != 1:
        return None
    return CredencialesBootstrapTenant(usuario_id=int(filas[0]["usuario_id"]),
                                       password_hash=str(filas[0]["password_hash"]))


__all__ = [
    "get_bootstrap_db", "get_current_tenant_admin", "get_current_tenant_cliente",
    "get_current_tenant_context", "obtener_credenciales_bootstrap", "publicar_token",
]
