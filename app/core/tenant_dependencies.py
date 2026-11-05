"""Dependencias de autorizacion empresarial (opt-in, sin romper CUs previos)."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.saas_rls_assertion import (
    aplicar_asercion,
    clave_disponible,
    es_postgres,
)
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
        # Solo DESPUES de validar JWT y membresia: contexto de auditoria
        # para esta transaccion. Ningun GUC prueba por si solo identidad SQL.
        # Ambos set_config son LOCAL a la transaccion (pool seguro).
        # Guardar Session.info DESPUES de las dos consultas evita conservar
        # un contexto parcialmente aplicado si una de ellas falla.
        db.execute(
            text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
            {"usuario_id": str(tenant.usuario_id)},
        )
        db.execute(
            text("SELECT set_config('app.empresa_id', :empresa_id, true)"),
            {"empresa_id": str(tenant.empresa_id)},
        )
        # after_begin lo reaplica si la misma Session abre otra transaccion.
        db.info["usuario_id"] = tenant.usuario_id
        db.info["empresa_id"] = tenant.empresa_id
        # Etapa C (5BK): el principal SQL restringido resuelve la empresa SOLO
        # con esta asercion firmada. Sin clave configurada, PostgreSQL queda
        # deny-all: se falla cerrado con 503 en lugar de consultar sin RLS.
        if es_postgres(db):
            if not clave_disponible() or not aplicar_asercion(
                db, tenant.empresa_id, tenant.usuario_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="El principal SQL restringido no esta configurado",
                )
        return tenant
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El esquema de autenticacion SaaS no esta disponible",
        ) from exc


def _tiene_permiso_tenant(
    db: Session, tenant: TenantContext, funcion: str, accion: str
) -> bool:
    """RBAC nominal por empresa (5BO).

    En PostgreSQL la autoridad es `saas.tiene_permiso_tenant`, que aplica
    excepcion por empresa > default por nombre de rol > denegado y NUNCA
    resuelve permisos de otra empresa. En SQLite (pruebas offline) se conserva
    la matriz global del primer parcial.
    """
    if es_postgres(db):
        try:
            return bool(
                db.scalar(
                    text(
                        "SELECT saas.tiene_permiso_tenant("
                        "CAST(:empresa AS bigint), CAST(:rol AS bigint), "
                        "CAST(:funcion AS text), CAST(:accion AS text))"
                    ),
                    {
                        "empresa": tenant.empresa_id,
                        "rol": tenant.rol_id,
                        "funcion": funcion,
                        "accion": accion,
                    },
                )
            )
        except SQLAlchemyError:
            # Fail closed: sin matriz disponible no se concede el permiso.
            return False
    return RolService.tiene_permiso(db, tenant.rol_id, funcion, accion)


def require_tenant_permission(funcion: str, accion: str):
    """Aplica permisos de rol ASIGNADO A LA EMPRESA, no usuario.rol_id."""
    def _verificador(
        tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not _tiene_permiso_tenant(db, tenant, funcion, accion):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permiso empresarial insuficiente",
            )
        return tenant
    return _verificador


def get_current_tenant_admin_con_permiso(funcion: str, accion: str):
    """ADMINISTRADOR personal + permiso nominal de la empresa (5BO)."""
    def _verificador(
        tenant: Annotated[TenantContext, Depends(get_current_tenant_admin)],
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not _tiene_permiso_tenant(db, tenant, funcion, accion):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permiso empresarial insuficiente",
            )
        return tenant
    return _verificador


def get_current_tenant_personal_con_permiso(funcion: str, accion: str):
    """Personal de la empresa + permiso nominal (5BO).

    No exige el NOMBRE de rol ADMINISTRADOR: la autoridad es la matriz RBAC
    por empresa. Un CAJERO solo obtiene acceso si un administrador le concede
    el permiso de forma explicita, y la matriz del administrador no es
    editable (anti-escalada).
    """
    def _verificador(
        tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if tenant.contexto != "personal":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Se requiere personal de esta empresa",
            )
        if not _tiene_permiso_tenant(db, tenant, funcion, accion):
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
