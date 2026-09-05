from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.productos import (
    DisponibilidadProductoResponse,
    ProductoDetalleResponse,
    ProductoResponse,
)
from app.services.productos_service import ProductoService

router = APIRouter(prefix="/productos", tags=["Productos"])


@router.get("", response_model=list[ProductoResponse])
def listar_productos(db: Session = Depends(get_db)):
    return ProductoService.listar_productos(db)


@router.get(
    "/{producto_id}/disponibilidad",
    response_model=DisponibilidadProductoResponse,
)
def obtener_disponibilidad(producto_id: int, db: Session = Depends(get_db)):
    disponibilidad = ProductoService.obtener_disponibilidad(db, producto_id)
    if disponibilidad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado",
        )
    return disponibilidad


@router.get("/{producto_id}", response_model=ProductoDetalleResponse)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = ProductoService.obtener_producto(db, producto_id)
    if producto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado",
        )
    return producto
