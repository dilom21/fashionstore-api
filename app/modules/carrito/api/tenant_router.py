"""Primera transaccion SaaS CU15: agregar linea de carrito de cliente propio.

No descuenta ni reserva inventario. El SP de reservas/ventas sigue bloqueado.
"""
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_cliente
from app.modules.carrito.repositories.tenant_repository import (
    CarritoTenantError, CarritoTenantRepository,
)


router = APIRouter(prefix="/tenant/carritos", tags=["Carrito Cliente SaaS"])
ClienteTenant = Annotated[TenantContext, Depends(get_current_tenant_cliente)]


class AgregarItemTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sucursal_id: int = Field(gt=0)
    inventario_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=1000)


class CarritoItemTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int
    detalle_id: int
    inventario_id: int
    cantidad: int
    estado: str


@router.post("/items", response_model=CarritoItemTenantResponse)
def agregar_item_carrito_empresa(
    datos: AgregarItemTenantRequest,
    tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    try:
        resultado = CarritoTenantRepository.agregar_item(
            db,
            empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            sucursal_id=datos.sucursal_id,
            inventario_id=datos.inventario_id,
            cantidad=datos.cantidad,
        )
        db.commit()
        return resultado
    except CarritoTenantError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except IntegrityError as exc:
        # Constraint UNIQUE/FK de PG, incluidas las de cliente/empresa.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Conflicto en el carrito empresarial"
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="No se pudo procesar el carrito"
        ) from exc
    except Exception:
        db.rollback()
        raise


class ActualizarCantidadTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cantidad: int = Field(gt=0, le=1000)


class EstadoCarritoTenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carrito_id: int
    estado: str
    cantidad_lineas: int


Resultado = TypeVar("Resultado")


def _ejecutar_mutacion(db: Session, operacion: Callable[[], Resultado]) -> Resultado:
    """Un commit por accion, rollback total ante error de negocio o SQL."""
    try:
        respuesta = operacion()
        db.commit()
        return respuesta
    except CarritoTenantError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Conflicto en el carrito empresarial"
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="No se pudo procesar el carrito"
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.patch(
    "/{carrito_id}/items/{detalle_id}",
    response_model=CarritoItemTenantResponse,
)
def actualizar_cantidad_carrito_empresa(
    carrito_id: int, detalle_id: int, datos: ActualizarCantidadTenantRequest,
    tenant: ClienteTenant, db: Session = Depends(get_db),
):
    return _ejecutar_mutacion(
        db,
        lambda: CarritoTenantRepository.actualizar_cantidad(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            carrito_id=carrito_id, detalle_id=detalle_id,
            cantidad=datos.cantidad,
        ),
    )


@router.delete(
    "/{carrito_id}/items/{detalle_id}",
    response_model=EstadoCarritoTenantResponse,
)
def eliminar_item_carrito_empresa(
    carrito_id: int, detalle_id: int, tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    return _ejecutar_mutacion(
        db,
        lambda: CarritoTenantRepository.eliminar_detalle(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            carrito_id=carrito_id, detalle_id=detalle_id,
        ),
    )


@router.delete("/{carrito_id}", response_model=EstadoCarritoTenantResponse)
def eliminar_carrito_empresa(
    carrito_id: int, tenant: ClienteTenant,
    db: Session = Depends(get_db),
):
    return _ejecutar_mutacion(
        db,
        lambda: CarritoTenantRepository.eliminar_carrito(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            carrito_id=carrito_id,
        ),
    )
