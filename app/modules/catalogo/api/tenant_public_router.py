"""Public, read-only storefront resolved by a validated active company slug."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.catalogo.repositories.tenant_public_repository import TenantPublicRepository

router = APIRouter(prefix="/tenant/public/{empresa_slug}", tags=["Tienda pública tenant"])
Slug = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class RespuestaPublica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    empresa: dict
    items: list[dict]


def _empresa(db: Session, slug: str) -> dict:
    resultado = TenantPublicRepository.resolver_empresa(db, slug)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    return resultado


@router.get("/catalogo", response_model=RespuestaPublica)
def catalogo_publico(
    empresa_slug: Slug,
    categoria_id: int | None = Query(default=None, gt=0),
    texto: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    empresa = _empresa(db, empresa_slug)
    return {"empresa": empresa, "items": TenantPublicRepository.listar_productos(
        db, int(empresa["id"]), categoria_id=categoria_id,
        texto=texto.strip() if texto else None, limit=limit, offset=offset,
    )}


@router.get("/catalogo/{producto_id}")
def producto_publico(empresa_slug: Slug, producto_id: int, db: Session = Depends(get_db)):
    empresa = _empresa(db, empresa_slug)
    producto = TenantPublicRepository.detalle_producto(db, int(empresa["id"]), producto_id)
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return {"empresa": empresa, "producto": producto}


@router.get("/sucursales", response_model=RespuestaPublica)
def sucursales_publicas(empresa_slug: Slug, db: Session = Depends(get_db)):
    empresa = _empresa(db, empresa_slug)
    return {"empresa": empresa, "items": TenantPublicRepository.listar_sucursales(db, int(empresa["id"]))}


@router.get("/temporadas-colecciones", response_model=RespuestaPublica)
def temporadas_publicas(empresa_slug: Slug, db: Session = Depends(get_db)):
    empresa = _empresa(db, empresa_slug)
    return {"empresa": empresa, "items": TenantPublicRepository.listar_temporadas_colecciones(db, int(empresa["id"]))}


@router.get("/promociones", response_model=RespuestaPublica)
def promociones_publicas(empresa_slug: Slug, db: Session = Depends(get_db)):
    empresa = _empresa(db, empresa_slug)
    return {"empresa": empresa, "items": TenantPublicRepository.listar_promociones(db, int(empresa["id"]))}
