"""Consultas agregadas de compras/proveedores para CU28 (solo lectura).

- ``valor_orden = SUM(DetalleOrdenCompra.cantidad * costo_unitario)`` (Decimal).
- ``valor_ordenes_recibidas`` solo suma ordenes ``RECIBIDA``: el modelo no
  tiene ``cantidad_recibida`` por detalle, por lo que ``PARCIAL`` se muestra
  como estado pero no se cuenta como mercaderia ingresada.
- Cumplimiento de fechas: promedio de dias entre ``fecha_estimada`` y
  ``fecha_recepcion`` (solo ordenes con ambas fechas). No es una puntuacion.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import Producto, VarianteProducto
from app.modules.compras.models.models import (
    DetalleOrdenCompra,
    OrdenCompra,
)
from app.modules.proveedores.models.models import Proveedor
from app.modules.sucursales.models.models import Sucursal

ESTADOS_ORDEN = ("BORRADOR", "ENVIADA", "PARCIAL", "RECIBIDA", "CANCELADA")
ESTADO_RECIBIDA = "RECIBIDA"
ESTADO_CANCELADA = "CANCELADA"


class ComprasReporteRepository:
    @staticmethod
    def _condiciones(
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(OrdenCompra.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(OrdenCompra.fecha_orden >= desde)
        if hasta is not None:
            condiciones.append(OrdenCompra.fecha_orden < hasta)
        return condiciones

    @staticmethod
    def _valor_orden():
        return func.coalesce(
            func.sum(
                DetalleOrdenCompra.cantidad * DetalleOrdenCompra.costo_unitario
            ),
            0,
        )

    @staticmethod
    def resumen(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> dict:
        condiciones = ComprasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        total = int(
            db.scalar(
                select(func.count())
                .select_from(OrdenCompra)
                .where(*condiciones)
            )
            or 0
        )
        unidades = int(
            db.scalar(
                select(
                    func.coalesce(func.sum(DetalleOrdenCompra.cantidad), 0)
                )
                .select_from(DetalleOrdenCompra)
                .join(
                    OrdenCompra,
                    OrdenCompra.id == DetalleOrdenCompra.orden_compra_id,
                )
                .where(*condiciones)
            )
            or 0
        )
        valor_total = db.scalar(
            select(ComprasReporteRepository._valor_orden())
            .select_from(DetalleOrdenCompra)
            .join(
                OrdenCompra,
                OrdenCompra.id == DetalleOrdenCompra.orden_compra_id,
            )
            .where(*condiciones)
        )
        valor_recibidas = db.scalar(
            select(ComprasReporteRepository._valor_orden())
            .select_from(DetalleOrdenCompra)
            .join(
                OrdenCompra,
                OrdenCompra.id == DetalleOrdenCompra.orden_compra_id,
            )
            .where(OrdenCompra.estado == ESTADO_RECIBIDA, *condiciones)
        )
        return {
            "total_ordenes": total,
            "unidades_ordenadas": unidades,
            "valor_total_ordenes": valor_total or Decimal("0"),
            "valor_ordenes_recibidas": valor_recibidas or Decimal("0"),
        }

    @staticmethod
    def por_estado(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = ComprasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        statement = (
            select(
                OrdenCompra.estado,
                func.count(func.distinct(OrdenCompra.id)),
                ComprasReporteRepository._valor_orden(),
            )
            .select_from(OrdenCompra)
            .outerjoin(
                DetalleOrdenCompra,
                DetalleOrdenCompra.orden_compra_id == OrdenCompra.id,
            )
            .where(*condiciones)
            .group_by(OrdenCompra.estado)
            .order_by(func.count(func.distinct(OrdenCompra.id)).desc())
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
        condiciones = ComprasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        valor = ComprasReporteRepository._valor_orden()
        statement = (
            select(
                Sucursal.id,
                Sucursal.nombre,
                func.count(func.distinct(OrdenCompra.id)),
                valor,
            )
            .select_from(OrdenCompra)
            .join(Sucursal, Sucursal.id == OrdenCompra.sucursal_id)
            .outerjoin(
                DetalleOrdenCompra,
                DetalleOrdenCompra.orden_compra_id == OrdenCompra.id,
            )
            .where(*condiciones)
            .group_by(Sucursal.id, Sucursal.nombre)
            .order_by(valor.desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_proveedor(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
    ) -> list:
        condiciones = ComprasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        valor = ComprasReporteRepository._valor_orden()
        dias = func.avg(
            func.extract(
                "epoch",
                OrdenCompra.fecha_recepcion - OrdenCompra.fecha_estimada,
            )
            / 86400.0
        )
        statement = (
            select(
                Proveedor.id,
                Proveedor.razon_social,
                func.count(func.distinct(OrdenCompra.id)),
                func.coalesce(func.sum(DetalleOrdenCompra.cantidad), 0),
                valor,
                func.count(func.distinct(OrdenCompra.id)).filter(
                    OrdenCompra.estado == ESTADO_RECIBIDA
                ),
                func.count(func.distinct(OrdenCompra.id)).filter(
                    OrdenCompra.estado == ESTADO_CANCELADA
                ),
                dias,
            )
            .select_from(OrdenCompra)
            .join(Proveedor, Proveedor.id == OrdenCompra.proveedor_id)
            .outerjoin(
                DetalleOrdenCompra,
                DetalleOrdenCompra.orden_compra_id == OrdenCompra.id,
            )
            .where(*condiciones)
            .group_by(Proveedor.id, Proveedor.razon_social)
            .order_by(valor.desc())
        )
        return list(db.execute(statement).all())

    @staticmethod
    def productos_abastecidos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        condiciones = ComprasReporteRepository._condiciones(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(DetalleOrdenCompra.cantidad), 0)
        valor = ComprasReporteRepository._valor_orden()
        statement = (
            select(
                VarianteProducto.id,
                VarianteProducto.sku,
                Producto.nombre,
                unidades,
                valor,
            )
            .select_from(DetalleOrdenCompra)
            .join(
                OrdenCompra,
                OrdenCompra.id == DetalleOrdenCompra.orden_compra_id,
            )
            .join(
                VarianteProducto,
                VarianteProducto.id == DetalleOrdenCompra.variante_producto_id,
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .where(*condiciones)
            .group_by(
                VarianteProducto.id, VarianteProducto.sku, Producto.nombre
            )
            .order_by(unidades.desc(), VarianteProducto.id)
            .limit(top)
        )
        return list(db.execute(statement).all())

