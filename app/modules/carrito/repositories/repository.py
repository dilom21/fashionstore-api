from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import RecursoProducto, VarianteProducto
from app.modules.inventario.models.models import Inventario

ESTADO_ACTIVO = "ACTIVO"
ESTADO_EXPIRADO = "EXPIRADO"
ESTADO_ELIMINADO = "ELIMINADO"
ESTADO_CONVERTIDO = "CONVERTIDO"


def _carga_completa():
    """Carga carrito + sucursal + detalles + inventario/variante/producto/talla/color/temporada."""
    return (
        joinedload(Carrito.sucursal),
        joinedload(Carrito.detalles)
        .joinedload(DetalleCarrito.inventario)
        .joinedload(Inventario.variante_producto)
        .joinedload(VarianteProducto.producto),
        joinedload(Carrito.detalles)
        .joinedload(DetalleCarrito.inventario)
        .joinedload(Inventario.variante_producto)
        .joinedload(VarianteProducto.talla),
        joinedload(Carrito.detalles)
        .joinedload(DetalleCarrito.inventario)
        .joinedload(Inventario.variante_producto)
        .joinedload(VarianteProducto.color),
        joinedload(Carrito.detalles)
        .joinedload(DetalleCarrito.inventario)
        .joinedload(Inventario.temporada),
    )


class CarritoRepository:
    @staticmethod
    def expirar_vencidos(db: Session, cliente_id: int, limite: datetime) -> int:
        """Marca EXPIRADO los carritos ACTIVO vencidos del cliente."""
        resultado = db.execute(
            update(Carrito)
            .where(
                Carrito.cliente_id == cliente_id,
                Carrito.estado == ESTADO_ACTIVO,
                Carrito.fecha_actualizacion <= limite,
            )
            .values(estado=ESTADO_EXPIRADO)
        )
        return int(resultado.rowcount or 0)

    @staticmethod
    def obtener_activo(
        db: Session,
        cliente_id: int,
        sucursal_id: int,
        *,
        limite_vigencia: datetime | None = None,
    ) -> Carrito | None:
        condiciones = [
            Carrito.cliente_id == cliente_id,
            Carrito.sucursal_id == sucursal_id,
            Carrito.estado == ESTADO_ACTIVO,
        ]
        if limite_vigencia is not None:
            condiciones.append(Carrito.fecha_actualizacion > limite_vigencia)
        statement = (
            select(Carrito).options(*_carga_completa()).where(*condiciones)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def obtener_por_id(db: Session, carrito_id: int) -> Carrito | None:
        statement = (
            select(Carrito)
            .options(*_carga_completa())
            .where(Carrito.id == carrito_id)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def listar_activos(
        db: Session, cliente_id: int, *, limite_vigencia: datetime | None = None
    ) -> list[Carrito]:
        condiciones = [
            Carrito.cliente_id == cliente_id,
            Carrito.estado == ESTADO_ACTIVO,
        ]
        if limite_vigencia is not None:
            condiciones.append(Carrito.fecha_actualizacion > limite_vigencia)
        statement = (
            select(Carrito)
            .options(*_carga_completa())
            .where(*condiciones)
            .order_by(
                Carrito.fecha_actualizacion.desc(),
                Carrito.id.desc(),
            )
        )
        return list(db.scalars(statement).unique().all())

    @staticmethod
    def crear(db: Session, cliente_id: int, sucursal_id: int) -> Carrito:
        ahora = datetime.now(timezone.utc)
        carrito = Carrito(
            cliente_id=cliente_id,
            sucursal_id=sucursal_id,
            fecha_creacion=ahora,
            fecha_actualizacion=ahora,
            estado=ESTADO_ACTIVO,
        )
        db.add(carrito)
        db.flush()
        return carrito

    @staticmethod
    def marcar_eliminado(db: Session, carrito: Carrito) -> None:
        carrito.estado = ESTADO_ELIMINADO
        db.flush()

    @staticmethod
    def marcar_expirado(db: Session, carrito: Carrito) -> None:
        carrito.estado = ESTADO_EXPIRADO
        db.flush()

    @staticmethod
    def marcar_convertido(db: Session, carrito: Carrito) -> None:
        """ACTIVO -> CONVERTIDO (lo usa CU19 al crear la venta digital)."""
        carrito.estado = ESTADO_CONVERTIDO
        db.flush()

    @staticmethod
    def obtener_detalle(
        db: Session, carrito_id: int, detalle_id: int
    ) -> DetalleCarrito | None:
        statement = select(DetalleCarrito).where(
            DetalleCarrito.id == detalle_id,
            DetalleCarrito.carrito_id == carrito_id,
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_detalle_por_inventario(
        db: Session, carrito_id: int, inventario_id: int
    ) -> DetalleCarrito | None:
        statement = select(DetalleCarrito).where(
            DetalleCarrito.carrito_id == carrito_id,
            DetalleCarrito.inventario_id == inventario_id,
        )
        return db.scalar(statement)

    @staticmethod
    def crear_detalle(
        db: Session, carrito_id: int, inventario_id: int, cantidad: int
    ) -> DetalleCarrito:
        detalle = DetalleCarrito(
            carrito_id=carrito_id,
            inventario_id=inventario_id,
            cantidad=cantidad,
        )
        db.add(detalle)
        db.flush()
        return detalle

    @staticmethod
    def eliminar_detalle(db: Session, detalle: DetalleCarrito) -> None:
        db.delete(detalle)
        db.flush()

    @staticmethod
    def contar_detalles(db: Session, carrito_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(DetalleCarrito)
            .where(DetalleCarrito.carrito_id == carrito_id)
        )
        return int(db.scalar(statement) or 0)


class RecursoProductoCarritoRepository:
    @staticmethod
    def listar_recursos_activos(
        db: Session, producto_ids: set[int]
    ) -> list[RecursoProducto]:
        """Recursos activos de los productos, priorizando los principales."""
        if not producto_ids:
            return []
        statement = (
            select(RecursoProducto)
            .where(
                RecursoProducto.producto_id.in_(producto_ids),
                RecursoProducto.estado.is_(True),
            )
            .order_by(
                RecursoProducto.es_principal.desc(),
                RecursoProducto.id.asc(),
            )
        )
        return list(db.scalars(statement).all())
