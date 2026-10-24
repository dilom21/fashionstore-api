"""Dependencias de autorizacion empresarial (opt-in, sin romper CUs previos)."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext, leer_token_empresa
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.roles.services.service import RolService

_tenant_bearer = HTTPBearer(auto_error=False)


def comprobar_saas_habilitado() -> None:
    """No consultar tablas nuevas mientras la migracion SaaS no este aplicada."""
    if not settings.saas_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La autenticacion multitenant aun no esta habilitada",
        )


def _error_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token empresarial invalido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_tenant_context(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_tenant_bearer)
    ],
    db: Session = Depends(get_db),
) -> TenantContext:
    comprobar_saas_habilitado()
    if credentials is None:
        raise _error_token()
    claims = leer_token_empresa(credentials.credentials)
    if claims is None:
        raise _error_token()
    usuario_id, empresa_id, contexto = claims

    try:
        registro = TenantAuthRepository.obtener_membresia_vigente(
            db, usuario_id=usuario_id, empresa_id=empresa_id, contexto=contexto
        )
        if registro is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado para la empresa solicitada",
            )

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
        # Establecer el contexto DESPUES de validar membresia. La variable no
        # reemplaza los filtros empresa_id de las consultas ni los FK compuestos.
        # set_config(..., true) limita el valor a la transaccion.
        db.info["empresa_id"] = tenant.empresa_id
        db.execute(
            text("SELECT set_config('app.empresa_id', :empresa_id, true)"),
            {"empresa_id": str(tenant.empresa_id)},
        )
        return tenant
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El esquema de autenticacion SaaS no esta disponible",
        ) from exc


def require_tenant_permission(funcion: str, accion: str):
    """Aplica permisos de rol ASIGNADO A LA EMPRESA, no usuario.rol_id."""
    def _verificador(
        tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not RolService.tiene_permiso(db, tenant.rol_id, funcion, accion):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permiso empresarial insuficiente",
            )
        return tenant
    return _verificador


def get_current_tenant_admin(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
) -> TenantContext:
    if tenant.contexto != "personal" or tenant.rol_nombre != "ADMINISTRADOR":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere ADMINISTRADOR de esta empresa",
        )
    return tenant


def get_current_tenant_cliente(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
) -> TenantContext:
    """Exige identidad en contexto cliente. La pertenencia al perfil concreto
    se vuelve a comprobar en SQL por empresa_id y usuario_id en cada escritura.
    """
    if tenant.contexto != "cliente":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere un cliente de esta empresa",
        )
    return tenant
