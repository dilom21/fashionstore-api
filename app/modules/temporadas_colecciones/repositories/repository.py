from datetime import date, datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import Producto, Temporada
from app.modules.inventario.models.models import Inventario
from app.modules.temporadas_colecciones.models.models import (
    Coleccion,
    ProductoColeccion,
)


def registrar_evento_bitacora(
    db: Session,
    *,
    usuario_id: int,
    accion: str,
    entidad_afectada: str,
    descripcion: str,
) -> None:
    """Escritura manual en bitacora para entidades sin trigger de auditoria.

    Las tablas temporada, coleccion y producto_coleccion no poseen trigger
    fn_registrar_bitacora, por lo que CU08 audita manualmente cada escritura.
    """
    db.add(
        Bitacora(
            usuario_id=usuario_id,
            fecha_hora=datetime.now(timezone.utc),
            accion=accion,
            entidad_afectada=entidad_afectada,
            descripcion=descripcion,
        )
    )
    db.flush()


class TemporadaRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Temporada]:
        statement = select(Temporada)
        if buscar:
            statement = statement.where(
                Temporada.nombre.ilike(f"%{buscar.strip()}%")
            )
        if estado is not None:
            statement = statement.where(Temporada.estado.is_(estado))
        statement = statement.order_by(
            Temporada.fecha_inicio.desc(), Temporada.id.desc()
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, temporada_id: int) -> Temporada | None:
        return db.get(Temporada, temporada_id)

    @staticmethod
    def buscar_por_nombre_normalizado(
        db: Session, nombre: str, excluir_id: int | None = None
    ) -> Temporada | None:
        statement = select(Temporada).where(
            func.lower(Temporada.nombre) == nombre.strip().lower()
        )
        if excluir_id is not None:
            statement = statement.where(Temporada.id != excluir_id)
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        nombre: str,
        fecha_inicio: date,
        fecha_fin: date,
    ) -> Temporada:
        temporada = Temporada(
            nombre=nombre,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            estado=True,
        )
        db.add(temporada)
        db.flush()
        return temporada

    @staticmethod
    def contar_colecciones_activas(db: Session, temporada_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Coleccion)
            .where(
                Coleccion.temporada_id == temporada_id,
                Coleccion.estado.is_(True),
            )
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def contar_inventario_con_stock(db: Session, temporada_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Inventario)
            .where(
                Inventario.temporada_id == temporada_id,
                or_(
                    Inventario.stock_actual > 0,
                    Inventario.stock_reservado > 0,
                ),
            )
        )
        return int(db.scalar(statement) or 0)


class ColeccionRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        temporada_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Coleccion]:
        statement = select(Coleccion).options(joinedload(Coleccion.temporada))
        if buscar:
            statement = statement.where(
                Coleccion.nombre.ilike(f"%{buscar.strip()}%")
            )
        if temporada_id is not None:
            statement = statement.where(Coleccion.temporada_id == temporada_id)
        if estado is not None:
            statement = statement.where(Coleccion.estado.is_(estado))
        statement = statement.order_by(
            Coleccion.temporada_id, Coleccion.nombre, Coleccion.id
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, coleccion_id: int) -> Coleccion | None:
        statement = (
            select(Coleccion)
            .options(joinedload(Coleccion.temporada))
            .where(Coleccion.id == coleccion_id)
        )
        return db.scalar(statement)

    @staticmethod
    def buscar_por_nombre_en_temporada(
        db: Session,
        *,
        temporada_id: int,
        nombre: str,
        excluir_id: int | None = None,
    ) -> Coleccion | None:
        statement = select(Coleccion).where(
            Coleccion.temporada_id == temporada_id,
            func.lower(Coleccion.nombre) == nombre.strip().lower(),
        )
        if excluir_id is not None:
            statement = statement.where(Coleccion.id != excluir_id)
        return db.scalar(statement)

    @staticmethod
    def crear(
        db: Session,
        *,
        temporada_id: int,
        nombre: str,
        descripcion: str | None,
    ) -> Coleccion:
        coleccion = Coleccion(
            temporada_id=temporada_id,
            nombre=nombre,
            descripcion=descripcion,
            estado=True,
        )
        db.add(coleccion)
        db.flush()
        return coleccion

    @staticmethod
    def listar_productos(db: Session, coleccion_id: int) -> list[Producto]:
        statement = (
            select(Producto)
            .join(
                ProductoColeccion,
                ProductoColeccion.producto_id == Producto.id,
            )
            .where(ProductoColeccion.coleccion_id == coleccion_id)
            .order_by(Producto.nombre, Producto.id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def contar_productos(db: Session, coleccion_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(ProductoColeccion)
            .where(ProductoColeccion.coleccion_id == coleccion_id)
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def listar_producto_ids(db: Session, coleccion_id: int) -> list[int]:
        statement = select(ProductoColeccion.producto_id).where(
            ProductoColeccion.coleccion_id == coleccion_id
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_productos_por_ids(
        db: Session, producto_ids: list[int]
    ) -> list[Producto]:
        if not producto_ids:
            return []
        statement = select(Producto).where(Producto.id.in_(producto_ids))
        return list(db.scalars(statement).all())

    @staticmethod
    def reemplazar_productos(
        db: Session, *, coleccion_id: int, producto_ids: list[int]
    ) -> None:
        """Reemplazo atomico del conjunto de productos de una coleccion.

        Borra las relaciones actuales e inserta el conjunto nuevo dentro de
        la misma transaccion. La PK compuesta impide duplicados.
        """
        db.execute(
            delete(ProductoColeccion).where(
                ProductoColeccion.coleccion_id == coleccion_id
            )
        )
        if producto_ids:
            db.add_all(
                [
                    ProductoColeccion(
                        producto_id=producto_id,
                        coleccion_id=coleccion_id,
                    )
                    for producto_id in producto_ids
                ]
            )
        db.flush()
