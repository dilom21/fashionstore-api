"""Administracion empresarial SaaS: datos propios y limites de plan por backend.

Solo GET con membresia JWT vigente; escrituras exigen ADMINISTRADOR de la
misma empresa y transaccion SQL. empresa_id nunca viene del cliente HTTP.
La simulacion academica de cambio de plan no procesa cobros recurrentes.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import (
    get_current_tenant_admin, get_current_tenant_context,
)
from app.modules.sucursales.repositories.tenant_saas_admin_repository import (
    AdministracionTenantError, TenantEmpresaAdminRepository as Repo,
)

router = APIRouter(prefix="/tenant/empresa", tags=["Administracion SaaS"])
Miembro = Annotated[TenantContext, Depends(get_current_tenant_context)]
Administrador = Annotated[TenantContext, Depends(get_current_tenant_admin)]


class EntradaEstrica(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrearSucursal(EntradaEstrica):
    ciudad_id: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=120)
    direccion: str = Field(min_length=1, max_length=255)
    telefono: str | None = Field(default=None, max_length=30)

    @field_validator("nombre", "direccion")
    @classmethod
    def solo_texto_no_vacio(cls, value):
        if not value.strip():
            raise ValueError("Campo obligatorio")
        return value.strip()


class CambiarEstado(EntradaEstrica):
    estado: bool


class CambiarPlan(EntradaEstrica):
    codigo: str = Field(min_length=1, max_length=50)

    @field_validator("codigo")
    @classmethod
    def normalizar_codigo(cls, value):
        return value.strip().upper()


def _resolver(db: Session, operacion, *, escritura: bool):
    try:
        resultado = operacion()
        if escritura:
            db.commit()
        return resultado
    except AdministracionTenantError as exc:
        if escritura:
            db.rollback()
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Conflicto de datos de la empresa"
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="Administracion de empresa no disponible"
        ) from exc
    except Exception:
        if escritura:
            db.rollback()
        raise


@router.get("/me")
def mi_empresa(tenant: Miembro, db: Session = Depends(get_db)):
    return _resolver(db,
        lambda: Repo.empresa_actual(db, tenant.empresa_id), escritura=False)


@router.get("/plan")
def mi_plan(tenant: Miembro, db: Session = Depends(get_db)):
    return _resolver(db,
        lambda: Repo.plan_actual(db, tenant.empresa_id), escritura=False)


@router.get("/sucursales")
def mis_sucursales(tenant: Miembro, db: Session = Depends(get_db)):
    return _resolver(db,
        lambda: Repo.sucursales(db, tenant.empresa_id), escritura=False)


@router.post("/sucursales", status_code=201)
def crear_sucursal(datos: CrearSucursal, admin: Administrador,
                   db: Session = Depends(get_db)):
    return _resolver(db, lambda: Repo.crear_sucursal(
        db, empresa_id=admin.empresa_id, ciudad_id=datos.ciudad_id,
        nombre=datos.nombre, direccion=datos.direccion,
        telefono=datos.telefono,
    ), escritura=True)


@router.patch("/sucursales/{sucursal_id}/estado")
def cambiar_estado_sucursal(sucursal_id: int, datos: CambiarEstado,
                             admin: Administrador,
                             db: Session = Depends(get_db)):
    if sucursal_id <= 0:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    return _resolver(db, lambda: Repo.cambiar_estado_sucursal(
        db, empresa_id=admin.empresa_id,
        sucursal_id=sucursal_id, habilitar=datos.estado,
    ), escritura=True)


@router.patch("/plan")
def cambiar_plan(datos: CambiarPlan, admin: Administrador,
                 db: Session = Depends(get_db)):
    """Cambio de plan simulado por ADMINISTRADOR empresarial en el prototipo.

    No existe procesamiento de pagos ni renovación automática de SaaS.
    """
    return _resolver(db, lambda: Repo.cambiar_plan(
        db, empresa_id=admin.empresa_id, codigo=datos.codigo,
    ), escritura=True)
