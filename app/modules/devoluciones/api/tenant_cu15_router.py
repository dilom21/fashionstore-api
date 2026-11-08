"""Contrato SaaS CU15. Todas las mutaciones delegan en SQL transaccional.

La ausencia de la migración responde 503 (fail-closed); nunca cae al CU25 ni
ejecuta el procedimiento legacy de devoluciones.
"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from io import BytesIO

from app.core.database import get_db
from app.core.tenant_dependencies import get_current_tenant_context, require_tenant_permission
from app.core.tenant_context import TenantContext
from app.modules.devoluciones.schemas.cu15_schemas import (
    ConfirmarDiferenciaCU15, ConfirmarReembolsoCU15, InspeccionCU15, NotaCU15,
    RechazoCU15, SolicitudCU15,
)

router = APIRouter(prefix="/tenant/devoluciones", tags=["CU15 Cambios y devoluciones"])
Tenant = Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "CONSULTAR"))]


def _not_installed(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="CU15 requiere instalar la migración propuesta")


def _call(db: Session, function: str, tenant: TenantContext, payload: dict):
    try:
        row = db.execute(text(f"SELECT * FROM public.{function}(:empresa_id, :actor_id, cast(:payload as jsonb))"), {
            "empresa_id": tenant.empresa_id, "actor_id": tenant.usuario_id, "payload": __import__("json").dumps(payload),
        }).mappings().one()
        db.commit()
        result = dict(row)
        # PostgreSQL devuelve las funciones jsonb como una única columna.
        # Conservamos el contrato JSON del endpoint en lugar de envolverlo.
        if len(result) == 1:
            value = next(iter(result.values()))
            if isinstance(value, (dict, list)):
                return value
        return result
    except SQLAlchemyError as exc:
        db.rollback()
        # Solo la ausencia de la migración es 503. Las reglas de negocio,
        # stock, plazo y RBAC deben llegar al cliente con su error real.
        message = str(exc)
        if 'undefined_function' in message or 'does not exist' in message:
            raise _not_installed(exc) from exc
        raise HTTPException(status_code=409, detail='Operación CU15 rechazada por la transacción') from exc


@router.get("/venta/{venta_id}/lineas")
def lineas(venta_id: int, tenant: Tenant, db: Session = Depends(get_db)):
    return _call(db, "cu15_lineas_elegibles", tenant, {"venta_id": venta_id})


@router.get("")
def listar(tenant: Tenant, db: Session = Depends(get_db), estado: str | None = None, pagina: int = 1, limite: int = 20):
    return _call(db, "cu15_listar", tenant, {"estado": estado, "pagina": pagina, "limite": limite})


@router.post("", status_code=status.HTTP_201_CREATED)
def solicitar(data: SolicitudCU15, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "CREAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_crear", tenant, data.model_dump(mode="json"))


@router.get("/{operacion_id}")
def detalle(operacion_id: int, tenant: Tenant, db: Session = Depends(get_db)):
    return _call(db, "cu15_detalle", tenant, {"operacion_id": operacion_id})


@router.post("/{operacion_id}/inspeccionar")
def inspeccionar(operacion_id: int, data: InspeccionCU15, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EDITAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_inspeccionar", tenant, {"operacion_id": operacion_id, **data.model_dump(mode="json")})


@router.post("/{operacion_id}/aprobar")
def aprobar(operacion_id: int, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EDITAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_aprobar", tenant, {"operacion_id": operacion_id})


@router.post("/{operacion_id}/rechazar")
def rechazar(operacion_id: int, data: RechazoCU15, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EDITAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_rechazar", tenant, {"operacion_id": operacion_id, **data.model_dump()})


@router.post("/{operacion_id}/completar")
def completar(operacion_id: int, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EJECUTAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_completar", tenant, {"operacion_id": operacion_id})


@router.post("/{operacion_id}/cotizar")
def cotizar(operacion_id: int, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EDITAR"))], db: Session = Depends(get_db)):
    """Congela y persiste la diferencia antes de solicitar el cobro."""
    return _call(db, "cu15_cotizar_diferencia", tenant, {"operacion_id": operacion_id})


@router.get("/{operacion_id}/nota", response_model=NotaCU15)
def nota(operacion_id: int, tenant: Tenant, db: Session = Depends(get_db)):
    return _call(db, "cu15_nota", tenant, {"operacion_id": operacion_id})


@router.get("/{operacion_id}/nota.pdf")
def nota_pdf(operacion_id: int, tenant: Tenant, db: Session = Depends(get_db)):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Generador PDF no instalado") from exc
    data = _call(db, "cu15_nota", tenant, {"operacion_id": operacion_id})
    output = BytesIO(); pdf = canvas.Canvas(output, pagesize=A4)
    y = 800
    for line in ["FASHIONSTORE — NOTA DE CAMBIO/DEVOLUCIÓN", f"N.º {data.get('numero', 'PENDIENTE')}", f"Tipo: {data.get('tipo', '')}", f"Estado: {data.get('estado', '')}", f"Venta: {data.get('venta_id', '')}", f"Origen: Bs {data.get('importe_origen', '0.00')}", f"Sustituto: Bs {data.get('importe_sustituto', '0.00')}", f"Diferencia: Bs {data.get('diferencia', '0.00')}", "Nota de control; no acredita reembolso si figura pendiente."]:
        pdf.drawString(50, y, str(line)); y -= 22
    pdf.save(); output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename=cu15-nota-{operacion_id}.pdf"})


@router.post("/{operacion_id}/reembolso/confirmar")
def confirmar_reembolso(operacion_id: int, data: ConfirmarReembolsoCU15, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EJECUTAR"))], db: Session = Depends(get_db)):
    return _call(db, "cu15_confirmar_reembolso", tenant, {"operacion_id": operacion_id, **data.model_dump()})


@router.post("/{operacion_id}/diferencia/confirmar")
def confirmar_diferencia(operacion_id: int, data: ConfirmarDiferenciaCU15, tenant: Annotated[TenantContext, Depends(require_tenant_permission("GESTIONAR_DEVOLUCIONES", "EJECUTAR"))], db: Session = Depends(get_db)):
    """Registra únicamente un cobro ya autorizado; no crea pagos ficticios."""
    return _call(db, "cu15_confirmar_diferencia", tenant, {"operacion_id": operacion_id, **data.model_dump()})
