from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.sucursales.models.models import Sucursal


class SucursalRepository:
    @staticmethod
    def listar_activas(db: Session) -> list[Sucursal]:
        statement = (
            select(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .where(Sucursal.estado.is_(True))
            .order_by(Sucursal.nombre)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_activa_por_id(db: Session, sucursal_id: int) -> Sucursal | None:
        statement = (
            select(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .where(Sucursal.id == sucursal_id, Sucursal.estado.is_(True))
        )
        return db.scalar(statement)
