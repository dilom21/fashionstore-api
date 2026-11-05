"""Customer-owned tenant queries and digital checkout transaction."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.orm import Session

from app.core.tenant_idempotency_models import OperacionClienteIdempotente
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import Color, Producto, Talla, Temporada, VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.pagos.models.models import Pago
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta


class ClienteDigitalError(Exception):
    def __init__(self, codigo: int, detalle: str):
        self.codigo, self.detalle = codigo, detalle
        super().__init__(detalle)


class TenantClienteRepository:
    @staticmethod
    def _id_sqlite(db: Session, tabla) -> int | None:
        """Emulate PostgreSQL BIGINT identity only for offline SQLite tests."""
        if db.get_bind().dialect.name != "sqlite":
            return None
        return int(db.scalar(select(func.coalesce(func.max(tabla.c.id), 0) + 1)) or 1)

    @staticmethod
    def cliente_id(db: Session, *, empresa_id: int, usuario_id: int) -> int:
        c = Cliente.__table__
        valor = db.scalar(select(c.c.id).where(
            c.c.empresa_id == empresa_id, c.c.usuario_id == usuario_id,
            c.c.estado.is_(True),
        ))
        if valor is None:
            raise ClienteDigitalError(403, "No existe cliente activo en esta empresa")
        return int(valor)

    @classmethod
    def listar_reservas(cls, db: Session, *, empresa_id: int, usuario_id: int) -> list[dict]:
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        r, s, dr = Reserva.__table__, Sucursal.__table__, DetalleReserva.__table__
        stmt = (
            select(
                r.c.id.label("reserva_id"), r.c.carrito_id, r.c.sucursal_id,
                s.c.nombre.label("sucursal_nombre"), r.c.fecha_reserva,
                r.c.fecha_atencion, r.c.estado, r.c.observacion,
                func.count(dr.c.id).label("cantidad_lineas"),
                func.coalesce(func.sum(dr.c.cantidad), 0).label("cantidad_unidades"),
            )
            .join(s, and_(s.c.id == r.c.sucursal_id, s.c.empresa_id == empresa_id))
            .outerjoin(dr, and_(dr.c.reserva_id == r.c.id, dr.c.empresa_id == empresa_id))
            .where(r.c.empresa_id == empresa_id, r.c.cliente_id == cliente_id)
            .group_by(r.c.id, s.c.nombre)
            .order_by(r.c.fecha_reserva.desc(), r.c.id.desc())
        )
        return [dict(x) for x in db.execute(stmt).mappings().all()]

    @classmethod
    def obtener_reserva(cls, db: Session, *, empresa_id: int, usuario_id: int,
                        reserva_id: int) -> dict:
        """Return one reservation with its lines, scoped by tenant and owner."""
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        reserva = db.execute(select(Reserva.__table__).where(
            Reserva.id == reserva_id, Reserva.empresa_id == empresa_id,
            Reserva.cliente_id == cliente_id,
        )).mappings().first()
        if reserva is None:
            raise ClienteDigitalError(404, "Reserva no encontrada")
        sucursal = db.execute(select(Sucursal.nombre).where(
            Sucursal.id == reserva["sucursal_id"],
            Sucursal.empresa_id == empresa_id,
        )).scalar_one_or_none()
        if sucursal is None:
            raise ClienteDigitalError(409, "Reserva inconsistente")
        lines = db.execute(select(
            DetalleReserva.id.label("detalle_id"),
            DetalleReserva.inventario_id, DetalleReserva.cantidad,
            Producto.id.label("producto_id"), Producto.nombre.label("producto_nombre"),
            VarianteProducto.id.label("variante_producto_id"), VarianteProducto.sku,
            Talla.id.label("talla_id"), Talla.nombre.label("talla_nombre"),
            Color.id.label("color_id"), Color.nombre.label("color_nombre"),
            Temporada.id.label("temporada_id"), Temporada.nombre.label("temporada_nombre"),
        ).join(Inventario, and_(Inventario.id == DetalleReserva.inventario_id,
                                Inventario.empresa_id == empresa_id))
        .join(VarianteProducto, and_(VarianteProducto.id == Inventario.variante_producto_id,
                                     VarianteProducto.empresa_id == empresa_id))
        .join(Producto, and_(Producto.id == VarianteProducto.producto_id,
                             Producto.empresa_id == empresa_id))
        .join(Talla, Talla.id == VarianteProducto.talla_id)
        .join(Color, Color.id == VarianteProducto.color_id)
        .join(Temporada, and_(Temporada.id == Inventario.temporada_id,
                              Temporada.empresa_id == empresa_id))
        .where(DetalleReserva.reserva_id == reserva_id,
               DetalleReserva.empresa_id == empresa_id)
        .order_by(DetalleReserva.id)).mappings().all()
        return {
            "reserva_id": int(reserva["id"]), "carrito_id": reserva["carrito_id"],
            "cliente_id": cliente_id, "sucursal_id": int(reserva["sucursal_id"]),
            "sucursal_nombre": sucursal, "fecha_reserva": reserva["fecha_reserva"],
            "fecha_atencion": reserva["fecha_atencion"], "estado": reserva["estado"],
            "observacion": reserva["observacion"],
            "items": [dict(line) | {"imagen_principal": None} for line in lines],
            "cantidad_total_unidades": sum(int(line["cantidad"]) for line in lines),
        }

    @classmethod
    def obtener_venta_detalle(cls, db: Session, *, empresa_id: int, usuario_id: int,
                              venta_id: int) -> dict:
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        venta = cls.obtener_venta(db, empresa_id=empresa_id, cliente_id=cliente_id,
                                  venta_id=venta_id)
        rows = db.execute(select(
            DetalleVenta.inventario_id, DetalleVenta.cantidad,
            DetalleVenta.precio_unitario,
        ).where(DetalleVenta.empresa_id == empresa_id,
                DetalleVenta.venta_id == venta_id).order_by(DetalleVenta.id)).mappings().all()
        return {**venta, "lineas": [dict(row) for row in rows]}

    @staticmethod
    def _huella(carrito_id: int, canal: str) -> str:
        carga = json.dumps(
            {"carrito_id": carrito_id, "canal": canal},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(carga.encode("utf-8")).hexdigest()

    @classmethod
    def crear_venta_digital(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int, canal: str, operacion_id: str,
    ) -> tuple[dict, bool]:
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        huella = cls._huella(carrito_id, canal)
        op = OperacionClienteIdempotente.__table__
        existente = db.execute(select(op).where(
            op.c.empresa_id == empresa_id, op.c.usuario_id == usuario_id,
            op.c.tipo == "VENTA_DIGITAL", op.c.operacion_id == operacion_id,
        ).with_for_update()).mappings().first()
        if existente is not None:
            if existente["huella"] != huella:
                raise ClienteDigitalError(409, "La clave idempotente corresponde a otra solicitud")
            if existente["estado"] != "PROCESADA" or existente["recurso_id"] is None:
                raise ClienteDigitalError(409, "La operación todavía está en proceso")
            return cls.obtener_venta(
                db, empresa_id=empresa_id, cliente_id=cliente_id,
                venta_id=int(existente["recurso_id"]),
            ), True

        db.execute(insert(op).values(
            empresa_id=empresa_id, usuario_id=usuario_id,
            tipo="VENTA_DIGITAL", operacion_id=operacion_id,
            huella=huella, estado="EN_PROCESO",
        ))
        car, det = Carrito.__table__, DetalleCarrito.__table__
        inv, var, pro = Inventario.__table__, VarianteProducto.__table__, Producto.__table__
        carro = db.execute(select(
            car.c.id, car.c.sucursal_id, car.c.estado,
        ).where(
            car.c.id == carrito_id, car.c.empresa_id == empresa_id,
            car.c.cliente_id == cliente_id,
        ).with_for_update()).mappings().first()
        if carro is None:
            raise ClienteDigitalError(404, "Carrito no encontrado")

        venta_existente = db.scalar(select(Venta.id).where(
            Venta.empresa_id == empresa_id, Venta.carrito_id == carrito_id,
            Venta.cliente_id == cliente_id,
        ))
        if venta_existente is not None:
            resultado = cls.obtener_venta(
                db, empresa_id=empresa_id, cliente_id=cliente_id,
                venta_id=int(venta_existente),
            )
            db.execute(update(op).where(
                op.c.empresa_id == empresa_id, op.c.usuario_id == usuario_id,
                op.c.tipo == "VENTA_DIGITAL", op.c.operacion_id == operacion_id,
            ).values(estado="PROCESADA", recurso_id=int(venta_existente)))
            db.commit()
            return resultado, True
        if carro["estado"] != "ACTIVO":
            raise ClienteDigitalError(409, "El carrito no está activo")

        lineas = db.execute(
            select(det.c.inventario_id, det.c.cantidad, pro.c.precio)
            .join(inv, and_(inv.c.id == det.c.inventario_id, inv.c.empresa_id == empresa_id))
            .join(var, and_(var.c.id == inv.c.variante_producto_id, var.c.empresa_id == empresa_id, var.c.estado.is_(True)))
            .join(pro, and_(pro.c.id == var.c.producto_id, pro.c.empresa_id == empresa_id, pro.c.estado.is_(True)))
            .where(
                det.c.empresa_id == empresa_id, det.c.carrito_id == carrito_id,
                inv.c.sucursal_id == carro["sucursal_id"],
                det.c.cantidad <= inv.c.stock_actual - inv.c.stock_reservado,
            ).with_for_update(of=inv)
        ).mappings().all()
        cantidad_real = int(db.scalar(select(func.count()).select_from(det).where(
            det.c.empresa_id == empresa_id, det.c.carrito_id == carrito_id,
        )) or 0)
        if not lineas or len(lineas) != cantidad_real:
            raise ClienteDigitalError(409, "El carrito contiene inventario no disponible")

        total = sum(
            (Decimal(str(x["precio"])) * int(x["cantidad"]) for x in lineas),
            Decimal("0.00"),
        )
        v, dv = Venta.__table__, DetalleVenta.__table__
        valores_venta = dict(
            empresa_id=empresa_id, cliente_id=cliente_id, empleado_id=None,
            sucursal_id=int(carro["sucursal_id"]), reserva_id=None,
            fecha_hora=datetime.now(timezone.utc), canal=canal,
            estado="PENDIENTE", total=total, carrito_id=carrito_id,
        )
        id_sqlite = cls._id_sqlite(db, v)
        if id_sqlite is not None:
            valores_venta["id"] = id_sqlite
        venta_id = int(db.scalar(insert(v).values(**valores_venta).returning(v.c.id)))
        siguiente_detalle = cls._id_sqlite(db, dv)
        detalles = [
            {"empresa_id": empresa_id, "venta_id": venta_id,
             "inventario_id": int(x["inventario_id"]),
             "cantidad": int(x["cantidad"]), "precio_unitario": x["precio"]}
            for x in lineas
        ]
        if siguiente_detalle is not None:
            for indice, detalle in enumerate(detalles):
                detalle["id"] = siguiente_detalle + indice
        db.execute(insert(dv), detalles)
        db.execute(update(car).where(
            car.c.id == carrito_id, car.c.empresa_id == empresa_id,
            car.c.cliente_id == cliente_id,
        ).values(estado="CONVERTIDO", fecha_actualizacion=datetime.now(timezone.utc)))
        db.execute(update(op).where(
            op.c.empresa_id == empresa_id, op.c.usuario_id == usuario_id,
            op.c.tipo == "VENTA_DIGITAL", op.c.operacion_id == operacion_id,
        ).values(estado="PROCESADA", recurso_id=venta_id))
        db.commit()
        return cls.obtener_venta(
            db, empresa_id=empresa_id, cliente_id=cliente_id, venta_id=venta_id,
        ), False

    @staticmethod
    def obtener_venta(db: Session, *, empresa_id: int, cliente_id: int, venta_id: int) -> dict:
        v, s = Venta.__table__, Sucursal.__table__
        fila = db.execute(select(
            v.c.id.label("venta_id"), v.c.carrito_id, v.c.sucursal_id,
            s.c.nombre.label("sucursal_nombre"), v.c.canal, v.c.estado,
            v.c.fecha_hora, v.c.total,
        ).join(s, and_(s.c.id == v.c.sucursal_id, s.c.empresa_id == empresa_id)).where(
            v.c.id == venta_id, v.c.empresa_id == empresa_id,
            v.c.cliente_id == cliente_id,
        )).mappings().first()
        if fila is None:
            raise ClienteDigitalError(404, "Venta no encontrada")
        return dict(fila)

    @classmethod
    def historial(cls, db: Session, *, empresa_id: int, usuario_id: int) -> list[dict]:
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        v, s, dv = Venta.__table__, Sucursal.__table__, DetalleVenta.__table__
        stmt = select(
            v.c.id.label("venta_id"), v.c.fecha_hora, v.c.canal, v.c.estado,
            v.c.total, v.c.sucursal_id, s.c.nombre.label("sucursal_nombre"),
            func.count(dv.c.id).label("cantidad_lineas"),
            func.coalesce(func.sum(dv.c.cantidad), 0).label("cantidad_total_unidades"),
        ).join(s, and_(s.c.id == v.c.sucursal_id, s.c.empresa_id == empresa_id)).join(
            dv, and_(dv.c.venta_id == v.c.id, dv.c.empresa_id == empresa_id)
        ).where(
            v.c.empresa_id == empresa_id, v.c.cliente_id == cliente_id,
        ).group_by(v.c.id, s.c.nombre).order_by(v.c.fecha_hora.desc(), v.c.id.desc())
        return [dict(x) for x in db.execute(stmt).mappings().all()]

    @classmethod
    def comprobante(cls, db: Session, *, empresa_id: int, usuario_id: int, venta_id: int) -> dict:
        cliente_id = cls.cliente_id(db, empresa_id=empresa_id, usuario_id=usuario_id)
        venta = cls.obtener_venta(db, empresa_id=empresa_id, cliente_id=cliente_id, venta_id=venta_id)
        if venta["estado"] != "COMPLETADA":
            raise ClienteDigitalError(409, "La venta aún no está completada")
        p = Pago.__table__
        pago = db.execute(select(
            p.c.id.label("pago_id"), p.c.fecha_hora, p.c.monto, p.c.metodo,
            p.c.estado, p.c.referencia_transaccion, p.c.pasarela,
        ).where(
            p.c.empresa_id == empresa_id, p.c.venta_id == venta_id,
            p.c.estado == "APROBADO",
        ).order_by(p.c.id.desc())).mappings().first()
        if pago is None:
            raise ClienteDigitalError(409, "La venta no tiene un pago aprobado")
        return {**venta, "pago": dict(pago)}
