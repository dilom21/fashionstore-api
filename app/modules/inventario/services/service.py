from sqlalchemy.orm import Session

from app.modules.inventario.models.models import Inventario
from app.modules.inventario.repositories.repository import InventarioRepository


class InventarioService:
    @staticmethod
    def listar_inventario(
        db: Session,
        sucursal_id: int | None = None,
        producto_id: int | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> list[Inventario]:
        return InventarioRepository.listar(
            db,
            sucursal_id=sucursal_id,
            producto_id=producto_id,
            categoria_id=categoria_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
        )

    @staticmethod
    def obtener_inventario(db: Session, inventario_id: int) -> Inventario | None:
        return InventarioRepository.obtener_por_id(db, inventario_id)

    @staticmethod
    def listar_disponibles_por_producto(db: Session, producto_id: int) -> list[Inventario]:
        return InventarioRepository.listar_disponibles_por_producto(db, producto_id)
