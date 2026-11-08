"""E2/5BN: rutas tenant de ordenes de compra y recepcion de mercaderia.

Doble guarda: `SAAS_AUTH_ENABLED` (membresia vigente) y `SAAS_COMPRAS_ENABLED`
(OFF por defecto). El cuerpo no puede fijar empresa, usuario, empleado, estado,
stock ni totales: todo se deriva en el servidor y en PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import (
    get_current_tenant_context,
    get_current_tenant_personal_con_permiso_catalogo,
)
from app.modules.compras.repositories.tenant_orden_compra_repository import (
    OrdenCompraTenantError,
    TenantOrdenCompraRepository,
)

router = APIRouter(prefix="/tenant/ordenes-compra", tags=["Compras SaaS"])

_ROLES_COMPRAS = {"ADMINISTRADOR", "ENCARGADO_SUCURSAL"}

EstadoOrdenCompra = Literal[
    "BORRADOR", "ENVIADA", "PARCIAL", "RECIBIDA", "CANCELADA"
]


# ---------------------------------------------------------------------------
# Cuerpos de peticion
# ---------------------------------------------------------------------------
class LineaOrdenCompraCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variante_producto_id: int = Field(gt=0)
    temporada_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=100000)
    costo_unitario: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class OrdenCompraCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proveedor_id: int = Field(gt=0)
    sucursal_id: int = Field(gt=0)
    fecha_estimada: datetime | None = None
    observacion: str | None = Field(default=None, max_length=2000)
    lineas: list[LineaOrdenCompraCrear] = Field(min_length=1, max_length=50)


class LineaRecepcion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detalle_orden_compra_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=100000)


class RecepcionCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operacion_id: UUID
    observacion: str | None = Field(default=None, max_length=2000)
    lineas: list[LineaRecepcion] = Field(min_length=1, max_length=50)


class CancelacionCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observacion: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class LineaOrdenCompraCreada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variante_producto_id: int
    temporada_id: int
    cantidad: int
    costo_unitario: float


class OrdenCompraCreada(BaseModel):
    """Respuesta de POST: solo lo que devuelve la rutina de creacion.

    La SP no calcula `proveedor_razon_social` ni `sucursal_nombre`; inventarlos
    obligaria a una consulta extra y a responder datos que pueden haber
    cambiado. GET /{orden_id} usa `OrdenCompraDetalle`, que si los consulta.
    """

    model_config = ConfigDict(extra="forbid")
    orden_compra_id: int
    estado: str
    proveedor_id: int
    sucursal_id: int
    total_lineas: int
    lineas: list[LineaOrdenCompraCreada]
    stock_modificado: bool


class OrdenCompraResumen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    proveedor_id: int
    proveedor_razon_social: str
    sucursal_id: int
    sucursal_nombre: str
    estado: str
    fecha_orden: datetime
    fecha_estimada: datetime | None = None
    fecha_recepcion: datetime | None = None
    total_lineas: int


class LineaOrdenCompraDetalle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    variante_producto_id: int
    sku: str
    temporada_id: int
    temporada_nombre: str
    cantidad: int
    costo_unitario: float
    cantidad_recibida: int
    cantidad_pendiente: int


class OrdenCompraDetalle(OrdenCompraResumen):
    model_config = ConfigDict(extra="forbid")
    lineas: list[LineaOrdenCompraDetalle]


class StockRecepcion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventario_id: int
    stock_actual: int
    stock_reservado: int


class RecepcionResultado(BaseModel):
    """Respuesta de POST /recepciones: datos de la SP aplanados.

    Se aplana el objeto `recepcion` (igual que la venta de contado) y se agrega
    `idempotente` para distinguir el replay que responde 200 del alta 201.
    """

    model_config = ConfigDict(extra="forbid")
    idempotente: bool
    recepcion_id: int
    orden_compra_id: int
    estado_orden: str
    unidades_recibidas: int
    stock: list[StockRecepcion]
    operacion_id: UUID


class TransicionOrden(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotente: bool
    orden_compra_id: int
    estado: str


# ---------------------------------------------------------------------------
# Guardas comunes
# ---------------------------------------------------------------------------
def _verificar_acceso(tenant: TenantContext) -> None:
    """Flags y alcance ANTES de tocar datos; la base revalida todo."""
    if not settings.saas_auth_enabled or not getattr(
        settings, "saas_compras_enabled", False
    ):
        raise HTTPException(status_code=503, detail="Compras SaaS aun no habilitadas")
    if tenant.contexto != "personal":
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")
    if tenant.rol_nombre not in _ROLES_COMPRAS:
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")


def _verificar_acceso_lectura(tenant: TenantContext) -> None:
    """Guarda de lectura; la autorización efectiva viene del catálogo RBAC."""
    if not settings.saas_auth_enabled or not getattr(
        settings, "saas_compras_enabled", False
    ):
        raise HTTPException(status_code=503, detail="Compras SaaS aun no habilitadas")
    if tenant.contexto != "personal":
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")


PersonalComprasConsulta = Annotated[
    TenantContext,
    Depends(
        get_current_tenant_personal_con_permiso_catalogo(
            "GESTIONAR_ORDENES_COMPRA", "CONSULTAR"
        )
    ),
]


def _recurso_no_encontrado() -> HTTPException:
    return HTTPException(status_code=404, detail="Recurso no encontrado")


def _verificar_id(orden_id: int) -> None:
    # Un ID invalido y uno ajeno responden igual: no se filtra existencia.
    if orden_id <= 0:
        raise _recurso_no_encontrado()


def _error_repositorio(db: Session, exc: OrdenCompraTenantError) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=exc.codigo, detail=exc.detalle)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=list[OrdenCompraResumen])
def listar_ordenes(
    tenant: PersonalComprasConsulta,
    estado: EstadoOrdenCompra | None = Query(default=None),
    proveedor_id: int | None = Query(default=None, gt=0),
    sucursal_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _verificar_acceso_lectura(tenant)
    try:
        return TenantOrdenCompraRepository.listar(
            db,
            empresa_id=tenant.empresa_id,
            estado=estado,
            proveedor_id=proveedor_id,
            sucursal_id=sucursal_id,
            limit=limit,
            offset=offset,
        )
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise


@router.post("", response_model=OrdenCompraCreada, status_code=201)
def crear_orden(
    datos: OrdenCompraCrear,
    response: Response,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso_lectura(tenant)
    try:
        orden = TenantOrdenCompraRepository.crear(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            proveedor_id=datos.proveedor_id,
            sucursal_id=datos.sucursal_id,
            fecha_estimada=datos.fecha_estimada,
            observacion=datos.observacion,
            lineas=[x.model_dump() for x in datos.lineas],
        )
        db.commit()
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    # La creacion no usa operacion_id: si alguna vez se reutiliza como replay,
    # la respuesta correcta sigue siendo 200 y no 201.
    if orden.get("idempotente") is True:
        response.status_code = 200
    return OrdenCompraCreada(**orden)


@router.get("/{orden_id}", response_model=OrdenCompraDetalle)
def obtener_orden(
    orden_id: int,
    tenant: PersonalComprasConsulta,
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(orden_id)
    try:
        orden = TenantOrdenCompraRepository.obtener(
            db, empresa_id=tenant.empresa_id, orden_id=orden_id
        )
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    if orden is None:
        raise _recurso_no_encontrado()
    return OrdenCompraDetalle(**orden)


@router.post("/{orden_id}/enviar", response_model=TransicionOrden)
def enviar_orden(
    orden_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(orden_id)
    try:
        resultado = TenantOrdenCompraRepository.enviar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            orden_id=orden_id,
        )
        db.commit()
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return TransicionOrden(**resultado)


@router.post("/{orden_id}/cancelar", response_model=TransicionOrden)
def cancelar_orden(
    orden_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
    datos: CancelacionCrear | None = None,
):
    _verificar_acceso(tenant)
    _verificar_id(orden_id)
    try:
        resultado = TenantOrdenCompraRepository.cancelar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            orden_id=orden_id,
            observacion=datos.observacion if datos is not None else None,
        )
        db.commit()
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return TransicionOrden(**resultado)


@router.post(
    "/{orden_id}/recepciones", response_model=RecepcionResultado, status_code=201
)
def recibir_orden(
    orden_id: int,
    datos: RecepcionCrear,
    response: Response,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(orden_id)
    try:
        resultado = TenantOrdenCompraRepository.recibir(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            orden_id=orden_id,
            operacion_id=str(datos.operacion_id),
            observacion=datos.observacion,
            lineas=[x.model_dump() for x in datos.lineas],
        )
        db.commit()
    except OrdenCompraTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    if resultado.get("idempotente") is True:
        response.status_code = 200
    return RecepcionResultado(**resultado)
