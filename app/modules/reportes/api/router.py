"""Endpoints de CU28 - Consultar dashboard y generar reportes (solo lectura).

Actores: ADMINISTRADOR (alcance global y/o sucursal concreta) y
ENCARGADO_SUCURSAL (solo la sucursal de su empleado activo). Se exige contexto
JWT ``personal`` y el RBAC real ``CONSULTAR_REPORTES``:

- CONSULTAR: dashboard, vistas previas JSON.
- EJECUTAR: generacion de PDF/XLSX/CSV.

El reporte de auditoria es exclusivo de ADMINISTRADOR aunque el ENCARGADO tenga
CONSULTAR_REPORTES (la bitacora es global y sensible).

CU28 no escribe datos de negocio, no crea tablas/vistas/SP y no persiste los
archivos generados.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_auth_context, require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reportes.schemas.auditoria import ReporteAuditoriaResponse
from app.modules.reportes.schemas.compras_clientes import (
    ReporteClientesCarritosResponse,
    ReporteComprasResponse,
)
from app.modules.reportes.schemas.dashboard import DashboardResponse
from app.modules.reportes.schemas.inventario import ReporteInventarioResponse
from app.modules.reportes.schemas.operaciones import (
    ReporteDevolucionesResponse,
    ReportePagosResponse,
    ReporteReservasResponse,
)
from app.modules.reportes.schemas.productos import ReporteProductosResponse
from app.modules.reportes.schemas.ventas import ReporteVentasResponse
from app.modules.reportes.services.reportes_service import (
    AGRUPACION_POR_DEFECTO,
    PAGINA_POR_DEFECTO,
    TAMANO_PAGINA_MAXIMO,
    TAMANO_PAGINA_POR_DEFECTO,
    TOP_MAXIMO,
    TOP_POR_DEFECTO,
    UMBRAL_STOCK_BAJO_MAXIMO,
    UMBRAL_STOCK_BAJO_POR_DEFECTO,
    FormatoReporteNoSoportadoError,
    GeneracionReporteError,
    RangoFechasInvalidoError,
    ReporteDemasiadoGrandeError,
    ReportesError,
    ReportesNoAutorizadoError,
    ReportesScopeError,
    ReportesService,
    SucursalReporteNoEncontradaError,
    TipoReporteNoSoportadoError,
)

FUNCION_REPORTES = "CONSULTAR_REPORTES"
CONTEXTO_PERSONAL = "personal"

router = APIRouter(prefix="/reportes", tags=["Reportes"])


def _error(detalle: str, codigo: int) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _contexto_personal(
    contexto: str = Depends(get_current_auth_context),
) -> str:
    """CU28 es exclusivo de PERSONAL: una sesion de cliente no entra."""
    if contexto != CONTEXTO_PERSONAL:
        raise _error(
            "No autorizado: se requiere sesion de personal",
            status.HTTP_403_FORBIDDEN,
        )
    return contexto


def _permiso(accion: str):
    """RBAC real de CU28 (CONSULTAR_REPORTES / CONSULTAR | EJECUTAR)."""
    return require_permission(FUNCION_REPORTES, accion)


def _map_error(exc: Exception) -> HTTPException:
    """Traduce errores de dominio a HTTP, sin exponer SQL ni stacktrace."""
    if isinstance(exc, (ReportesNoAutorizadoError, ReportesScopeError)):
        return _error(
            "No autorizado: la operacion excede su alcance de reportes",
            status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, SucursalReporteNoEncontradaError):
        return _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    if isinstance(
        exc,
        (
            RangoFechasInvalidoError,
            FormatoReporteNoSoportadoError,
            TipoReporteNoSoportadoError,
            ReporteDemasiadoGrandeError,
        ),
    ):
        return _error(
            str(exc) or "Solicitud invalida",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, GeneracionReporteError):
        return _error(
            "No fue posible generar el reporte",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return _error(
        "No fue posible procesar el reporte",
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def filtros_temporales(
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    sucursal_id: int | None = Query(default=None, ge=1),
    agrupacion: Literal["DIA", "MES"] = Query(default=AGRUPACION_POR_DEFECTO),
) -> dict:
    """Filtros comunes: fechas (intervalo [desde, hasta+1d)) y alcance."""
    return {
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "sucursal_id": sucursal_id,
        "agrupacion": agrupacion,
    }


def filtros_paginacion(
    pagina: int = Query(default=PAGINA_POR_DEFECTO, ge=1),
    tamano_pagina: int = Query(
        default=TAMANO_PAGINA_POR_DEFECTO, ge=1, le=TAMANO_PAGINA_MAXIMO
    ),
) -> dict:
    return {"pagina": pagina, "tamano_pagina": tamano_pagina}


def _sin_agrupacion(filtros: dict) -> dict:
    """Reportes que no manejan series temporales ignoran ``agrupacion``."""
    return {
        clave: valor
        for clave, valor in filtros.items()
        if clave != "agrupacion"
    }


@router.get("/dashboard", response_model=DashboardResponse)
def consultar_dashboard(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Resumen ejecutivo del periodo (KPIs, evolucion, canales, comparativo)."""
    try:
        return ReportesService.consultar_dashboard(
            db, usuario, top=top, **filtros
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/ventas", response_model=ReporteVentasResponse)
def consultar_ventas(
    filtros: dict = Depends(filtros_temporales),
    paginacion: dict = Depends(filtros_paginacion),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Reporte de ventas: resumen, desgloses y tabla paginada en SQL."""
    try:
        return ReportesService.consultar_ventas(
            db, usuario, **filtros, **paginacion
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/productos", response_model=ReporteProductosResponse)
def consultar_productos(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Rankings de productos, categorias, tallas, colores y temporadas."""
    try:
        return ReportesService.consultar_productos(
            db, usuario, top=top, **_sin_agrupacion(filtros)
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/inventario", response_model=ReporteInventarioResponse)
def consultar_inventario(
    filtros: dict = Depends(filtros_temporales),
    paginacion: dict = Depends(filtros_paginacion),
    umbral_stock_bajo: int = Query(
        default=UMBRAL_STOCK_BAJO_POR_DEFECTO,
        ge=0,
        le=UMBRAL_STOCK_BAJO_MAXIMO,
    ),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Existencias (stock actual/reservado/disponible) y movimientos."""
    try:
        return ReportesService.consultar_inventario(
            db,
            usuario,
            umbral_stock_bajo=umbral_stock_bajo,
            **_sin_agrupacion(filtros),
            **paginacion,
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/reservas", response_model=ReporteReservasResponse)
def consultar_reservas(
    filtros: dict = Depends(filtros_temporales),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Reservas por estado/sucursal, evolucion y conversion a venta."""
    try:
        return ReportesService.consultar_reservas(db, usuario, **filtros)
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/devoluciones", response_model=ReporteDevolucionesResponse)
def consultar_devoluciones(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Devoluciones reales (CU25) por estado, motivos y valor referencial."""
    try:
        return ReportesService.consultar_devoluciones(
            db, usuario, top=top, **filtros
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/pagos", response_model=ReportePagosResponse)
def consultar_pagos(
    filtros: dict = Depends(filtros_temporales),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Transacciones de pago (no ventas) por estado, metodo y pasarela."""
    try:
        return ReportesService.consultar_pagos(db, usuario, **filtros)
    except ReportesError as exc:
        raise _map_error(exc)


@router.get(
    "/compras-proveedores", response_model=ReporteComprasResponse
)
def consultar_compras_proveedores(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Ordenes de compra por estado/proveedor/sucursal y valor (Decimal)."""
    try:
        return ReportesService.consultar_compras_proveedores(
            db, usuario, top=top, **_sin_agrupacion(filtros)
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get(
    "/clientes-carritos", response_model=ReporteClientesCarritosResponse
)
def consultar_clientes_carritos(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Clientes compradores/recurrentes (sin PII) y estado de carritos."""
    try:
        return ReportesService.consultar_clientes_carritos(
            db, usuario, top=top, **_sin_agrupacion(filtros)
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/auditoria", response_model=ReporteAuditoriaResponse)
def consultar_auditoria(
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    agrupacion: Literal["DIA", "MES"] = Query(
        default=AGRUPACION_POR_DEFECTO
    ),
    pagina: int = Query(default=PAGINA_POR_DEFECTO, ge=1),
    tamano_pagina: int = Query(
        default=TAMANO_PAGINA_POR_DEFECTO, ge=1, le=TAMANO_PAGINA_MAXIMO
    ),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("CONSULTAR")),
    db: Session = Depends(get_db),
):
    """Bitacora agregada. SOLO ADMINISTRADOR (403 tambien para ENCARGADO)."""
    try:
        return ReportesService.consultar_auditoria(
            db,
            usuario,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            agrupacion=agrupacion,
            pagina=pagina,
            tamano_pagina=tamano_pagina,
        )
    except ReportesError as exc:
        raise _map_error(exc)


@router.get("/exportar/{tipo}")
def exportar_reporte(
    tipo: str,
    formato: Literal["PDF", "XLSX", "CSV"] = Query(...),
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(default=TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    umbral_stock_bajo: int = Query(
        default=UMBRAL_STOCK_BAJO_POR_DEFECTO,
        ge=0,
        le=UMBRAL_STOCK_BAJO_MAXIMO,
    ),
    contexto: str = Depends(_contexto_personal),
    usuario: Usuario = Depends(_permiso("EJECUTAR")),
    db: Session = Depends(get_db),
):
    """Genera PDF/XLSX/CSV en memoria con los MISMOS filtros y datos JSON."""
    try:
        contenido, nombre, media_type = ReportesService.generar_reporte(
            db,
            usuario,
            tipo=tipo,
            formato=formato,
            top=top,
            umbral_stock_bajo=umbral_stock_bajo,
            **filtros,
        )
    except ReportesError as exc:
        raise _map_error(exc)

    return Response(
        content=contenido,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Content-Length": str(len(contenido)),
        },
    )


