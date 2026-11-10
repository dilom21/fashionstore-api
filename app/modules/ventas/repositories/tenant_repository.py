"""Lecturas tenant de CU13. Las escrituras pasan por SP SECURITY DEFINER."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.modules.catalogo.models.models import VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.ventas.models.models import DetalleVenta, Venta
from app.modules.pagos.models.models import Pago
from app.modules.sucursales.models.models import Sucursal


class VentaTenantRepository:
    @staticmethod
    def _carga_completa():
        return (
            joinedload(Venta.sucursal),
            joinedload(Venta.detalles)
            .joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.producto),
            joinedload(Venta.detalles)
            .joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.talla),
            joinedload(Venta.detalles)
            .joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.color),
            joinedload(Venta.detalles)
            .joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.temporada),
        )

    @classmethod
    def obtener_por_id(
        cls, db: Session, *, empresa_id: int, venta_id: int
    ) -> Venta | None:
        if isinstance(venta_id, bool) or not isinstance(venta_id, int) or venta_id <= 0:
            return None
        statement = (
            select(Venta)
            .options(*cls._carga_completa())
            .where(Venta.id == venta_id, Venta.empresa_id == empresa_id)
        )
        return db.scalars(statement).unique().first()

    @classmethod
    def obtener_por_reserva(
        cls, db: Session, *, empresa_id: int, reserva_id: int
    ) -> Venta | None:
        statement = (
            select(Venta)
            .options(*cls._carga_completa())
            .where(
                Venta.empresa_id == empresa_id,
                Venta.reserva_id == reserva_id,
            )
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def _condiciones_historial(
        *, empresa_id: int, cliente_id: int, estados: tuple[str, ...],
        canal: str | None = None, desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> list:
        condiciones = [
            Venta.empresa_id == empresa_id,
            Venta.cliente_id == cliente_id,
            Venta.estado.in_(estados),
        ]
        if canal is not None:
            condiciones.append(Venta.canal == canal)
        if desde is not None:
            condiciones.append(Venta.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Venta.fecha_hora < hasta)
        return condiciones

    @classmethod
    def listar_historial_cliente(
        cls, db: Session, *, empresa_id: int, cliente_id: int,
        estados: tuple[str, ...], canal: str | None = None,
        desde: datetime | None = None, hasta: datetime | None = None,
        limit: int = 20, offset: int = 0,
    ) -> list:
        condiciones = cls._condiciones_historial(
            empresa_id=empresa_id, cliente_id=cliente_id, estados=estados,
            canal=canal, desde=desde, hasta=hasta,
        )
        statement = (
            select(
                Venta.id.label("venta_id"), Venta.fecha_hora, Venta.canal,
                Venta.estado, Venta.total, Venta.sucursal_id,
                Sucursal.nombre.label("sucursal_nombre"),
                func.coalesce(func.sum(DetalleVenta.cantidad), 0).label(
                    "cantidad_total_unidades"
                ),
                func.count(DetalleVenta.id).label("cantidad_lineas"),
            )
            .join(
                Sucursal,
                (Sucursal.id == Venta.sucursal_id)
                & (Sucursal.empresa_id == Venta.empresa_id),
            )
            .outerjoin(
                DetalleVenta,
                (DetalleVenta.venta_id == Venta.id)
                & (DetalleVenta.empresa_id == Venta.empresa_id),
            )
            .where(*condiciones)
            .group_by(
                Venta.id, Venta.fecha_hora, Venta.canal, Venta.estado,
                Venta.total, Venta.sucursal_id, Sucursal.nombre,
            )
            .order_by(Venta.fecha_hora.desc(), Venta.id.desc())
            .limit(limit).offset(offset)
        )
        return list(db.execute(statement).all())

    @classmethod
    def contar_historial_cliente(
        cls, db: Session, *, empresa_id: int, cliente_id: int,
        estados: tuple[str, ...], canal: str | None = None,
        desde: datetime | None = None, hasta: datetime | None = None,
    ) -> int:
        condiciones = cls._condiciones_historial(
            empresa_id=empresa_id, cliente_id=cliente_id, estados=estados,
            canal=canal, desde=desde, hasta=hasta,
        )
        return int(db.scalar(select(func.count()).select_from(Venta).where(*condiciones)) or 0)

    @staticmethod
    def obtener_ultimo_pago(
        db: Session, *, empresa_id: int, venta_id: int
    ) -> Pago | None:
        return db.scalars(
            select(Pago).where(
                Pago.empresa_id == empresa_id,
                Pago.venta_id == venta_id,
            ).order_by(Pago.id.desc()).limit(1)
        ).first()
