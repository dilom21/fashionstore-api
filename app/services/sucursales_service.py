from sqlalchemy.orm import Session

from app.models.personas import Sucursal
from app.repositories.sucursales_repository import SucursalRepository


class SucursalService:
    @staticmethod
    def listar_sucursales(db: Session) -> list[Sucursal]:
        return SucursalRepository.listar_activas(db)

    @staticmethod
    def obtener_sucursal(db: Session, sucursal_id: int) -> Sucursal | None:
        return SucursalRepository.obtener_activa_por_id(db, sucursal_id)
