"""Lecturas CU11 del cliente tenant, sin efectos de escritura."""
from __future__ import annotations

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.catalogo.models.models import (
    Color,
    Producto,
    RecursoProducto,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.sucursales.models.models import Sucursal


class ReservaTenantReadError(Exception):
    def __init__(self, codigo: int, detalle: str) -> None:
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


class ReservaTenantReadRepository:
    @staticmethod
    def _identidad_valida(empresa_id: int, usuario_id: int) -> None:
        if any(
            isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0
            for valor in (empresa_id, usuario_id)
        ):
            raise ReservaTenantReadError(403, "Identidad de cliente invalida")

    @classmethod
    def _cliente_activo(
        cls, db: Session, *, empresa_id: int, usuario_id: int
    ) -> int:
        cls._identidad_valida(empresa_id, usuario_id)
        cliente = Cliente.__table__
        cliente_id = db.scalar(
            select(cliente.c.id).where(
                cliente.c.empresa_id == empresa_id,
                cliente.c.usuario_id == usuario_id,
                cliente.c.estado.is_(True),
            )
        )
        if cliente_id is None:
            raise ReservaTenantReadError(
                403, "No existe cliente activo en esta empresa"
            )
        return int(cliente_id)

    @staticmethod
    def _base_reservas(empresa_id: int, cliente_id: int):
        reserva, sucursal = Reserva.__table__, Sucursal.__table__
        detalle = DetalleReserva.__table__
        cantidad_lineas = (
            select(func.count(detalle.c.id))
            .where(
                detalle.c.empresa_id == empresa_id,
                detalle.c.reserva_id == reserva.c.id,
            )
            .correlate(reserva)
            .scalar_subquery()
        )
        cantidad_unidades = (
            select(func.coalesce(func.sum(detalle.c.cantidad), 0))
            .where(
                detalle.c.empresa_id == empresa_id,
                detalle.c.reserva_id == reserva.c.id,
            )
            .correlate(reserva)
            .scalar_subquery()
        )
        return (
            select(
                reserva.c.id.label("reserva_id"),
                reserva.c.carrito_id,
                reserva.c.sucursal_id,
                sucursal.c.nombre.label("sucursal_nombre"),
                reserva.c.fecha_reserva,
                reserva.c.fecha_atencion,
                reserva.c.estado,
                reserva.c.observacion,
                cantidad_lineas.label("cantidad_lineas"),
                cantidad_unidades.label("cantidad_unidades"),
            )
            .join(
                sucursal,
                and_(
                    sucursal.c.id == reserva.c.sucursal_id,
                    sucursal.c.empresa_id == empresa_id,
                ),
            )
            .where(
                reserva.c.empresa_id == empresa_id,
                reserva.c.cliente_id == cliente_id,
            )
        )

    @classmethod
    def listar(
        cls,
        db: Session,
        *,
        empresa_id: int,
        usuario_id: int,
        estado: str | None,
        limit: int,
        offset: int,
    ) -> dict:
        cliente_id = cls._cliente_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id
        )
        reserva = Reserva.__table__
        base = cls._base_reservas(empresa_id, cliente_id)
        if estado is not None:
            base = base.where(reserva.c.estado == estado)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        filas = db.execute(
            base.order_by(reserva.c.fecha_reserva.desc(), reserva.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings().all()
        return {
            "items": [dict(fila) for fila in filas],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @classmethod
    def obtener(
        cls,
        db: Session,
        *,
        empresa_id: int,
        usuario_id: int,
        reserva_id: int,
    ) -> dict:
        if isinstance(reserva_id, bool) or reserva_id <= 0:
            raise ReservaTenantReadError(404, "Reserva no encontrada")
        cliente_id = cls._cliente_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id
        )
        reserva = Reserva.__table__
        cabecera = db.execute(
            cls._base_reservas(empresa_id, cliente_id).where(
                reserva.c.id == reserva_id
            )
        ).mappings().one_or_none()
        if cabecera is None:
            # 404 uniforme: no permite enumerar reservas de otro tenant/cliente.
            raise ReservaTenantReadError(404, "Reserva no encontrada")

        detalle, inventario = DetalleReserva.__table__, Inventario.__table__
        variante, producto = VarianteProducto.__table__, Producto.__table__
        talla, color, temporada = Talla.__table__, Color.__table__, Temporada.__table__
        recurso = RecursoProducto.__table__
        imagen = (
            select(recurso.c.url)
            .where(
                recurso.c.empresa_id == empresa_id,
                recurso.c.producto_id == producto.c.id,
                recurso.c.estado.is_(True),
                recurso.c.tipo == "IMAGEN",
            )
            .order_by(
                case(
                    (
                        and_(
                            recurso.c.es_principal.is_(True),
                            recurso.c.color_id == variante.c.color_id,
                        ),
                        0,
                    ),
                    (
                        and_(
                            recurso.c.es_principal.is_(True),
                            recurso.c.color_id.is_(None),
                        ),
                        1,
                    ),
                    else_=2,
                ),
                recurso.c.id.asc(),
            )
            .limit(1)
            .correlate(producto, variante)
            .scalar_subquery()
        )
        sentencia = (
            select(
                detalle.c.id.label("detalle_id"),
                detalle.c.inventario_id,
                producto.c.id.label("producto_id"),
                producto.c.nombre.label("producto_nombre"),
                imagen.label("imagen_principal_url"),
                variante.c.id.label("variante_producto_id"),
                variante.c.sku,
                talla.c.id.label("talla_id"),
                talla.c.nombre.label("talla_nombre"),
                color.c.id.label("color_id"),
                color.c.nombre.label("color_nombre"),
                temporada.c.id.label("temporada_id"),
                temporada.c.nombre.label("temporada_nombre"),
                detalle.c.cantidad,
            )
            .select_from(detalle)
            .join(
                inventario,
                and_(
                    inventario.c.id == detalle.c.inventario_id,
                    inventario.c.empresa_id == empresa_id,
                    inventario.c.sucursal_id == cabecera["sucursal_id"],
                ),
            )
            .join(
                variante,
                and_(
                    variante.c.id == inventario.c.variante_producto_id,
                    variante.c.empresa_id == empresa_id,
                ),
            )
            .join(
                producto,
                and_(
                    producto.c.id == variante.c.producto_id,
                    producto.c.empresa_id == empresa_id,
                ),
            )
            .join(talla, talla.c.id == variante.c.talla_id)
            .join(color, color.c.id == variante.c.color_id)
            .join(
                temporada,
                and_(
                    temporada.c.id == inventario.c.temporada_id,
                    temporada.c.empresa_id == empresa_id,
                ),
            )
            .where(
                detalle.c.empresa_id == empresa_id,
                detalle.c.reserva_id == reserva_id,
            )
            .order_by(detalle.c.id.asc())
        )
        items = [dict(fila) for fila in db.execute(sentencia).mappings().all()]
        if len(items) != int(cabecera["cantidad_lineas"]):
            raise ReservaTenantReadError(409, "Reserva con datos inconsistentes")
        resultado = dict(cabecera)
        resultado["items"] = items
        return resultado
