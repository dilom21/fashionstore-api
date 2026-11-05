"""E1/5BM: ruta tenant de venta presencial de contado.

Doble guarda: `SAAS_AUTH_ENABLED` (membresia vigente) y `SAAS_VENTAS_ENABLED`
(OFF por defecto). El cuerpo no puede fijar empresa, usuario, precio, total ni
estado; todo se deriva en el servidor y en PostgreSQL.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.ventas.repositories.tenant_venta_contado_repository import (
    TenantVentaContadoRepository, VentaContadoError,
)
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.ventas.schemas.comprobante import ComprobanteVentaResponse
from app.modules.ventas.services.comprobante_service import (
    ComprobanteVentaService, ComprobanteVentaError,
    ComprobanteNoAutorizadoError, VentaComprobanteNoEncontradaError,
    VentaNoCompletadaError, PagoAprobadoNoEncontradoError,
)

router = APIRouter(prefix="/tenant/ventas", tags=["Ventas SaaS (contado)"])

_ROLES_VENTA = {"ADMINISTRADOR", "ENCARGADO_SUCURSAL", "CAJERO"}


class ComprobanteContadoTenantResponse(BaseModel):
    """DTO tenant-safe que no necesita leer ``usuario`` bajo RLS."""
    venta_id: int
    fecha_hora: object
    canal: str
    estado_venta: str
    total: float
    pago: dict
    items: list[dict]


class LineaVentaContado(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventario_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=1000)


class VentaContadoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sucursal_id: int = Field(gt=0)
    operacion_id: UUID
    metodo_pago: Literal["EFECTIVO", "TARJETA", "TRANSFERENCIA", "QR", "OTRO"] = "EFECTIVO"
    lineas: list[LineaVentaContado] = Field(min_length=1, max_length=50)


class LineaVentaContadoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventario_id: int
    cantidad: int
    precio_unitario: float
    subtotal: float


class StockVentaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventario_id: int
    stock_actual: int
    stock_reservado: int


class VentaContadoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int
    pago_id: int
    estado: str
    canal: str
    metodo_pago: str
    total: float
    moneda: str
    operacion_id: UUID
    idempotente: bool
    lineas: list[LineaVentaContadoResponse]
    stock: list[StockVentaResponse]


@router.post("/contado", response_model=VentaContadoResponse, status_code=201)
def registrar_venta_contado(
    datos: VentaContadoRequest,
    response: Response,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    if not settings.saas_auth_enabled or not getattr(
        settings, "saas_ventas_enabled", False
    ):
        raise HTTPException(status_code=503, detail="Ventas SaaS aun no habilitadas")
    if tenant.contexto != "personal" or tenant.rol_nombre not in _ROLES_VENTA:
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")
    try:
        venta = TenantVentaContadoRepository.registrar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            sucursal_id=datos.sucursal_id,
            operacion_id=str(datos.operacion_id),
            metodo_pago=datos.metodo_pago,
            lineas=[x.model_dump() for x in datos.lineas],
        )
        db.commit()
    except VentaContadoError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except Exception:
        db.rollback()
        raise
    if venta.get("idempotente"):
        response.status_code = 200
    return VentaContadoResponse(**venta)


@router.get("/contado/{venta_id}/comprobante", response_model=ComprobanteContadoTenantResponse)
def comprobante_venta_contado(
    venta_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    if not settings.saas_auth_enabled or not getattr(settings, "saas_ventas_enabled", False):
        raise HTTPException(status_code=503, detail="Ventas SaaS aun no habilitadas")
    if tenant.contexto != "personal" or tenant.rol_nombre not in _ROLES_VENTA:
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")
    try:
        # The tenant pre-check prevents an administrator from crossing company
        # boundaries before the legacy CU23 role/sucursal checks run.
        TenantVentaContadoRepository.comprobante(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            venta_id=venta_id,
        )
        comprobante = TenantVentaContadoRepository.comprobante(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            venta_id=venta_id,
        )
        return {
            "venta_id": comprobante["venta_id"],
            "fecha_hora": comprobante["fecha_hora"],
            "canal": comprobante["canal"],
            "estado_venta": comprobante["estado"],
            "total": comprobante["total"],
            "pago": comprobante["pago"],
            "items": comprobante.get("items", []),
        }
    except VentaContadoError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except VentaComprobanteNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Venta no encontrada") from exc
    except ComprobanteNoAutorizadoError as exc:
        raise HTTPException(status_code=403, detail="Venta fuera de alcance") from exc
    except VentaNoCompletadaError as exc:
        raise HTTPException(status_code=409, detail="La venta aun no esta COMPLETADA") from exc
    except PagoAprobadoNoEncontradoError as exc:
        raise HTTPException(status_code=409, detail="La venta no tiene un pago APROBADO") from exc
    except ComprobanteVentaError as exc:
        raise HTTPException(status_code=409, detail="No fue posible emitir el comprobante") from exc
