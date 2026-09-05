from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.services.service import ProductoService
from app.modules.inventario.schemas.schemas import InventarioResponse
from app.modules.inventario.services.service import InventarioService
from app.modules.sucursales.services.service import SucursalService

router = APIRouter(prefix="/inventario", tags=["Inventario"])


@router.get("", response_model=list[InventarioResponse])
def listar_inventario(
    sucursal_id: int | None = Query(default=None, gt=0),
    producto_id: int | None = Query(default=None, gt=0),
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    _: Usuario = Depends(get_current_user),
):
    return InventarioService.listar_inventario(
        db,
        sucursal_id=sucursal_id,
        producto_id=producto_id,
        categoria_id=categoria_id,
        talla_id=talla_id,
        color_id=color_id,
        temporada_id=temporada_id,
    )


@router.get("/sucursal/{sucursal_id}", response_model=list[InventarioResponse])
def inventario_por_sucursal(
    sucursal_id: int,
    db: Session = Depends(get_db),
    _: Usuario = Depends(get_current_user),
):
    if SucursalService.obtener_sucursal(db, sucursal_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sucursal no encontrada",
        )
    return InventarioService.listar_inventario(db, sucursal_id=sucursal_id)


@router.get("/producto/{producto_id}", response_model=list[InventarioResponse])
def inventario_por_producto(
    producto_id: int,
    db: Session = Depends(get_db),
    _: Usuario = Depends(get_current_user),
):
    if ProductoService.obtener_producto(db, producto_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado",
        )
    return InventarioService.listar_inventario(db, producto_id=producto_id)


@router.get("/{inventario_id}", response_model=InventarioResponse)
def obtener_inventario(
    inventario_id: int,
    db: Session = Depends(get_db),
    _: Usuario = Depends(get_current_user),
):
    inventario = InventarioService.obtener_inventario(db, inventario_id)
    if inventario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventario no encontrado",
        )
    return inventario
