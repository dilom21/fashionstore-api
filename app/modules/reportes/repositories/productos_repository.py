"""Consultas agregadas de productos/catalogo para CU28 (solo lectura).

Todas las metricas parten de ventas ``COMPLETADA``.

Criterios reales:

- Temporada: ``inventario.temporada_id`` (la Venta no guarda temporada).
- Coleccion: ``producto_coleccion`` es M:N, por lo que un producto con varias
  colecciones contribuye a CADA una de ellas: la suma de la seccion puede
  superar el total global. Documentado a proposito para no inducir a error.
- Ranking: ORDER BY + LIMIT en SQL.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    Talla,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.temporadas_colecciones.models.models import (
    Coleccion,
    ProductoColeccion,
    Temporada,
)
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_COMPLETADA = "COMPLETADA"


class ProductosReporteRepository:
    @staticmethod
    def _detalle_completado(
        *, desde: datetime | None, hasta: datetime | None, sucursal_id: int | None
    ):
        """SELECT base: lineas de ventas COMPLETADAS con su inventario."""
        condiciones = [Venta.estado == ESTADO_COMPLETADA]
        if sucursal_id is not None:
            condiciones.append(Venta.sucursal_id == sucursal_id)
        if desde is not None:
            condiciones.append(Venta.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Venta.fecha_hora < hasta)
        return (
            select(
                DetalleVenta.id.label("detalle_id"),
                DetalleVenta.cantidad.label("cantidad"),
                DetalleVenta.precio_unitario.label("precio_unitario"),
                Inventario.id.label("inventario_id"),
                Inventario.temporada_id.label("temporada_id"),
                VarianteProducto.id.label("variante_id"),
                VarianteProducto.producto_id.label("producto_id"),
                VarianteProducto.talla_id.label("talla_id"),
                VarianteProducto.color_id.label("color_id"),
            )
            .select_from(DetalleVenta)
            .join(Venta, Venta.id == DetalleVenta.venta_id)
            .join(Inventario, Inventario.id == DetalleVenta.inventario_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .where(*condiciones)
        ).subquery()

    @staticmethod
    def top_variantes(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(
                VarianteProducto.id,
                VarianteProducto.sku,
                Producto.nombre,
                Talla.nombre,
                Color.nombre,
                unidades,
                monto,
            )
            .select_from(sub)
            .join(VarianteProducto, VarianteProducto.id == sub.c.variante_id)
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .join(Talla, Talla.id == VarianteProducto.talla_id)
            .join(Color, Color.id == VarianteProducto.color_id)
            .group_by(
                VarianteProducto.id,
                VarianteProducto.sku,
                Producto.nombre,
                Talla.nombre,
                Color.nombre,
            )
            .order_by(unidades.desc(), VarianteProducto.id)
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_talla(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(Talla.nombre, unidades, monto)
            .select_from(sub)
            .join(Talla, Talla.id == sub.c.talla_id)
            .group_by(Talla.nombre)
            .order_by(unidades.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_color(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(Color.nombre, unidades, monto)
            .select_from(sub)
            .join(Color, Color.id == sub.c.color_id)
            .group_by(Color.nombre)
            .order_by(unidades.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_temporada(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        """Temporada real de la linea: ``inventario.temporada_id``."""
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(Temporada.nombre, unidades, monto)
            .select_from(sub)
            .join(Temporada, Temporada.id == sub.c.temporada_id)
            .group_by(Temporada.nombre)
            .order_by(unidades.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_coleccion(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        """Coleccion real via ``producto_coleccion`` (M:N; ver docstring)."""
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(Coleccion.nombre, unidades, monto)
            .select_from(sub)
            .join(
                ProductoColeccion,
                ProductoColeccion.producto_id == sub.c.producto_id,
            )
            .join(Coleccion, Coleccion.id == ProductoColeccion.coleccion_id)
            .group_by(Coleccion.nombre)
            .order_by(unidades.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def resumen_catalogo(db: Session) -> dict:
        productos = db.execute(
            select(
                func.count().filter(Producto.estado.is_(True)),
                func.count().filter(Producto.estado.is_(False)),
            ).select_from(Producto)
        ).one()
        variantes = db.execute(
            select(
                func.count().filter(VarianteProducto.estado.is_(True)),
                func.count().filter(VarianteProducto.estado.is_(False)),
            ).select_from(VarianteProducto)
        ).one()
        return {
            "productos_activos": int(productos[0] or 0),
            "productos_inactivos": int(productos[1] or 0),
            "variantes_activas": int(variantes[0] or 0),
            "variantes_inactivas": int(variantes[1] or 0),
        }


    @staticmethod
    def top_productos(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
        por_monto: bool = False,
    ) -> list:
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        orden = monto if por_monto else unidades
        statement = (
            select(
                Producto.id,
                Producto.nombre,
                Categoria.nombre,
                unidades,
                monto,
            )
            .select_from(sub)
            .join(Producto, Producto.id == sub.c.producto_id)
            .join(Categoria, Categoria.id == Producto.categoria_id)
            .group_by(Producto.id, Producto.nombre, Categoria.nombre)
            .order_by(orden.desc(), Producto.id)
            .limit(top)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def por_categoria(
        db: Session,
        *,
        desde: datetime | None,
        hasta: datetime | None,
        sucursal_id: int | None,
        top: int = 10,
    ) -> list:
        sub = ProductosReporteRepository._detalle_completado(
            desde=desde, hasta=hasta, sucursal_id=sucursal_id
        )
        unidades = func.coalesce(func.sum(sub.c.cantidad), 0)
        monto = func.coalesce(
            func.sum(sub.c.cantidad * sub.c.precio_unitario), 0
        )
        statement = (
            select(Categoria.nombre, unidades, monto)
            .select_from(sub)
            .join(Producto, Producto.id == sub.c.producto_id)
            .join(Categoria, Categoria.id == Producto.categoria_id)
            .group_by(Categoria.nombre)
            .order_by(unidades.desc())
            .limit(top)
        )
        return list(db.execute(statement).all())
