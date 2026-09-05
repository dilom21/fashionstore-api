from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalogo import Categoria


class CategoriaRepository:
    @staticmethod
    def listar_activas(db: Session) -> list[Categoria]:
        statement = (
            select(Categoria)
            .where(Categoria.estado.is_(True))
            .order_by(Categoria.nombre)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_activa_por_id(db: Session, categoria_id: int) -> Categoria | None:
        statement = select(Categoria).where(
            Categoria.id == categoria_id,
            Categoria.estado.is_(True),
        )
        return db.scalar(statement)
