"""Solo escrituras del catalogo en tenant autentificado y admin de personal.

Nunca recibe empresa_id de HTTP. No altera los handlers legados.
"""
from decimal import Decimal
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_admin
from app.modules.catalogo.api.tenant_router import (
    CategoriaTenantResponse, ProductoTenantResponse, VarianteTenantResponse,
)
from app.modules.catalogo.repositories.tenant_repository import TenantCatalogoRepository
from app.modules.catalogo.repositories.tenant_write_repository import TenantCatalogoWriteRepository


router = APIRouter(prefix="/tenant/catalogo", tags=["Catalogo SaaS (escrituras)"])
AdminTenant = Annotated[TenantContext, Depends(get_current_tenant_admin)]
T = TypeVar("T")


class EscrituraTenantBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("nombre", "sku", check_fields=False, mode="before")
    @classmethod
    def normalizar_texto_obligatorio(cls, value):
        if isinstance(value, str):
            resultado = value.strip()
            if not resultado:
                raise ValueError("El campo no puede estar vacio")
            return resultado
        return value

    @model_validator(mode="after")
    def verificar_patch_no_vacio(self):
        # No aceptar {}. Las creaciones tienen campos obligatorios
        # y no pasan por esta condicion vacia.
        if not self.model_fields_set:
            raise ValueError("Se requiere al menos un campo")
        return self


class CategoriaCrear(EscrituraTenantBase):
    nombre: str = Field(min_length=1, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)


class CategoriaEditar(EscrituraTenantBase):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    descripcion: str | None = Field(default=None, max_length=255)


class ProductoCrear(EscrituraTenantBase):
    categoria_id: int = Field(gt=0)
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=1000)
    precio: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class ProductoEditar(EscrituraTenantBase):
    categoria_id: int | None = Field(default=None, gt=0)
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=1000)
    precio: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class VarianteCrear(EscrituraTenantBase):
    talla_id: int = Field(gt=0)
    color_id: int = Field(gt=0)
    sku: str = Field(min_length=1, max_length=80)


class VarianteEditar(EscrituraTenantBase):
    talla_id: int | None = Field(default=None, gt=0)
    color_id: int | None = Field(default=None, gt=0)
    sku: str | None = Field(default=None, min_length=1, max_length=80)


def _recurso_ajeno() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurso no encontrado")


def _operacion(db: Session, fn: Callable[[], T]) -> T:
    """Un commit atomico por operacion; rollback ante conflictos de datos."""
    try:
        resultado = fn()
        if resultado is None or resultado is False:
            db.rollback()
            raise _recurso_ajeno()
        db.commit()
        return resultado
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicto con los datos del catalogo de la empresa",
        ) from exc
    except Exception:
        db.rollback()
        raise


def _exigir_cambios_validos(cambios: dict) -> None:
    # Pydantic permite null al hacer PATCH para campos opcionales, pero NO
    # puede usarse para anular claves/nombres/precio obligatorios.
    if any(cambios.get(k, "__ausente__") is None for k in (
        "nombre", "categoria_id", "precio", "sku", "talla_id", "color_id",
    )):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se permite vaciar un campo obligatorio",
        )


@router.post("/categorias", response_model=CategoriaTenantResponse, status_code=201)
def crear_categoria(datos: CategoriaCrear, admin: AdminTenant, db: Session = Depends(get_db)):
    nuevo_id = _operacion(
        db, lambda: TenantCatalogoWriteRepository.crear_categoria(
            db, empresa_id=admin.empresa_id, usuario_id=admin.usuario_id,
            nombre=datos.nombre, descripcion=datos.descripcion,
        )
    )
    return TenantCatalogoRepository.obtener_categoria(
        db, empresa_id=admin.empresa_id, categoria_id=nuevo_id,
    )


@router.patch("/categorias/{categoria_id}", response_model=CategoriaTenantResponse)
def editar_categoria(
    categoria_id: int, datos: CategoriaEditar, admin: AdminTenant, db: Session = Depends(get_db),
):
    if categoria_id <= 0:
        raise _recurso_ajeno()
    cambios = datos.model_dump(exclude_unset=True)
    _exigir_cambios_validos(cambios)
    _operacion(
        db, lambda: TenantCatalogoWriteRepository.actualizar_categoria(
            db, empresa_id=admin.empresa_id, usuario_id=admin.usuario_id,
            categoria_id=categoria_id, cambios=cambios,
        )
    )
    return TenantCatalogoRepository.obtener_categoria(
        db, empresa_id=admin.empresa_id, categoria_id=categoria_id,
    )


@router.post("/productos", response_model=ProductoTenantResponse, status_code=201)
def crear_producto(datos: ProductoCrear, admin: AdminTenant, db: Session = Depends(get_db)):
    nuevo_id = _operacion(
        db, lambda: TenantCatalogoWriteRepository.crear_producto(
            db, empresa_id=admin.empresa_id, categoria_id=datos.categoria_id,
            nombre=datos.nombre, descripcion=datos.descripcion, precio=datos.precio,
        )
    )
    return TenantCatalogoRepository.obtener_producto(
        db, empresa_id=admin.empresa_id, producto_id=nuevo_id,
    )


@router.patch("/productos/{producto_id}", response_model=ProductoTenantResponse)
def editar_producto(
    producto_id: int, datos: ProductoEditar, admin: AdminTenant, db: Session = Depends(get_db),
):
    if producto_id <= 0:
        raise _recurso_ajeno()
    cambios = datos.model_dump(exclude_unset=True)
    _exigir_cambios_validos(cambios)
    _operacion(
        db, lambda: TenantCatalogoWriteRepository.actualizar_producto(
            db, empresa_id=admin.empresa_id, producto_id=producto_id, cambios=cambios,
        )
    )
    return TenantCatalogoRepository.obtener_producto(
        db, empresa_id=admin.empresa_id, producto_id=producto_id,
    )


@router.post(
    "/productos/{producto_id}/variantes",
    response_model=VarianteTenantResponse,
    status_code=201,
)
def crear_variante(
    producto_id: int, datos: VarianteCrear, admin: AdminTenant, db: Session = Depends(get_db),
):
    if producto_id <= 0:
        raise _recurso_ajeno()
    nuevo_id = _operacion(
        db, lambda: TenantCatalogoWriteRepository.crear_variante(
            db, empresa_id=admin.empresa_id, producto_id=producto_id,
            talla_id=datos.talla_id, color_id=datos.color_id, sku=datos.sku,
        )
    )
    return TenantCatalogoRepository.obtener_variante(
        db, empresa_id=admin.empresa_id, variante_id=nuevo_id,
    )


@router.patch("/variantes/{variante_id}", response_model=VarianteTenantResponse)
def editar_variante(
    variante_id: int, datos: VarianteEditar, admin: AdminTenant, db: Session = Depends(get_db),
):
    if variante_id <= 0:
        raise _recurso_ajeno()
    cambios = datos.model_dump(exclude_unset=True)
    _exigir_cambios_validos(cambios)
    _operacion(
        db, lambda: TenantCatalogoWriteRepository.actualizar_variante(
            db, empresa_id=admin.empresa_id, variante_id=variante_id, cambios=cambios,
        )
    )
    return TenantCatalogoRepository.obtener_variante(
        db, empresa_id=admin.empresa_id, variante_id=variante_id,
    )
