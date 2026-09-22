"""Consultas agregadas de clientes y carritos para CU28 (solo lectura).

- Solo ventas ``COMPLETADA`` y con ``cliente_id`` no nulo.
- ``cliente_recurrente`` = cliente con mas de una venta COMPLETADA en el
  periodo consultado. ``Cliente`` no tiene ``fecha_creacion``, por lo que no
  se afirma "cliente nuevo".
- PII minimizada: id, nombre y apellido (sin CI ni telefono).
- Carritos: estados reales ACTIVO/EXPIRADO/ELIMINADO/CONVERTIDO y
  tasa = CONVERTIDO / total del periodo * 100.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_COMPLETADA = "COMPLETADA"
ESTADOS_CARRITO = ("ACTIVO", "EXPIRADO", "ELIMINADO", "CONVERTIDO")
ESTADO_CONVERTIDO = "CONVERTIDO"


class ClientesReporteRepository:
    @staticmethod
    def _condiciones(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = [
            Venta.estado == ESTADO_COMPLETADA,
            Venta.cliente_id.isnot(None),
        ]
        if sucursal_id is not None:
            condiciones.append(Venta.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Venta.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Venta.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def _por_cliente(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ):
        """Una fila por venta y luego agregado por cliente (sin duplicar montos)."""
        ventas = (
            select(
                Venta.id.label("venta_id"),
                Venta.cliente_id.label("cliente_id"),
                Venta.total.label("total"),
                func.coalesce(func.sum(DetalleVenta.cantidad), 0).label(
                    "unidades"
                ),
            )
            .outerjoin(DetalleVenta, DetalleVenta.venta_id == Venta.id)
            .where(
                *ClientesReporteRepository._condiciones(
                    desde=desde, hasta=hasta, sucursal_id=sucursal_id
                )
            )
            .group_by(Venta.id)
            .subquery()
        )
        return (
            select(
                ventas.c.cliente_id,
                func.count(ventas.c.venta_id).label("compras"),
                func.coalesce(func.sum(ventas.c.total), 0).label("monto"),
                func.coalesce(func.sum(ventas.c.unidades), 0).label(
                    "unidades"
                ),
            )
            .group_by(ventas.c.cliente_id)
            .subquery()
        )

    @staticmethod
    def resumen(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        agregado = ClientesReporteRepository._por_cliente(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        fila = db.execute(
            select(
                func.count(agregado.c.cliente_id),
                func.count(agregado.c.cliente_id).filter(
                    agregado.c.compras > 1
                ),
                func.coalesce(func.sum(agregado.c.compras), 0),
                func.coalesce(func.sum(agregado.c.unidades), 0),
                func.coalesce(func.sum(agregado.c.monto), 0),
            )
        ).one()
        return {
            "clientes_compradores": int(fila[0] or 0),
            "clientes_recurrentes": int(fila[1] or 0),
            "compras_totales": int(fila[2] or 0),
            "unidades_totales": int(fila[3] or 0),
            "monto_total": fila[4] or Decimal("0"),
        }

    @staticmethod
    def compras_por_cliente(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        agregado = ClientesReporteRepository._por_cliente(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                Cliente.id,
                Cliente.nombre,
                Cliente.apellido,
                agregado.c.compras,
                agregado.c.unidades,
                agregado.c.monto,
            )
            .join(Cliente, Cliente.id == agregado.c.cliente_id)
            .order_by(agregado.c.monto.desc(), Cliente.id)
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def carritos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Carrito.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Carrito.fecha_creacion >= desde)
        if hasta is not None:
            condiciones.append(Carrito.fecha_creacion < hasta)

        total = int(
            db.scalar(
                select(func.count()).select_from(Carrito).where(*condiciones)
            )
            or 0
        )
        convertidos = int(
            db.scalar(
                select(func.count())
                .select_from(Carrito)
                .where(Carrito.estado == ESTADO_CONVERTIDO, *condiciones)
            )
            or 0
        )
        por_estado = list(
            db.execute(
                select(Carrito.estado, func.count(Carrito.id))
                .where(*condiciones)
                .group_by(Carrito.estado)
                .order_by(func.count(Carrito.id).desc())
            ).all()
        )
        return {
            "total_carritos": total,
            "carritos_convertidos": convertidos,
            "por_estado": por_estado,
        }

