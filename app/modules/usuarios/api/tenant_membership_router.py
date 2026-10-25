"""CU04 SaaS: gestión de membresías empresariales (sin rutas legacy).

Solo administradores PERSONAL cuya identidad y membresía vienen de JWT
firmado y base verificada. Nunca aceptar empresa_id ni rol del JWT como
permiso; el id empresarial procede de get_current_tenant_admin.
No habilitar modo SaaS de producción ni modificar claves/roles globales.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_admin
from app.modules.usuarios.repositories.tenant_membership_repository import (
    GestionMembresiaTenantError, GestionMembresiasTenantRepository as Repo,
)

router=APIRouter(prefix="/tenant/empresa",tags=["Usuarios y roles SaaS"])
Administrador=Annotated[TenantContext,Depends(get_current_tenant_admin)]


class Estricto(BaseModel):
    model_config=ConfigDict(extra="forbid")


class AsignarRolEmpresa(Estricto):
    rol_id:int=Field(gt=0)


class EstadoMembresiaEmpresa(Estricto):
    estado:bool


def _ejecutar(db:Session,operacion,*,escritura:bool):
    try:
        result=operacion()
        if escritura:
            db.commit()
        return result
    except GestionMembresiaTenantError as exc:
        if escritura:
            db.rollback()
        raise HTTPException(status_code=exc.codigo,detail=exc.detalle) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409,
                            detail="Conflicto de membresía empresarial") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503,
                            detail="Gestión empresarial no disponible") from exc
    except Exception:
        if escritura:
            db.rollback()
        raise


@router.get("/usuarios")
def usuarios_empresa(admin:Administrador,db:Session=Depends(get_db)):
    return _ejecutar(db,lambda:Repo.listar(db,admin.empresa_id),
                    escritura=False)


@router.get("/roles")
def roles_empresa(admin:Administrador,db:Session=Depends(get_db)):
    return _ejecutar(db,lambda:Repo.roles_disponibles(
        db,admin.empresa_id),escritura=False)


@router.patch("/usuarios/{usuario_id}/rol")
def cambiar_rol_empresa(usuario_id:int,datos:AsignarRolEmpresa,
                       admin:Administrador,db:Session=Depends(get_db)):
    if usuario_id<=0:
        raise HTTPException(status_code=404,detail="Usuario no encontrado")
    return _ejecutar(db,lambda:Repo.cambiar_rol(
        db,empresa_id=admin.empresa_id,actor_id=admin.usuario_id,
        usuario_id=usuario_id,rol_id=datos.rol_id),escritura=True)


@router.patch("/usuarios/{usuario_id}/estado")
def cambiar_membresia_empresa(usuario_id:int,datos:EstadoMembresiaEmpresa,
                              admin:Administrador,db:Session=Depends(get_db)):
    if usuario_id<=0:
        raise HTTPException(status_code=404,detail="Usuario no encontrado")
    return _ejecutar(db,lambda:Repo.cambiar_estado(
        db,empresa_id=admin.empresa_id,actor_id=admin.usuario_id,
        usuario_id=usuario_id,estado=datos.estado),escritura=True)
