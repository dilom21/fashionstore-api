"""CU16 SaaS: PREVALIDACION de reserva, no transaccion de reserva.

La elegibilidad observada puede cambiar inmediatamente por compras
concurrentes. Esta clase NO crea reservas, NO reserva stock, NO invoca SP ni
bloquea filas. La verificacion definitiva deberá ejecutarse dentro de un
procedimiento tenant-aware de PostgreSQL, aun no habilitado.
"""
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.carrito.models.models import DetalleCarrito
from app.modules.carrito.repositories.tenant_read_repository import CarritoTenantReadRepository
from app.modules.carrito.repositories.tenant_repository import CarritoTenantError
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import Reserva
from app.modules.sucursales.models.models import Sucursal


class TenantReservaPrevalidacionRepository:
    @staticmethod
    def _fecha_futura(fecha_atencion: datetime) -> datetime:
        if fecha_atencion.tzinfo is None:
            # CU16 del primer parcial interpreta fecha naive como UTC.
            fecha_atencion = fecha_atencion.replace(tzinfo=timezone.utc)
        fecha_atencion = fecha_atencion.astimezone(timezone.utc)
        if fecha_atencion <= datetime.now(timezone.utc):
            raise CarritoTenantError(422, "La fecha de atencion debe ser futura")
        return fecha_atencion

    @staticmethod
    def _lineas_elegibles(
        db: Session, *, empresa_id: int, carrito_id: int, sucursal_id: int
    ) -> list[dict]:
        d = DetalleCarrito.__table__
        i, s = Inventario.__table__, Sucursal.__table__
        v, p, c = VarianteProducto.__table__, Producto.__table__, Categoria.__table__
        t, co, temporada = Talla.__table__, Color.__table__, Temporada.__table__
        stmt = (
            select(
                d.c.id.label("detalle_id"),
                d.c.cantidad,
                i.c.stock_actual,
                i.c.stock_reservado,
            )
            .select_from(d)
            .join(i, and_(
                i.c.id == d.c.inventario_id,
                i.c.empresa_id == empresa_id,
                i.c.sucursal_id == sucursal_id,
            ))
            .join(s, and_(
                s.c.id == i.c.sucursal_id,
                s.c.empresa_id == empresa_id,
                s.c.estado.is_(True),
            ))
            .join(v, and_(
                v.c.id == i.c.variante_producto_id,
                v.c.empresa_id == empresa_id,
                v.c.estado.is_(True),
            ))
            .join(p, and_(
                p.c.id == v.c.producto_id,
                p.c.empresa_id == empresa_id,
                p.c.estado.is_(True),
            ))
            .join(c, and_(
                c.c.id == p.c.categoria_id,
                c.c.empresa_id == empresa_id,
                c.c.estado.is_(True),
            ))
            .join(temporada, and_(
                temporada.c.id == i.c.temporada_id,
                temporada.c.empresa_id == empresa_id,
            ))
            .join(t, and_(
                t.c.id == v.c.talla_id, t.c.estado.is_(True),
            ))
            .join(co, and_(
                co.c.id == v.c.color_id, co.c.estado.is_(True),
            ))
            .where(
                d.c.empresa_id == empresa_id,
                d.c.carrito_id == carrito_id,
            )
            .order_by(d.c.id)
        )
        return [dict(r) for r in db.execute(stmt).mappings().all()]

    @classmethod
    def verificar(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int, fecha_atencion: datetime,
    ) -> dict:
        fecha = cls._fecha_futura(fecha_atencion)
        # Verifica carrito ACTIVO, vigencia, pertenencia empresa/cliente y
        # consistencia de TODAS las relaciones de líneas.
        carrito = CarritoTenantReadRepository.obtener(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
            carrito_id=carrito_id,
        )
        lineas = carrito["items"]
        if not lineas:
            raise CarritoTenantError(409, "El carrito no contiene prendas")

        r = Reserva.__table__
        # La unicidad original de carrito_id impide reutilizar carrito para
        # una segunda reserva: cotejar sin revelar detalles de otra empresa.
        if db.scalar(select(r.c.id).where(r.c.carrito_id == carrito_id)) is not None:
            raise CarritoTenantError(409, "El carrito ya fue reservado")

        validas = cls._lineas_elegibles(
            db, empresa_id=empresa_id,
            carrito_id=carrito_id,
            sucursal_id=carrito["sucursal_id"],
        )
        if len(validas) != len(lineas):
            raise CarritoTenantError(409, "Prendas no disponibles para reservar")
        total = 0
        for fila in validas:
            cantidad = int(fila["cantidad"])
            if cantidad <= 0 or cantidad > (
                int(fila["stock_actual"]) - int(fila["stock_reservado"])
            ):
                raise CarritoTenantError(409, "Stock insuficiente para reservar")
            total += cantidad
        return {
            "carrito_id": carrito_id,
            "sucursal_id": carrito["sucursal_id"],
            "fecha_atencion": fecha,
            "cantidad_lineas": len(validas),
            "cantidad_unidades": total,
            "stock_verificado_preliminarmente": True,
            "reserva_creada": False,
            "requiere_verificacion_transaccional": True,
        }
