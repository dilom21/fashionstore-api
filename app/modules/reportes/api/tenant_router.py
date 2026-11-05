"""Conventional and generative reports scoped by validated tenant identity."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from io import BytesIO
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.asistencia_inteligente.providers.base import AsistenteIAError
from app.modules.reportes.services.tenant_reportes_service import (
    ReporteTenantError, TenantReportesService,
)
from app.modules.reportes.services.tenant_ml_service import TenantMLService

router = APIRouter(prefix="/tenant/reportes", tags=["Reportes tenant"])
PersonalReportes = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("REPORTES", "CONSULTAR")),
]


class FiltrosReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    sucursal_id: int | None = None


class ConsultaReporte(FiltrosReporte):
    tipo: str | None = None
    pregunta: str | None = None


def _dynamic(db: Session, tenant: TenantContext, request: ConsultaReporte) -> dict:
    try:
        return TenantReportesService.dinamico(
            db, empresa_id=tenant.empresa_id, tipo=request.tipo or "",
            pregunta=request.pregunta, fecha_desde=request.fecha_desde,
            fecha_hasta=request.fecha_hasta, sucursal_id=request.sucursal_id)
    except ReporteTenantError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc


def _resumen(db: Session, tenant: TenantContext, filtros: FiltrosReporte) -> dict:
    try:
        return TenantReportesService.resumen(
            db, empresa_id=tenant.empresa_id,
            fecha_desde=filtros.fecha_desde, fecha_hasta=filtros.fecha_hasta,
            sucursal_id=filtros.sucursal_id,
        )
    except ReporteTenantError as exc:
        raise HTTPException(status_code=exc.codigo, detail=exc.detalle) from exc


@router.get("/resumen")
def reporte_resumen(
    tenant: PersonalReportes,
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    sucursal_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    return _resumen(db, tenant, FiltrosReporte(
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, sucursal_id=sucursal_id,
    ))


@router.post("/generativo")
def reporte_generativo(
    filtros: FiltrosReporte, tenant: PersonalReportes, db: Session = Depends(get_db),
):
    if not tenant.permite_reportes_ia:
        raise HTTPException(status_code=403, detail="El plan vigente no incluye reportes generativos")
    resumen = _resumen(db, tenant, filtros)
    try:
        return TenantReportesService.generar(resumen)
    except AsistenteIAError as exc:
        raise HTTPException(status_code=502, detail="No fue posible generar el análisis") from exc


@router.post("/consulta")
def reporte_consulta(request: ConsultaReporte, tenant: PersonalReportes,
                     db: Session = Depends(get_db)):
    """Natural-language report selection remains bounded to aggregate tenant data."""
    return _dynamic(db, tenant, request)


@router.get("/analitica/ml")
def analitica_ml(tenant: PersonalReportes):
    return TenantMLService.analizar(tenant.empresa_id)


@router.get("/exportar/{tipo}")
def exportar_reporte(tipo: str, tenant: PersonalReportes, db: Session = Depends(get_db),
                     formato: str = Query(default="xlsx", pattern="^(xlsx|pdf)$"),
                     fecha_desde: date | None = None, fecha_hasta: date | None = None,
                     sucursal_id: int | None = Query(default=None, gt=0)):
    report = _dynamic(db, tenant, ConsultaReporte(tipo=tipo, fecha_desde=fecha_desde,
                                                   fecha_hasta=fecha_hasta,
                                                   sucursal_id=sucursal_id))
    output = BytesIO()
    if formato == "xlsx":
        from openpyxl import Workbook
        workbook = Workbook(); sheet = workbook.active; sheet.title = "Reporte"
        sheet.append(["Métrica", "Valor"])
        for row in report["filas"]: sheet.append([row["metrica"], str(row["valor"])])
        workbook.save(output); media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen.canvas import Canvas
        canvas = Canvas(output, pagesize=letter); canvas.drawString(50, 750, f"Tenant report: {report['tipo']}")
        y = 720
        for row in report["filas"]:
            canvas.drawString(50, y, f"{row['metrica']}: {row['valor']}"); y -= 20
        canvas.save(); media = "application/pdf"
    output.seek(0)
    return StreamingResponse(output, media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{tipo}.{formato}"'})
