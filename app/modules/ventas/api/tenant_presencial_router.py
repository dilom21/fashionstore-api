"""CU13 SaaS - ventas presenciales (directa o desde reserva)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente, require_tenant_permission
from app.modules.ventas.schemas.comprobante import ComprobanteVentaResponse
from app.modules.ventas.schemas.schemas import (
    RealizarCompraDigitalRequest,
    RegistrarVentaPresencialRequest,
    VentaDigitalResponse,
    VentaPresencialResponse,
)
from app.modules.ventas.schemas.historial import (
    CanalHistorialFiltro, EstadoHistorialFiltro, HistorialCompraDetalleResponse,
    HistorialComprasResponse,
)
from app.modules.ventas.services.historial_service import (
    CompraAunNoHistoricaError, CompraHistorialNoAutorizadaError,
    CompraHistorialNoEncontradaError, HistorialComprasError,
    HistorialComprasService, PAGINA_POR_DEFECTO, RangoFechasHistorialError,
    TAMANO_PAGINA_MAXIMO, TAMANO_PAGINA_POR_DEFECTO,
)
from app.modules.ventas.services.comprobante_service import (
    ComprobanteNoAutorizadoError,
    ComprobanteVentaError,
    ComprobanteVentaService,
    PagoAprobadoNoEncontradoError,
    VentaComprobanteNoEncontradaError,
    VentaNoCompletadaError,
)
from app.modules.ventas.services.tenant_presencial_service import (
    VentaTenantClienteNoEncontradoError,
    VentaTenantDuplicadaError,
    VentaTenantError,
    VentaTenantInventarioError,
    VentaTenantNoEncontradaError,
    VentaTenantRegistroError,
    VentaTenantReservaEstadoError,
    VentaTenantReservaNoEncontradaError,
    VentaTenantRolNoAutorizadoError,
    VentaTenantScopeError,
    VentaTenantSeleccionError,
    VentaTenantStockError,
    VentaTenantPresencialService,
)
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada,
    VentaTenantIntegridadError,
)
from app.modules.ventas.services.tenant_digital_service import (
    VentaDigitalTenantConflicto, VentaDigitalTenantService,
)

FUNCION_VENTAS = "GESTIONAR_VENTAS"
TenantCrear = Annotated[
    TenantContext, Depends(require_tenant_permission(FUNCION_VENTAS, "CREAR"))
]
TenantConsultar = Annotated[
    TenantContext,
    Depends(require_tenant_permission(FUNCION_VENTAS, "CONSULTAR")),
]
TenantCliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]

router = APIRouter(prefix="/tenant/ventas", tags=["Ventas SaaS CU13"])


def _comprobar_habilitado() -> None:
    if not settings.saas_auth_enabled or not settings.saas_ventas_enabled:
        raise HTTPException(
            status_code=503, detail="Ventas SaaS aun no habilitadas"
        )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, VentaTenantRolNoAutorizadoError):
        return HTTPException(
            status_code=403,
            detail="No autorizado: se requiere ADMINISTRADOR, ENCARGADO_SUCURSAL o CAJERO",
        )
    if isinstance(exc, VentaTenantScopeError):
        return HTTPException(
            status_code=403, detail="No autorizado: operacion fuera de su sucursal"
        )
    if isinstance(
        exc,
        (
            VentaTenantNoEncontradaError,
            VentaTenantClienteNoEncontradoError,
            VentaTenantReservaNoEncontradaError,
        ),
    ):
        return HTTPException(status_code=404, detail="Recurso de la venta no encontrado")
    if isinstance(
        exc,
        (
            VentaTenantStockError,
            VentaTenantDuplicadaError,
            VentaTenantReservaEstadoError,
            VentaTenantInventarioError,
            VentaTenantRegistroError,
            VentaTenantIntegridadError,
        ),
    ):
        return HTTPException(
            status_code=409, detail="No fue posible registrar la venta presencial"
        )
    if isinstance(exc, VentaTenantSeleccionError):
        return HTTPException(
            status_code=422, detail="Seleccion de venta invalida"
        )
    if isinstance(exc, VentaTenantDeshabilitada):
        return HTTPException(
            status_code=503,
            detail="Procedimiento CU13 o aislamiento SQL no disponible",
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Venta no disponible")
    raise exc


def _map_comprobante_error(exc: Exception) -> HTTPException:
    if isinstance(exc, VentaComprobanteNoEncontradaError):
        return HTTPException(status_code=404, detail="Venta no encontrada")
    if isinstance(exc, ComprobanteNoAutorizadoError):
        return HTTPException(
            status_code=403,
            detail="No autorizado: la venta no esta a su alcance",
        )
    if isinstance(exc, VentaNoCompletadaError):
        return HTTPException(
            status_code=409, detail="La venta aun no esta COMPLETADA"
        )
    if isinstance(exc, PagoAprobadoNoEncontradoError):
        return HTTPException(
            status_code=409, detail="La venta no tiene un pago APROBADO"
        )
    raise exc


def _map_historial_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CompraHistorialNoEncontradaError):
        return HTTPException(status_code=404, detail="Compra no encontrada")
    if isinstance(exc, CompraHistorialNoAutorizadaError):
        return HTTPException(status_code=403, detail="Compra fuera de su alcance")
    if isinstance(exc, CompraAunNoHistoricaError):
        return HTTPException(status_code=409, detail="La venta aun no es historica")
    if isinstance(exc, RangoFechasHistorialError):
        return HTTPException(status_code=422, detail="Rango de fechas invalido")
    raise exc


@router.post(
    "/presencial",
    response_model=VentaPresencialResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_venta_presencial_tenant(
    datos: RegistrarVentaPresencialRequest,
    tenant: TenantCrear,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return VentaTenantPresencialService.registrar(db, tenant, datos)
    except VentaTenantError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/digital", response_model=VentaDigitalResponse,
    status_code=status.HTTP_201_CREATED,
)
def realizar_compra_digital_tenant(
    datos: RealizarCompraDigitalRequest,
    tenant: TenantCliente,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return VentaDigitalTenantService.crear(db, tenant, datos)
    except VentaDigitalTenantConflicto as exc:
        raise HTTPException(status_code=409, detail="No fue posible convertir el carrito") from exc
    except VentaTenantDeshabilitada as exc:
        raise HTTPException(status_code=503, detail="Compra digital SaaS no disponible") from exc


@router.get(
    "/{venta_id:int}/comprobante",
    response_model=ComprobanteVentaResponse,
)
def obtener_comprobante_venta_tenant(
    venta_id: int,
    tenant: TenantConsultar,
    db: Session = Depends(get_db),
):
    """CU23 SaaS - comprobante de venta COMPLETADA con pago APROBADO."""
    _comprobar_habilitado()
    try:
        return ComprobanteVentaService.generar_comprobante_tenant(
            db, tenant, venta_id
        )
    except ComprobanteVentaError as exc:
        raise _map_comprobante_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Comprobante no disponible"
        ) from exc


@router.get("/{venta_id:int}", response_model=VentaPresencialResponse)
def obtener_venta_presencial_tenant(
    venta_id: int,
    tenant: TenantConsultar,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return VentaTenantPresencialService.obtener(db, tenant, venta_id)
    except VentaTenantError as exc:
        raise _map_error(exc) from exc


@router.get("/historial", response_model=HistorialComprasResponse)
def consultar_historial_cliente_tenant(
    tenant: TenantCliente,
    estado: EstadoHistorialFiltro | None = Query(default=None),
    canal: CanalHistorialFiltro | None = Query(default=None),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    pagina: int = Query(default=PAGINA_POR_DEFECTO, ge=1),
    tamano_pagina: int = Query(default=TAMANO_PAGINA_POR_DEFECTO, ge=1, le=TAMANO_PAGINA_MAXIMO),
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return HistorialComprasService.consultar_historial_tenant(
            db, tenant, estado=estado.value if estado else None,
            canal=canal.value if canal else None, fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta, pagina=pagina, tamano_pagina=tamano_pagina,
        )
    except HistorialComprasError as exc:
        raise _map_historial_error(exc) from exc


@router.get("/historial/{venta_id}", response_model=HistorialCompraDetalleResponse)
def obtener_detalle_historial_cliente_tenant(
    venta_id: int, tenant: TenantCliente, db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return HistorialComprasService.obtener_detalle_compra_tenant(
            db, tenant, venta_id
        )
    except HistorialComprasError as exc:
        raise _map_historial_error(exc) from exc


@router.get(
    "/historial/{venta_id}/comprobante", response_model=ComprobanteVentaResponse
)
def obtener_comprobante_cliente_tenant(
    venta_id: int, tenant: TenantCliente, db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return ComprobanteVentaService.generar_comprobante_cliente_tenant(
            db, tenant, venta_id
        )
    except ComprobanteVentaError as exc:
        raise _map_comprobante_error(exc) from exc
