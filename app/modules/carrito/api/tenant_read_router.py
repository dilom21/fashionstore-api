"""Consultas CU15 de carrito propio en SaaS, sin efectos de escritura."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.carrito.repositories.tenant_read_repository import CarritoTenantReadRepository
from app.modules.carrito.repositories.tenant_repository import CarritoTenantError


router = APIRouter(prefix="/tenant/carritos", tags=["Carrito Cliente SaaS (lecturas)"])
ClienteActual = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


class CarritoResumenSaaS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int
    sucursal_id: int
    sucursal_nombre: str
    cantidad_lineas: int
    cantidad_unidades: int
    subtotal: Decimal
    fecha_actualizacion: datetime


class ListaCarritosSaaS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CarritoResumenSaaS]
    total_carritos_activos: int


class ItemCarritoSaaS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detalle_id: int
    inventario_id: int
    producto_id: int
    producto_nombre: str
    precio_unitario: Decimal
    variante_producto_id: int
    sku: str
    talla_id: int
    talla_nombre: str
    color_id: int
    color_nombre: str
    temporada_id: int
    temporada_nombre: str
    cantidad: int
    stock_disponible: int
    subtotal_linea: Decimal


class CarritoDetalleSaaS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int
    sucursal_id: int
    sucursal_nombre: str
    estado: str
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    items: list[ItemCarritoSaaS]
    cantidad_total_unidades: int
    subtotal_carrito: Decimal


def _consultar(operacion):
    try:
        return operacion()
    except CarritoTenantError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="No se pudo consultar el carrito"
        ) from exc


@router.get("", response_model=ListaCarritosSaaS)
def listar_carritos_cliente_empresa(
    cliente: ClienteActual,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return _consultar(
        lambda: CarritoTenantReadRepository.listar(
            db,
            empresa_id=cliente.empresa_id,
            usuario_id=cliente.usuario_id,
            limit=limit, offset=offset,
        )
    )


@router.get("/{carrito_id}", response_model=CarritoDetalleSaaS)
def obtener_carrito_cliente_empresa(
    carrito_id: int,
    cliente: ClienteActual,
    db: Session = Depends(get_db),
):
    return _consultar(
        lambda: CarritoTenantReadRepository.obtener(
            db, empresa_id=cliente.empresa_id,
            usuario_id=cliente.usuario_id, carrito_id=carrito_id,
        )
    )
