from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Bitacora
from app.modules.catalogo.models.models import Producto
from app.modules.proveedores.models.models import (
    OrdenCompra,
    Proveedor,
    ProveedorProducto,
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

    Las tablas proveedor y proveedor_producto no poseen trigger
    fn_registrar_bitacora, por lo que CU11 audita manualmente cada escritura
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


class ProveedorRepository:
    @staticmethod
    def listar(
        db: Session,
        *,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Proveedor]:
        statement = select(Proveedor)
        if buscar:
            patron = f"%{buscar.strip()}%"
            statement = statement.where(
                or_(
                    Proveedor.razon_social.ilike(patron),
                    Proveedor.nit.ilike(patron),
                    Proveedor.correo.ilike(patron),
                )
            )
        if estado is not None:
            statement = statement.where(Proveedor.estado.is_(estado))
        statement = statement.order_by(Proveedor.razon_social, Proveedor.id)
        return list(db.scalars(statement).all())

    @staticmethod
    def obtener_por_id(db: Session, proveedor_id: int) -> Proveedor | None:
        return db.get(Proveedor, proveedor_id)

    @staticmethod
    def buscar_por_nit(
        db: Session, nit: str, *, excluir_id: int | None = None
    ) -> Proveedor | None:
        statement = select(Proveedor).where(Proveedor.nit == nit)
        if excluir_id is not None:
            statement = statement.where(Proveedor.id != excluir_id)
        return db.scalars(statement).first()

    @staticmethod
    def crear(
        db: Session,
        *,
        razon_social: str,
        nit: str | None,
        correo: str | None,
        telefono: str | None,
        direccion: str | None,
    ) -> Proveedor:
        proveedor = Proveedor(
            razon_social=razon_social,
            nit=nit,
            correo=correo,
            telefono=telefono,
            direccion=direccion,
            estado=True,
        )
        db.add(proveedor)
        db.flush()
        return proveedor

    @staticmethod
    def contar_productos(db: Session, proveedor_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(ProveedorProducto)
            .where(ProveedorProducto.proveedor_id == proveedor_id)
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def contar_ordenes_compra_activas(
        db: Session, proveedor_id: int, estados: tuple[str, ...]
    ) -> int:
        statement = (
            select(func.count())
            .select_from(OrdenCompra)
            .where(
                OrdenCompra.proveedor_id == proveedor_id,
                OrdenCompra.estado.in_(estados),
            )
        )
        return int(db.scalar(statement) or 0)

    @staticmethod
    def listar_productos(
        db: Session, proveedor_id: int
    ) -> list[ProveedorProducto]:
        statement = (
            select(ProveedorProducto)
            .options(joinedload(ProveedorProducto.producto))
            .where(ProveedorProducto.proveedor_id == proveedor_id)
            .order_by(ProveedorProducto.producto_id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def listar_producto_ids(db: Session, proveedor_id: int) -> list[int]:
        statement = select(ProveedorProducto.producto_id).where(
            ProveedorProducto.proveedor_id == proveedor_id
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
        db: Session,
        *,
        proveedor_id: int,
        items: list[dict],
    ) -> None:
        """Reemplazo atomico del conjunto de productos de un proveedor.

        Borra las relaciones actuales e inserta el conjunto nuevo dentro de
        la misma transaccion. La PK compuesta impide duplicados.
        """
        db.execute(
            delete(ProveedorProducto).where(
                ProveedorProducto.proveedor_id == proveedor_id
            )
        )
        if items:
            db.add_all(
                [
                    ProveedorProducto(
                        proveedor_id=proveedor_id,
                        producto_id=item["producto_id"],
                        costo_referencia=item["costo_referencia"],
                        estado=item["estado"],
                    )
                    for item in items
                ]
            )
        db.flush()
