"""T02/T04 customer channel: reservations, checkout, payment and history."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.pagos.errors import PagoElectronicoError, StripeWebhookFirmaError
from app.modules.pagos.schemas.electronico import EstadoPagoVentaResponse, IntencionPagoResponse, WebhookStripeResponse
from app.modules.pagos.services.tenant_electronico_service import PagoTenantError, TenantPagoElectronicoService
from app.modules.ventas.repositories.tenant_cliente_repository import ClienteDigitalError, TenantClienteRepository
from app.modules.reservas.models.models import Reserva

router = APIRouter(tags=["Cliente SaaS"])
Cliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


class ReservaClienteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserva_id: int
    carrito_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    cantidad_lineas: int
    cantidad_unidades: int


class ReservaClienteDetalleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserva_id: int
    carrito_id: int | None
    cliente_id: int
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    items: list[dict]
    cantidad_total_unidades: int


class VentaDigitalTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int = Field(gt=0)
    canal: Literal["WEB", "MOVIL"] = "WEB"


class VentaDigitalTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int
    carrito_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    canal: str
    estado: str
    fecha_hora: datetime
    total: Decimal
    idempotente: bool


class HistorialTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[dict]


class VentaDetalleTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int
    carrito_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    canal: str
    estado: str
    fecha_hora: datetime
    total: Decimal
    lineas: list[dict]


class ComprobanteTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int
    carrito_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    canal: str
    estado: str
    fecha_hora: datetime
    total: Decimal
    pago: dict


class CrearPagoTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int = Field(gt=0)


def _uuid(clave: str) -> str:
    try:
        return str(UUID(clave))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key debe ser UUID") from exc


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ClienteDigitalError, PagoTenantError)):
        return HTTPException(status_code=exc.codigo, detail=exc.detalle)
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="Conflicto de operación idempotente")
    if isinstance(exc, StripeWebhookFirmaError):
        return HTTPException(status_code=400, detail="Firma de webhook inválida")
    if isinstance(exc, PagoElectronicoError):
        return HTTPException(status_code=502, detail="Pasarela de pago no disponible")
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Operación empresarial no disponible")
    raise exc


@router.get("/tenant/cliente/reservas", response_model=list[ReservaClienteResponse])
def reservas_cliente(cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return TenantClienteRepository.listar_reservas(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/tenant/cliente/reservas/{reserva_id}", response_model=ReservaClienteDetalleResponse)
def detalle_reserva_cliente(reserva_id: int, cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return TenantClienteRepository.obtener_reserva(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id,
            reserva_id=reserva_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/tenant/ventas/digital", response_model=VentaDigitalTenantResponse, status_code=201)
def venta_digital(
    datos: VentaDigitalTenantRequest,
    cliente: Cliente,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        resultado, repetida = TenantClienteRepository.crear_venta_digital(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id,
            carrito_id=datos.carrito_id, canal=datos.canal,
            operacion_id=_uuid(idempotency_key),
        )
        return {**resultado, "idempotente": repetida}
    except Exception as exc:
        db.rollback()
        raise _error(exc) from exc


@router.get("/tenant/ventas/historial", response_model=HistorialTenantResponse)
def historial_cliente(cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return {"items": TenantClienteRepository.historial(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id
        )}
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/tenant/ventas/historial/{venta_id}", response_model=VentaDetalleTenantResponse)
def detalle_venta_cliente(venta_id: int, cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return TenantClienteRepository.obtener_venta_detalle(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id,
            venta_id=venta_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/tenant/ventas/{venta_id}/comprobante", response_model=ComprobanteTenantResponse)
def comprobante_cliente(venta_id: int, cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return TenantClienteRepository.comprobante(
            db, empresa_id=cliente.empresa_id, usuario_id=cliente.usuario_id,
            venta_id=venta_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/tenant/pagos/stripe/intencion", response_model=IntencionPagoResponse, status_code=201)
def crear_intencion(
    datos: CrearPagoTenantRequest, cliente: Cliente,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        return TenantPagoElectronicoService.crear_intencion(
            db, tenant=cliente, venta_id=datos.venta_id,
            operacion_id=_uuid(idempotency_key),
        )
    except Exception as exc:
        db.rollback()
        raise _error(exc) from exc


@router.get("/tenant/pagos/stripe/ventas/{venta_id}/estado", response_model=EstadoPagoVentaResponse)
def estado_pago(venta_id: int, cliente: Cliente, db: Session = Depends(get_db)):
    try:
        return TenantPagoElectronicoService.estado(db, tenant=cliente, venta_id=venta_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/tenant/public/{empresa_slug}/pagos/stripe/webhook", response_model=WebhookStripeResponse)
async def webhook_empresa(empresa_slug: str, request: Request, db: Session = Depends(get_db)):
    try:
        return TenantPagoElectronicoService.procesar_webhook(
            db, empresa_slug=empresa_slug, payload=await request.body(),
            firma=request.headers.get("stripe-signature", ""),
        )
    except Exception as exc:
        db.rollback()
        raise _error(exc) from exc
