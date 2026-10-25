"""CU01/CU02 SaaS: empresas, planes y suscripciones por SUPERADMIN.

No usar JWT empresarial como superadmin. Rutas con credencial propia,
doble flag OFF por defecto y SQL global aislado de servicios tenant.
La clave de superadministrador no aparece en cuerpos ni responses.
No procesamiento de pagos ni renovaciones automáticas.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.saas_superadmin_auth import get_superadmin_saas
from app.modules.sucursales.repositories.saas_superadmin_repository import (
    SuperadminSaaSError, SuperadminSaaSRepository as Repo,
)

router=APIRouter(prefix="/saas/superadmin",tags=["Superadministrador SaaS"],
                 dependencies=[Depends(get_superadmin_saas)])


class EntradaSegura(BaseModel):
    model_config=ConfigDict(extra="forbid")


class CrearEmpresa(EntradaSegura):
    razon_social:str=Field(min_length=1,max_length=180)
    nombre_comercial:str=Field(min_length=1,max_length=150)
    slug:str=Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",max_length=80)
    nit:str|None=Field(default=None,max_length=40)
    correo_contacto:str|None=Field(default=None,max_length=150)
    telefono:str|None=Field(default=None,max_length=35)
    plan_id:int=Field(gt=0)
    fecha_inicio:date
    fecha_vencimiento:date

    @field_validator("razon_social","nombre_comercial")
    @classmethod
    def no_blancos(cls,value:str):
        value=value.strip()
        if not value:
            raise ValueError("Texto obligatorio")
        return value


class Estado(EntradaSegura):
    estado:bool


class CrearPlan(EntradaSegura):
    codigo:str=Field(min_length=1,max_length=35,pattern=r"^[A-Z][A-Z0-9_]*$")
    nombre:str=Field(min_length=1,max_length=100)
    descripcion:str|None=None
    max_sucursales_activas:int=Field(ge=1,le=1000)
    permite_productos:bool=True
    permite_ventas:bool=True
    permite_reservas:bool=True
    permite_offline:bool=True
    permite_reportes_convencionales:bool=True
    permite_reportes_ia:bool=False

    @field_validator("nombre")
    @classmethod
    def no_blanco(cls,value:str):
        value=value.strip()
        if not value:
            raise ValueError("Nombre obligatorio")
        return value


class AsignarSuscripcion(EntradaSegura):
    plan_id:int=Field(gt=0)
    fecha_inicio:date
    fecha_vencimiento:date
    observacion:str|None=Field(default=None,max_length=1000)


class EstadoSuscripcion(EntradaSegura):
    estado:str=Field(pattern=r"^(PENDIENTE|SUSPENDIDA|VENCIDA|CANCELADA)$")


def _result(db:Session,accion,*,escritura:bool):
    try:
        result=accion()
        if escritura:
            db.commit()
        return result
    except SuperadminSaaSError as exc:
        if escritura:
            db.rollback()
        raise HTTPException(status_code=exc.codigo,detail=exc.mensaje) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409,
                            detail="Conflicto de empresa, plan o suscripción") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503,
                            detail="Administración global no disponible") from exc
    except Exception:
        if escritura:
            db.rollback()
        raise


@router.get("/empresas")
def listar_empresas(db:Session=Depends(get_db)):
    return _result(db,lambda:Repo.listar_empresas(db),escritura=False)


@router.post("/empresas",status_code=201)
def registrar_empresa(datos:CrearEmpresa,db:Session=Depends(get_db)):
    return _result(db,lambda:Repo.crear_empresa(db,**datos.model_dump()),
                   escritura=True)


@router.patch("/empresas/{empresa_id}/estado")
def estado_empresa(empresa_id:int,datos:Estado,db:Session=Depends(get_db)):
    if empresa_id<=0:
        raise HTTPException(status_code=404,detail="Empresa no encontrada")
    return _result(db,lambda:Repo.cambiar_estado_empresa(
        db,empresa_id,datos.estado),escritura=True)


@router.get("/planes")
def listar_planes(db:Session=Depends(get_db)):
    return _result(db,lambda:Repo.listar_planes(db),escritura=False)


@router.post("/planes",status_code=201)
def registrar_plan(datos:CrearPlan,db:Session=Depends(get_db)):
    return _result(db,lambda:Repo.crear_plan(db,datos.model_dump()),
                   escritura=True)


@router.patch("/planes/{plan_id}/estado")
def estado_plan(plan_id:int,datos:Estado,db:Session=Depends(get_db)):
    if plan_id<=0:
        raise HTTPException(status_code=404,detail="Plan no encontrado")
    return _result(db,lambda:Repo.cambiar_estado_plan(
        db,plan_id,datos.estado),escritura=True)


@router.get("/empresas/{empresa_id}/suscripciones")
def listar_suscripciones(empresa_id:int,db:Session=Depends(get_db)):
    if empresa_id<=0:
        raise HTTPException(status_code=404,detail="Empresa no encontrada")
    return _result(db,lambda:Repo.listar_suscripciones(
        db,empresa_id),escritura=False)


@router.post("/empresas/{empresa_id}/suscripciones",status_code=201)
def crear_suscripcion(empresa_id:int,datos:AsignarSuscripcion,
                      db:Session=Depends(get_db)):
    if empresa_id<=0:
        raise HTTPException(status_code=404,detail="Empresa no encontrada")
    return _result(db,lambda:Repo.asignar_suscripcion(
        db,empresa_id=empresa_id,**datos.model_dump()),escritura=True)


@router.patch("/empresas/{empresa_id}/suscripciones/{suscripcion_id}/estado")
def cambiar_suscripcion(empresa_id:int,suscripcion_id:int,
                        datos:EstadoSuscripcion,
                        db:Session=Depends(get_db)):
    if empresa_id<=0 or suscripcion_id<=0:
        raise HTTPException(status_code=404,detail="Suscripción no encontrada")
    return _result(db,lambda:Repo.cambiar_estado_suscripcion(
        db,empresa_id=empresa_id,suscripcion_id=suscripcion_id,
        estado=datos.estado),escritura=True)
