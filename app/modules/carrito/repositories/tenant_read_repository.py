"""CU15 SaaS: consultas del carrito del cliente actual, SIEMPRE solo lectura.

No se reutilizan relaciones ORM lazy-load legacy: cada JOIN de una tabla
empresarial exige empresa_id, además de cliente.usuario_id. Ni SELECT FOR
UPDATE ni expiraciones: un GET nunca debe cambiar datos ni tomar row locks.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.carrito.repositories.tenant_repository import (
    CarritoTenantError, VIGENCIA_CARRITO_HORAS,
)


class CarritoTenantReadRepository:
    @staticmethod
    def _validar_identidad(empresa_id: int, usuario_id: int) -> None:
        if any(
            isinstance(v, bool) or not isinstance(v, int) or v <= 0
            for v in (empresa_id, usuario_id)
        ):
            raise CarritoTenantError(403, "Identidad de cliente invalida")

    @classmethod
    def _cliente_activo(cls, db: Session, *, empresa_id: int, usuario_id: int) -> int:
        cls._validar_identidad(empresa_id, usuario_id)
        c = Cliente.__table__
        cliente_id = db.scalar(
            select(c.c.id).where(
                c.c.empresa_id == empresa_id,
                c.c.usuario_id == usuario_id,
                c.c.estado.is_(True),
            )
        )
        if cliente_id is None:
            raise CarritoTenantError(403, "No existe cliente activo en esta empresa")
        return int(cliente_id)

    @staticmethod
    def _carritos_del_cliente(empresa_id: int, cliente_id: int):
        car, suc = Carrito.__table__, Sucursal.__table__
        return (
            select(
                car.c.id.label("carrito_id"),
                car.c.sucursal_id,
                suc.c.nombre.label("sucursal_nombre"),
                car.c.estado,
                car.c.fecha_creacion,
                car.c.fecha_actualizacion,
            )
            .join(
                suc, and_(
                    suc.c.id == car.c.sucursal_id,
                    suc.c.empresa_id == empresa_id,
                ),
            )
            .where(
                car.c.empresa_id == empresa_id,
                car.c.cliente_id == cliente_id,
            )
        )

    @staticmethod
    def _vigente(fecha, ahora: datetime) -> bool:
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha > ahora - timedelta(hours=VIGENCIA_CARRITO_HORAS)

    @staticmethod
    def _lineas(db: Session, *, empresa_id: int, carrito_id: int, sucursal_id: int) -> list[dict]:
        det, inv = DetalleCarrito.__table__, Inventario.__table__
        var, pro, cat = VarianteProducto.__table__, Producto.__table__, Categoria.__table__
        talla, color, tem = Talla.__table__, Color.__table__, Temporada.__table__
        sentencia = (
            select(
                det.c.id.label("detalle_id"),
                det.c.inventario_id,
                det.c.cantidad,
                pro.c.id.label("producto_id"),
                pro.c.nombre.label("producto_nombre"),
                pro.c.precio.label("precio_unitario"),
                var.c.id.label("variante_producto_id"),
                var.c.sku,
                talla.c.id.label("talla_id"),
                talla.c.nombre.label("talla_nombre"),
                color.c.id.label("color_id"),
                color.c.nombre.label("color_nombre"),
                tem.c.id.label("temporada_id"),
                tem.c.nombre.label("temporada_nombre"),
                (inv.c.stock_actual - inv.c.stock_reservado).label("stock_disponible"),
            )
            .select_from(det)
            .join(inv, and_(
                inv.c.id == det.c.inventario_id,
                inv.c.empresa_id == empresa_id,
                inv.c.sucursal_id == sucursal_id,
            ))
            .join(var, and_(
                var.c.id == inv.c.variante_producto_id,
                var.c.empresa_id == empresa_id,
            ))
            .join(pro, and_(
                pro.c.id == var.c.producto_id,
                pro.c.empresa_id == empresa_id,
            ))
            .join(cat, and_(
                cat.c.id == pro.c.categoria_id,
                cat.c.empresa_id == empresa_id,
            ))
            .join(tem, and_(
                tem.c.id == inv.c.temporada_id,
                tem.c.empresa_id == empresa_id,
            ))
            .join(talla, talla.c.id == var.c.talla_id)
            .join(color, color.c.id == var.c.color_id)
            .where(
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            )
            .order_by(det.c.id)
        )
        filas = [dict(x) for x in db.execute(sentencia).mappings().all()]
        # No ocultar silenciosamente lineas corruptas o de otro tenant.
        # Si la cuenta no coincide, no se devuelve contenido parcial.
        cantidad_total = int(db.scalar(
            select(func.count()).select_from(det).where(
                det.c.carrito_id == carrito_id,
            )
        ) or 0)
        if len(filas) != cantidad_total:
            raise CarritoTenantError(409, "El carrito contiene datos inconsistentes")
        return filas

    @classmethod
    def _resumen(
        cls, db: Session, *, empresa_id: int, carro: dict
    ) -> tuple[dict, list[dict]]:
        lineas = cls._lineas(
            db,
            empresa_id=empresa_id,
            carrito_id=int(carro["carrito_id"]),
            sucursal_id=int(carro["sucursal_id"]),
        )
        subtotal = Decimal("0.00")
        unidades = 0
        for linea in lineas:
            precio = Decimal(str(linea["precio_unitario"]))
            cantidad = int(linea["cantidad"])
            linea["precio_unitario"] = precio
            linea["subtotal_linea"] = precio * cantidad
            subtotal += linea["subtotal_linea"]
            unidades += cantidad
        return {
            "carrito_id": int(carro["carrito_id"]),
            "sucursal_id": int(carro["sucursal_id"]),
            "sucursal_nombre": carro["sucursal_nombre"],
            "cantidad_lineas": len(lineas),
            "cantidad_unidades": unidades,
            "subtotal": subtotal,
            "fecha_actualizacion": carro["fecha_actualizacion"],
        }, lineas

    @classmethod
    def listar(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        limit: int = 50, offset: int = 0,
    ) -> dict:
        if (
            isinstance(limit, bool) or not isinstance(limit, int)
            or isinstance(offset, bool) or not isinstance(offset, int)
            or not (1 <= limit <= 100) or offset < 0
        ):
            raise ValueError("Paginacion invalida")
        cliente_id = cls._cliente_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
        )
        ahora = datetime.now(timezone.utc)
        car = Carrito.__table__
        carros = db.execute(
            cls._carritos_del_cliente(empresa_id, cliente_id)
            .where(
                car.c.estado == "ACTIVO",
                car.c.fecha_actualizacion >
                ahora - timedelta(hours=VIGENCIA_CARRITO_HORAS),
            )
            .order_by(car.c.fecha_actualizacion.desc(), car.c.id.desc())
            .limit(limit).offset(offset)
        ).mappings().all()
        items = []
        for carro in carros:
            resumen, _ = cls._resumen(
                db, empresa_id=empresa_id, carro=dict(carro),
            )
            items.append(resumen)
        return {"items": items, "total_carritos_activos": len(items)}

    @classmethod
    def obtener(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int,
    ) -> dict:
        if isinstance(carrito_id, bool) or not isinstance(carrito_id, int) or carrito_id <= 0:
            raise CarritoTenantError(404, "Carrito no encontrado")
        cliente_id = cls._cliente_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
        )
        car = Carrito.__table__
        carro = db.execute(
            cls._carritos_del_cliente(empresa_id, cliente_id)
            .where(car.c.id == carrito_id)
        ).mappings().one_or_none()
        if carro is None:
            raise CarritoTenantError(404, "Carrito no encontrado")
        if carro["estado"] != "ACTIVO" or not cls._vigente(
            carro["fecha_actualizacion"], datetime.now(timezone.utc)
        ):
            raise CarritoTenantError(409, "El carrito ya no esta activo")
        resumen, lineas = cls._resumen(
            db, empresa_id=empresa_id, carro=dict(carro),
        )
        return {
            "carrito_id": resumen["carrito_id"],
            "sucursal_id": resumen["sucursal_id"],
            "sucursal_nombre": resumen["sucursal_nombre"],
            "estado": carro["estado"],
            "fecha_creacion": carro["fecha_creacion"],
            "fecha_actualizacion": resumen["fecha_actualizacion"],
            "items": lineas,
            "cantidad_total_unidades": resumen["cantidad_unidades"],
            "subtotal_carrito": resumen["subtotal"],
        }
