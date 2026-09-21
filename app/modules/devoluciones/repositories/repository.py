"""Acceso a datos de CU25 - Registrar devolucion de productos.

Solo consultas/persistencia y la invocacion del procedimiento existente
``sp_registrar_devolucion``. Las reglas de negocio (roles, alcance, estados,
disponibilidad, transiciones) viven en el service.

No hay bloqueos globales: se bloquean la venta y sus lineas relevantes
(``SELECT ... FOR UPDATE``) dentro de la misma transaccion.
"""

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.catalogo.models.models import VarianteProducto
from app.modules.devoluciones.models.models import DetalleDevolucion, Devolucion
from app.modules.inventario.models.models import Inventario
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta

ESTADO_SOLICITADA = "SOLICITADA"

# Autoridad transaccional: valida estado APROBADA, verifica que cada detalle
# pertenezca a la venta, calcula lo ya devuelto, bloquea inventario, incrementa
# stock_actual, registra MovimientoInventario DEVOLUCION y marca COMPLETADA.
SQL_REGISTRAR_DEVOLUCION = text(
    "CALL public.sp_registrar_devolucion(:devolucion_id, :usuario_id)"
)


class DevolucionRepository:
    # ------------------------------------------------------------------
    # Venta y sus lineas
    # ------------------------------------------------------------------

    @staticmethod
    def _cadena_detalle_venta(atributo):
        """Cadena COMPLETA explicita: DetalleVenta -> inventario -> variante -> atributo.

        Se construye entera en cada llamada (joinedload es generativo: crear el
        path una vez y reusar un loader a medias produce un path equivocado).
        """
        return (
            joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(atributo)
        )

    @staticmethod
    def _carga_detalles_venta():
        """DetalleVenta + inventario/variante/producto/talla/color."""
        return (
            DevolucionRepository._cadena_detalle_venta(VarianteProducto.producto),
            DevolucionRepository._cadena_detalle_venta(VarianteProducto.talla),
            DevolucionRepository._cadena_detalle_venta(VarianteProducto.color),
        )

    @staticmethod
    def obtener_venta_para_devolucion(db: Session, venta_id: int) -> Venta | None:
        """Venta + sucursal + lineas (lectura, sin bloqueo)."""
        statement = (
            select(Venta)
            .options(
                joinedload(Venta.sucursal),
                joinedload(Venta.detalles).options(
                    *DevolucionRepository._carga_detalles_venta()
                ),
            )
            .where(Venta.id == venta_id)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def bloquear_venta(db: Session, venta_id: int) -> Venta | None:
        """SELECT ... FOR UPDATE sobre la venta (serializa devoluciones)."""
        statement = select(Venta).where(Venta.id == venta_id).with_for_update()
        return db.scalars(statement).first()

    @staticmethod
    def bloquear_detalles_venta(
        db: Session, venta_id: int
    ) -> list[DetalleVenta]:
        """Bloquea las lineas de la venta en orden estable de id."""
        statement = (
            select(DetalleVenta)
            .where(DetalleVenta.venta_id == venta_id)
            .order_by(DetalleVenta.id)
            .with_for_update()
        )
        return list(db.scalars(statement).unique().all())

    @staticmethod
    def cantidades_comprometidas(
        db: Session,
        venta_id: int,
        *,
        estados: tuple[str, ...],
        excluir_devolucion_id: int | None = None,
    ) -> dict[int, int]:
        """SUM(cantidad) por ``detalle_venta_id`` segun los estados dados.

        Se resuelve agregadamente (una sola consulta) para toda la venta.
        """
        statement = (
            select(
                DetalleDevolucion.detalle_venta_id,
                func.coalesce(func.sum(DetalleDevolucion.cantidad), 0),
            )
            .join(
                Devolucion, Devolucion.id == DetalleDevolucion.devolucion_id
            )
            .where(
                Devolucion.venta_id == venta_id,
                Devolucion.estado.in_(estados),
            )
            .group_by(DetalleDevolucion.detalle_venta_id)
        )
        if excluir_devolucion_id is not None:
            statement = statement.where(Devolucion.id != excluir_devolucion_id)
        return {
            int(fila[0]): int(fila[1])
            for fila in db.execute(statement).all()
        }

    @staticmethod
    def obtener_cliente(db: Session, cliente_id: int) -> Cliente | None:
        return db.get(Cliente, cliente_id)

    # ------------------------------------------------------------------
    # Devolucion y sus lineas
    # ------------------------------------------------------------------

    @staticmethod
    def crear_devolucion(
        db: Session,
        *,
        venta_id: int,
        motivo: str,
        observacion: str | None,
        fecha_hora: datetime,
    ) -> Devolucion:
        devolucion = Devolucion(
            venta_id=venta_id,
            fecha_hora=fecha_hora,
            motivo=motivo,
            estado=ESTADO_SOLICITADA,
            observacion=observacion,
        )
        db.add(devolucion)
        db.flush()
        return devolucion

    @staticmethod
    def crear_detalle(
        db: Session,
        *,
        devolucion_id: int,
        detalle_venta_id: int,
        cantidad: int,
        motivo: str | None,
    ) -> DetalleDevolucion:
        detalle = DetalleDevolucion(
            devolucion_id=devolucion_id,
            detalle_venta_id=detalle_venta_id,
            cantidad=cantidad,
            motivo=motivo,
        )
        db.add(detalle)
        db.flush()
        return detalle

    @staticmethod
    def _cadena_detalle_devolucion(atributo):
        """Cadena COMPLETA explicita:
        Devolucion.detalles -> DetalleDevolucion.detalle_venta
        -> DetalleVenta.inventario -> Inventario.variante_producto -> atributo.

        Cada rama (producto/talla/color) se construye desde cero: nunca se
        reutiliza un loader parcial que termine en DetalleVenta.inventario.
        """
        return (
            selectinload(Devolucion.detalles)
            .joinedload(DetalleDevolucion.detalle_venta)
            .joinedload(DetalleVenta.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(atributo)
        )

    @staticmethod
    def _carga_detalle_devolucion():
        """Lineas de la devolucion + detalle_venta + producto/talla/color."""
        return (
            DevolucionRepository._cadena_detalle_devolucion(
                VarianteProducto.producto
            ),
            DevolucionRepository._cadena_detalle_devolucion(
                VarianteProducto.talla
            ),
            DevolucionRepository._cadena_detalle_devolucion(
                VarianteProducto.color
            ),
        )

    @staticmethod
    def obtener_por_id(
        db: Session, devolucion_id: int, *, populate_existing: bool = False
    ) -> Devolucion | None:
        """Devolucion + venta + sucursal + lineas con producto/talla/color.

        ``populate_existing=True`` sobrescribe los atributos de la instancia que
        ya estuviera en el identity map (necesario cuando PostgreSQL modifica la
        fila desde un procedimiento almacenado y la sesion aun tiene el valor
        viejo, p. ej. el SP de CU25 al pasar a COMPLETADA).
        """
        statement = (
            select(Devolucion)
            .options(
                joinedload(Devolucion.venta).joinedload(Venta.sucursal),
                *DevolucionRepository._carga_detalle_devolucion(),
            )
            .where(Devolucion.id == devolucion_id)
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return db.scalars(statement).unique().first()

    @staticmethod
    def bloquear_devolucion(
        db: Session, devolucion_id: int
    ) -> Devolucion | None:
        """SELECT ... FOR UPDATE sobre la devolucion (transiciones seguras)."""
        statement = (
            select(Devolucion)
            .where(Devolucion.id == devolucion_id)
            .with_for_update()
        )
        return db.scalars(statement).first()

    @staticmethod
    def listar_detalles(
        db: Session, devolucion_id: int
    ) -> list[DetalleDevolucion]:
        statement = (
            select(DetalleDevolucion)
            .where(DetalleDevolucion.devolucion_id == devolucion_id)
            .order_by(DetalleDevolucion.id)
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def actualizar_estado(
        db: Session, devolucion: Devolucion, estado: str
    ) -> None:
        devolucion.estado = estado
        db.flush()

    @staticmethod
    def procesar_devolucion(
        db: Session, *, devolucion_id: int, usuario_id: int
    ) -> None:
        """Ejecuta ``sp_registrar_devolucion`` (autoridad del procesamiento)."""
        db.execute(
            SQL_REGISTRAR_DEVOLUCION,
            {"devolucion_id": devolucion_id, "usuario_id": usuario_id},
        )

    # ------------------------------------------------------------------
    # Listado (agregado, sin N+1)
    # ------------------------------------------------------------------

    @staticmethod
    def _condiciones_listado(
        *,
        sucursal_id: int | None,
        estado: str | None,
        desde: datetime | None,
        hasta: datetime | None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Venta.sucursal_id == sucursal_id)
        if estado is not None:
            condiciones.append(Devolucion.estado == estado)
        if desde is not None:
            condiciones.append(Devolucion.fecha_hora >= desde)
        if hasta is not None:
            condiciones.append(Devolucion.fecha_hora < hasta)
        return condiciones

    @staticmethod
    def listar_devoluciones(
        db: Session,
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Una sola consulta con JOIN sucursal y agregados de lineas."""
        condiciones = DevolucionRepository._condiciones_listado(
            sucursal_id=sucursal_id,
            estado=estado,
            desde=desde,
            hasta=hasta,
        )
        statement = (
            select(
                Devolucion.id.label("devolucion_id"),
                Devolucion.venta_id.label("venta_id"),
                Devolucion.fecha_hora.label("fecha_hora"),
                Devolucion.estado.label("estado"),
                Devolucion.motivo.label("motivo"),
                Sucursal.id.label("sucursal_id"),
                Sucursal.nombre.label("sucursal_nombre"),
                func.count(DetalleDevolucion.id).label("cantidad_lineas"),
                func.coalesce(func.sum(DetalleDevolucion.cantidad), 0).label(
                    "cantidad_total_unidades"
                ),
            )
            .join(Venta, Venta.id == Devolucion.venta_id)
            .join(Sucursal, Sucursal.id == Venta.sucursal_id)
            .outerjoin(
                DetalleDevolucion,
                DetalleDevolucion.devolucion_id == Devolucion.id,
            )
            .where(*condiciones)
            .group_by(
                Devolucion.id,
                Devolucion.venta_id,
                Devolucion.fecha_hora,
                Devolucion.estado,
                Devolucion.motivo,
                Sucursal.id,
                Sucursal.nombre,
            )
            .order_by(Devolucion.fecha_hora.desc(), Devolucion.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(statement).all())

    @staticmethod
    def contar_devoluciones(
        db: Session,
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> int:
        condiciones = DevolucionRepository._condiciones_listado(
            sucursal_id=sucursal_id,
            estado=estado,
            desde=desde,
            hasta=hasta,
        )
        statement = (
            select(func.count())
            .select_from(Devolucion)
            .join(Venta, Venta.id == Devolucion.venta_id)
            .where(*condiciones)
        )
        return int(db.scalar(statement) or 0)

