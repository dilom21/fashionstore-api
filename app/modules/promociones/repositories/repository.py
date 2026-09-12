from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import Producto
from app.modules.promociones.models.models import Promocion, PromocionProducto


def registrar_evento_bitacora(
    db: Session,
    *,
    usuario_id: int,
    accion: str,
    entidad_afectada: str,
    descripcion: str,
) -> None:
    """Escritura manual en bitacora para entidades sin trigger de auditoria.

    Las tablas promocion y promocion_producto no poseen trigger
    fn_registrar_bitacora, por lo que CU10 audita manualmente cada escritura
    dentro de la misma transaccion.
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


class PromocionRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
        tipo_descuento: str | None = None,
    ) -> list[Promocion]:
        statement = select(Promocion)
        if buscar:
            statement = statement.where(
                Promocion.nombre.ilike(f"%{buscar.strip()}%")
            )
        if estado is not None:
            statement = statement.where(Promocion.estado.is_(estado))
        if tipo_descuento:
            statement = statement.where(
                Promocion.tipo_descuento == tipo_descuento
            )
        statement = statement.order_by(
            Promocion.fecha_inicio.desc(), Promocion.id.desc()
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, promocion_id: int) -> Promocion | None:
        return db.get(Promocion, promocion_id)

    @staticmethod
    def crear(
        db: Session,
        *,
        nombre: str,
        descripcion: str | None,
        tipo_descuento: str,
        valor_descuento: Decimal,
        fecha_inicio: datetime,
        fecha_fin: datetime,
    ) -> Promocion:
        promocion = Promocion(
            nombre=nombre,
            descripcion=descripcion,
            tipo_descuento=tipo_descuento,
            valor_descuento=valor_descuento,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            estado=True,
        )
        db.add(promocion)
        db.flush()
        return promocion

    @staticmethod
    def listar_productos(db: Session, promocion_id: int) -> list[Producto]:
        statement = (
            select(Producto)
            .join(
                PromocionProducto,
                PromocionProducto.producto_id == Producto.id,
            )
            .where(PromocionProducto.promocion_id == promocion_id)
            .order_by(Producto.nombre, Producto.id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def contar_productos(db: Session, promocion_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(PromocionProducto)
            .where(PromocionProducto.promocion_id == promocion_id)
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def listar_producto_ids(db: Session, promocion_id: int) -> list[int]:
        statement = select(PromocionProducto.producto_id).where(
            PromocionProducto.promocion_id == promocion_id
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
        db: Session, *, promocion_id: int, producto_ids: list[int]
    ) -> None:
        """Reemplazo atomico del conjunto de productos de una promocion.

        Borra las relaciones actuales e inserta el conjunto nuevo dentro de
        la misma transaccion. La PK compuesta impide duplicados.
        """
        db.execute(
            delete(PromocionProducto).where(
                PromocionProducto.promocion_id == promocion_id
            )
        )
        if producto_ids:
            db.add_all(
                [
                    PromocionProducto(
                        promocion_id=promocion_id,
                        producto_id=producto_id,
                    )
                    for producto_id in producto_ids
                ]
            )
        db.flush()
