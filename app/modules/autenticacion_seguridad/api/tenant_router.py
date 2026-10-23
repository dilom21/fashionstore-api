"""Login SaaS opcional; no modifica los endpoints /auth/* existentes."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, emitir_token_empresa
from app.core.tenant_dependencies import (
    comprobar_saas_habilitado,
    get_current_tenant_context,
)
from app.modules.autenticacion_seguridad.schemas.schemas import LoginRequest
from app.modules.autenticacion_seguridad.services.service import AuthService
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)

router = APIRouter(prefix="/auth/tenant", tags=["Autenticacion SaaS"])


class TenantLoginRequest(LoginRequest):
    model_config = ConfigDict(extra="forbid")
    empresa_slug: str = Field(
        min_length=1, max_length=80, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$"
    )
    contexto: Literal["cliente", "personal"]


class TenantLoginResponse(BaseModel):
    """Respuesta minima para nuevo cliente SaaS; no devuelve password."""
    access_token: str
    token_type: str = "bearer"
    empresa_id: int
    empresa_slug: str
    empresa_nombre: str
    rol: str
    contexto: Literal["cliente", "personal"]


class TenantMeResponse(BaseModel):
    usuario_id: int
    empresa_id: int
    empresa_slug: str
    empresa_nombre: str
    rol: str
    contexto: Literal["cliente", "personal"]
    plan_codigo: str
    permite_reportes_ia: bool


@router.post("/login", response_model=TenantLoginResponse)
def login_tenant(
    datos: TenantLoginRequest,
    db: Session = Depends(get_db),
):
    """Autenticacion con empresa explicita; no acepta empresa_id/rol externos."""
    comprobar_saas_habilitado()
    usuario = AuthService.autenticar_credenciales(
        db, datos.correo, datos.password
    )
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas o empresa no autorizada",
        )
    try:
        registro = TenantAuthRepository.obtener_membresia_vigente(
            db,
            usuario_id=usuario.id,
            empresa_slug=datos.empresa_slug,
            contexto=datos.contexto,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El esquema de autenticacion SaaS no esta disponible",
        ) from exc

    if registro is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas o empresa no autorizada",
        )

    token = emitir_token_empresa(
        usuario_id=usuario.id,
        empresa_id=int(registro["empresa_id"]),
        contexto=datos.contexto,
    )
    return TenantLoginResponse(
        access_token=token,
        empresa_id=int(registro["empresa_id"]),
        empresa_slug=registro["empresa_slug"],
        empresa_nombre=registro["empresa_nombre"],
        rol=registro["rol_nombre"],
        contexto=datos.contexto,
    )


@router.get("/me", response_model=TenantMeResponse)
def tenant_me(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
):
    return TenantMeResponse(
        usuario_id=tenant.usuario_id,
        empresa_id=tenant.empresa_id,
        empresa_slug=tenant.empresa_slug,
        empresa_nombre=tenant.empresa_nombre,
        rol=tenant.rol_nombre,
        contexto=tenant.contexto,
        plan_codigo=tenant.plan_codigo,
        permite_reportes_ia=tenant.permite_reportes_ia,
    )
