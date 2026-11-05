"""Tenant-safe inventory Kardex endpoints."""
from datetime import date, datetime, time, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.inventario.repositories.tenant_movimientos_repository import TenantMovimientosRepository
from app.modules.inventario.schemas.schemas import MovimientoInventarioConsultaResponse, MovimientoInventarioItemResponse, TipoMovimiento

router = APIRouter(prefix="/tenant/movimientos-inventario", tags=["Inventario SaaS"])

PersonalInventario = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("VENTAS", "CONSULTAR")),
]


def _sucursal_efectiva(
    db: Session, tenant: TenantContext, sucursal_solicitada: int | None
) -> int | None:
    if tenant.rol_nombre == "ADMINISTRADOR":
        return sucursal_solicitada
    sucursal_actor = TenantMovimientosRepository.sucursal_del_actor(
        db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id
    )
    if sucursal_actor is None:
        raise HTTPException(status_code=403, detail="El personal no tiene una sucursal activa")
    if sucursal_solicitada is not None and sucursal_solicitada != sucursal_actor:
        raise HTTPException(status_code=403, detail="La sucursal no corresponde a su alcance")
    return sucursal_actor


def _rango(desde: date | None, hasta: date | None):
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(status_code=422, detail="fecha_desde no puede ser posterior a fecha_hasta")
    inicio = datetime.combine(desde, time.min, tzinfo=timezone.utc) if desde else None
    fin = datetime.combine(hasta, time.max, tzinfo=timezone.utc) if hasta else None
    return inicio, fin


@router.get("", response_model=MovimientoInventarioConsultaResponse)
def listar_movimientos(
    tenant: PersonalInventario,
    sucursal_id: int | None = Query(default=None, gt=0),
    tipo: TipoMovimiento | None = None,
    producto: str | None = Query(default=None, min_length=1, max_length=120),
    producto_id: int | None = Query(default=None, gt=0),
    variante_producto_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    usuario_id: int | None = Query(default=None, gt=0),
    referencia_tipo: str | None = Query(default=None, min_length=1, max_length=80),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    desde, hasta = _rango(fecha_desde, fecha_hasta)
    sucursal = _sucursal_efectiva(db, tenant, sucursal_id)
    filas, total = TenantMovimientosRepository.listar(
        db,
        empresa_id=tenant.empresa_id,
        sucursal_id=sucursal,
        tipo=tipo.value if tipo else None,
        producto=producto,
        producto_id=producto_id,
        variante_producto_id=variante_producto_id,
        temporada_id=temporada_id,
        usuario_id=usuario_id,
        referencia_tipo=referencia_tipo,
        fecha_desde=desde,
        fecha_hasta=hasta,
        limit=limit,
        offset=offset,
    )
    return {"items": filas, "total": total, "limit": limit, "offset": offset}


@router.get("/{movimiento_id}", response_model=MovimientoInventarioItemResponse)
def obtener_movimiento(
    movimiento_id: int,
    tenant: PersonalInventario,
    db: Session = Depends(get_db),
):
    sucursal = _sucursal_efectiva(db, tenant, None)
    fila = TenantMovimientosRepository.obtener(
        db,
        empresa_id=tenant.empresa_id,
        movimiento_id=movimiento_id,
        sucursal_id=sucursal,
    )
    if fila is None:
        raise HTTPException(status_code=404, detail="Movimiento de inventario no encontrado")
    return fila
