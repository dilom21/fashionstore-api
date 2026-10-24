"""CU15 SaaS: agregar item al carrito propio, sin reservar ni descontar stock.

Una sola transaccion para caducar carritos vencidos, crear/reusar carrito
y agregar/sumar su linea. Se valida propietario, empresa, sucursal y
cadena inventario->variante->producto->categoria dentro de SQL.

El bloque FOR UPDATE sobre cliente serializa altas del mismo comprador en PG;
el bloque de inventario protege la lectura de disponibilidad. Las FK tenant
de PostgreSQL y la UNIQUE parcial siguen siendo defensas obligatorias.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.catalogo.models.models import (
    Categoria, Color, Producto, Talla, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal

VIGENCIA_CARRITO_HORAS = 2


class CarritoTenantError(Exception):
    def __init__(self, codigo: int, detalle: str):
        self.codigo = codigo
        self.detalle = detalle
        super().__init__(detalle)


class CarritoTenantRepository:
    @staticmethod
    def _cliente_id(db: Session, *, empresa_id: int, usuario_id: int) -> int:
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
               for v in (empresa_id, usuario_id)):
            raise CarritoTenantError(403, "Identidad de cliente invalida")
        c = Cliente.__table__
        # Bloquear al cliente permite serializar dos altas simultaneas.
        stmt = select(c.c.id).where(
            c.c.empresa_id == empresa_id,
            c.c.usuario_id == usuario_id,
            c.c.estado.is_(True),
        ).with_for_update(of=c)
        id_ = db.scalar(stmt)
        if id_ is None:
            raise CarritoTenantError(403, "No existe cliente activo en esta empresa")
        return int(id_)

    @staticmethod
    def _stock_valido(
        db: Session, *, empresa_id: int, sucursal_id: int, inventario_id: int,
    ) -> int:
        i, v = Inventario.__table__, VarianteProducto.__table__
        p, c = Producto.__table__, Categoria.__table__
        s = Sucursal.__table__
        t, co = Talla.__table__, Color.__table__
        temporada = Temporada.__table__
        stmt = (
            select(i.c.stock_actual, i.c.stock_reservado)
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
            # La temporada tambien es un recurso empresarial: exigir tenant.
            .join(temporada, and_(
                temporada.c.id == i.c.temporada_id,
                temporada.c.empresa_id == empresa_id,
            ))
            .join(t, and_(t.c.id == v.c.talla_id, t.c.estado.is_(True)))
            .join(co, and_(co.c.id == v.c.color_id, co.c.estado.is_(True)))
            .where(
                i.c.id == inventario_id,
                i.c.empresa_id == empresa_id,
                i.c.sucursal_id == sucursal_id,
            )
            .with_for_update(of=i)
        )
        fila = db.execute(stmt).mappings().one_or_none()
        if fila is None:
            raise CarritoTenantError(404, "Inventario no disponible")
        return int(fila["stock_actual"]) - int(fila["stock_reservado"])

    @classmethod
    def agregar_item(
        cls,
        db: Session, *,
        empresa_id: int, usuario_id: int,
        sucursal_id: int, inventario_id: int, cantidad: int,
    ) -> dict:
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
               for v in (sucursal_id, inventario_id, cantidad)):
            raise CarritoTenantError(422, "Cantidad o identificador invalido")

        cliente_id = cls._cliente_id(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
        )
        ahora = datetime.now(timezone.utc)
        limite = ahora - timedelta(hours=VIGENCIA_CARRITO_HORAS)
        carrito, linea = Carrito.__table__, DetalleCarrito.__table__

        # Solo caduca carritos de este cliente en este tenant.
        db.execute(
            update(carrito).where(
                carrito.c.empresa_id == empresa_id,
                carrito.c.cliente_id == cliente_id,
                carrito.c.estado == "ACTIVO",
                carrito.c.fecha_actualizacion <= limite,
            ).values(estado="EXPIRADO")
        )
        disponible = cls._stock_valido(
            db, empresa_id=empresa_id,
            sucursal_id=sucursal_id, inventario_id=inventario_id,
        )
        actual = db.scalar(
            select(carrito.c.id).where(
                carrito.c.empresa_id == empresa_id,
                carrito.c.cliente_id == cliente_id,
                carrito.c.sucursal_id == sucursal_id,
                carrito.c.estado == "ACTIVO",
                carrito.c.fecha_actualizacion > limite,
            ).with_for_update(of=carrito)
        )
        if actual is None:
            actual = db.scalar(
                insert(carrito).values(
                    empresa_id=empresa_id,
                    cliente_id=cliente_id, sucursal_id=sucursal_id,
                    fecha_creacion=ahora, fecha_actualizacion=ahora,
                    estado="ACTIVO",
                ).returning(carrito.c.id)
            )

        detalle = db.execute(
            select(linea.c.id, linea.c.cantidad).where(
                linea.c.empresa_id == empresa_id,
                linea.c.carrito_id == actual,
                linea.c.inventario_id == inventario_id,
            ).with_for_update(of=linea)
        ).mappings().one_or_none()
        cantidad_final = int(detalle["cantidad"]) + cantidad if detalle else cantidad
        if cantidad_final > disponible:
            raise CarritoTenantError(409, "Stock insuficiente")

        if detalle:
            detalle_id = int(detalle["id"])
            db.execute(update(linea).where(
                linea.c.id == detalle_id,
                linea.c.empresa_id == empresa_id,
                linea.c.carrito_id == actual,
            ).values(cantidad=cantidad_final))
        else:
            detalle_id = db.scalar(
                insert(linea).values(
                    empresa_id=empresa_id,
                    carrito_id=actual,
                    inventario_id=inventario_id, cantidad=cantidad_final,
                ).returning(linea.c.id)
            )
        # El trigger CU15 actualiza actividad al modificar detalle; reforzamos
        # explicitamente la fecha para que el test SQLite replique vigencia.
        db.execute(
            update(carrito).where(
                carrito.c.id == actual,
                carrito.c.empresa_id == empresa_id,
                carrito.c.cliente_id == cliente_id,
                carrito.c.estado == "ACTIVO",
            ).values(fecha_actualizacion=ahora)
        )
        return {
            "carrito_id": int(actual),
            "detalle_id": int(detalle_id),
            "inventario_id": inventario_id,
            "cantidad": cantidad_final,
            "estado": "ACTIVO",
        }

    @classmethod
    def _carrito_propio_activo(
        cls, db: Session, *, empresa_id: int, usuario_id: int, carrito_id: int
    ) -> dict:
        """Bloquea el cliente y SU carrito. Un ID ajeno no revela titularidad.

        Un carrito vencido recibe 409 aunque la fila no tenga aun estado
        EXPIRADO: los errores hacen rollback, no se debe reactivar.
        El siguiente POST valido expira los carritos obsoletos.
        """
        if isinstance(carrito_id, bool) or not isinstance(carrito_id, int) or carrito_id <= 0:
            raise CarritoTenantError(404, "Carrito no encontrado")
        cliente_id = cls._cliente_id(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
        )
        car = Carrito.__table__
        dato = db.execute(
            select(
                car.c.id, car.c.sucursal_id, car.c.cliente_id,
                car.c.estado, car.c.fecha_actualizacion,
            ).where(
                car.c.id == carrito_id,
                car.c.empresa_id == empresa_id,
                car.c.cliente_id == cliente_id,
            ).with_for_update(of=car)
        ).mappings().one_or_none()
        if dato is None:
            raise CarritoTenantError(404, "Carrito no encontrado")

        ultima = dato["fecha_actualizacion"]
        # SQLite en pruebas retorna DATETIME sin zona; PG conserva TIMESTAMPTZ.
        if ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=timezone.utc)
        if dato["estado"] != "ACTIVO" or ultima <= (
            datetime.now(timezone.utc) - timedelta(hours=VIGENCIA_CARRITO_HORAS)
        ):
            raise CarritoTenantError(409, "El carrito ya no esta activo")
        return dict(dato)

    @staticmethod
    def _detalle_propio(
        db: Session, *, empresa_id: int, carrito_id: int, detalle_id: int,
    ) -> dict:
        if isinstance(detalle_id, bool) or not isinstance(detalle_id, int) or detalle_id <= 0:
            raise CarritoTenantError(404, "Linea de carrito no encontrada")
        det = DetalleCarrito.__table__
        fila = db.execute(
            select(det.c.id, det.c.inventario_id, det.c.cantidad).where(
                det.c.id == detalle_id,
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            ).with_for_update(of=det)
        ).mappings().one_or_none()
        if fila is None:
            raise CarritoTenantError(404, "Linea de carrito no encontrada")
        return dict(fila)

    @classmethod
    def actualizar_cantidad(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int, detalle_id: int, cantidad: int,
    ) -> dict:
        """La cantidad REEMPLAZA la previa; no se suma como en POST."""
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
            raise CarritoTenantError(422, "Cantidad invalida")
        car = cls._carrito_propio_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
            carrito_id=carrito_id,
        )
        detalle = cls._detalle_propio(
            db, empresa_id=empresa_id,
            carrito_id=carrito_id, detalle_id=detalle_id,
        )
        disponible = cls._stock_valido(
            db, empresa_id=empresa_id,
            sucursal_id=int(car["sucursal_id"]),
            inventario_id=int(detalle["inventario_id"]),
        )
        if cantidad > disponible:
            raise CarritoTenantError(409, "Stock insuficiente")
        det = DetalleCarrito.__table__
        db.execute(
            update(det).where(
                det.c.id == detalle_id,
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            ).values(cantidad=cantidad)
        )
        carrito = Carrito.__table__
        db.execute(
            update(carrito).where(
                carrito.c.id == carrito_id,
                carrito.c.empresa_id == empresa_id,
                carrito.c.cliente_id == int(car["cliente_id"]),
                carrito.c.estado == "ACTIVO",
            ).values(fecha_actualizacion=datetime.now(timezone.utc))
        )
        return {
            "carrito_id": carrito_id,
            "detalle_id": detalle_id,
            "inventario_id": int(detalle["inventario_id"]),
            "cantidad": cantidad,
            "estado": "ACTIVO",
        }

    @classmethod
    def eliminar_detalle(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int, detalle_id: int,
    ) -> dict:
        """DELETE fisico de linea; carrito vacio => ELIMINADO, nunca ACTIVO."""
        cls._carrito_propio_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
            carrito_id=carrito_id,
        )
        cls._detalle_propio(
            db, empresa_id=empresa_id,
            carrito_id=carrito_id, detalle_id=detalle_id,
        )
        det = DetalleCarrito.__table__
        db.execute(
            delete(det).where(
                det.c.id == detalle_id,
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            )
        )
        restantes = int(db.scalar(
            select(func.count()).select_from(det).where(
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            )
        ) or 0)
        car = Carrito.__table__
        estado = "ELIMINADO" if restantes == 0 else "ACTIVO"
        db.execute(
            update(car).where(
                car.c.id == carrito_id,
                car.c.empresa_id == empresa_id,
                car.c.estado == "ACTIVO",
            ).values(
                estado=estado, fecha_actualizacion=datetime.now(timezone.utc),
            )
        )
        return {
            "carrito_id": carrito_id,
            "estado": estado,
            "cantidad_lineas": restantes,
        }

    @classmethod
    def eliminar_carrito(
        cls, db: Session, *, empresa_id: int, usuario_id: int,
        carrito_id: int,
    ) -> dict:
        """Baja LOGICA: preserva detalles y trazabilidad para auditoria."""
        cls._carrito_propio_activo(
            db, empresa_id=empresa_id, usuario_id=usuario_id,
            carrito_id=carrito_id,
        )
        car = Carrito.__table__
        db.execute(
            update(car).where(
                car.c.id == carrito_id,
                car.c.empresa_id == empresa_id,
                car.c.estado == "ACTIVO",
            ).values(
                estado="ELIMINADO", fecha_actualizacion=datetime.now(timezone.utc),
            )
        )
        det = DetalleCarrito.__table__
        restantes = int(db.scalar(
            select(func.count()).select_from(det).where(
                det.c.empresa_id == empresa_id,
                det.c.carrito_id == carrito_id,
            )
        ) or 0)
        return {
            "carrito_id": carrito_id,
            "estado": "ELIMINADO",
            "cantidad_lineas": restantes,
        }
