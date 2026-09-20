"""Acceso a datos de CU19 - Realizar compra digital.

Solo consultas/persistencia y la invocacion del procedimiento existente para
recalcular el total. Las reglas de negocio viven en el service.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.modules.catalogo.models.models import VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_PENDIENTE = "PENDIENTE"

# Autoridad de base de datos para recalcular venta.total a partir de sus
# detalles. No se reimplementa el calculo en Python.
SQL_RECALCULAR_TOTAL = text(
    "CALL public.sp_recalcular_total_venta(:venta_id)"
)


class VentaRepository:
    @staticmethod
    def _carga_completa():
        """Venta + sucursal + detalles + inventario/variante/producto/talla/color/temporada."""
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

    @staticmethod
    def obtener_por_id(db: Session, venta_id: int) -> Venta | None:
        statement = (
            select(Venta)
            .options(*VentaRepository._carga_completa())
            .where(Venta.id == venta_id)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def obtener_por_carrito(db: Session, carrito_id: int) -> Venta | None:
        statement = select(Venta).where(Venta.carrito_id == carrito_id)
        return db.scalars(statement).first()

    @staticmethod
    def obtener_por_reserva(db: Session, reserva_id: int) -> Venta | None:
        statement = select(Venta).where(Venta.reserva_id == reserva_id)
        return db.scalars(statement).first()

    @staticmethod
    def crear(
        db: Session,
        *,
        cliente_id: int | None,
        sucursal_id: int,
        canal: str,
        fecha_hora: datetime,
        carrito_id: int | None = None,
        empleado_id: int | None = None,
        reserva_id: int | None = None,
    ) -> Venta:
        """Crea la venta PENDIENTE. El total real lo fija el trigger/SP de la BD.

        Se inserta con total 0 porque la columna es NOT NULL; al insertar los
        detalle_venta, trg_recalcular_total_venta actualiza el total.

        CU19 usa carrito_id; CU20 usa empleado_id/reserva_id (o carrito_id nulo
        en la venta presencial directa). La tabla admite todos como nullable.
        """
        venta = Venta(
            cliente_id=cliente_id,
            empleado_id=empleado_id,
            sucursal_id=sucursal_id,
            reserva_id=reserva_id,
            fecha_hora=fecha_hora,
            canal=canal,
            estado=ESTADO_PENDIENTE,
            total=Decimal("0.00"),
            carrito_id=carrito_id,
        )
        db.add(venta)
        db.flush()
        return venta

    @staticmethod
    def crear_detalle(
        db: Session,
        *,
        venta_id: int,
        inventario_id: int,
        cantidad: int,
        precio_unitario: Decimal,
    ) -> DetalleVenta:
        detalle = DetalleVenta(
            venta_id=venta_id,
            inventario_id=inventario_id,
            cantidad=cantidad,
            precio_unitario=precio_unitario,
        )
        db.add(detalle)
        db.flush()
        return detalle

    @staticmethod
    def recalcular_total(db: Session, venta_id: int) -> None:
        """Reutiliza sp_recalcular_total_venta (autoridad de la base)."""
        db.execute(SQL_RECALCULAR_TOTAL, {"venta_id": venta_id})
