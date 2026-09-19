"""Reglas de negocio de CU16 - Gestionar reserva de prendas (CLIENTE).

La operacion critica (validar stock, incrementar inventario.stock_reservado,
crear detalle_reserva, registrar el movimiento RESERVA y marcar el carrito como
CONVERTIDO) la ejecuta PostgreSQL en el procedimiento
sp_crear_reserva_desde_carrito, dentro de la transaccion de SQLAlchemy. Aqui
solo se valida el contexto, se traduce el error del motor a errores de dominio
y se controla el commit/rollback (una reserva es todo o nada).
"""

from datetime import datetime, timezone

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.carrito.repositories.repository import (
    RecursoProductoCarritoRepository,
)
from app.modules.carrito.services.service import (
    CarritoNoActivoError,
    CarritoNoEncontradoError,
    CarritoService,
)
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.reservas.repositories.repository import (
    ESTADOS_CANCELABLES,
    ReservaRepository,
)
from app.modules.reservas.schemas.schemas import (
    CancelarReservaRequest,
    CrearReservaRequest,
    ReservaDetalleResponse,
    ReservaItemResponse,
    ReservaListaResponse,
    ReservaResumenResponse,
)

# SQLSTATE de PostgreSQL usados solo para clasificar el error (nunca se
# devuelven al cliente: no se exponen SQL, constraints ni mensajes del motor).
SQLSTATE_UNIQUE_VIOLATION = "23505"
SQLSTATE_RAISE_EXCEPTION = "P0001"


class ReservaError(Exception):
    """Base de errores de negocio de CU16 (mapped a HTTP en el router)."""


class ReservaNoEncontradaError(ReservaError):
    pass


class ReservaAjenaError(ReservaError):
    """La reserva pertenece a otro cliente."""


class ReservaEstadoInvalidoError(ReservaError):
    """La reserva no puede cancelarse en su estado actual."""


class FechaAtencionInvalidaError(ReservaError):
    pass


class CarritoVacioError(ReservaError):
    """El carrito no contiene prendas para reservar."""


class StockInsuficienteReservaError(ReservaError):
    pass


class ReservaDuplicadaError(ReservaError):
    """El carrito ya origino una reserva (UNIQUE reserva.carrito_id)."""


class ReservaRegistroInvalidoError(ReservaError):
    """Error de motor no clasificable en las categorias anteriores."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _normalizar_fecha(valor: datetime) -> datetime:
    """Garantiza comparacion consistente (naive -> UTC)."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor


def _sqlstate(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    valor = getattr(origen, "sqlstate", None)
    return str(valor) if valor else None


def _mensaje_bd(exc: DBAPIError) -> str:
    """Texto del motor SOLO para clasificar el error interno."""
    origen = getattr(exc, "orig", None)
    return str(origen or exc).lower()


def _traducir_error_bd(exc: DBAPIError) -> ReservaError:
    """Traduce la excepcion del motor a un error de dominio seguro.

    El procedimiento no usa SQLSTATE propios: todos sus RAISE EXCEPTION llegan
    como P0001, por lo que se clasifica por el texto (sin devolverlo nunca).
    """
    estado = _sqlstate(exc)
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        return ReservaDuplicadaError()

    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "stock insuficiente" in mensaje:
            return StockInsuficienteReservaError()
        if "no se encuentra activo" in mensaje:
            return CarritoNoActivoError()
        if "carrito" in mensaje and "no existe" in mensaje:
            return CarritoNoEncontradoError()
        if "no contiene prendas" in mensaje:
            return CarritoVacioError()
        if "fecha de atenci" in mensaje:
            return FechaAtencionInvalidaError()
        if "no puede cancelarse" in mensaje or "no puede finalizarse" in mensaje:
            return ReservaEstadoInvalidoError()
        if "reserva" in mensaje and "no existe" in mensaje:
            return ReservaNoEncontradaError()

    return ReservaRegistroInvalidoError()


def _stock_disponible(inventario) -> int:
    return inventario.stock_actual - inventario.stock_reservado


def _validar_stock_carrito(carrito) -> None:
    """Fast-fail de la misma regla del SP (stock_actual - stock_reservado).

    Es solo lectura: la validacion definitiva (y el bloqueo FOR UPDATE) la
    hace sp_crear_reserva_desde_carrito dentro de su transaccion.
    """
    for detalle in carrito.detalles:
        if detalle.cantidad > _stock_disponible(detalle.inventario):
            raise StockInsuficienteReservaError()


def _resolver_imagenes(db: Session, detalles: list[DetalleReserva]):
    """Imagen principal por (producto, color) reutilizando recurso_producto.

    Se reutiliza el repositorio de recursos de CU15: recurso_producto es del
    modulo de catalogo y alli ya se resuelve principal/color en una consulta.
    """
    pares = {
        (
            detalle.inventario.variante_producto.producto_id,
            detalle.inventario.variante_producto.color_id,
        )
        for detalle in detalles
    }
    if not pares:
        return {}

    producto_ids = {producto_id for producto_id, _ in pares}
    recursos = RecursoProductoCarritoRepository.listar_recursos_activos(
        db, producto_ids
    )
    por_producto: dict[int, list] = {}
    for recurso in recursos:
        por_producto.setdefault(recurso.producto_id, []).append(recurso)

    resultado: dict[tuple[int, int], str | None] = {}
    for producto_id, color_id in pares:
        lista = por_producto.get(producto_id, [])
        elegido = next(
            (r.url for r in lista if r.es_principal and r.color_id == color_id),
            None,
        )
        if elegido is None:
            elegido = next(
                (r.url for r in lista if r.es_principal and r.color_id is None),
                None,
            )
        if elegido is None and lista:
            elegido = lista[0].url
        resultado[(producto_id, color_id)] = elegido
    return resultado


def _item_response(
    detalle: DetalleReserva, imagen: str | None
) -> ReservaItemResponse:
    inventario = detalle.inventario
    variante = inventario.variante_producto
    producto = variante.producto
    return ReservaItemResponse(
        detalle_id=detalle.id,
        inventario_id=detalle.inventario_id,
        producto_id=producto.id,
        producto_nombre=producto.nombre,
        imagen_principal=imagen,
        variante_producto_id=variante.id,
        sku=variante.sku,
        talla_id=variante.talla.id,
        talla_nombre=variante.talla.nombre,
        color_id=variante.color.id,
        color_nombre=variante.color.nombre,
        temporada_id=inventario.temporada_id,
        temporada_nombre=inventario.temporada.nombre,
        cantidad=detalle.cantidad,
    )


def _resumen_response(reserva: Reserva) -> ReservaResumenResponse:
    return ReservaResumenResponse(
        reserva_id=reserva.id,
        carrito_id=reserva.carrito_id,
        cliente_id=reserva.cliente_id,
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


def _detalle_response(db: Session, reserva: Reserva) -> ReservaDetalleResponse:
    detalles = list(reserva.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[ReservaItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_response(detalle, imagen))

    return ReservaDetalleResponse(
        reserva_id=reserva.id,
        carrito_id=reserva.carrito_id,
        cliente_id=reserva.cliente_id,
        sucursal_id=reserva.sucursal_id,
        sucursal_nombre=reserva.sucursal.nombre,
        fecha_reserva=reserva.fecha_reserva,
        fecha_atencion=reserva.fecha_atencion,
        estado=reserva.estado,
        observacion=reserva.observacion,
        items=items,
        cantidad_total_unidades=sum(item.cantidad for item in items),
    )


class ReservaService:
    """Reglas de negocio de CU16 - Gestionar reserva de prendas (CLIENTE)."""

    @staticmethod
    def _usuario_id(cliente) -> int:
        """usuario_id del cliente autenticado (autor del movimiento Kardex)."""
        usuario_id = cliente.usuario_id
        if usuario_id is None:
            raise ReservaRegistroInvalidoError()
        return int(usuario_id)

    @staticmethod
    def _validar_reserva_propia(db: Session, cliente, reserva_id: int) -> Reserva:
        reserva = ReservaRepository.obtener_por_id(db, reserva_id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        if reserva.cliente_id != cliente.id:
            raise ReservaAjenaError()
        return reserva

    @staticmethod
    def _obtener_para_response(
        db: Session, reserva_id: int | None, carrito_id: int | None
    ) -> Reserva:
        reserva = None
        if reserva_id is not None:
            reserva = ReservaRepository.obtener_por_id(db, reserva_id)
        if reserva is None and carrito_id is not None:
            reserva = ReservaRepository.obtener_por_carrito(db, carrito_id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        return reserva

    @staticmethod
    def crear(
        db: Session, cliente, datos: CrearReservaRequest
    ) -> ReservaDetalleResponse:
        """Convierte el carrito ACTIVO del cliente en una reserva PENDIENTE.

        Atomico: el procedimiento de PostgreSQL crea reserva + detalles,
        incrementa stock_reservado, registra el movimiento RESERVA y marca el
        carrito como CONVERTIDO. Si algo falla -> rollback total y ningun
        cambio queda persistido (ni carrito CONVERTIDO ni stock reservado).
        """
        fecha_atencion = _normalizar_fecha(datos.fecha_atencion)
        if fecha_atencion <= _ahora():
            raise FechaAtencionInvalidaError()

        usuario_id = ReservaService._usuario_id(cliente)

        # Regla unica de CU15: propietario + vigencia de 2 horas + ACTIVO.
        # Un carrito vencido se marca EXPIRADO y se rechaza; uno ELIMINADO o
        # CONVERTIDO tambien se rechaza.
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, datos.carrito_id
        )
        if not carrito.detalles:
            raise CarritoVacioError()
        _validar_stock_carrito(carrito)

        observacion = (datos.observacion or "").strip() or None
        try:
            reserva_id = ReservaRepository.crear_desde_carrito(
                db,
                carrito_id=carrito.id,
                fecha_atencion=fecha_atencion,
                observacion=observacion,
                usuario_id=usuario_id,
            )
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        reserva = ReservaService._obtener_para_response(db, reserva_id, carrito.id)
        return _detalle_response(db, reserva)

    @staticmethod
    def listar(
        db: Session, cliente, estado: str | None = None
    ) -> ReservaListaResponse:
        """Reservas del cliente autenticado (cualquier estado), mas recientes primero."""
        reservas = ReservaRepository.listar_por_cliente(
            db, cliente.id, estado=estado
        )
        items = [_resumen_response(reserva) for reserva in reservas]
        return ReservaListaResponse(items=items, total_reservas=len(items))

    @staticmethod
    def obtener(
        db: Session, cliente, reserva_id: int
    ) -> ReservaDetalleResponse:
        reserva = ReservaService._validar_reserva_propia(db, cliente, reserva_id)
        return _detalle_response(db, reserva)

    @staticmethod
    def cancelar(
        db: Session,
        cliente,
        reserva_id: int,
        datos: CancelarReservaRequest | None = None,
    ) -> ReservaDetalleResponse:
        """Cancela una reserva PENDIENTE/CONFIRMADA liberando stock_reservado.

        El carrito permanece CONVERTIDO: cancelar NO lo reactiva. Si el cliente
        quiere reservar de nuevo debe generar otro carrito.
        """
        reserva = ReservaService._validar_reserva_propia(db, cliente, reserva_id)
        if reserva.estado not in ESTADOS_CANCELABLES:
            raise ReservaEstadoInvalidoError()

        usuario_id = ReservaService._usuario_id(cliente)
        observacion = ((datos.observacion if datos else None) or "").strip() or None

        try:
            ReservaRepository.cancelar_reserva(
                db,
                reserva_id=reserva.id,
                usuario_id=usuario_id,
                observacion=observacion,
            )
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        reserva = ReservaRepository.obtener_por_id(db, reserva.id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        return _detalle_response(db, reserva)
