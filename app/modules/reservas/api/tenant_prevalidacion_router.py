"""CU16 SaaS: prevalidacion exclusivamente informativa (NO RESERVA)."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.carrito.repositories.tenant_repository import CarritoTenantError
from app.modules.reservas.repositories.tenant_prevalidacion_repository import (
    TenantReservaPrevalidacionRepository,
)


router = APIRouter(
    prefix="/tenant/reservas", tags=["Reservas SaaS (prevalidacion sin escritura)"]
)
Cliente = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


class PrevalidarReservaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int = Field(gt=0)
    fecha_atencion: datetime
    observacion: str | None = Field(default=None, max_length=500)


class PrevalidacionReservaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int
    sucursal_id: int
    fecha_atencion: datetime
    cantidad_lineas: int
    cantidad_unidades: int
    stock_verificado_preliminarmente: bool
    reserva_creada: bool
    requiere_verificacion_transaccional: bool


@router.post("/prevalidar", response_model=PrevalidacionReservaResponse)
def prevalidar_reserva_cliente(
    datos: PrevalidarReservaRequest,
    cliente: Cliente,
    db: Session = Depends(get_db),
):
    # El nombre y la respuesta distinguen este endpoint de crear reserva.
    # No se llama a sp_crear_reserva_desde_carrito ni se ejecuta COMMIT.
    try:
        return TenantReservaPrevalidacionRepository.verificar(
            db,
            empresa_id=cliente.empresa_id,
            usuario_id=cliente.usuario_id,
            carrito_id=datos.carrito_id,
            fecha_atencion=datos.fecha_atencion,
        )
    except CarritoTenantError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Prevalidacion de reserva no disponible"
        ) from exc
