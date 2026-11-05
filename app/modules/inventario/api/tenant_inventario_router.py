"""5BZ: inventario tenant por sucursal (SOLO LECTURA, GET).

`empresa_id` se deriva siempre del JWT/membresia validada; nunca se acepta
desde query ni cuerpo. Con SAAS_AUTH_ENABLED=false la dependencia responde 503
y el gate de rutas legacy bloquea la ruta antes del router.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.inventario.repositories.tenant_inventario_repository import (
    TenantInventarioRepository,
)

router = APIRouter(prefix="/tenant/inventario", tags=["Inventario SaaS"])


class InventarioTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    sucursal_id: int
    sucursal_nombre: str
    variante_producto_id: int
    producto_id: int
    producto_nombre: str
    sku: str
    talla_id: int
    talla: str
    color_id: int
    color: str
    temporada_id: int
    temporada_nombre: str
    stock_actual: int
    stock_reservado: int
    stock_disponible: int


PersonalVentas = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("VENTAS", "CONSULTAR")),
]


@router.get("", response_model=list[InventarioTenantResponse])
def listar_inventario(
    tenant: PersonalVentas,
    sucursal_id: int | None = Query(default=None, gt=0),
    texto: str | None = Query(default=None, min_length=1, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Inventario de la empresa con stock disponible calculado en SQL."""
    return TenantInventarioRepository.listar(
        db,
        empresa_id=tenant.empresa_id,
        sucursal_id=sucursal_id,
        texto=texto.strip() if texto else None,
        limit=limit,
        offset=offset,
    )
