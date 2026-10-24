"""Seis familias de consultas comerciales de administrador (B3/P5).

Se exponen vistas MINIMAS id/estado, sin pagos, totales, datos de clientes
ni mutaciones. Las transacciones legacy siguen cerradas en modo SaaS.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_commercial import TenantComercialRepository
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_admin

TipoComercial = Literal[
    "carritos", "reservas", "ventas", "pagos", "compras", "devoluciones",
]

Admin = Annotated[TenantContext, Depends(get_current_tenant_admin)]
router = APIRouter(prefix="/tenant/comercial", tags=["Consulta Comercial SaaS"])


class OperacionTenantResumen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    estado: str


def _no_encontrado() -> HTTPException:
    return HTTPException(status_code=404, detail="Recurso no encontrado")


@router.get("/{tipo}", response_model=list[OperacionTenantResumen])
def listar_operaciones(
    tipo: TipoComercial,
    admin: Admin,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return TenantComercialRepository.listar(
        db, empresa_id=admin.empresa_id, tipo=tipo,
        limit=limit, offset=offset,
    )


@router.get("/{tipo}/{recurso_id}", response_model=OperacionTenantResumen)
def obtener_operacion(
    tipo: TipoComercial, recurso_id: int, admin: Admin,
    db: Session = Depends(get_db),
):
    if recurso_id <= 0:
        raise _no_encontrado()
    resultado = TenantComercialRepository.obtener(
        db, empresa_id=admin.empresa_id, tipo=tipo, recurso_id=recurso_id,
    )
    if resultado is None:
        raise _no_encontrado()
    return resultado
