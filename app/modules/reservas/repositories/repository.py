"""Acceso a datos de CU16 - Gestionar reserva de prendas.

Solo consultas y llamadas a los procedimientos ya existentes en PostgreSQL.
Las reglas de negocio viven en el service.
"""

from datetime import datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.catalogo.models.models import VarianteProducto
from app.modules.inventario.models.models import Inventario
from app.modules.reservas.models.models import DetalleReserva, Reserva

# Estados reales permitidos por la tabla reserva (CK reserva_estado).
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_CONFIRMADA = "CONFIRMADA"
ESTADO_ATENDIDA = "ATENDIDA"
ESTADO_CANCELADA = "CANCELADA"
ESTADO_VENCIDA = "VENCIDA"
ESTADOS_CANCELABLES = (ESTADO_PENDIENTE, ESTADO_CONFIRMADA)

# Operaciones criticas ya implementadas en PostgreSQL: NO se reimplementan en
# Python. Los parametros van enlazados (nunca SQL construido por concatenacion).
# El INOUT p_reserva_id se envia como NULL y PostgreSQL devuelve su valor en el
# resultado del CALL (verificado contra la base real).
SQL_CREAR_DESDE_CARRITO = text(
    "CALL public.sp_crear_reserva_desde_carrito("
    ":carrito_id, :fecha_atencion, :observacion, :usuario_id, "
    "CAST(:reserva_id AS bigint))"
)

SQL_CANCELAR_RESERVA = text(
    "CALL public.sp_cancelar_reserva(:reserva_id, :usuario_id, :observacion)"
)

# CU18: atencion sin compra (libera stock_reservado y marca ATENDIDA).
# El procedimiento ya existe en la base: CU18 solo lo invoca.
SQL_FINALIZAR_SIN_COMPRA = text(
    "CALL public.sp_finalizar_reserva_sin_compra("
    ":reserva_id, :usuario_id, :observacion)"
)


class ReservaRepository:
    @staticmethod
    def _carga_completa():
        """Reserva + sucursal + detalles + inventario/variante/producto/talla/color/temporada."""
        return (
            joinedload(Reserva.sucursal),
            joinedload(Reserva.detalles)
            .joinedload(DetalleReserva.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.producto),
            joinedload(Reserva.detalles)
            .joinedload(DetalleReserva.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.talla),
            joinedload(Reserva.detalles)
            .joinedload(DetalleReserva.inventario)
            .joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.color),
            joinedload(Reserva.detalles)
            .joinedload(DetalleReserva.inventario)
            .joinedload(Inventario.temporada),
        )

    @staticmethod
    def obtener_por_id(db: Session, reserva_id: int) -> Reserva | None:
        statement = (
            select(Reserva)
            .options(*ReservaRepository._carga_completa())
            .where(Reserva.id == reserva_id)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def obtener_por_carrito(db: Session, carrito_id: int) -> Reserva | None:
        statement = (
            select(Reserva)
            .options(*ReservaRepository._carga_completa())
            .where(Reserva.carrito_id == carrito_id)
        )
        return db.scalars(statement).unique().first()

    @staticmethod
    def listar_por_cliente(
        db: Session, cliente_id: int, *, estado: str | None = None
    ) -> list[Reserva]:
        condiciones = [Reserva.cliente_id == cliente_id]
        if estado is not None:
            condiciones.append(Reserva.estado == estado)

        statement = (
            select(Reserva)
            .options(*ReservaRepository._carga_completa())
            .where(*condiciones)
            .order_by(Reserva.fecha_reserva.desc(), Reserva.id.desc())
        )
        return list(db.scalars(statement).unique().all())

    @staticmethod
    def crear_desde_carrito(
        db: Session,
        *,
        carrito_id: int,
        fecha_atencion: datetime,
        observacion: str | None,
        usuario_id: int,
    ) -> int | None:
        """Ejecuta sp_crear_reserva_desde_carrito y devuelve el reserva_id.

        El procedimiento valida fecha, estado y propietario del carrito,
        bloquea inventarios (FOR UPDATE), valida stock disponible, incrementa
        stock_reservado, crea detalle_reserva + movimiento RESERVA y marca el
        carrito como CONVERTIDO. Todo dentro de la transaccion de SQLAlchemy.
        """
        resultado = db.execute(
            SQL_CREAR_DESDE_CARRITO,
            {
                "carrito_id": carrito_id,
                "fecha_atencion": fecha_atencion,
                "observacion": observacion,
                "usuario_id": usuario_id,
                "reserva_id": None,
            },
        )
        fila = resultado.fetchone()
        if fila is None or fila[0] is None:
            return None
        return int(fila[0])

    @staticmethod
    def cancelar_reserva(
        db: Session,
        *,
        reserva_id: int,
        usuario_id: int,
        observacion: str | None,
    ) -> None:
        """Ejecuta sp_cancelar_reserva (libera stock_reservado + movimiento).

        El procedimiento solo permite cancelar PENDIENTE o CONFIRMADA y nunca
        reactiva el carrito: la reserva queda CANCELADA y el carrito CONVERTIDO.
        """
        db.execute(
            SQL_CANCELAR_RESERVA,
            {
                "reserva_id": reserva_id,
                "usuario_id": usuario_id,
                "observacion": observacion,
            },
        )

    # ------------------------------------------------------------------
    # CU17 - Gestion de reservas por sucursal (solo acceso a datos)
    # ------------------------------------------------------------------

    @staticmethod
    def _carga_gestion():
        """Reserva + cliente + sucursal + detalles (misma carga de CU16)."""
        return ReservaRepository._carga_completa() + (
            joinedload(Reserva.cliente),
        )

    @staticmethod
    def _condiciones_gestion(
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        buscar: str | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
    ) -> list:
        condiciones = []
        if sucursal_id is not None:
            condiciones.append(Reserva.sucursal_id == sucursal_id)
        if estado is not None:
            condiciones.append(Reserva.estado == estado)
        if fecha_desde is not None:
            condiciones.append(Reserva.fecha_atencion >= fecha_desde)
        if fecha_hasta is not None:
            condiciones.append(Reserva.fecha_atencion <= fecha_hasta)

        termino = (buscar or "").strip()
        if termino:
            patron = f"%{termino}%"
            coincidencias = [
                Cliente.nombre.ilike(patron),
                Cliente.apellido.ilike(patron),
            ]
            if termino.isdigit():
                coincidencias.append(Reserva.id == int(termino))
            condiciones.append(
                Reserva.cliente_id.in_(
                    select(Cliente.id).where(or_(*coincidencias))
                )
            )
        return condiciones

    @staticmethod
    def listar_gestion(
        db: Session,
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        buscar: str | None = None,
        fecha_desde: datetime | None = None,
        fecha_hasta: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Reserva], int]:
        """Listado paginado para personal + total con los mismos filtros.

        Proximas atenciones primero (fecha_atencion ASC, id ASC).
        """
        condiciones = ReservaRepository._condiciones_gestion(
            sucursal_id=sucursal_id,
            estado=estado,
            buscar=buscar,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        total = int(
            db.scalar(
                select(func.count())
                .select_from(Reserva)
                .where(*condiciones)
            )
            or 0
        )
        statement = (
            select(Reserva)
            .options(*ReservaRepository._carga_gestion())
            .where(*condiciones)
            .order_by(Reserva.fecha_atencion.asc(), Reserva.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(statement).unique().all()), total

    @staticmethod
    def obtener_para_actualizar(db: Session, reserva_id: int) -> Reserva | None:
        """Bloquea la reserva (SELECT ... FOR UPDATE) para cambiar su estado.

        Sin relaciones a proposito: PostgreSQL no permite FOR UPDATE sobre el
        lado opcional de un outer join y aqui solo se necesita la fila base.
        """
        statement = (
            select(Reserva).where(Reserva.id == reserva_id).with_for_update()
        )
        return db.scalars(statement).first()

    @staticmethod
    def marcar_confirmada(db: Session, reserva: Reserva) -> None:
        """PENDIENTE -> CONFIRMADA (sin tocar inventario ni detalles)."""
        reserva.estado = ESTADO_CONFIRMADA
        db.flush()

    # ------------------------------------------------------------------
    # CU18 - Atencion de reservas en sucursal (solo acceso a datos)
    # ------------------------------------------------------------------

    @staticmethod
    def finalizar_sin_compra(
        db: Session,
        *,
        reserva_id: int,
        usuario_id: int,
        observacion: str | None,
    ) -> None:
        """Ejecuta sp_finalizar_reserva_sin_compra (CONFIRMADA -> ATENDIDA).

        El procedimiento bloquea la reserva, valida el estado, bloquea los
        inventarios, libera unicamente stock_reservado, registra
        LIBERACION_RESERVA por detalle y marca la reserva ATENDIDA.
        No se reimplementa esa logica en Python.
        """
        db.execute(
            SQL_FINALIZAR_SIN_COMPRA,
            {
                "reserva_id": reserva_id,
                "usuario_id": usuario_id,
                "observacion": observacion,
            },
        )
