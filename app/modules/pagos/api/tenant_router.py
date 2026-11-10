"""CU13 SaaS - pago presencial y confirmacion de venta."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, get_webhook_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente, require_tenant_permission
from app.modules.pagos.schemas.electronico import (
    CrearIntencionPagoRequest, EstadoPagoVentaResponse, IntencionPagoResponse,
    WebhookStripeResponse,
)
from app.modules.pagos.services.tenant_stripe_service import (
    PagoStripeTenantService, StripeTenantConflicto,
)
from app.modules.pagos.schemas.schemas import (
    PagoPresencialResponse,
    RegistrarPagoPresencialRequest,
)
from app.modules.pagos.services.tenant_service import (
    PagoTenantConfirmacionError,
    PagoTenantError,
    PagoTenantEstadoError,
    PagoTenantMetodoError,
    PagoTenantRegistroError,
    PagoTenantReservaError,
    PagoTenantRolNoAutorizadoError,
    PagoTenantScopeError,
    PagoTenantStockError,
    PagoTenantVentaNoEncontradaError,
    PagoTenantVentaNoPresencialError,
    PagoTenantYaAprobadoError,
    PagoTenantPresencialService,
)
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada,
    VentaTenantIntegridadError,
)

FUNCION_PAGOS = "GESTIONAR_PAGOS"
TenantCrear = Annotated[
    TenantContext, Depends(require_tenant_permission(FUNCION_PAGOS, "CREAR"))
]
TenantCliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]

router = APIRouter(prefix="/tenant/pagos", tags=["Pagos SaaS CU13"])


def _comprobar_habilitado() -> None:
    if not settings.saas_auth_enabled or not settings.saas_ventas_enabled:
        raise HTTPException(
            status_code=503, detail="Pagos SaaS aun no habilitados"
        )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PagoTenantRolNoAutorizadoError):
        return HTTPException(
            status_code=403,
            detail="No autorizado: se requiere ADMINISTRADOR, ENCARGADO_SUCURSAL o CAJERO",
        )
    if isinstance(exc, PagoTenantScopeError):
        return HTTPException(
            status_code=403, detail="No autorizado: operacion fuera de su sucursal"
        )
    if isinstance(exc, PagoTenantVentaNoEncontradaError):
        return HTTPException(status_code=404, detail="Venta no encontrada")
    if isinstance(
        exc,
        (
            PagoTenantVentaNoPresencialError,
            PagoTenantEstadoError,
            PagoTenantYaAprobadoError,
            PagoTenantStockError,
            PagoTenantReservaError,
            PagoTenantRegistroError,
            PagoTenantConfirmacionError,
            VentaTenantIntegridadError,
        ),
    ):
        return HTTPException(
            status_code=409, detail="No fue posible registrar el pago presencial"
        )
    if isinstance(exc, PagoTenantMetodoError):
        return HTTPException(status_code=422, detail="Metodo de pago no permitido")
    if isinstance(exc, VentaTenantDeshabilitada):
        return HTTPException(
            status_code=503,
            detail="Procedimiento CU13 o aislamiento SQL no disponible",
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Pago no disponible")
    raise exc


@router.post(
    "/presencial",
    response_model=PagoPresencialResponse,
    status_code=status.HTTP_201_CREATED,
)
def registrar_pago_presencial_tenant(
    datos: RegistrarPagoPresencialRequest,
    tenant: TenantCrear,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return PagoTenantPresencialService.registrar(db, tenant, datos)
    except PagoTenantError as exc:
        raise _map_error(exc) from exc


@router.post("/stripe/intencion", response_model=IntencionPagoResponse)
def crear_intencion_stripe_tenant(
    datos: CrearIntencionPagoRequest, tenant: TenantCliente,
    db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return PagoStripeTenantService.crear_intencion(db, tenant, datos)
    except StripeTenantConflicto as exc:
        raise HTTPException(status_code=409, detail="Venta digital no disponible") from exc


@router.get("/stripe/ventas/{venta_id}/estado", response_model=EstadoPagoVentaResponse)
def estado_stripe_tenant(
    venta_id: int, tenant: TenantCliente, db: Session = Depends(get_db),
):
    _comprobar_habilitado()
    try:
        return PagoStripeTenantService.estado(db, tenant, venta_id)
    except StripeTenantConflicto as exc:
        raise HTTPException(status_code=404, detail="Venta no encontrada") from exc


@router.post("/stripe/webhook", response_model=WebhookStripeResponse)
async def webhook_stripe_tenant(
    request: Request, db: Session = Depends(get_webhook_db),
):
    _comprobar_habilitado()
    if not settings.database_webhook_url:
        raise HTTPException(status_code=503, detail="Pool Stripe no configurado")
    payload = await request.body()
    firma = request.headers.get("stripe-signature", "")
    if not firma:
        raise HTTPException(status_code=400, detail="Firma Stripe requerida")
    try:
        return PagoStripeTenantService.webhook(db, payload, firma)
    except Exception as exc:
        # No filtrar payload, firma, secretos ni SQL. Stripe reintentara 5xx.
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail="Webhook no procesado") from exc
