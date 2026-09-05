from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.sucursales import SucursalResponse
from app.services.sucursales_service import SucursalService

router = APIRouter(prefix="/sucursales", tags=["Sucursales"])


@router.get("", response_model=list[SucursalResponse])
def listar_sucursales(db: Session = Depends(get_db)):
    return SucursalService.listar_sucursales(db)


@router.get("/{sucursal_id}", response_model=SucursalResponse)
def obtener_sucursal(sucursal_id: int, db: Session = Depends(get_db)):
    sucursal = SucursalService.obtener_sucursal(db, sucursal_id)
    if sucursal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sucursal no encontrada",
        )
    return sucursal
