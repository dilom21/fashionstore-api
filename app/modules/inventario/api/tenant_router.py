"""CU08 SaaS: adaptacion de las consultas CU13/CU14, sin escrituras."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.inventario.schemas.schemas import (
    DisponibilidadFiltro,
    InventarioConsultaResponse,
    InventarioFiltrosResponse,
    InventarioPosConsultaResponse,
    MovimientoInventarioConsultaResponse,
    TipoMovimiento,
)
from app.modules.inventario.services.service import (
    DisponibilidadInvalidaError,
    InventarioService,
    RangoFechasInvalidoError,
    RolInventarioTenantError,
    SucursalNoEncontradaError,
    SucursalScopeError,
    TipoMovimientoInvalidoError,
)


router = APIRouter(prefix="/tenant/inventario", tags=["Inventario SaaS"])
TenantActual = Annotated[TenantContext, Depends(get_current_tenant_context)]


def _traducir_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RolInventarioTenantError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Rol empresarial sin acceso al inventario",
        )
    if isinstance(exc, SucursalScopeError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado para la sucursal solicitada",
        )
    if isinstance(exc, SucursalNoEncontradaError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sucursal no encontrada",
        )
    if isinstance(exc, RangoFechasInvalidoError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )
    if isinstance(exc, TipoMovimientoInvalidoError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tipo de movimiento invalido",
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Filtro de disponibilidad invalido",
    )


@router.get("", response_model=InventarioConsultaResponse)
def consultar_inventario_tenant(
    tenant: TenantActual,
    sucursal_id: int | None = Query(default=None, gt=0),
    producto: str | None = Query(default=None, min_length=1),
    producto_id: int | None = Query(default=None, gt=0),
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    disponibilidad: DisponibilidadFiltro | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        return InventarioService.consultar_inventario_tenant(
            db, tenant,
            sucursal_id=sucursal_id, producto=producto,
            producto_id=producto_id, categoria_id=categoria_id,
            talla_id=talla_id, color_id=color_id,
            temporada_id=temporada_id,
            disponibilidad=disponibilidad, limit=limit, offset=offset,
        )
    except (RolInventarioTenantError, SucursalScopeError,
            SucursalNoEncontradaError, DisponibilidadInvalidaError) as exc:
        raise _traducir_error(exc) from exc


@router.get("/pos", response_model=InventarioPosConsultaResponse)
def consultar_inventario_pos_tenant(
    tenant: TenantActual,
    db: Session = Depends(get_db),
    sucursal_id: int | None = Query(default=None, gt=0),
    producto: str | None = Query(default=None, min_length=1),
    producto_id: int | None = Query(default=None, gt=0),
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    disponibilidad: DisponibilidadFiltro | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """CU13 POS - inventario vendible, mínimo y limitado por sucursal."""
    try:
        return InventarioService.consultar_inventario_pos_tenant(
            db, tenant,
            sucursal_id=sucursal_id, producto=producto,
            producto_id=producto_id, categoria_id=categoria_id,
            talla_id=talla_id, color_id=color_id,
            temporada_id=temporada_id,
            disponibilidad=disponibilidad, limit=limit, offset=offset,
        )
    except (RolInventarioTenantError, SucursalScopeError,
            SucursalNoEncontradaError, DisponibilidadInvalidaError) as exc:
        raise _traducir_error(exc) from exc


@router.get("/movimientos", response_model=MovimientoInventarioConsultaResponse)
def consultar_movimientos_tenant(
    tenant: TenantActual,
    sucursal_id: int | None = Query(default=None, gt=0),
    tipo: TipoMovimiento | None = Query(default=None),
    producto: str | None = Query(default=None, min_length=1),
    producto_id: int | None = Query(default=None, gt=0),
    variante_producto_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    usuario_id: int | None = Query(default=None, gt=0),
    referencia_tipo: str | None = Query(default=None, min_length=1),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        return InventarioService.consultar_movimientos_tenant(
            db, tenant,
            sucursal_id=sucursal_id, tipo=tipo, producto=producto,
            producto_id=producto_id,
            variante_producto_id=variante_producto_id,
            temporada_id=temporada_id, usuario_id=usuario_id,
            referencia_tipo=referencia_tipo, fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta, limit=limit, offset=offset,
        )
    except (RolInventarioTenantError, SucursalScopeError,
            SucursalNoEncontradaError, RangoFechasInvalidoError,
            TipoMovimientoInvalidoError) as exc:
        raise _traducir_error(exc) from exc


@router.get("/filtros", response_model=InventarioFiltrosResponse)
def filtros_inventario_tenant(
    tenant: TenantActual, db: Session = Depends(get_db),
):
    try:
        return InventarioService.filtros_tenant(db, tenant)
    except (RolInventarioTenantError, SucursalScopeError) as exc:
        raise _traducir_error(exc) from exc
