"""Catalogo SaaS de SOLO LECTURA: empresa fijada por token/membresia.

Estas rutas nuevas no comparten handlers con el catalogo legacy. Con
SAAS_AUTH_ENABLED=false responden 503 desde la dependencia de tenant.
"""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.catalogo.repositories.tenant_repository import (
    TenantCatalogoRepository,
)


router = APIRouter(prefix="/tenant/catalogo", tags=["Catalogo SaaS"])


class CategoriaTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    descripcion: str | None


class ProductoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    categoria_id: int
    categoria_nombre: str


class VarianteTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    producto_id: int
    sku: str
    talla: str
    color: str


TenantActual = Annotated[TenantContext, Depends(get_current_tenant_context)]


def _no_encontrado() -> HTTPException:
    # Igual respuesta si no existe o pertenece a otra empresa.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Recurso no encontrado",
    )


@router.get("/categorias", response_model=list[CategoriaTenantResponse])
def listar_categorias(
    tenant: TenantActual, db: Session = Depends(get_db),
):
    return TenantCatalogoRepository.listar_categorias(db, empresa_id=tenant.empresa_id)


@router.get("/categorias/{categoria_id}", response_model=CategoriaTenantResponse)
def obtener_categoria(
    categoria_id: int,
    tenant: TenantActual,
    db: Session = Depends(get_db),
):
    if categoria_id < 1:
        raise _no_encontrado()
    resultado = TenantCatalogoRepository.obtener_categoria(
        db, empresa_id=tenant.empresa_id, categoria_id=categoria_id,
    )
    if resultado is None:
        raise _no_encontrado()
    return resultado


@router.get("/productos", response_model=list[ProductoTenantResponse])
def listar_productos(
    tenant: TenantActual,
    categoria_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return TenantCatalogoRepository.listar_productos(
        db,
        empresa_id=tenant.empresa_id,
        categoria_id=categoria_id,
        limit=limit,
        offset=offset,
    )


@router.get("/productos/{producto_id}", response_model=ProductoTenantResponse)
def obtener_producto(
    producto_id: int,
    tenant: TenantActual,
    db: Session = Depends(get_db),
):
    if producto_id < 1:
        raise _no_encontrado()
    resultado = TenantCatalogoRepository.obtener_producto(
        db, empresa_id=tenant.empresa_id, producto_id=producto_id,
    )
    if resultado is None:
        raise _no_encontrado()
    return resultado


@router.get(
    "/productos/{producto_id}/variantes",
    response_model=list[VarianteTenantResponse],
)
def listar_variantes(
    producto_id: int,
    tenant: TenantActual,
    db: Session = Depends(get_db),
):
    if producto_id < 1:
        raise _no_encontrado()
    # Un producto de otra empresa NO puede revelar ni siquiera una lista vacia
    # como indicio de existencia: primero exigimos propiedad de este producto.
    producto = TenantCatalogoRepository.obtener_producto(
        db, empresa_id=tenant.empresa_id, producto_id=producto_id,
    )
    if producto is None:
        raise _no_encontrado()
    return TenantCatalogoRepository.listar_variantes(
        db, empresa_id=tenant.empresa_id, producto_id=producto_id,
    )


@router.get("/variantes/{variante_id}", response_model=VarianteTenantResponse)
def obtener_variante(
    variante_id: int,
    tenant: TenantActual,
    db: Session = Depends(get_db),
):
    if variante_id < 1:
        raise _no_encontrado()
    resultado = TenantCatalogoRepository.obtener_variante(
        db, empresa_id=tenant.empresa_id, variante_id=variante_id,
    )
    if resultado is None:
        raise _no_encontrado()
    return resultado
