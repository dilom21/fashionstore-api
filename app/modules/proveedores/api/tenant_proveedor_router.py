"""5C0: rutas tenant de alta de proveedores.

Doble guarda: `SAAS_AUTH_ENABLED` (membresia vigente, ya exigida por la
dependencia de tenant) y `SAAS_COMPRAS_ENABLED` (OFF por defecto). El cuerpo no
puede fijar empresa, usuario ni estado: todo se deriva en el servidor y en
PostgreSQL. El permiso nominal exigido es `COMPRAS/GESTIONAR`, el mismo que
usan las ordenes de compra.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.proveedores.repositories.tenant_proveedor_repository import (
    ProveedorTenantError,
    TenantProveedorRepository,
)

router = APIRouter(prefix="/tenant/proveedores", tags=["Proveedores SaaS"])

PersonalCompras = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("COMPRAS", "GESTIONAR")),
]


class ProveedorTenantCrear(BaseModel):
    """Alta minima: el contrato se deriva de `public.proveedor`."""

    model_config = ConfigDict(extra="forbid")
    razon_social: str = Field(min_length=1, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=150)
    telefono: str | None = Field(default=None, max_length=30)
    direccion: str | None = Field(default=None, max_length=255)
    producto_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("razon_social")
    @classmethod
    def _razon_no_vacia(cls, valor: str) -> str:
        limpio = " ".join(valor.split())
        if not limpio:
            raise ValueError("La razon social no puede estar vacia")
        return limpio

    @field_validator("nit", "telefono", "direccion")
    @classmethod
    def _opcional_limpio(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpio = valor.strip()
        return limpio or None

    @field_validator("correo")
    @classmethod
    def _correo_valido(cls, valor: str | None) -> str | None:
        # Sin dependencia de email-validator: mismo criterio que el SP.
        if valor is None:
            return None
        limpio = valor.strip().lower()
        if not limpio:
            return None
        partes = limpio.split("@")
        if (len(partes) != 2 or not partes[0] or not partes[1]
                or "." not in partes[1] or " " in limpio):
            raise ValueError("El correo no es valido")
        return limpio

    @field_validator("producto_ids")
    @classmethod
    def _productos_positivos(cls, valores: list[int]) -> list[int]:
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
               for v in valores):
            raise ValueError("Cada producto_id debe ser un entero positivo")
        return sorted(set(valores))


class ProveedorTenantProductos(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producto_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("producto_ids")
    @classmethod
    def _productos_positivos(cls, valores: list[int]) -> list[int]:
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
               for v in valores):
            raise ValueError("Cada producto_id debe ser un entero positivo")
        return sorted(set(valores))


class ProveedorTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    razon_social: str
    nit: str | None
    correo: str | None
    telefono: str | None
    direccion: str | None
    estado: bool
    total_productos: int


class ProveedorProductosTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proveedor_id: int
    total_productos: int


def _verificar_modulo() -> None:
    if not settings.saas_auth_enabled or not getattr(
        settings, "saas_compras_enabled", False
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Compras SaaS aun no habilitadas",
        )


def _verificar_id(proveedor_id: int) -> None:
    # Un ID invalido y uno ajeno responden igual: no se filtra existencia.
    if proveedor_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recurso no encontrado",
        )


def _error_repositorio(db: Session, exc: ProveedorTenantError) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=exc.codigo, detail=exc.detalle)


@router.post("", response_model=ProveedorTenantResponse, status_code=201)
def crear_proveedor(
    datos: ProveedorTenantCrear,
    tenant: PersonalCompras,
    db: Session = Depends(get_db),
):
    _verificar_modulo()
    try:
        proveedor = TenantProveedorRepository.crear(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            razon_social=datos.razon_social,
            nit=datos.nit,
            correo=datos.correo,
            telefono=datos.telefono,
            direccion=datos.direccion,
            producto_ids=datos.producto_ids,
        )
        db.commit()
    except ProveedorTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return ProveedorTenantResponse(**proveedor)


@router.put(
    "/{proveedor_id}/productos",
    response_model=ProveedorProductosTenantResponse,
)
def reemplazar_productos_proveedor(
    proveedor_id: int,
    datos: ProveedorTenantProductos,
    tenant: PersonalCompras,
    db: Session = Depends(get_db),
):
    _verificar_modulo()
    _verificar_id(proveedor_id)
    try:
        resultado = TenantProveedorRepository.reemplazar_productos(
            db,
            empresa_id=tenant.empresa_id,
            proveedor_id=proveedor_id,
            usuario_id=tenant.usuario_id,
            producto_ids=datos.producto_ids,
        )
        db.commit()
    except ProveedorTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return ProveedorProductosTenantResponse(**resultado)
