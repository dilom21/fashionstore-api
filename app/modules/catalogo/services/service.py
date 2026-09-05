from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Categoria, Producto
from app.modules.catalogo.repositories.repository import (
    CategoriaRepository,
    ProductoRepository,
)
from app.modules.catalogo.schemas.schemas import (
    DisponibilidadProductoResponse,
    DisponibilidadSucursalResponse,
    DisponibilidadVarianteResponse,
)
from app.modules.inventario.services.service import InventarioService


class CategoriaService:
    @staticmethod
    def listar_categorias(db: Session) -> list[Categoria]:
        return CategoriaRepository.listar_activas(db)

    @staticmethod
    def obtener_categoria(db: Session, categoria_id: int) -> Categoria | None:
        return CategoriaRepository.obtener_activa_por_id(db, categoria_id)


class ProductoService:
    @staticmethod
    def listar_productos(db: Session) -> list[Producto]:
        return ProductoRepository.listar(db)

    @staticmethod
    def obtener_producto(db: Session, producto_id: int) -> Producto | None:
        return ProductoRepository.obtener_por_id(db, producto_id)

    @staticmethod
    def obtener_disponibilidad(
        db: Session, producto_id: int
    ) -> DisponibilidadProductoResponse | None:
        producto = ProductoRepository.obtener_por_id(db, producto_id)
        if producto is None:
            return None

        sucursales: dict[int, DisponibilidadSucursalResponse] = {}
        for inventario in InventarioService.listar_disponibles_por_producto(db, producto_id):
            sucursal_id = inventario.sucursal.id
            if sucursal_id not in sucursales:
                sucursales[sucursal_id] = DisponibilidadSucursalResponse(
                    sucursal_id=sucursal_id,
                    sucursal=inventario.sucursal.nombre,
                    variantes=[],
                )

            sucursales[sucursal_id].variantes.append(
                DisponibilidadVarianteResponse(
                    variante_id=inventario.variante_producto.id,
                    sku=inventario.variante_producto.sku,
                    talla=inventario.variante_producto.talla.nombre,
                    color=inventario.variante_producto.color.nombre,
                    temporada=inventario.temporada.nombre,
                    stock_disponible=(
                        inventario.stock_actual - inventario.stock_reservado
                    ),
                )
            )

        return DisponibilidadProductoResponse(
            producto_id=producto.id,
            producto=producto.nombre,
            sucursales=list(sucursales.values()),
        )
