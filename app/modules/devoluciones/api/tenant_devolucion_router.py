"""E2/5BN: rutas tenant de devoluciones de venta y su procesamiento.

Doble guarda: `SAAS_AUTH_ENABLED` (membresia vigente) y
`SAAS_DEVOLUCIONES_ENABLED` (OFF por defecto). El cuerpo no puede fijar
empresa, usuario, precios, monto, estado ni reembolso: todo se deriva en el
servidor y en PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_context
from app.modules.devoluciones.repositories.tenant_devolucion_repository import (
    DevolucionTenantError,
    TenantDevolucionRepository,
)

router = APIRouter(prefix="/tenant/devoluciones", tags=["Devoluciones SaaS"])

_ROLES_DEVOLUCION = {"ADMINISTRADOR", "ENCARGADO_SUCURSAL"}

EstadoDevolucion = Literal["SOLICITADA", "APROBADA", "RECHAZADA", "COMPLETADA"]


# ---------------------------------------------------------------------------
# Cuerpos de peticion
# ---------------------------------------------------------------------------
class ItemDevolucionCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detalle_venta_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=100000)
    reponer: bool = True
    motivo: str | None = Field(default=None, max_length=255)


class DevolucionCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venta_id: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)
    observacion: str | None = Field(default=None, max_length=2000)
    operacion_id: UUID
    items: list[ItemDevolucionCrear] = Field(min_length=1, max_length=50)


class ProcesoDevolucion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operacion_id: UUID


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class DevolucionResumen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    venta_id: int
    fecha_hora: datetime
    estado: str
    motivo: str
    observacion: str | None = None
    sucursal_id: int
    sucursal_nombre: str
    venta_total: float
    unidades: int
    monto_estimado: float


class ItemDevolucionCreada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detalle_venta_id: int
    inventario_id: int
    cantidad: int
    reponer: bool
    precio_unitario: float


class ReembolsoDeclarado(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: str
    ejecutado: bool
    medio: str
    monto: float


class DevolucionCreada(BaseModel):
    """Respuesta de POST: datos de la SP de creacion, aplanados.

    Se aplana el objeto `devolucion` (igual que la venta de contado) y se
    agrega `idempotente`. No incluye el encabezado consultable (sucursal,
    fecha, observacion) porque la rutina no lo calcula; GET /{devolucion_id}
    responde `DevolucionDetalle` con la consulta completa.
    """

    model_config = ConfigDict(extra="forbid")
    idempotente: bool
    devolucion_id: int
    venta_id: int
    estado: str
    motivo: str
    unidades: int
    monto_estimado: float
    moneda: str
    items: list[ItemDevolucionCreada]
    reembolso: ReembolsoDeclarado
    stock_modificado: bool
    operacion_id: UUID


class ItemDevolucionDetalle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detalle_devolucion_id: int
    detalle_venta_id: int
    cantidad: int
    motivo: str | None = None
    reponer: bool
    inventario_id: int
    precio_unitario: float
    sku: str
    subtotal: float


class ReembolsoDetalle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reembolso_id: int
    estado: str
    monto: float
    moneda: str
    medio: str
    ejecutado: bool
    referencia: str | None = None
    fecha_creacion: datetime
    fecha_ejecucion: datetime | None = None


class DevolucionDetalle(DevolucionResumen):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemDevolucionDetalle]
    # Puede no existir mientras la devolucion no se haya procesado.
    reembolso: ReembolsoDetalle | None = None


class StockDevolucion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventario_id: int
    stock_actual: int
    stock_reservado: int


class ReembolsoProcesado(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reembolso_id: int
    estado: str
    ejecutado: bool
    medio: str
    monto: float
    moneda: str


class DevolucionProcesada(BaseModel):
    """Respuesta de POST /{id}/procesar: contrato propio del procesamiento.

    Aplana el objeto `devolucion` de la SP (incluye stock repuesto y el
    reembolso declarado con `reembolso_id`) y agrega `idempotente`; no es el
    mismo contrato que `DevolucionDetalle`, que consulta la fila completa.
    """

    model_config = ConfigDict(extra="forbid")
    idempotente: bool
    devolucion_id: int
    venta_id: int
    estado: str
    unidades: int
    unidades_repuestas: int
    stock: list[StockDevolucion]
    reembolso: ReembolsoProcesado
    operacion_id: UUID


class TransicionDevolucion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotente: bool
    devolucion_id: int
    estado: str


class LineaVentaDevolucion(BaseModel):
    """Linea de venta con lo que aun se puede devolver de ella."""

    model_config = ConfigDict(extra="forbid")
    detalle_venta_id: int
    inventario_id: int
    variante_producto_id: int
    sku: str
    producto_nombre: str
    talla: str
    color: str
    cantidad: int
    precio_unitario: float
    cantidad_devuelta: int
    cantidad_disponible_devolver: int


class LineasVentaDevolucion(BaseModel):
    """Cabecera + lineas: cuerpo minimo para construir una devolucion."""

    model_config = ConfigDict(extra="forbid")
    venta_id: int
    sucursal_id: int
    estado: str
    total: float
    lineas: list[LineaVentaDevolucion]


# ---------------------------------------------------------------------------
# Guardas comunes
# ---------------------------------------------------------------------------
def _verificar_acceso(tenant: TenantContext) -> None:
    """Flags y alcance ANTES de tocar datos; la base revalida todo."""
    if not settings.saas_auth_enabled or not getattr(
        settings, "saas_devoluciones_enabled", False
    ):
        raise HTTPException(
            status_code=503, detail="Devoluciones SaaS aun no habilitadas"
        )
    if tenant.contexto != "personal":
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")
    if tenant.rol_nombre not in _ROLES_DEVOLUCION:
        raise HTTPException(status_code=403, detail="Se requiere personal autorizado")


def _recurso_no_encontrado() -> HTTPException:
    return HTTPException(status_code=404, detail="Recurso no encontrado")


def _verificar_id(devolucion_id: int) -> None:
    # Un ID invalido y uno ajeno responden igual: no se filtra existencia.
    if devolucion_id <= 0:
        raise _recurso_no_encontrado()


def _error_repositorio(db: Session, exc: DevolucionTenantError) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=exc.codigo, detail=exc.detalle)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
# 5BZ: se declara ANTES de `/{devolucion_id}` para que ninguna regla de un
# solo segmento pueda capturarla. Permite construir POST /tenant/devoluciones
# con `detalle_venta_id` real (sin IDs manuales) y muestra cuanto queda por
# devolver de cada linea.
@router.get(
    "/ventas/{venta_id}/lineas", response_model=LineasVentaDevolucion,
)
def lineas_venta_para_devolucion(
    venta_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(venta_id)
    try:
        resultado = TenantDevolucionRepository.lineas_venta(
            db, empresa_id=tenant.empresa_id, venta_id=venta_id,
        )
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    # Venta inexistente y venta de otra empresa responden igual: no se filtra
    # existencia entre tenants.
    if resultado is None:
        raise _recurso_no_encontrado()
    return LineasVentaDevolucion(**resultado)


@router.get("", response_model=list[DevolucionResumen])
def listar_devoluciones(
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    estado: EstadoDevolucion | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    try:
        return TenantDevolucionRepository.listar(
            db,
            empresa_id=tenant.empresa_id,
            estado=estado,
            limit=limit,
            offset=offset,
        )
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise


@router.post("", response_model=DevolucionCreada, status_code=201)
def crear_devolucion(
    datos: DevolucionCrear,
    response: Response,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    try:
        resultado = TenantDevolucionRepository.crear(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            venta_id=datos.venta_id,
            operacion_id=str(datos.operacion_id),
            motivo=datos.motivo,
            observacion=datos.observacion,
            items=[x.model_dump() for x in datos.items],
        )
        db.commit()
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    if resultado.get("idempotente") is True:
        response.status_code = 200
    return DevolucionCreada(**resultado)


@router.get("/{devolucion_id}", response_model=DevolucionDetalle)
def obtener_devolucion(
    devolucion_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(devolucion_id)
    try:
        devolucion = TenantDevolucionRepository.obtener(
            db, empresa_id=tenant.empresa_id, devolucion_id=devolucion_id
        )
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    if devolucion is None:
        raise _recurso_no_encontrado()
    return DevolucionDetalle(**devolucion)


@router.post("/{devolucion_id}/aprobar", response_model=TransicionDevolucion)
def aprobar_devolucion(
    devolucion_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(devolucion_id)
    try:
        resultado = TenantDevolucionRepository.aprobar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            devolucion_id=devolucion_id,
        )
        db.commit()
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return TransicionDevolucion(**resultado)


@router.post("/{devolucion_id}/rechazar", response_model=TransicionDevolucion)
def rechazar_devolucion(
    devolucion_id: int,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(devolucion_id)
    try:
        resultado = TenantDevolucionRepository.rechazar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            devolucion_id=devolucion_id,
        )
        db.commit()
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return TransicionDevolucion(**resultado)


@router.post("/{devolucion_id}/procesar", response_model=DevolucionProcesada)
def procesar_devolucion(
    devolucion_id: int,
    datos: ProcesoDevolucion,
    tenant: Annotated[TenantContext, Depends(get_current_tenant_context)],
    db: Session = Depends(get_db),
):
    _verificar_acceso(tenant)
    _verificar_id(devolucion_id)
    try:
        resultado = TenantDevolucionRepository.procesar(
            db,
            empresa_id=tenant.empresa_id,
            usuario_id=tenant.usuario_id,
            devolucion_id=devolucion_id,
            operacion_id=str(datos.operacion_id),
        )
        db.commit()
    except DevolucionTenantError as exc:
        raise _error_repositorio(db, exc) from exc
    except Exception:
        db.rollback()
        raise
    return DevolucionProcesada(**resultado)
