from datetime import date, datetime, time, timezone

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import Temporada, VarianteProducto
from app.modules.compras.models.models import (
    DetalleOrdenCompra,
    OrdenCompra,
)
from app.modules.proveedores.models.models import Proveedor, ProveedorProducto
from app.modules.sucursales.models.models import Sucursal

_UTC = timezone.utc


def registrar_evento_bitacora(
    db: Session,
    *,
    usuario_id: int,
    accion: str,
    entidad_afectada: str,
    descripcion: str,
) -> None:
    """Escritura manual en bitacora para entidades sin trigger de auditoria.

    orden_compra ya posee trg_bitacora_orden_compra, por lo que CU12 NO la
    audita manualmente. detalle_orden_compra no tiene trigger: este helper
    registra el evento resumen del reemplazo de detalles.
    """
    db.add(
        Bitacora(
            usuario_id=usuario_id,
            fecha_hora=datetime.now(_UTC),
            accion=accion,
            entidad_afectada=entidad_afectada,
            descripcion=descripcion,
        )
    )
    db.flush()


class OrdenCompraRepository:
    @staticmethod
    def _consulta_base():
        return select(OrdenCompra).options(
            joinedload(OrdenCompra.proveedor),
            joinedload(OrdenCompra.sucursal),
        )

    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        proveedor_id: int | None = None,
        sucursal_id: int | None = None,
        estado: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
    ) -> list[OrdenCompra]:
        statement = OrdenCompraRepository._consulta_base()

        if buscar:
            patron = f"%{buscar.strip()}%"
            statement = statement.join(OrdenCompra.proveedor).where(
                or_(
                    OrdenCompra.observacion.ilike(patron),
                    Proveedor.razon_social.ilike(patron),
                    cast(OrdenCompra.id, String).ilike(patron),
                )
            )
        if proveedor_id is not None:
            statement = statement.where(
                OrdenCompra.proveedor_id == proveedor_id
            )
        if sucursal_id is not None:
            statement = statement.where(
                OrdenCompra.sucursal_id == sucursal_id
            )
        if estado is not None:
            statement = statement.where(OrdenCompra.estado == estado)
        if fecha_desde is not None:
            statement = statement.where(
                OrdenCompra.fecha_orden
                >= datetime.combine(fecha_desde, time.min, tzinfo=_UTC)
            )
        if fecha_hasta is not None:
            statement = statement.where(
                OrdenCompra.fecha_orden
                <= datetime.combine(fecha_hasta, time.max, tzinfo=_UTC)
            )

        statement = statement.order_by(
            OrdenCompra.fecha_orden.desc(), OrdenCompra.id.desc()
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, orden_id: int) -> OrdenCompra | None:
        statement = OrdenCompraRepository._consulta_base().where(
            OrdenCompra.id == orden_id
        )
        return db.scalar(statement)

    @staticmethod
    def obtener_proveedor(db: Session, proveedor_id: int) -> Proveedor | None:
        return db.get(Proveedor, proveedor_id)

    @staticmethod
    def obtener_sucursal(db: Session, sucursal_id: int) -> Sucursal | None:
        return db.get(Sucursal, sucursal_id)

    @staticmethod
    def crear(
        db: Session,
        *,
        proveedor_id: int,
        sucursal_id: int,
        empleado_id: int | None,
        fecha_estimada: datetime | None,
        observacion: str | None,
    ) -> OrdenCompra:
        orden = OrdenCompra(
            proveedor_id=proveedor_id,
            sucursal_id=sucursal_id,
            empleado_id=empleado_id,
            fecha_estimada=fecha_estimada,
            estado="BORRADOR",
            observacion=observacion,
        )
        db.add(orden)
        db.flush()
        return orden

    # ------------------------------------------------------------------
    # Detalles
    # ------------------------------------------------------------------

    @staticmethod
    def listar_detalles(
        db: Session, orden_id: int
    ) -> list[DetalleOrdenCompra]:
        statement = (
            select(DetalleOrdenCompra)
            .options(
                joinedload(DetalleOrdenCompra.variante).joinedload(
                    VarianteProducto.producto
                ),
                joinedload(DetalleOrdenCompra.temporada),
            )
            .where(DetalleOrdenCompra.orden_compra_id == orden_id)
            .order_by(DetalleOrdenCompra.id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def contar_detalles(db: Session, orden_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(DetalleOrdenCompra)
            .where(DetalleOrdenCompra.orden_compra_id == orden_id)
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def contar_detalles_por_orden(
        db: Session, orden_ids: list[int]
    ) -> dict[int, int]:
        if not orden_ids:
            return {}
        statement = (
            select(
                DetalleOrdenCompra.orden_compra_id,
                func.count(DetalleOrdenCompra.id),
            )
            .where(DetalleOrdenCompra.orden_compra_id.in_(orden_ids))
            .group_by(DetalleOrdenCompra.orden_compra_id)
        )
        return {
            int(orden_id): int(total)
            for orden_id, total in db.execute(statement).all()
        }

    @staticmethod
    def reemplazar_detalles(
        db: Session,
        *,
        orden_id: int,
        items: list[dict],
    ) -> None:
        """Reemplazo atomico del conjunto de detalles de la orden.

        La PK/UNIQUE (orden_compra_id, variante_producto_id, temporada_id)
        impide duplicados; el servicio deduplica antes de llegar aqui.
        """
        db.execute(
            delete(DetalleOrdenCompra).where(
                DetalleOrdenCompra.orden_compra_id == orden_id
            )
        )
        if items:
            db.add_all(
                [
                    DetalleOrdenCompra(
                        orden_compra_id=orden_id,
                        variante_producto_id=item["variante_producto_id"],
                        temporada_id=item["temporada_id"],
                        cantidad=item["cantidad"],
                        costo_unitario=item["costo_unitario"],
                    )
                    for item in items
                ]
            )
        db.flush()

    # ------------------------------------------------------------------
    # Catalogos de validacion
    # ------------------------------------------------------------------

    @staticmethod
    def obtener_variantes(
        db: Session, variante_ids: list[int]
    ) -> list[VarianteProducto]:
        if not variante_ids:
            return []
        statement = (
            select(VarianteProducto)
            .options(joinedload(VarianteProducto.producto))
            .where(VarianteProducto.id.in_(variante_ids))
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_temporadas(
        db: Session, temporada_ids: list[int]
    ) -> list[Temporada]:
        if not temporada_ids:
            return []
        statement = select(Temporada).where(Temporada.id.in_(temporada_ids))
        return list(db.scalars(statement).all())

    @staticmethod
    def productos_asociados_activos(
        db: Session, proveedor_id: int, producto_ids: list[int]
    ) -> set[int]:
        if not producto_ids:
            return set()
        statement = select(ProveedorProducto.producto_id).where(
            ProveedorProducto.proveedor_id == proveedor_id,
            ProveedorProducto.producto_id.in_(producto_ids),
            ProveedorProducto.estado.is_(True),
        )
        return {int(producto_id) for producto_id in db.scalars(statement).all()}
