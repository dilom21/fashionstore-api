"""5BO / Etapa F: superficie de verificación del superadministrador.

Solo lectura y solo con la credencial global (nunca un JWT tenant):

* `GET /saas/superadmin/claves`    -> identificadores vigentes (sin huellas
  completas ni claves) para comprobar una rotación o revocación.
* `GET /saas/superadmin/auditoria` -> últimos eventos globales registrados
  (método, ruta, código, resultado, empresa afectada, fecha). No incluye
  cuerpos de request, claves, tokens ni IP en claro.

La bitácora global (`saas.bitacora_global`) sigue siendo independiente; estos
endpoints no la exponen al tenant ni la mezclan con la bitácora empresarial.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.saas_admin_db import get_saas_admin_db
from app.core.saas_superadmin_auth import claves_configuradas, get_superadmin_saas

router = APIRouter(prefix="/saas/superadmin", tags=["Superadministrador SaaS"],
                   dependencies=[Depends(get_superadmin_saas)])

SesionAdmin = Annotated[Session, Depends(get_saas_admin_db)]


@router.get("/claves")
def claves_vigentes():
    """Identificadores de clave vigentes y huella PARCIAL (12 hex)."""
    claves = claves_configuradas()
    return {
        "vigentes": len(claves),
        "rotacion_simultanea": len(claves) > 1,
        "claves": claves,
        "nota": (
            "La clave en claro nunca se expone. Revocar = quitar la huella de "
            "SAAS_SUPERADMIN_KEY_SHA256 y reiniciar el proceso."
        ),
    }


@router.get("/auditoria")
def auditoria_global(
    db: SesionAdmin,
    limite: int = Query(default=50, ge=1, le=200),
):
    try:
        valor = db.execute(
            text("SELECT saas_admin.listar_auditoria(CAST(:limite AS integer))"),
            {"limite": limite},
        ).scalar()
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="Auditoría global no disponible"
        ) from exc
    eventos = valor if isinstance(valor, list) else []
    return {"total": len(eventos), "eventos": eventos}
