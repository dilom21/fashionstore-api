from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.catalogo.schemas.schemas import (
    CategoriaResponse,
    DisponibilidadProductoResponse,
    ProductoDetalleResponse,
    ProductoResponse,
)
from app.modules.catalogo.services.service import CategoriaService, ProductoService

# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------

categorias_router = APIRouter(prefix="/categorias", tags=["Categorias"])


@categorias_router.get("", response_model=list[CategoriaResponse])
def listar_categorias(db: Session = Depends(get_db)):
    return CategoriaService.listar_categorias(db)


@categorias_router.get("/{categoria_id}", response_model=CategoriaResponse)
def obtener_categoria(categoria_id: int, db: Session = Depends(get_db)):
    categoria = CategoriaService.obtener_categoria(db, categoria_id)
    if categoria is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Categoria no encontrada",
        )
    return categoria


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

productos_router = APIRouter(prefix="/productos", tags=["Productos"])


@productos_router.get("", response_model=list[ProductoResponse])
def listar_productos(db: Session = Depends(get_db)):
    return ProductoService.listar_productos(db)


@productos_router.get(
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


@productos_router.get("/{producto_id}", response_model=ProductoDetalleResponse)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = ProductoService.obtener_producto(db, producto_id)
    if producto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado",
        )
    return producto


# ---------------------------------------------------------------------------
# Router del modulo catalogo (productos + categorias)
# ---------------------------------------------------------------------------

router = APIRouter()
router.include_router(productos_router)
router.include_router(categorias_router)
