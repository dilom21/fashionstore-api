"""Tenant merchandising administration protected by nominal catalog RBAC."""
from typing import Annotated

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext
from app.core.tenant_dependencies import get_current_tenant_personal_con_permiso
from app.modules.catalogo.models.models import Producto, Temporada
from app.modules.catalogo.repositories.tenant_merchandising_repository import TenantMerchandisingRepository as Repo
from app.modules.promociones.models.models import Promocion, PromocionProducto
from app.modules.promociones.schemas.schemas import (
    PrecioPromocionalResponse, PromocionCreate, PromocionEstadoUpdate,
    PromocionProductosResponse, PromocionProductosUpdate, PromocionResponse,
    PromocionUpdate, TIPOS_DESCUENTO,
)
from app.modules.temporadas_colecciones.models.models import Coleccion, ProductoColeccion
from app.modules.temporadas_colecciones.schemas.schemas import (
    ColeccionCreate, ColeccionEstadoUpdate, ColeccionProductosResponse,
    ColeccionProductosUpdate, ColeccionDetalleResponse, ColeccionResponse,
    ColeccionUpdate, TemporadaCreate,
    TemporadaDetalleResponse, TemporadaEstadoUpdate, TemporadaResponse,
    TemporadaUpdate,
)

router = APIRouter(prefix="/tenant/merchandising", tags=["Merchandising tenant"])
Consulta = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("CATALOGO", "CONSULTAR")),
]
Crear = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("CATALOGO", "CREAR")),
]
Editar = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("CATALOGO", "EDITAR")),
]
Ejecutar = Annotated[
    TenantContext,
    Depends(get_current_tenant_personal_con_permiso("CATALOGO", "EJECUTAR")),
]


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
def listar_temporadas(
    tenant: Consulta,
    buscar: str | None = Query(default=None, min_length=1, max_length=100),
    estado: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return Repo.listar(db, Temporada, tenant.empresa_id, buscar=buscar,
                       estado=estado, limit=limit, offset=offset)


@router.get("/temporadas/{item_id}", response_model=TemporadaDetalleResponse)
def detalle_temporada(item_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Temporada, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Temporada")
    colecciones = Repo.colecciones(db, tenant.empresa_id, temporada_id=item_id, limit=100, offset=0)
    return {**item.__dict__, "total_colecciones": len(colecciones)}


@router.post("/temporadas", response_model=TemporadaResponse, status_code=201)
def crear_temporada(datos: TemporadaCreate, tenant: Crear, db: Session = Depends(get_db)):
    if datos.fecha_inicio > datos.fecha_fin:
        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    item = Repo.crear_temporada(db, tenant.empresa_id, datos)
    _commit(db, "Ya existe una temporada con esos datos")
    db.refresh(item)
    return item


@router.patch("/temporadas/{item_id}", response_model=TemporadaResponse)
def actualizar_temporada(item_id: int, datos: TemporadaUpdate, tenant: Editar, db: Session = Depends(get_db)):
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
def estado_temporada(item_id: int, datos: TemporadaEstadoUpdate, tenant: Ejecutar, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Temporada, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Temporada")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la temporada")
    db.refresh(item)
    return item


@router.get("/colecciones", response_model=list[ColeccionResponse])
def listar_colecciones(
    tenant: Consulta,
    temporada_id: int | None = Query(default=None, gt=0),
    buscar: str | None = Query(default=None, min_length=1, max_length=150),
    estado: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return Repo.colecciones(db, tenant.empresa_id, temporada_id=temporada_id,
                            buscar=buscar, estado=estado, limit=limit, offset=offset)


@router.get("/colecciones/{item_id}", response_model=ColeccionDetalleResponse)
def detalle_coleccion(item_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    item = Repo.obtener_coleccion(db, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Colección")
    return {**item.__dict__, "temporada": item.temporada,
            "total_productos": len(Repo.productos_de_coleccion(db, tenant.empresa_id, item_id))}


@router.get("/colecciones/{item_id}/productos", response_model=ColeccionProductosResponse)
def productos_de_coleccion(item_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    if Repo.obtener_coleccion(db, tenant.empresa_id, item_id) is None:
        _no_encontrado("Colección")
    productos = Repo.productos_de_coleccion(db, tenant.empresa_id, item_id)
    return {"coleccion_id": item_id, "total": len(productos), "productos": productos}


@router.post("/colecciones", response_model=ColeccionResponse, status_code=201)
def crear_coleccion(datos: ColeccionCreate, tenant: Crear, db: Session = Depends(get_db)):
    _temporada_valida(db, tenant.empresa_id, datos.temporada_id)
    item = Repo.crear_coleccion(db, tenant.empresa_id, datos)
    _commit(db, "Ya existe una colección con esos datos")
    db.refresh(item)
    return item


@router.patch("/colecciones/{item_id}", response_model=ColeccionResponse)
def actualizar_coleccion(item_id: int, datos: ColeccionUpdate, tenant: Editar, db: Session = Depends(get_db)):
    item = Repo.obtener_coleccion(db, tenant.empresa_id, item_id)
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
def estado_coleccion(item_id: int, datos: ColeccionEstadoUpdate, tenant: Ejecutar, db: Session = Depends(get_db)):
    item = Repo.obtener_coleccion(db, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Colección")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la colección")
    db.refresh(item)
    return item


@router.put("/colecciones/{item_id}/productos", response_model=ColeccionProductosResponse)
def productos_coleccion(item_id: int, datos: ColeccionProductosUpdate, tenant: Editar, db: Session = Depends(get_db)):
    if Repo.obtener_coleccion(db, tenant.empresa_id, item_id) is None:
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
def listar_promociones(
    tenant: Consulta,
    buscar: str | None = Query(default=None, min_length=1, max_length=120),
    estado: bool | None = None,
    tipo_descuento: str | None = Query(default=None, min_length=1, max_length=20),
    vigentes: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if tipo_descuento is not None:
        tipo_descuento = tipo_descuento.strip().upper()
        if tipo_descuento not in TIPOS_DESCUENTO:
            raise HTTPException(status_code=422, detail="Tipo de descuento inválido")
    ahora = datetime.now(timezone.utc) if vigentes else None
    items = Repo.listar(db, Promocion, tenant.empresa_id, buscar=buscar,
                         estado=estado, tipo_descuento=tipo_descuento,
                         vigente_ahora=ahora,
                         limit=limit, offset=offset)
    return items


@router.get("/promociones/{item_id}", response_model=PromocionResponse)
def detalle_promocion(item_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Promocion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Promoción")
    return item


@router.get("/promociones/{item_id}/productos", response_model=PromocionProductosResponse)
def productos_de_promocion(item_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    if Repo.obtener(db, Promocion, tenant.empresa_id, item_id) is None:
        _no_encontrado("Promoción")
    productos = Repo.productos_de_promocion(db, tenant.empresa_id, item_id)
    return {"promocion_id": item_id, "total": len(productos), "productos": productos}


@router.get("/productos/{producto_id}/precio-promocional", response_model=PrecioPromocionalResponse)
def precio_promocional(producto_id: int, tenant: Consulta, db: Session = Depends(get_db)):
    producto = db.scalar(select(Producto).where(
        Producto.id == producto_id, Producto.empresa_id == tenant.empresa_id,
        Producto.estado.is_(True),
    ))
    if producto is None:
        _no_encontrado("Producto")
    promociones = Repo.promociones_vigentes_para_producto(db, tenant.empresa_id, producto_id)
    precio = Repo.precio_promocional(producto.precio, promociones[0]) if len(promociones) == 1 else None
    return {"producto_id": producto_id, "precio_base": producto.precio,
            "promociones_vigentes": promociones, "precio_promocional": precio,
            "precio_definitivo": len(promociones) == 1}


def _validar_promocion(datos):
    if datos.fecha_inicio > datos.fecha_fin:
        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    tipo = datos.tipo_descuento.upper()
    if tipo not in {"PORCENTAJE", "MONTO"}:
        raise HTTPException(status_code=422, detail="Tipo de descuento inválido")
    if datos.valor_descuento <= 0 or (tipo == "PORCENTAJE" and datos.valor_descuento > 100):
        raise HTTPException(status_code=422, detail="Valor de descuento inválido")


@router.post("/promociones", response_model=PromocionResponse, status_code=201)
def crear_promocion(datos: PromocionCreate, tenant: Crear, db: Session = Depends(get_db)):
    _validar_promocion(datos)
    item = Repo.crear_promocion(db, tenant.empresa_id, datos)
    _commit(db, "No fue posible crear la promoción")
    db.refresh(item)
    return item


@router.patch("/promociones/{item_id}", response_model=PromocionResponse)
def actualizar_promocion(item_id: int, datos: PromocionUpdate, tenant: Editar, db: Session = Depends(get_db)):
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
def estado_promocion(item_id: int, datos: PromocionEstadoUpdate, tenant: Ejecutar, db: Session = Depends(get_db)):
    item = Repo.obtener(db, Promocion, tenant.empresa_id, item_id)
    if item is None:
        _no_encontrado("Promoción")
    item.estado = datos.estado
    _commit(db, "No fue posible actualizar la promoción")
    db.refresh(item)
    return item


@router.put("/promociones/{item_id}/productos", response_model=PromocionProductosResponse)
def productos_promocion(item_id: int, datos: PromocionProductosUpdate, tenant: Editar, db: Session = Depends(get_db)):
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
