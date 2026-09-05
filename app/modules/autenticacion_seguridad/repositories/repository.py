from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)


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

    @staticmethod
    def obtener_cliente_activo_por_usuario_id(
        db: Session, usuario_id: int
    ) -> Cliente | None:
        statement = select(Cliente).where(
            Cliente.usuario_id == usuario_id,
            Cliente.estado.is_(True),
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_empleado_activo_por_usuario_id(
        db: Session, usuario_id: int
    ) -> Empleado | None:
        statement = select(Empleado).where(
            Empleado.usuario_id == usuario_id,
            Empleado.estado.is_(True),
        )
        return db.scalar(statement)
