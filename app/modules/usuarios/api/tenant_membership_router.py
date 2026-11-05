"""CU04 SaaS: gestión de membresías empresariales (sin rutas legacy).

Solo PERSONAL cuya identidad y membresía vienen de JWT firmado y base
verificada. Nunca aceptar empresa_id ni rol del JWT como permiso; el id
empresarial procede del TenantContext validado.

5BO agrega el mínimo funcional de CU03/CU04:
  * alta de personal NUEVO con invitación de un solo uso;
  * cambio de sucursal del empleado;
  * consulta y edición de la matriz RBAC nominal de la empresa.

5BZ alinea la coherencia RBAC de las LECTURAS:
  * `GET /usuarios` y `GET /roles` se autorizan por la MATRIZ NOMINAL
    (`PERSONAL/CONSULTAR`), igual que `GET /permisos`; antes exigían el NOMBRE
    de rol ADMINISTRADOR y dejaban fuera a ENCARGADO_SUCURSAL, a quien la
    propia matriz le concede la consulta.
  * La ESCRITURA sigue exigiendo ADMINISTRADOR o `PERSONAL/ADMINISTRAR`: el
    alta y las mutaciones de personal usan `GestorPersonal` y la matriz de
    permisos por rol `GestorMatriz` (ADMINISTRADOR + `PERSONAL/ADMINISTRAR`).
  * No se concede ningún permiso nuevo: `CAJERO` no tiene `PERSONAL/*`, por lo
    que sus lecturas siguen respondiendo 403.

No habilitar modo SaaS de producción ni modificar claves/roles globales.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import (
    get_current_tenant_admin,
    get_current_tenant_admin_con_permiso,
    get_current_tenant_personal_con_permiso,
)
from app.modules.usuarios.repositories.tenant_membership_repository import (
    GestionMembresiaTenantError, GestionMembresiasTenantRepository as Repo,
)
from app.modules.usuarios.repositories.tenant_personal_repository import (
    PersonalTenantError, PersonalTenantRepository as PersonalRepo,
    expiracion_invitacion,
)

router=APIRouter(prefix="/tenant/empresa",tags=["Usuarios y roles SaaS"])
Administrador=Annotated[TenantContext,Depends(get_current_tenant_admin)]
GestorPersonal=Annotated[
    TenantContext, Depends(get_current_tenant_personal_con_permiso("PERSONAL","ADMINISTRAR"))
]
ConsultaPersonal=Annotated[
    TenantContext, Depends(get_current_tenant_personal_con_permiso("PERSONAL","CONSULTAR"))
]
GestorMatriz=Annotated[
    TenantContext, Depends(get_current_tenant_admin_con_permiso("PERSONAL","ADMINISTRAR"))
]


class Estricto(BaseModel):
    model_config=ConfigDict(extra="forbid")


class AsignarRolEmpresa(Estricto):
    rol_id:int=Field(gt=0)


class EstadoMembresiaEmpresa(Estricto):
    estado:bool


class AltaPersonalEmpresa(Estricto):
    correo:str=Field(min_length=5,max_length=150,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    nombres:str=Field(min_length=1,max_length=120)
    apellidos:str=Field(min_length=1,max_length=120)
    ci:str=Field(min_length=1,max_length=35)
    telefono:str|None=Field(default=None,max_length=30)
    sucursal_id:int=Field(gt=0)
    rol_id:int=Field(gt=0)
    expira_horas:int=Field(default=72,ge=1,le=168)

    @field_validator("nombres","apellidos","ci")
    @classmethod
    def no_vacio(cls,value:str):
        resultado=value.strip()
        if not resultado:
            raise ValueError("Campo obligatorio")
        return resultado


class CambiarSucursalPersonal(Estricto):
    sucursal_id:int=Field(gt=0)


class PermisoItem(Estricto):
    funcion:str=Field(min_length=3,max_length=40,
        pattern=r"^[A-Za-z_]{3,40}$")
    accion:str=Field(min_length=3,max_length=40,
        pattern=r"^[A-Za-z_]{3,40}$")
    permitido:bool

    @field_validator("funcion","accion")
    @classmethod
    def normalizar(cls,value:str):
        return value.strip().upper()


class ActualizarPermisosRol(Estricto):
    permisos:list[PermisoItem]=Field(min_length=1,max_length=80)


def _ejecutar(db:Session,operacion,*,escritura:bool):
    try:
        result=operacion()
        if escritura:
            db.commit()
        return result
    except (GestionMembresiaTenantError, PersonalTenantError) as exc:
        if escritura:
            db.rollback()
        detalle=getattr(exc,"detalle",str(exc))
        raise HTTPException(status_code=exc.codigo,detail=detalle) from exc
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
def usuarios_empresa(admin:ConsultaPersonal,db:Session=Depends(get_db)):
    """Listado del personal: lectura por matriz nominal (`PERSONAL/CONSULTAR`).

    No exige el nombre de rol ADMINISTRADOR: ver la nota de coherencia RBAC del
    docstring del módulo.
    """
    return _ejecutar(db,lambda:Repo.listar(db,admin.empresa_id),
                    escritura=False)


@router.get("/roles")
def roles_empresa(admin:ConsultaPersonal,db:Session=Depends(get_db)):
    """Roles asignables de la empresa: misma autorización de lectura que
    `GET /permisos` (`PERSONAL/CONSULTAR`)."""
    return _ejecutar(db,lambda:Repo.roles_disponibles(
        db,admin.empresa_id),escritura=False)


@router.post("/usuarios",status_code=201)
def alta_personal_empresa(datos:AltaPersonalEmpresa,
                          admin:GestorPersonal,
                          db:Session=Depends(get_db)):
    """Alta de una cuenta NUEVA de personal con invitación de un solo uso.

    La contraseña no la elige el administrador ni viaja en el cuerpo: se genera
    un hash Argon2 de un valor aleatorio inutilizable y el usuario fija su
    contraseña al aceptar la invitación. El token se devuelve UNA sola vez.
    """
    token=secrets.token_urlsafe(32)
    resultado=_ejecutar(db,lambda:PersonalRepo.crear_personal(
        db,
        empresa_id=admin.empresa_id,
        actor_id=admin.usuario_id,
        correo=datos.correo.strip().lower(),
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        ci=datos.ci,
        telefono=datos.telefono,
        sucursal_id=datos.sucursal_id,
        rol_id=datos.rol_id,
        password_hash=hash_password(secrets.token_urlsafe(48)),
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expira_en=expiracion_invitacion(datos.expira_horas),
    ),escritura=True)
    return {
        **resultado,
        "invitacion": {
            "token": token,
            "expira_en": resultado.get("expira_en"),
            "uso": "unico",
            "aceptacion": "POST /auth/tenant/invitaciones/aceptar",
        },
    }


@router.patch("/usuarios/{usuario_id}/sucursal")
def cambiar_sucursal_personal(usuario_id:int,
                              datos:CambiarSucursalPersonal,
                              admin:GestorPersonal,
                              db:Session=Depends(get_db)):
    if usuario_id<=0:
        raise HTTPException(status_code=404,detail="Usuario no encontrado")
    return _ejecutar(db,lambda:PersonalRepo.cambiar_sucursal(
        db,empresa_id=admin.empresa_id,actor_id=admin.usuario_id,
        usuario_id=usuario_id,sucursal_id=datos.sucursal_id),escritura=True)


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


@router.get("/permisos")
def permisos_empresa(admin:ConsultaPersonal,db:Session=Depends(get_db)):
    """Matriz RBAC efectiva de los roles que usa esta empresa."""
    return _ejecutar(db,lambda:PersonalRepo.permisos_empresa(
        db,admin.empresa_id),escritura=False)


@router.patch("/roles/{rol_id}/permisos")
def actualizar_permisos_rol(rol_id:int,datos:ActualizarPermisosRol,
                            admin:GestorMatriz,
                            db:Session=Depends(get_db)):
    """Excepciones de permisos por empresa. ADMINISTRADOR no es editable."""
    if rol_id<=0:
        raise HTTPException(status_code=404,detail="Rol no encontrado")
    return _ejecutar(db,lambda:PersonalRepo.actualizar_permisos_rol(
        db,empresa_id=admin.empresa_id,actor_id=admin.usuario_id,
        rol_id=rol_id,
        permisos=[p.model_dump() for p in datos.permisos]),escritura=True)
