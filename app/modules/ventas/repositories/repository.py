"""Acceso a datos de CU19 - Realizar compra digital.

Solo consultas/persistencia y la invocacion del procedimiento existente para
recalcular el total. Las reglas de negocio viven en el service.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload

from app.modules.catalogo.models.models import VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.pagos.models.models import Pago
from app.modules.sucursales.models.models import Sucursal
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

    # ------------------------------------------------------------------
    # CU24 - Historial de compras del cliente (solo lectura)
    # ------------------------------------------------------------------

    @staticmethod
    def _condiciones_historial(
        *,
        cliente_id: int,
        estados: tuple[str, ...],
        canal: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> list:
        """Filtros del historial. Siempre acotado al cliente autenticado."""
        condiciones = [
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

    @staticmethod
    def listar_historial_cliente(
        db: Session,
        *,
        cliente_id: int,
        estados: tuple[str, ...],
        canal: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Una sola consulta: venta + sucursal + SUM(cantidad) + COUNT(lineas).

        El JOIN trae el nombre de la sucursal y los OUTER JOIN/GROUP BY
        agregan las lineas, por lo que una pagina de N compras no genera N
        consultas adicionales (sin N+1). La paginacion se hace en SQL.
        """
        condiciones = VentaRepository._condiciones_historial(
            cliente_id=cliente_id,
            estados=estados,
            canal=canal,
            desde=desde,
            hasta=hasta,
        )
        statement = (
            select(
                Venta.id.label("venta_id"),
                Venta.fecha_hora.label("fecha_hora"),
                Venta.canal.label("canal"),
                Venta.estado.label("estado"),
                Venta.total.label("total"),
                Venta.sucursal_id.label("sucursal_id"),
                Sucursal.nombre.label("sucursal_nombre"),
                func.coalesce(func.sum(DetalleVenta.cantidad), 0).label(
                    "cantidad_total_unidades"
                ),
                func.count(DetalleVenta.id).label("cantidad_lineas"),
            )
            .join(Sucursal, Sucursal.id == Venta.sucursal_id)
            .outerjoin(DetalleVenta, DetalleVenta.venta_id == Venta.id)
            .where(*condiciones)
            .group_by(
                Venta.id,
                Venta.fecha_hora,
                Venta.canal,
                Venta.estado,
                Venta.total,
                Venta.sucursal_id,
                Sucursal.nombre,
            )
            .order_by(Venta.fecha_hora.desc(), Venta.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def contar_historial_cliente(
        db: Session,
        *,
        cliente_id: int,
        estados: tuple[str, ...],
        canal: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> int:
        """Total de registros con los mismos filtros (una sola consulta)."""
        condiciones = VentaRepository._condiciones_historial(
            cliente_id=cliente_id,
            estados=estados,
            canal=canal,
            desde=desde,
            hasta=hasta,
        )
        statement = select(func.count()).select_from(Venta).where(*condiciones)
        return int(db.scalar(statement) or 0)

    @staticmethod
    def obtener_ultimo_pago(db: Session, venta_id: int) -> Pago | None:
        """Pago mas reciente de la venta (1 venta -> N intentos de pago).

        Se usa el ultimo por ``Pago.id DESC`` para reflejar el estado final:
        APROBADO en COMPLETADA, REEMBOLSADO en REEMBOLSADA y el ultimo intento
        existente en CANCELADA. ``pago`` puede ser ``None`` y eso es valido.
        """
        statement = (
            select(Pago)
            .where(Pago.venta_id == venta_id)
            .order_by(Pago.id.desc())
            .limit(1)
        )
        return db.scalars(statement).first()

