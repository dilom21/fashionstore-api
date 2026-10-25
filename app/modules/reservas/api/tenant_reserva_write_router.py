"""CU16 SaaS: creación comercial con JWT y RLS; DOBLE FLAG DESACTIVADO.

Esta ruta no se expone en modo SaaS sin SAAS_RESERVAS_ENABLED=true.
El adaptador bloquea operaciones si el rol SQL no tiene RLS efectivo
para UNA sola empresa, permisos al procedimiento definitivo y plan apto.
NO utiliza el SP experimental sp_lab_reservar_5aj.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente, get_current_tenant_context
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    ReservaTenantDeshabilitada, ReservaTenantIntegridadError,
    crear_reserva_transaccional_sin_exposicion_http,
    cancelar_reserva_transaccional_tenant,
    finalizar_reserva_sin_compra_transaccional_tenant,
)

router = APIRouter(prefix="/tenant/reservas", tags=["Reservas SaaS CU16"])
Cliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]
Miembro = Annotated[TenantContext, Depends(get_current_tenant_context)]


class CrearReservaTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int = Field(gt=0)
    fecha_atencion: datetime
    observacion: str | None = Field(default=None, max_length=500)

    @field_validator("fecha_atencion")
    @classmethod
    def fecha_futura_aware(cls, fecha: datetime) -> datetime:
        if fecha.tzinfo is None or fecha.utcoffset() is None:
            raise ValueError("Fecha debe incluir zona horaria")
        if fecha.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("La fecha de atencion debe ser futura")
        return fecha


class ReservaCreadaTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserva_id: int
    carrito_id: int
    estado: str


@router.post("", response_model=ReservaCreadaTenantResponse, status_code=201)
def crear_reserva_tenant(
    datos: CrearReservaTenantRequest,
    cliente: Cliente,
    db: Session = Depends(get_db),
):
    # Defensa adicional: llamar directamente al router no evita el flag.
    if not settings.saas_auth_enabled or not settings.saas_reservas_enabled:
        raise HTTPException(status_code=503,
                            detail="Reservas SaaS aun no habilitadas")
    try:
        reserva_id = crear_reserva_transaccional_sin_exposicion_http(
            db,
            tenant=cliente,
            carrito_id=datos.carrito_id,
            fecha_atencion=datos.fecha_atencion,
            observacion=datos.observacion,
        )
        return ReservaCreadaTenantResponse(
            reserva_id=reserva_id, carrito_id=datos.carrito_id, estado="PENDIENTE"
        )
    except ReservaTenantDeshabilitada as exc:
        raise HTTPException(
            status_code=503,
            detail="Procedimiento CU16 o aislamiento SQL no disponible"
        ) from exc
    except ReservaTenantIntegridadError as exc:
        raise HTTPException(
            status_code=409, detail="Reserva inconsistente"
        ) from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Operación no autorizada") from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Reserva en conflicto") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Reserva no disponible"
        ) from exc



class ObservacionReservaTenant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observacion: str | None = Field(default=None, max_length=500)


class ReservaEstadoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserva_id: int
    estado: str


def _error_cambio_reserva(exc: Exception) -> HTTPException:
    if isinstance(exc, ReservaTenantDeshabilitada):
        return HTTPException(status_code=503, detail="Operacion SaaS no disponible")
    if isinstance(exc, ReservaTenantIntegridadError):
        return HTTPException(status_code=409, detail="Estado de reserva inconsistente")
    if isinstance(exc, (PermissionError, ValueError)):
        return HTTPException(status_code=403, detail="Operacion no autorizada")
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="Reserva en conflicto")
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(status_code=503, detail="Reserva no disponible")
    raise exc


@router.patch("/{reserva_id}/cancelar", response_model=ReservaEstadoTenantResponse)
def cancelar_reserva_tenant(
    reserva_id: int, datos: ObservacionReservaTenant,
    miembro: Miembro, db: Session = Depends(get_db),
):
    if not settings.saas_auth_enabled or not settings.saas_reservas_enabled:
        raise HTTPException(status_code=503, detail="Reservas SaaS aun no habilitadas")
    if reserva_id <= 0:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if not (
        (miembro.contexto == "cliente" and miembro.rol_nombre == "CLIENTE")
        or (miembro.contexto == "personal" and miembro.rol_nombre
            in {"ADMINISTRADOR", "ENCARGADO_SUCURSAL"})
    ):
        raise HTTPException(status_code=403, detail="Operación no autorizada")
    try:
        estado = cancelar_reserva_transaccional_tenant(
            db, tenant=miembro, reserva_id=reserva_id,
            observacion=datos.observacion,
        )
        return ReservaEstadoTenantResponse(reserva_id=reserva_id, estado=estado)
    except Exception as exc:
        raise _error_cambio_reserva(exc) from exc


@router.patch("/{reserva_id}/finalizar-sin-compra",
              response_model=ReservaEstadoTenantResponse)
def finalizar_reserva_sin_compra_tenant(
    reserva_id: int, datos: ObservacionReservaTenant,
    miembro: Miembro, db: Session = Depends(get_db),
):
    if not settings.saas_auth_enabled or not settings.saas_reservas_enabled:
        raise HTTPException(status_code=503, detail="Reservas SaaS aun no habilitadas")
    if reserva_id <= 0:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if not (miembro.contexto == "personal" and miembro.rol_nombre
            in {"ADMINISTRADOR", "ENCARGADO_SUCURSAL", "CAJERO"}):
        raise HTTPException(status_code=403, detail="Operación no autorizada")
    try:
        estado = finalizar_reserva_sin_compra_transaccional_tenant(
            db, tenant=miembro, reserva_id=reserva_id,
            observacion=datos.observacion,
        )
        return ReservaEstadoTenantResponse(reserva_id=reserva_id, estado=estado)
    except Exception as exc:
        raise _error_cambio_reserva(exc) from exc
