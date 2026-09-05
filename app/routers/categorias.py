from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.categorias import CategoriaResponse
from app.services.categorias_service import CategoriaService

router = APIRouter(prefix="/categorias", tags=["Categorias"])


@router.get("", response_model=list[CategoriaResponse])
def listar_categorias(db: Session = Depends(get_db)):
    return CategoriaService.listar_categorias(db)


@router.get("/{categoria_id}", response_model=CategoriaResponse)
def obtener_categoria(categoria_id: int, db: Session = Depends(get_db)):
    categoria = CategoriaService.obtener_categoria(db, categoria_id)
    if categoria is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Categoria no encontrada",
        )
    return categoria
