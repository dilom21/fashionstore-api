"""Reglas de negocio de CU17 - Gestionar reservas de sucursal.

Actores exclusivos: ADMINISTRADOR y ENCARGADO_SUCURSAL.

El cliente crea la reserva (CU16) y la sucursal la gestiona aqui:
PENDIENTE -> CONFIRMADA, o PENDIENTE/CONFIRMADA -> CANCELADA.

Confirmar NO toca inventario (CU16 ya reservo las prendas). Cancelar delega en
el procedimiento existente sp_cancelar_reserva, que libera stock_reservado y
registra LIBERACION_RESERVA; el carrito permanece CONVERTIDO.

El RBAC por si solo NO basta: la funcion GESTIONAR_RESERVAS tambien esta
asignada a CAJERO y CLIENTE, por lo que aqui se valida ademas el rol.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reservas.models.models import Reserva
from app.modules.reservas.repositories.repository import (
    ESTADO_PENDIENTE,
    ESTADOS_CANCELABLES,
    ReservaRepository,
)
from app.modules.reservas.schemas.schemas import (
    CancelarReservaSucursalRequest,
    ReservaSucursalDetalleResponse,
    ReservaSucursalListaResponse,
    ReservaSucursalResumenResponse,
)
from app.modules.reservas.services.service import (
    ReservaError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    # Helpers del modulo reservas (misma domain): se reutilizan para no
    # duplicar la traduccion de errores del motor ni el armado de las prendas.
    _item_response,
    _resolver_imagenes,
    _traducir_error_bd,
)

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROLES_GESTION_RESERVAS = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL)

LIMITE_POR_DEFECTO = 20
LIMITE_MAXIMO = 100


class RolGestionReservaNoAutorizadoError(ReservaError):
    """El rol autenticado no puede gestionar reservas de sucursal (CU17)."""


class ReservaSucursalScopeError(ReservaError):
    """La reserva o sucursal solicitada esta fuera del alcance del encargado."""


class RangoFechasInvalidoError(ReservaError):
    """fecha_desde es posterior a fecha_hasta."""


def _rango_fechas(
    fecha_desde: date | None, fecha_hasta: date | None
) -> tuple[datetime | None, datetime | None]:
    """Convierte fechas inclusivas a rango de datetimes UTC (igual que CU14)."""
    if (
        fecha_desde is not None
        and fecha_hasta is not None
        and fecha_desde > fecha_hasta
    ):
        raise RangoFechasInvalidoError()

    desde = (
        datetime.combine(fecha_desde, time.min, tzinfo=timezone.utc)
        if fecha_desde is not None
        else None
    )
    hasta = (
        datetime.combine(fecha_hasta, time.max, tzinfo=timezone.utc)
        if fecha_hasta is not None
        else None
    )
    return desde, hasta


def _resumen_response(reserva: Reserva) -> ReservaSucursalResumenResponse:
    cliente = reserva.cliente
    return ReservaSucursalResumenResponse(
        reserva_id=reserva.id,
        carrito_id=reserva.carrito_id,
        cliente_id=reserva.cliente_id,
        cliente_nombre=cliente.nombre,
        cliente_apellido=cliente.apellido,
        cliente_telefono=cliente.telefono,
        sucursal_id=reserva.sucursal_id,
        sucursal_nombre=reserva.sucursal.nombre,
        fecha_reserva=reserva.fecha_reserva,
        fecha_atencion=reserva.fecha_atencion,
        estado=reserva.estado,
        observacion=reserva.observacion,
        cantidad_lineas=len(reserva.detalles),
        cantidad_unidades=sum(
            detalle.cantidad for detalle in reserva.detalles
        ),
    )


def _detalle_response(
    db: Session, reserva: Reserva
) -> ReservaSucursalDetalleResponse:
    detalles = list(reserva.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_response(detalle, imagen))

    cliente = reserva.cliente
    return ReservaSucursalDetalleResponse(
        reserva_id=reserva.id,
        carrito_id=reserva.carrito_id,
        cliente_id=reserva.cliente_id,
        cliente_nombre=cliente.nombre,
        cliente_apellido=cliente.apellido,
        cliente_telefono=cliente.telefono,
        sucursal_id=reserva.sucursal_id,
        sucursal_nombre=reserva.sucursal.nombre,
        fecha_reserva=reserva.fecha_reserva,
        fecha_atencion=reserva.fecha_atencion,
        estado=reserva.estado,
        observacion=reserva.observacion,
        items=items,
        cantidad_total_unidades=sum(item.cantidad for item in items),
    )


class ReservaSucursalService:
    """Reglas de negocio de CU17 - Gestion de reservas por sucursal."""

    @staticmethod
    def _validar_rol_gestion(usuario: Usuario) -> str:
        """ADMINISTRADOR y ENCARGADO_SUCURSAL unicamente (RBAC no basta).

        La funcion GESTIONAR_RESERVAS tambien esta asignada a CAJERO y CLIENTE;
        CU17 debe rechazarlos igualmente.
        """
        rol = str(usuario.rol.nombre).strip().upper()
        if rol not in ROLES_GESTION_RESERVAS:
            raise RolGestionReservaNoAutorizadoError()
        return rol

    @staticmethod
    def _sucursal_scope(usuario: Usuario) -> int | None:
        """Alcance de sucursal efectivo (None = todas, solo Administrador)."""
        rol = ReservaSucursalService._validar_rol_gestion(usuario)
        if rol == ROL_ADMINISTRADOR:
            return None

        empleado = usuario.empleado
        if empleado is None or not empleado.estado or empleado.sucursal_id is None:
            raise ReservaSucursalScopeError()
        return int(empleado.sucursal_id)

    @staticmethod
    def resolver_sucursal_listado(
        usuario: Usuario, sucursal_id: int | None
    ) -> int | None:
        """Sucursal efectiva del listado.

        ADMINISTRADOR: la solicitada o None (todas las sucursales).
        ENCARGADO_SUCURSAL: la suya; si pide otra -> 403 (no se ignora).
        """
        scope = ReservaSucursalService._sucursal_scope(usuario)
        if scope is None:
            return sucursal_id
        if sucursal_id is not None and int(sucursal_id) != scope:
            raise ReservaSucursalScopeError()
        return scope

    @staticmethod
    def _validar_alcance_reserva(usuario: Usuario, reserva: Reserva) -> None:
        scope = ReservaSucursalService._sucursal_scope(usuario)
        if scope is not None and int(reserva.sucursal_id) != scope:
            raise ReservaSucursalScopeError()

    @staticmethod
    def _obtener_validada(
        db: Session, usuario: Usuario, reserva_id: int
    ) -> Reserva:
        """Orden de validacion: rol -> existencia (404) -> alcance (403)."""
        ReservaSucursalService._validar_rol_gestion(usuario)
        reserva = ReservaRepository.obtener_por_id(db, reserva_id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        ReservaSucursalService._validar_alcance_reserva(usuario, reserva)
        return reserva

    @staticmethod
    def _recargar(db: Session, reserva_id: int) -> Reserva:
        reserva = ReservaRepository.obtener_por_id(db, reserva_id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        return reserva

    @staticmethod
    def listar(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        buscar: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        limit: int = LIMITE_POR_DEFECTO,
        offset: int = 0,
    ) -> ReservaSucursalListaResponse:
        """Reservas visibles para el personal segun su alcance de sucursal."""
        sucursal_efectiva = ReservaSucursalService.resolver_sucursal_listado(
            usuario, sucursal_id
        )
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)

        reservas, total = ReservaRepository.listar_gestion(
            db,
            sucursal_id=sucursal_efectiva,
            estado=estado,
            buscar=buscar,
            fecha_desde=desde,
            fecha_hasta=hasta,
            limit=limit,
            offset=offset,
        )
        return ReservaSucursalListaResponse(
            items=[_resumen_response(reserva) for reserva in reservas],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def obtener(
        db: Session, usuario: Usuario, reserva_id: int
    ) -> ReservaSucursalDetalleResponse:
        reserva = ReservaSucursalService._obtener_validada(
            db, usuario, reserva_id
        )
        return _detalle_response(db, reserva)

    @staticmethod
    def confirmar(
        db: Session, usuario: Usuario, reserva_id: int
    ) -> ReservaSucursalDetalleResponse:
        """PENDIENTE -> CONFIRMADA con bloqueo de fila.

        No toca inventario ni detalle_reserva: CU16 ya reservo las prendas.
        Si al tomar el lock el estado ya no es PENDIENTE -> 409, evitando
        transiciones inconsistentes entre peticiones simultaneas.
        """
        reserva = ReservaSucursalService._obtener_validada(db, usuario, reserva_id)

        bloqueada = ReservaRepository.obtener_para_actualizar(db, reserva.id)
        if bloqueada is None:
            raise ReservaNoEncontradaError()
        if bloqueada.estado != ESTADO_PENDIENTE:
            raise ReservaEstadoInvalidoError()

        try:
            ReservaRepository.marcar_confirmada(db, bloqueada)
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return _detalle_response(
            db, ReservaSucursalService._recargar(db, reserva.id)
        )

    @staticmethod
    def cancelar(
        db: Session,
        usuario: Usuario,
        reserva_id: int,
        datos: CancelarReservaSucursalRequest | None = None,
    ) -> ReservaSucursalDetalleResponse:
        """Cancela PENDIENTE/CONFIRMADA usando sp_cancelar_reserva.

        El procedimiento bloquea la reserva, libera unicamente stock_reservado y
        registra el movimiento LIBERACION_RESERVA. El carrito sigue CONVERTIDO.
        """
        reserva = ReservaSucursalService._obtener_validada(db, usuario, reserva_id)
        if reserva.estado not in ESTADOS_CANCELABLES:
            raise ReservaEstadoInvalidoError()

        observacion = ((datos.observacion if datos else None) or "").strip() or None
        try:
            ReservaRepository.cancelar_reserva(
                db,
                reserva_id=reserva.id,
                usuario_id=int(usuario.id),
                observacion=observacion,
            )
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return _detalle_response(
            db, ReservaSucursalService._recargar(db, reserva.id)
        )
