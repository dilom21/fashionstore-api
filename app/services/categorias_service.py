from sqlalchemy.orm import Session

from app.models.catalogo import Categoria
from app.repositories.categorias_repository import CategoriaRepository


class CategoriaService:
    @staticmethod
    def listar_categorias(db: Session) -> list[Categoria]:
        return CategoriaRepository.listar_activas(db)

    @staticmethod
    def obtener_categoria(db: Session, categoria_id: int) -> Categoria | None:
        return CategoriaRepository.obtener_activa_por_id(db, categoria_id)
