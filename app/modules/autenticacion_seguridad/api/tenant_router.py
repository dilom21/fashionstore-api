"""Login SaaS opcional; no modifica los endpoints /auth/* existentes."""
import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_bootstrap_db, get_db
from app.core.password_policy import password_cumple_politica
from app.core.saas_invitation_rate_limit import (
    registrar_exito,
    registrar_fallo,
    verificar_limite,
)
from app.core.security import hash_password
from app.core.tenant_context import TenantContext, emitir_token_empresa
from app.core.tenant_dependencies import (
    comprobar_saas_habilitado,
    get_current_tenant_context,
    publicar_token,
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
    correo: str | None = None
    empleado_id: int | None = None
    nombre: str | None = None
    apellido: str | None = None
    sucursal_id: int | None = None


@router.post("/login", response_model=TenantLoginResponse)
def login_tenant(
    datos: TenantLoginRequest,
    db: Session = Depends(get_db),
    db_bootstrap: Session = Depends(get_bootstrap_db),
):
    """Autenticacion con empresa explicita; no acepta empresa_id/rol externos."""
    comprobar_saas_habilitado()
    # Bootstrap 5R: consulta SOLO usuario_id y password_hash y verifica
    # Argon2 en backend; evita cargar Usuario/Rol ORM del primer parcial.
    # NO equivale a contar con un rol PostgreSQL restringido ni RLS aplicado.
    try:
        usuario_id = AuthService.autenticar_credenciales_tenant(
            db_bootstrap, datos.correo, datos.password
        )
        if usuario_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales invalidas o empresa no autorizada",
            )
        empresa_id = TenantAuthRepository.obtener_empresa_id_por_slug(
            db_bootstrap, datos.empresa_slug
        )
        if empresa_id is None:
            raise HTTPException(status_code=401, detail="Credenciales invalidas o empresa no autorizada")
        token = emitir_token_empresa(
            usuario_id=usuario_id, empresa_id=empresa_id, contexto=datos.contexto
        )
        publicar_token(db, token)
        registro = TenantAuthRepository.obtener_membresia_vigente(
            db,
            usuario_id=usuario_id,
            empresa_id=empresa_id,
            contexto=datos.contexto,
        )
    except SQLAlchemyError as exc:
        # El contrato instalado usa RLS + fashionstore_private.actor_*.
        # Un JWT sin membresía vigente puede abortar la consulta con 42501;
        # es una denegación de autenticación, no una caída del esquema.
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == "42501":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales invalidas o empresa no autorizada",
            ) from exc
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
        usuario_id=usuario_id,
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
    db: Session = Depends(get_db),
):
    perfil = None
    if tenant.contexto == "personal":
        perfil = TenantAuthRepository.obtener_perfil_personal(
            db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
        )
        if perfil is None:
            raise HTTPException(status_code=403, detail="Perfil de personal no autorizado para esta empresa")
    return TenantMeResponse(
        usuario_id=tenant.usuario_id,
        empresa_id=tenant.empresa_id,
        empresa_slug=tenant.empresa_slug,
        empresa_nombre=tenant.empresa_nombre,
        rol=tenant.rol_nombre,
        contexto=tenant.contexto,
        plan_codigo=tenant.plan_codigo,
        permite_reportes_ia=tenant.permite_reportes_ia,
        correo=perfil.correo if perfil else None,
        empleado_id=perfil.empleado_id if perfil else None,
        nombre=perfil.nombre if perfil else None,
        apellido=perfil.apellido if perfil else None,
        sucursal_id=perfil.sucursal_id if perfil else None,
    )


class AceptarInvitacionRequest(BaseModel):
    """Token de un solo uso + contrasena elegida por el propio usuario."""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=8, max_length=128, repr=False)
    password_confirmacion: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("token")
    @classmethod
    def token_no_vacio(cls, value: str):
        resultado = value.strip()
        if not resultado:
            raise ValueError("Token obligatorio")
        return resultado


@router.post("/invitaciones/aceptar")
def aceptar_invitacion_personal(
    datos: AceptarInvitacionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Consume la invitación de personal y fija la contraseña (Argon2).

    No revela si el token existió, expiró o fue usado: un único mensaje neutro.
    La validación de política se hace antes de tocar la base y no expone datos.

    5BP/B9: el límite por IP y por huella parcial del token se evalúa antes de
    la política y de la base. Un origen saturado recibe 429 + Retry-After sin
    llegar a Argon2 ni a `saas.aceptar_invitacion_personal`.
    """
    comprobar_saas_habilitado()
    ip_hash, token_huella = verificar_limite(request, datos.token)
    if datos.password != datos.password_confirmacion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Las contraseñas no coinciden",
        )
    if not password_cumple_politica(datos.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "La contraseña debe tener entre 8 y 128 caracteres e incluir "
                "mayúscula, minúscula, número y carácter especial"
            ),
        )
    token_hash = hashlib.sha256(datos.token.encode("utf-8")).hexdigest()
    try:
        valor = db.execute(
            text(
                "SELECT saas.aceptar_invitacion_personal("
                "CAST(:token AS text), CAST(:hash AS text))"
            ),
            {"token": token_hash, "hash": hash_password(datos.password)},
        ).scalar()
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        estado = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if estado == "42501":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Aceptación de invitaciones no disponible",
            ) from exc
        # Fallo del token (inexistente, expirado o ya usado): consume cupo.
        registrar_fallo(ip_hash, token_huella)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitación inválida o expirada",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Aceptación de invitaciones no disponible",
        ) from exc

    datos_respuesta = valor if isinstance(valor, dict) else {}
    if not datos_respuesta.get("ok"):
        registrar_fallo(ip_hash, token_huella)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitación inválida o expirada",
        )
    registrar_exito(ip_hash, token_huella)
    return {
        "aceptada": True,
        "usuario_id": datos_respuesta.get("usuario_id"),
    }
