"""Catalogo publico SaaS por slug.

Esta ruta permanece cerrada hasta que Supabase instale una proyeccion publica
minima y su principal NOLOGIN sin BYPASSRLS. No usa app.actor_token ni intenta
convertir una conexion comercial en una sesion tenant.
"""
from typing import Annotated
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.catalogo.repositories.public_repository import PublicCatalogoRepository
from app.modules.catalogo.schemas.public_schemas import (
    PublicCatalogoResponse,
    PublicProductoDetalleResponse,
    PublicProductoResponse,
)

router = APIRouter(prefix="/catalogo/public", tags=["Catalogo publico SaaS"])
Db = Annotated[Session, Depends(get_db)]


def _db_publica(db: Session) -> None:
    if not settings.saas_public_catalog_enabled:
        raise HTTPException(
            status_code=503,
            detail="El contrato publico SaaS requiere una proyeccion SQL autorizada",
        )
    # La consulta de la proyeccion es la barrera final. No se hace fallback al
    # catalogo legacy si falla RLS/ACL.


def _validar_slug(slug: str) -> str:
    normalizado = slug.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalizado):
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return normalizado


def _error_sql(exc: SQLAlchemyError) -> HTTPException:
    # No propagar DSN, SQL, nombres de tablas ni detalles de PostgreSQL.
    return HTTPException(status_code=503, detail="Catalogo publico no disponible")


def _validar_disponibilidad(valor: str) -> str:
    normalizado = valor.strip().upper()
    if normalizado not in {"TODOS", "CON_STOCK", "SIN_STOCK", "STOCK_BAJO"}:
        raise HTTPException(status_code=422, detail="Filtro de disponibilidad invalido")
    return normalizado


@router.get("/{empresa_slug}/productos", response_model=list[PublicProductoResponse])
def listar_productos(
    empresa_slug: str,
    db: Db,
    buscar: str | None = None,
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    coleccion_id: int | None = Query(default=None, gt=0),
    sucursal_id: int | None = Query(default=None, gt=0),
    disponibilidad: str = Query(default="TODOS"),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    _db_publica(db)
    empresa_slug = _validar_slug(empresa_slug)
    disponibilidad = _validar_disponibilidad(disponibilidad)
    try:
        return PublicCatalogoRepository.listar_productos(
            db, empresa_slug=empresa_slug, buscar=buscar,
            categoria_id=categoria_id, talla_id=talla_id, color_id=color_id,
            temporada_id=temporada_id, coleccion_id=coleccion_id,
            sucursal_id=sucursal_id, disponibilidad=disponibilidad,
            limit=limit, offset=offset,
        )
    except SQLAlchemyError as exc:
        raise _error_sql(exc) from exc


@router.get("/{empresa_slug}/productos/{producto_id}", response_model=PublicProductoDetalleResponse)
def detalle_producto(empresa_slug: str, producto_id: int, db: Db):
    _db_publica(db)
    empresa_slug = _validar_slug(empresa_slug)
    if producto_id < 1:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    try:
        producto = PublicCatalogoRepository.detalle_producto(
            db, empresa_slug=empresa_slug, producto_id=producto_id
        )
    except SQLAlchemyError as exc:
        raise _error_sql(exc) from exc
    if producto is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return producto


@router.get("/{empresa_slug}/filtros", response_model=PublicCatalogoResponse)
def filtros(empresa_slug: str, db: Db):
    _db_publica(db)
    empresa_slug = _validar_slug(empresa_slug)
    try:
        return PublicCatalogoRepository.filtros(db, empresa_slug=empresa_slug)
    except SQLAlchemyError as exc:
        raise _error_sql(exc) from exc
