"""Consultas agregadas de ventas para CU28 (solo lectura).

Evita duplicar montos: ``_ventas_agregadas`` construye una subconsulta con UNA
fila por venta (total + unidades), de modo que los agrupamientos posteriores
(canal, sucursal, empleado, periodo) suman cada venta una sola vez.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente, Empleado
from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_COMPLETADA = "COMPLETADA"
ESTADOS_VENTA = (
    "PENDIENTE",
    "PAGADA",
    "COMPLETADA",
    "CANCELADA",
    "REEMBOLSADA",
)
AGRUPACIONES = ("DIA", "MES")


class VentasReporteRepository:
    @staticmethod
    def _condiciones(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Venta.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Venta.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Venta.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def _ventas_agregadas(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        estados: tuple[str, ...] | None = (ESTADO_COMPLETADA,),
    ):
        """Subconsulta: una fila por venta con total y unidades reales."""
        condiciones = VentasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        if estados is not None:
            condiciones.append(Venta.estado.in_(estados))
        return (
            select(
                Venta.id.label("venta_id"),
                Venta.sucursal_id.label("sucursal_id"),
                Venta.canal.label("canal"),
                Venta.empleado_id.label("empleado_id"),
                Venta.fecha_hora.label("fecha_hora"),
                Venta.total.label("total"),
                func.coalesce(func.sum(DetalleVenta.cantidad), 0).label(
                    "unidades"
                ),
            )
            .outerjoin(DetalleVenta, DetalleVenta.venta_id == Venta.id)
            .where(*condiciones)
            .group_by(Venta.id)
            .subquery()
        )

    @staticmethod
    def kpis(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        """Ventas completadas, monto total y unidades (agregados simples)."""
        condiciones = VentasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        fila = db.execute(
            select(
                func.count(Venta.id),
                func.coalesce(func.sum(Venta.total), 0),
            ).where(Venta.estado == ESTADO_COMPLETADA, *condiciones)
        ).one()
        unidades = db.scalar(
            select(func.coalesce(func.sum(DetalleVenta.cantidad), 0))
            .select_from(DetalleVenta)
            .join(Venta, Venta.id == DetalleVenta.venta_id)
            .where(Venta.estado == ESTADO_COMPLETADA, *condiciones)
        )
        return {
            "ventas_completadas": int(fila[0] or 0),
            "total_vendido": fila[1] or 0,
            "unidades_vendidas": int(unidades or 0),
        }

    @staticmethod
    def por_estado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = VentasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Venta.estado,
                func.count(Venta.id),
                func.coalesce(func.sum(Venta.total), 0),
            )
            .where(*condiciones)
            .group_by(Venta.estado)
            .order_by(func.count(Venta.id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_canal(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        sub = VentasReporteRepository._ventas_agregadas(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                sub.c.canal,
                func.count(sub.c.venta_id),
                func.coalesce(func.sum(sub.c.total), 0),
                func.coalesce(func.sum(sub.c.unidades), 0),
            )
            .group_by(sub.c.canal)
            .order_by(func.count(sub.c.venta_id).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_sucursal(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        sub = VentasReporteRepository._ventas_agregadas(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Sucursal.id,
                Sucursal.nombre,
                func.count(sub.c.venta_id),
                func.coalesce(func.sum(sub.c.total), 0),
                func.coalesce(func.sum(sub.c.unidades), 0),
            )
            .join(Sucursal, Sucursal.id == sub.c.sucursal_id)
            .group_by(Sucursal.id, Sucursal.nombre)
            .order_by(func.coalesce(func.sum(sub.c.total), 0).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_empleado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        sub = VentasReporteRepository._ventas_agregadas(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Empleado.id,
                Empleado.nombres,
                Empleado.apellidos,
                func.count(sub.c.venta_id),
                func.coalesce(func.sum(sub.c.total), 0),
            )
            .join(Empleado, Empleado.id == sub.c.empleado_id)
            .group_by(Empleado.id, Empleado.nombres, Empleado.apellidos)
            .order_by(func.coalesce(func.sum(sub.c.total), 0).desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def evolucion(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        agrupacion: str = "DIA",
    ) -> list:
        unidad = "day" if agrupacion.upper() == "DIA" else "month"
        sub = VentasReporteRepository._ventas_agregadas(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        periodo = func.date_trunc(unidad, sub.c.fecha_hora).label("periodo")
        statement = (
            select(
                periodo,
                func.count(sub.c.venta_id),
                func.coalesce(func.sum(sub.c.total), 0),
                func.coalesce(func.sum(sub.c.unidades), 0),
            )
            .group_by(periodo)
            .order_by(periodo)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def top_productos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        """Ranking por unidades con ORDER BY + LIMIT en SQL."""
        condiciones = VentasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(DetalleVenta.cantidad), 0)
        monto = func.coalesce(
            func.sum(DetalleVenta.cantidad * DetalleVenta.precio_unitario), 0
        )
        statement = (
            select(Producto.id, Producto.nombre, unidades, monto)
            .select_from(DetalleVenta)
            .join(Venta, Venta.id == DetalleVenta.venta_id)
            .join(Inventario, Inventario.id == DetalleVenta.inventario_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .where(Venta.estado == ESTADO_COMPLETADA, *condiciones)
            .group_by(Producto.id, Producto.nombre)
            .order_by(unidades.desc(), monto.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def listar(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Tabla paginada en SQL (todos los estados, con su estado visible)."""
        sub = VentasReporteRepository._ventas_agregadas(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id, estados=None
        )
        statement = (
            select(
                sub.c.venta_id,
                sub.c.fecha_hora,
                Sucursal.nombre,
                sub.c.canal,
                Venta.estado,
                Cliente.nombre,
                Cliente.apellido,
                Empleado.nombres,
                Empleado.apellidos,
                sub.c.unidades,
                sub.c.total,
            )
            .join(Sucursal, Sucursal.id == sub.c.sucursal_id)
            .join(Venta, Venta.id == sub.c.venta_id)
            .outerjoin(Cliente, Cliente.id == Venta.cliente_id)
            .outerjoin(Empleado, Empleado.id == sub.c.empleado_id)
            .order_by(sub.c.fecha_hora.desc(), sub.c.venta_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def contar(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> int:
        condiciones = VentasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        return int(
            db.scalar(
                select(func.count()).select_from(Venta).where(*condiciones)
            )
            or 0
        )


