from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.seguridad import Usuario


class AuthRepository:
    @staticmethod
    def obtener_usuario_activo_por_correo(
        db: Session, correo: str
    ) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(joinedload(Usuario.rol))
            .where(Usuario.correo == correo, Usuario.estado.is_(True))
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_usuario_activo_por_id(db: Session, usuario_id: int) -> Usuario | None:
        statement = (
            select(Usuario)
            .options(joinedload(Usuario.rol))
            .where(Usuario.id == usuario_id, Usuario.estado.is_(True))
        )
        return db.scalar(statement)
