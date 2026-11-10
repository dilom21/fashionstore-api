"""CU16 SaaS: adaptador tenant de dashboard y reportes de CU28.

La implementación de agregados y exportadores se reutiliza de CU28. Esta capa
es la frontera SaaS: identidad, permiso, empresa y alcance de sucursal vienen
del TenantContext; nunca del body ni de un ``empresa_id`` del frontend.
"""
from datetime import date
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import (
    get_current_tenant_context,
    require_tenant_permission,
)
from app.modules.autenticacion_seguridad.models.models import Empleado
from app.modules.reportes.api.router import (
    _map_error,
    _sin_agrupacion,
    filtros_paginacion,
    filtros_temporales,
)
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
from app.modules.reportes.schemas.generativos import (
    ReporteGenerativoRequest, ReporteGenerativoResponse,
)
from app.modules.reportes.services.generativos_service import (
    ReporteGenerativoError, ReporteGenerativoIntencionError,
    ReporteGenerativoProveedorError, generar_reporte,
)
from app.modules.reportes.services.reportes_service import (
    AGRUPACION_POR_DEFECTO,
    FORMATOS,
    MAX_FILAS_EXPORTACION,
    PAGINA_POR_DEFECTO,
    TAMANO_PAGINA_MAXIMO,
    TAMANO_PAGINA_POR_DEFECTO,
    TOP_MAXIMO,
    TOP_POR_DEFECTO,
    TIPOS_REPORTE,
    UMBRAL_STOCK_BAJO_MAXIMO,
    UMBRAL_STOCK_BAJO_POR_DEFECTO,
    ReportesError,
    ReportesService,
)
from app.modules.sucursales.models.models import Sucursal
from app.modules.predicciones.repositories import (
    obtener_historico, obtener_producto, obtener_sucursal,
)
from app.modules.predicciones.schemas import PrediccionRequest, PrediccionResponse
from app.modules.predicciones.services.demand_model import (
    HORIZONS, PredictionArtifactError, predict, load_verified_artifact,
)
from app.modules.analitica_no_supervisada.schemas import PatronesRequest, PatronesResponse
from app.modules.analitica_no_supervisada.services import PatternArtifactError, analizar

router = APIRouter(prefix="/tenant/reportes", tags=["CU16 Dashboard y reportes SaaS"])
TenantConsulta = Annotated[
    TenantContext,
    Depends(require_tenant_permission("CONSULTAR_REPORTES", "CONSULTAR")),
]
TenantEjecucion = Annotated[
    TenantContext,
    Depends(require_tenant_permission("CONSULTAR_REPORTES", "EJECUTAR")),
]


def _actor(db: Session, tenant: TenantContext, sucursal_id: int | None):
    """Construye la vista mínima que CU28 necesita sin confiar en rol legacy."""
    if tenant.contexto != "personal" or tenant.rol_nombre not in {
        "ADMINISTRADOR", "ENCARGADO_SUCURSAL"
    }:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Rol empresarial sin acceso a reportes")
    empleado = db.scalar(
        select(Empleado).where(
            Empleado.usuario_id == tenant.usuario_id,
            Empleado.empresa_id == tenant.empresa_id,
            Empleado.estado.is_(True),
        )
    )
    if tenant.rol_nombre == "ENCARGADO_SUCURSAL" and empleado is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="No existe una sucursal activa para el personal")
    if sucursal_id is not None:
        sucursal = db.scalar(select(Sucursal).where(
            Sucursal.id == sucursal_id,
            Sucursal.empresa_id == tenant.empresa_id,
            Sucursal.estado.is_(True),
        ))
        if sucursal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Sucursal no encontrada")
        if empleado is not None and tenant.rol_nombre == "ENCARGADO_SUCURSAL" \
                and empleado.sucursal_id != sucursal_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="No autorizado para la sucursal solicitada")
    # ReportesService solo lee rol.nombre y empleado.sucursal_id. El actor
    # sintético evita reutilizar Usuario.rol_id/token legacy; el TenantContext
    # ya publicó app.actor_token y las consultas siguen protegidas por RLS.
    return SimpleNamespace(
        id=tenant.usuario_id,
        rol=SimpleNamespace(nombre=tenant.rol_nombre),
        empleado=empleado,
    )


def _call(fn, db: Session, tenant: TenantContext, filtros: dict, **kwargs):
    try:
        actor = _actor(db, tenant, filtros.get("sucursal_id"))
        return fn(db, actor, **filtros, **kwargs)
    except ReportesError as exc:
        raise _map_error(exc) from exc


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    tenant: TenantConsulta = None,
    db: Session = Depends(get_db),
):
    return _call(ReportesService.consultar_dashboard, db, tenant, filtros, top=top)


@router.get("/ventas", response_model=ReporteVentasResponse)
def ventas(
    filtros: dict = Depends(filtros_temporales), paginacion: dict = Depends(filtros_paginacion),
    tenant: TenantConsulta = None, db: Session = Depends(get_db),
):
    return _call(ReportesService.consultar_ventas, db, tenant, filtros, **paginacion)


@router.get("/productos", response_model=ReporteProductosResponse)
def productos(
    filtros: dict = Depends(filtros_temporales), top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    tenant: TenantConsulta = None, db: Session = Depends(get_db),
):
    return _call(ReportesService.consultar_productos, db, tenant, _sin_agrupacion(filtros), top=top)


@router.get("/inventario", response_model=ReporteInventarioResponse)
def inventario(
    filtros: dict = Depends(filtros_temporales), paginacion: dict = Depends(filtros_paginacion),
    umbral_stock_bajo: int = Query(UMBRAL_STOCK_BAJO_POR_DEFECTO, ge=0, le=UMBRAL_STOCK_BAJO_MAXIMO),
    tenant: TenantConsulta = None, db: Session = Depends(get_db),
):
    return _call(ReportesService.consultar_inventario, db, tenant, _sin_agrupacion(filtros),
                 umbral_stock_bajo=umbral_stock_bajo, **paginacion)


@router.get("/reservas", response_model=ReporteReservasResponse)
def reservas(filtros: dict = Depends(filtros_temporales), tenant: TenantConsulta = None,
             db: Session = Depends(get_db)):
    return _call(ReportesService.consultar_reservas, db, tenant, filtros)


@router.get("/devoluciones", response_model=ReporteDevolucionesResponse)
def devoluciones(filtros: dict = Depends(filtros_temporales),
                 top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
                 tenant: TenantConsulta = None, db: Session = Depends(get_db)):
    return _call(ReportesService.consultar_devoluciones, db, tenant, filtros, top=top)


@router.get("/pagos", response_model=ReportePagosResponse)
def pagos(filtros: dict = Depends(filtros_temporales), tenant: TenantConsulta = None,
          db: Session = Depends(get_db)):
    return _call(ReportesService.consultar_pagos, db, tenant, filtros)


@router.get("/compras-proveedores", response_model=ReporteComprasResponse)
def compras(filtros: dict = Depends(filtros_temporales),
            top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
            tenant: TenantConsulta = None, db: Session = Depends(get_db)):
    return _call(ReportesService.consultar_compras_proveedores, db, tenant,
                 _sin_agrupacion(filtros), top=top)


@router.get("/clientes-carritos", response_model=ReporteClientesCarritosResponse)
def clientes(filtros: dict = Depends(filtros_temporales),
             top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
             tenant: TenantConsulta = None, db: Session = Depends(get_db)):
    return _call(ReportesService.consultar_clientes_carritos, db, tenant,
                 _sin_agrupacion(filtros), top=top)


@router.get("/auditoria", response_model=ReporteAuditoriaResponse)
def auditoria(
    fecha_desde: date | None = None, fecha_hasta: date | None = None,
    agrupacion: Literal["DIA", "MES"] = AGRUPACION_POR_DEFECTO,
    pagina: int = Query(PAGINA_POR_DEFECTO, ge=1),
    tamano_pagina: int = Query(TAMANO_PAGINA_POR_DEFECTO, ge=1, le=TAMANO_PAGINA_MAXIMO),
    tenant: TenantConsulta = None, db: Session = Depends(get_db),
):
    if tenant.rol_nombre != "ADMINISTRADOR":
        raise HTTPException(status_code=403, detail="Auditoria requiere ADMINISTRADOR")
    return _call(ReportesService.consultar_auditoria, db, tenant, {
        "fecha_desde": fecha_desde, "fecha_hasta": fecha_hasta,
        "agrupacion": agrupacion, "sucursal_id": None,
        "pagina": pagina, "tamano_pagina": tamano_pagina,
    })


@router.get("/exportar/{tipo}")
def exportar(
    tipo: str, formato: Literal["PDF", "XLSX", "CSV"] = Query(...),
    filtros: dict = Depends(filtros_temporales),
    top: int = Query(TOP_POR_DEFECTO, ge=1, le=TOP_MAXIMO),
    umbral_stock_bajo: int = Query(UMBRAL_STOCK_BAJO_POR_DEFECTO, ge=0, le=UMBRAL_STOCK_BAJO_MAXIMO),
    tenant: TenantEjecucion = None, db: Session = Depends(get_db),
):
    if tipo not in TIPOS_REPORTE or formato not in FORMATOS:
        raise HTTPException(status_code=422, detail="Tipo o formato de reporte no soportado")
    try:
        actor = _actor(db, tenant, filtros.get("sucursal_id"))
        contenido, nombre, media_type = ReportesService.generar_reporte(
            db, actor, tipo=tipo, formato=formato, top=top,
            umbral_stock_bajo=umbral_stock_bajo, **filtros,
        )
    except ReportesError as exc:
        raise _map_error(exc) from exc
    return Response(content=contenido, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{nombre}"',
        "Content-Length": str(len(contenido)),
    })


@router.post("/generativo", response_model=ReporteGenerativoResponse)
def generativo(
    datos: ReporteGenerativoRequest,
    tenant: TenantConsulta = None,
    db: Session = Depends(get_db),
):
    """Interpreta una solicitud con IA y ejecuta solo reportes allowlist."""
    if not tenant.permite_reportes_ia:
        raise HTTPException(status_code=403,
                            detail="El plan de esta empresa no habilita reportes con IA")
    try:
        actor = _actor(db, tenant, None)
        return generar_reporte(
            db, actor, datos,
            actor_factory=lambda sucursal_id: _actor(db, tenant, sucursal_id),
        )
    except ReporteGenerativoIntencionError as exc:
        raise HTTPException(status_code=422,
                            detail="La solicitud no corresponde a un reporte soportado") from exc
    except ReporteGenerativoProveedorError as exc:
        raise HTTPException(status_code=502,
                            detail="El proveedor de IA no pudo interpretar la solicitud") from exc
    except ReportesError as exc:
        raise _map_error(exc) from exc
    except ReporteGenerativoError as exc:
        raise HTTPException(status_code=502,
                            detail="No fue posible generar el reporte") from exc


@router.post("/predicciones", response_model=PrediccionResponse)
def prediccion_demanda(
    datos: PrediccionRequest,
    tenant: TenantConsulta = None,
    db: Session = Depends(get_db),
):
    """Predice unidades futuras sin aceptar empresa_id del cliente.

    La autorización ocurre antes de cargar el artefacto o consultar el
    histórico. El histórico se agrega por día y solo incluye COMPLETADAS.
    """
    if not tenant.permite_reportes_ia:
        raise HTTPException(status_code=403,
                            detail="El plan de esta empresa no habilita predicciones")
    try:
        producto = obtener_producto(db, empresa_id=tenant.empresa_id,
                                    producto_id=datos.producto_id)
        if producto is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        actor = _actor(db, tenant, datos.sucursal_id)
        sucursal_id = datos.sucursal_id
        if tenant.rol_nombre == "ENCARGADO_SUCURSAL":
            sucursal_id = actor.empleado.sucursal_id
        sucursal = (obtener_sucursal(db, empresa_id=tenant.empresa_id,
                                      sucursal_id=sucursal_id)
                    if sucursal_id is not None else None)
        if datos.sucursal_id is not None and sucursal is None:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada")
        _, metadata = load_verified_artifact()
        filas = obtener_historico(db, empresa_id=tenant.empresa_id,
                                  producto_id=producto.id, sucursal_id=sucursal_id,
                                  hasta=date.today())
        if not filas:
            raise HTTPException(status_code=422,
                                detail={"estado": "SIN_DATOS",
                                        "mensaje": "No hay ventas COMPLETADAS para este filtro"})
        valores = {fecha: float(unidades or 0) for fecha, unidades in filas}
        fecha_corte = max(fecha for fecha, _ in filas)
        historico = [valores.get(fecha_corte.fromordinal(fecha_corte.toordinal() - offset), 0)
                     for offset in range(89, -1, -1)]
        if sum(1 for value in historico if value > 0) < 28:
            raise HTTPException(status_code=422,
                                detail={"estado": "NO_DISPONIBLE",
                                        "mensaje": "Se requieren 28 días observables de histórico"})
        valor = predict(historico, fecha_corte, datos.horizonte_dias)
        return PrediccionResponse(
            tipo="DEMANDA_UNIDADES", horizonte_dias=datos.horizonte_dias,
            unidad="UNIDADES_VENDIDAS", producto_id=producto.id,
            producto_nombre=producto.nombre, sucursal_id=sucursal_id,
            sucursal_nombre=sucursal.nombre if sucursal else None,
            fecha_corte_datos=fecha_corte, valor_predicho=round(valor, 2),
            historico_dias=len(historico), modelo=metadata["algorithm"],
            version_modelo=metadata["version"], procedencia_entrenamiento="SINTETICO",
            metricas_validacion=metadata["metrics_validation"],
            baseline_metricas_test=metadata["baseline_metrics_test"],
            aviso="Estimación entrenada con datos sintéticos; no garantiza precisión comercial.",
        )
    except HTTPException:
        raise
    except (PredictionArtifactError, ValueError) as exc:
        raise HTTPException(status_code=503,
            detail="Predicción no disponible: artefacto o datos inválidos") from exc


@router.post("/patrones", response_model=PatronesResponse)
def patrones_no_supervisados(
    datos: PatronesRequest,
    tenant: TenantConsulta = None,
    db: Session = Depends(get_db),
):
    """Agrupa productos únicamente con agregados autorizados del tenant."""
    if not tenant.permite_reportes_ia:
        raise HTTPException(status_code=403, detail="El plan de esta empresa no habilita analítica avanzada")
    try:
        actor = _actor(db, tenant, datos.sucursal_id)
        if tenant.rol_nombre == "ENCARGADO_SUCURSAL" and datos.sucursal_id is None and actor.empleado:
            datos = datos.model_copy(update={"sucursal_id": actor.empleado.sucursal_id})
        return analizar(db, tenant, datos)
    except HTTPException:
        raise
    except (PatternArtifactError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Patrones no disponibles: artefacto o datos inválidos") from exc
