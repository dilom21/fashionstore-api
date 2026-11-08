"""Catalogo SaaS de SOLO LECTURA: empresa fijada por token/membresia.

Estas rutas nuevas no comparten handlers con el catalogo legacy. Con
SAAS_AUTH_ENABLED=false responden 503 desde la dependencia de tenant.

5BZ: se agregan listados tenant de diccionarios, proveedores, temporadas y
variantes para que la UI no tenga que escribir IDs a mano. Los diccionarios
globales (`talla`, `color`, `ciudad`) solo se abren a personal con permiso.
"""
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import (
    get_current_tenant_context, get_current_tenant_personal_con_permiso,
)
from app.modules.catalogo.repositories.tenant_repository import (
    TenantCatalogoRepository,
)


router = APIRouter(prefix="/tenant/catalogo", tags=["Catalogo SaaS"])


class CategoriaTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    descripcion: str | None
    estado: bool


class ProductoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal
    categoria_id: int
    categoria_nombre: str
    estado: bool


class VarianteTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    producto_id: int
    sku: str
    talla_id: int
    talla: str
    color_id: int
    color: str
    estado: bool


class RecursoProductoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    producto_id: int
    color_id: int | None
    tipo: str
    url: str
    es_principal: bool
    estado: bool
    color: str | None


class DiccionarioTenantResponse(BaseModel):
    """Entrada de un diccionario GLOBAL: nunca expone empresa_id."""

    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str


class ProveedorTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    razon_social: str
    estado: bool


class TemporadaTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date
    estado: bool


class VarianteListadoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    producto_id: int
    producto_nombre: str
    sku: str
    talla_id: int
    talla: str
    color_id: int
    color: str


TenantActual = Annotated[TenantContext, Depends(get_current_tenant_context)]
PersonalCatalogo = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("CATALOGO", "CONSULTAR")),
]
PersonalSucursales = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("SUCURSALES", "CONSULTAR")),
]
PersonalCompras = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("COMPRAS", "CONSULTAR")),
]


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


# ---------------------------------------------------------------------------
# 5BZ: listados tenant para construir formularios sin IDs manuales
# ---------------------------------------------------------------------------
@router.get("/tallas", response_model=list[DiccionarioTenantResponse])
def listar_tallas(
    tenant: PersonalCatalogo, db: Session = Depends(get_db),
):
    """Diccionario GLOBAL de tallas activas (compartido por las empresas)."""
    return TenantCatalogoRepository.listar_tallas(db)


@router.get("/colores", response_model=list[DiccionarioTenantResponse])
def listar_colores(
    tenant: PersonalCatalogo, db: Session = Depends(get_db),
):
    """Diccionario GLOBAL de colores activos (compartido por las empresas)."""
    return TenantCatalogoRepository.listar_colores(db)


@router.get("/ciudades", response_model=list[DiccionarioTenantResponse])
def listar_ciudades(
    tenant: PersonalSucursales, db: Session = Depends(get_db),
):
    """Diccionario GLOBAL de ciudades: alimenta el alta de sucursales."""
    return TenantCatalogoRepository.listar_ciudades(db)


@router.get("/proveedores", response_model=list[ProveedorTenantResponse])
def listar_proveedores(
    tenant: PersonalCompras, db: Session = Depends(get_db),
):
    """Proveedores de la empresa; alimenta el alta de ordenes de compra."""
    return TenantCatalogoRepository.listar_proveedores(
        db, empresa_id=tenant.empresa_id,
    )


@router.get("/temporadas", response_model=list[TemporadaTenantResponse])
def listar_temporadas(
    tenant: PersonalCompras, db: Session = Depends(get_db),
):
    """Temporadas de la empresa; el inventario exige temporada_id real."""
    return TenantCatalogoRepository.listar_temporadas(
        db, empresa_id=tenant.empresa_id,
    )


@router.get("/variantes", response_model=list[VarianteListadoTenantResponse])
def listar_variantes_empresa(
    tenant: PersonalCatalogo,
    producto_id: int | None = Query(default=None, gt=0),
    texto: str | None = Query(default=None, min_length=1, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Variantes de toda la empresa, filtrables por producto o texto."""
    return TenantCatalogoRepository.listar_variantes_empresa(
        db,
        empresa_id=tenant.empresa_id,
        producto_id=producto_id,
        texto=texto.strip() if texto else None,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/productos/{producto_id}/recursos",
    response_model=list[RecursoProductoTenantResponse],
)
def listar_recursos_producto(
    producto_id: int,
    tenant: PersonalCatalogo,
    estado: bool | None = None,
    db: Session = Depends(get_db),
):
    if producto_id < 1:
        raise _no_encontrado()
    if TenantCatalogoRepository.obtener_producto(
        db, empresa_id=tenant.empresa_id, producto_id=producto_id,
    ) is None:
        raise _no_encontrado()
    return TenantCatalogoRepository.listar_recursos(
        db, empresa_id=tenant.empresa_id, producto_id=producto_id, estado=estado,
    )


@router.get(
    "/recursos-producto/{recurso_id}",
    response_model=RecursoProductoTenantResponse,
)
def obtener_recurso_producto(
    recurso_id: int,
    tenant: PersonalCatalogo,
    db: Session = Depends(get_db),
):
    if recurso_id < 1:
        raise _no_encontrado()
    resultado = TenantCatalogoRepository.obtener_recurso(
        db, empresa_id=tenant.empresa_id, recurso_id=recurso_id,
    )
    if resultado is None:
        raise _no_encontrado()
    return resultado
