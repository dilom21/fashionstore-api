"""Tenant merchandising administration protected by nominal catalog RBAC."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.catalogo.models.models import Temporada
from app.modules.catalogo.repositories.tenant_merchandising_repository import TenantMerchandisingRepository as Repo
from app.modules.promociones.models.models import Promocion, PromocionProducto
from app.modules.promociones.schemas.schemas import (
    PromocionCreate, PromocionEstadoUpdate, PromocionProductosResponse,
    PromocionProductosUpdate, PromocionResponse, PromocionUpdate,
)
from app.modules.temporadas_colecciones.models.models import Coleccion, ProductoColeccion
from app.modules.temporadas_colecciones.schemas.schemas import (
    ColeccionCreate, ColeccionEstadoUpdate, ColeccionProductosResponse,
    ColeccionProductosUpdate, ColeccionResponse, ColeccionUpdate,
    TemporadaCreate, TemporadaEstadoUpdate, TemporadaResponse, TemporadaUpdate,
)

router = APIRouter(prefix="/tenant/merchandising", tags=["Merchandising tenant"])
Consulta = Annotated[TenantContext, Depends(get_current_tenant_personal_con_permiso("CATALOGO", "CONSULTAR"))]
Administra = Annotated[TenantContext, Depends(get_current_tenant_personal_con_permiso("CATALOGO", "ADMINISTRAR"))]


def _no_encontrado(nombre: str):
    raise HTTPException(status_code=404, detail=f"{nombre} no encontrada")


def _commit(db: Session, mensaje: str):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=mensaje) from exc


def _temporada_valida(db: Session, empresa_id: int, temporada_id: int) -> Temporada:
    item = Repo.obtener(db, Temporada, empresa_id, temporada_id)
    if item is None:
        _no_encontrado("Temporada")
    if not item.estado:
        raise HTTPException(status_code=409, detail="La temporada está inactiva")
    return item


@router.get("/temporadas", response_model=list[TemporadaResponse])
def listar_temporadas(tenant: Consulta, db: Session = Depends(get_db)):
    return Repo.listar(db, Temporada, tenant.empresa_id)


@router.post("/temporadas", response_model=TemporadaResponse, status_code=201)
def crear_temporada(datos: TemporadaCreate, tenant: Administra, db: Session = Depends(get_db)):
    if datos.fecha_inicio > datos.fecha_fin:
        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    item = Repo.crear_temporada(db, tenant.empresa_id, datos)
    _commit(db, "Ya existe una temporada con esos datos")
    db.refresh(item)
    return item


@router.patch("/temporadas/{item_id}", response_model=TemporadaResponse)
def actualizar_temporada(item_id: int, datos: TemporadaUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Temporada, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Temporada")
    cambios = datos.model_dump(exclude_unset=True)
    for campo, valor in cambios.items():
        setattr(item, campo, valor.strip() if campo == "nombre" and valor else valor)
    if item.fecha_inicio > item.fecha_fin:
        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    _commit(db, "No fue posible actualizar la temporada")
    db.refresh(item)
    return item


@router.patch("/temporadas/{item_id}/estado", response_model=TemporadaResponse)
def estado_temporada(item_id: int, datos: TemporadaEstadoUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Temporada, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Temporada")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la temporada")
    db.refresh(item)
    return item


@router.get("/colecciones", response_model=list[ColeccionResponse])
def listar_colecciones(tenant: Consulta, db: Session = Depends(get_db)):
    return Repo.listar(db, Coleccion, tenant.empresa_id)


@router.post("/colecciones", response_model=ColeccionResponse, status_code=201)
def crear_coleccion(datos: ColeccionCreate, tenant: Administra, db: Session = Depends(get_db)):
    _temporada_valida(db, tenant.empresa_id, datos.temporada_id)
    item = Repo.crear_coleccion(db, tenant.empresa_id, datos)
    _commit(db, "Ya existe una colección con esos datos")
    db.refresh(item)
    return item


@router.patch("/colecciones/{item_id}", response_model=ColeccionResponse)
def actualizar_coleccion(item_id: int, datos: ColeccionUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Coleccion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Colección")
    cambios = datos.model_dump(exclude_unset=True)
    if "temporada_id" in cambios:
        _temporada_valida(db, tenant.empresa_id, cambios["temporada_id"])
    for campo, valor in cambios.items():
        setattr(item, campo, valor.strip() if campo == "nombre" and valor else valor)
    _commit(db, "No fue posible actualizar la colección")
    db.refresh(item)
    return item


@router.patch("/colecciones/{item_id}/estado", response_model=ColeccionResponse)
def estado_coleccion(item_id: int, datos: ColeccionEstadoUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Coleccion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Colección")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la colección")
    db.refresh(item)
    return item


@router.put("/colecciones/{item_id}/productos", response_model=ColeccionProductosResponse)
def productos_coleccion(item_id: int, datos: ColeccionProductosUpdate, tenant: Administra, db: Session = Depends(get_db)):
    if Repo.obtener(db, Coleccion, tenant.empresa_id, item_id) is None:
        _no_encontrado("Colección")
    try:
        productos = Repo.reemplazar_productos(
            db, empresa_id=tenant.empresa_id, relation=ProductoColeccion,
            parent_column=ProductoColeccion.coleccion_id, parent_id=item_id,
            producto_ids=datos.producto_ids, parent_attribute="coleccion_id",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _commit(db, "No fue posible actualizar los productos")
    return {"coleccion_id": item_id, "total": len(productos), "productos": productos}


@router.get("/promociones", response_model=list[PromocionResponse])
def listar_promociones(tenant: Consulta, db: Session = Depends(get_db)):
    return Repo.listar(db, Promocion, tenant.empresa_id)


def _validar_promocion(datos):
    if datos.fecha_inicio > datos.fecha_fin:
        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    tipo = datos.tipo_descuento.upper()
    if tipo not in {"PORCENTAJE", "MONTO"}:
        raise HTTPException(status_code=422, detail="Tipo de descuento inválido")
    if datos.valor_descuento <= 0 or (tipo == "PORCENTAJE" and datos.valor_descuento > 100):
        raise HTTPException(status_code=422, detail="Valor de descuento inválido")


@router.post("/promociones", response_model=PromocionResponse, status_code=201)
def crear_promocion(datos: PromocionCreate, tenant: Administra, db: Session = Depends(get_db)):
    _validar_promocion(datos)
    item = Repo.crear_promocion(db, tenant.empresa_id, datos)
    _commit(db, "No fue posible crear la promoción")
    db.refresh(item)
    return item


@router.patch("/promociones/{item_id}", response_model=PromocionResponse)
def actualizar_promocion(item_id: int, datos: PromocionUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Promocion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Promoción")
    cambios = datos.model_dump(exclude_unset=True)
    for campo, valor in cambios.items():
        if campo == "tipo_descuento" and valor:
            valor = valor.upper()
        setattr(item, campo, valor.strip() if campo == "nombre" and valor else valor)
    _validar_promocion(item)
    _commit(db, "No fue posible actualizar la promoción")
    db.refresh(item)
    return item


@router.patch("/promociones/{item_id}/estado", response_model=PromocionResponse)
def estado_promocion(item_id: int, datos: PromocionEstadoUpdate, tenant: Administra, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Promocion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Promoción")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la promoción")
    db.refresh(item)
    return item


@router.put("/promociones/{item_id}/productos", response_model=PromocionProductosResponse)
def productos_promocion(item_id: int, datos: PromocionProductosUpdate, tenant: Administra, db: Session = Depends(get_db)):
    if Repo.obtener(db, Promocion, tenant.empresa_id, item_id) is None:
        _no_encontrado("Promoción")
    try:
        productos = Repo.reemplazar_productos(
            db, empresa_id=tenant.empresa_id, relation=PromocionProducto,
            parent_column=PromocionProducto.promocion_id, parent_id=item_id,
            producto_ids=datos.producto_ids, parent_attribute="promocion_id",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _commit(db, "No fue posible actualizar los productos")
    return {"promocion_id": item_id, "total": len(productos), "productos": productos}
