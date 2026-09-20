"""Reglas de negocio de CU20 - Registrar venta presencial.

Actores: ADMINISTRADOR (alcance global), ENCARGADO_SUCURSAL y CAJERO (atados a
la sucursal de su empleado autenticado). CLIENTE queda fuera aunque el RBAC le
conceda permisos: el service aplica una allowlist explicita de roles.

Dos origenes:

- Venta presencial directa: el personal envia inventario_id + cantidad. La
  sucursal se deriva de las filas de inventario (deben pertenecer todas a la
  misma sucursal) y se valida contra el alcance del empleado. El cliente es
  opcional (la tabla admite venta anonima).
- Venta presencial desde CU18: se reutiliza
  ``AtencionReservaService.preparar_venta`` (sin HTTP interno) para revalidar la
  reserva CONFIRMADA y la seleccion. Cliente y sucursal se derivan de la reserva.

CU20 SOLO crea ``venta`` + ``detalle_venta`` en estado PENDIENTE. NO registra
pago, NO llama sp_confirmar_venta, NO descuenta stock_actual, NO consume ni
libera stock_reservado y NO marca la reserva ATENDIDA: esa es la frontera con
CU21. El total lo fija la autoridad de la base (trigger/SP sobre detalle_venta).
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente, Usuario
from app.modules.inventario.repositories.repository import InventarioRepository
from app.modules.reservas.repositories.repository import ReservaRepository
from app.modules.reservas.schemas.schemas import (
    PrepararVentaItemRequest,
    PrepararVentaRequest,
)
from app.modules.reservas.services.atencion_service import (
    AtencionReservaScopeError,
    AtencionReservaService,
    RolAtencionReservaNoAutorizadoError,
    SeleccionVentaInvalidaError,
)
from app.modules.reservas.services.service import (
    ReservaEstadoInvalidoError as _ReservaEstadoInvalidoCU18,
)
from app.modules.reservas.services.service import (
    ReservaNoEncontradaError as _ReservaNoEncontradaCU18,
)
from app.modules.ventas.repositories.repository import VentaRepository
from app.modules.ventas.schemas.schemas import (
    RegistrarVentaPresencialRequest,
    VentaItemResponse,
    VentaPresencialResponse,
)
from app.modules.ventas.services.service import (
    SQLSTATE_RAISE_EXCEPTION,
    SQLSTATE_UNIQUE_VIOLATION,
    _constraint_violada,
    _establecer_contexto_bitacora,
    _inventario_activo,
    _item_response,
    _mensaje_bd,
    _resolver_imagenes,
    _sqlstate,
)

CANAL_PRESENCIAL = "PRESENCIAL"

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
# Allowlist coherente con CU18: ADMIN global, ENCARGADO/CAJERO por sucursal.
ROLES_VENTA_PRESENCIAL = (
    ROL_ADMINISTRADOR,
    ROL_ENCARGADO_SUCURSAL,
    ROL_CAJERO,
)

CONSTRAINT_VENTA_RESERVA = "uq_venta_reserva"
CONSTRAINT_DETALLE_VENTA = "uq_detalle_venta"


# ---------------------------------------------------------------------------
# Errores de dominio de CU20
# ---------------------------------------------------------------------------


class VentaPresencialError(Exception):
    """Base de errores de negocio de CU20 (mapped a HTTP en el router)."""


class VentaSinItemsError(VentaPresencialError):
    """No hay ninguna unidad por vender."""


class InventarioDuplicadoError(VentaPresencialError):
    """La misma fila de inventario aparece dos veces en el request."""


class InventarioNoEncontradoError(VentaPresencialError):
    pass


class InventarioSucursalInvalidaError(VentaPresencialError):
    """Las lineas pertenecen a sucursales distintas."""


class ProductoNoDisponibleError(VentaPresencialError):
    """Inventario/variante/producto/talla/color/sucursal inactivos."""


class StockInsuficienteError(VentaPresencialError):
    pass


class ReservaNoEncontradaError(VentaPresencialError):
    pass


class ReservaEstadoInvalidoError(VentaPresencialError):
    """La reserva no esta CONFIRMADA."""


class SeleccionReservaInvalidaError(VentaPresencialError):
    """La seleccion no coincide con detalle_reserva."""


class VentaReservaDuplicadaError(VentaPresencialError):
    """La reserva ya origino una venta (UNIQUE venta.reserva_id)."""


class EmpleadoNoValidoError(VentaPresencialError):
    """El rol autorizado no tiene un empleado activo asociado."""


class ClienteNoEncontradoError(VentaPresencialError):
    pass


class ClienteInactivoError(VentaPresencialError):
    pass


class VentaPresencialScopeError(VentaPresencialError):
    """La operacion esta fuera del alcance/rol del usuario autenticado."""


class RegistroVentaInvalidoError(VentaPresencialError):
    """Error de motor no clasificable en las categorias anteriores."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _stock_disponible(inventario) -> int:
    """Regla CU15/CU19: stock disponible = stock_actual - stock_reservado."""
    return inventario.stock_actual - inventario.stock_reservado


def _rol(usuario: Usuario) -> str:
    return str(usuario.rol.nombre).strip().upper()


def _validar_rol(usuario: Usuario) -> str:
    rol = _rol(usuario)
    if rol not in ROLES_VENTA_PRESENCIAL:
        raise VentaPresencialScopeError()
    return rol


def _empleado_id(usuario: Usuario, rol: str) -> int | None:
    """Empleado autenticado que registra la venta (nunca viene del body).

    ADMINISTRADOR sin perfil de empleado: venta.empleado_id admite NULL en el
    esquema real, por lo que la venta se registra sin empleado (no se inventa).
    ENCARGADO_SUCURSAL/CAJERO requieren empleado activo.
    """
    empleado = usuario.empleado
    if empleado is not None and empleado.estado:
        return int(empleado.id)
    if rol == ROL_ADMINISTRADOR:
        return None
    raise EmpleadoNoValidoError()


def _sucursal_alcance(usuario: Usuario) -> int | None:
    """Alcance efectivo: None (global) para ADMIN; sucursal del empleado si no."""
    rol = _validar_rol(usuario)
    if rol == ROL_ADMINISTRADOR:
        return None
    empleado = usuario.empleado
    if empleado is None or not empleado.estado or empleado.sucursal_id is None:
        raise EmpleadoNoValidoError()
    return int(empleado.sucursal_id)


def _validar_cliente(db: Session, cliente_id: int | None) -> int | None:
    """Venta directa: cliente opcional; si viene, debe existir y estar activo."""
    if cliente_id is None:
        return None
    cliente = db.get(Cliente, int(cliente_id))
    if cliente is None:
        raise ClienteNoEncontradoError()
    if not cliente.estado:
        raise ClienteInactivoError()
    return int(cliente.id)


def _traducir_error_bd(exc: DBAPIError) -> VentaPresencialError:
    """Traduce la excepcion del motor a un error de dominio seguro."""
    estado = _sqlstate(exc)
    constraint = _constraint_violada(exc)
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        if constraint == CONSTRAINT_VENTA_RESERVA or CONSTRAINT_VENTA_RESERVA in mensaje:
            return VentaReservaDuplicadaError()
        if constraint == CONSTRAINT_DETALLE_VENTA or CONSTRAINT_DETALLE_VENTA in mensaje:
            return InventarioDuplicadoError()
        return RegistroVentaInvalidoError()

    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "sucursal" in mensaje:
            return InventarioSucursalInvalidaError()
        if "stock" in mensaje:
            return StockInsuficienteError()
        return RegistroVentaInvalidoError()

    return RegistroVentaInvalidoError()


def _venta_response(db: Session, venta) -> VentaPresencialResponse:
    detalles = list(venta.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[VentaItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_response(detalle, imagen))

    return VentaPresencialResponse(
        venta_id=venta.id,
        cliente_id=venta.cliente_id,
        empleado_id=venta.empleado_id,
        sucursal_id=venta.sucursal_id,
        sucursal_nombre=venta.sucursal.nombre,
        reserva_id=venta.reserva_id,
        canal=venta.canal,
        estado=venta.estado,
        fecha_hora=venta.fecha_hora,
        total=Decimal(venta.total),
        items=items,
        cantidad_total_unidades=sum(item.cantidad for item in items),
    )


def _persistir(
    db: Session,
    usuario: Usuario,
    *,
    cliente_id: int | None,
    empleado_id: int | None,
    sucursal_id: int,
    reserva_id: int | None,
    lineas: list[tuple[int, int, Decimal]],
) -> VentaPresencialResponse:
    """Unidad atomica: crear venta -> detalles -> recalcular total -> commit.

    Ante cualquier error de motor se hace rollback total: nunca queda una venta
    con detalles incompletos ni la reserva modificada (CU20 no la toca).
    """
    _establecer_contexto_bitacora(db, int(usuario.id))
    venta_id: int | None = None
    try:
        venta = VentaRepository.crear(
            db,
            cliente_id=cliente_id,
            empleado_id=empleado_id,
            sucursal_id=sucursal_id,
            reserva_id=reserva_id,
            carrito_id=None,
            canal=CANAL_PRESENCIAL,
            fecha_hora=_ahora(),
        )
        venta_id = venta.id

        for inventario_id, cantidad, precio_unitario in lineas:
            VentaRepository.crear_detalle(
                db,
                venta_id=venta.id,
                inventario_id=inventario_id,
                cantidad=cantidad,
                precio_unitario=precio_unitario,
            )

        # Autoridad de la base: recalcula venta.total a partir de detalles.
        VentaRepository.recalcular_total(db, venta.id)
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _traducir_error_bd(exc) from exc

    venta = VentaRepository.obtener_por_id(db, venta_id)
    if venta is None:
        raise RegistroVentaInvalidoError()
    return _venta_response(db, venta)


class VentaPresencialService:
    """Reglas de negocio de CU20 - Registrar venta presencial."""

    @staticmethod
    def registrar(
        db: Session,
        usuario: Usuario,
        datos: RegistrarVentaPresencialRequest,
    ) -> VentaPresencialResponse:
        _validar_rol(usuario)
        if not datos.items:
            raise VentaSinItemsError()
        if datos.reserva_id is not None:
            return VentaPresencialService._desde_reserva(db, usuario, datos)
        return VentaPresencialService._directa(db, usuario, datos)

    # ------------------------------------------------------------------
    # Camino 1 - Venta presencial directa
    # ------------------------------------------------------------------

    @staticmethod
    def _directa(
        db: Session,
        usuario: Usuario,
        datos: RegistrarVentaPresencialRequest,
    ) -> VentaPresencialResponse:
        rol = _validar_rol(usuario)
        alcance = _sucursal_alcance(usuario)
        empleado_id = _empleado_id(usuario, rol)
        cliente_id = _validar_cliente(db, datos.cliente_id)

        vistos: set[int] = set()
        lineas: list[tuple[int, int, Decimal]] = []
        sucursal_id: int | None = None

        for item in datos.items:
            inventario_id = int(item.inventario_id)
            if inventario_id in vistos:
                raise InventarioDuplicadoError()
            vistos.add(inventario_id)

            inventario = InventarioRepository.obtener_por_id(db, inventario_id)
            if inventario is None:
                raise InventarioNoEncontradoError()
            if not _inventario_activo(inventario):
                raise ProductoNoDisponibleError()

            if sucursal_id is None:
                sucursal_id = int(inventario.sucursal_id)
            elif int(inventario.sucursal_id) != sucursal_id:
                raise InventarioSucursalInvalidaError()

            cantidad = int(item.cantidad)
            if cantidad > _stock_disponible(inventario):
                raise StockInsuficienteError()

            precio = Decimal(inventario.variante_producto.producto.precio)
            if precio < 0:
                raise RegistroVentaInvalidoError()
            lineas.append((inventario_id, cantidad, precio))

        if sucursal_id is None:
            raise VentaSinItemsError()
        if alcance is not None and sucursal_id != alcance:
            raise VentaPresencialScopeError()

        return _persistir(
            db,
            usuario,
            cliente_id=cliente_id,
            empleado_id=empleado_id,
            sucursal_id=sucursal_id,
            reserva_id=None,
            lineas=lineas,
        )

    # ------------------------------------------------------------------
    # Camino 2 - Venta presencial desde CU18 (reserva CONFIRMADA)
    # ------------------------------------------------------------------

    @staticmethod
    def _desde_reserva(
        db: Session,
        usuario: Usuario,
        datos: RegistrarVentaPresencialRequest,
    ) -> VentaPresencialResponse:
        # Reutiliza la validacion de CU18 sin HTTP interno: rol, alcance,
        # estado CONFIRMADA, inventarios de detalle_reserva, cantidad <=
        # reservada, sin duplicados y al menos una unidad comprada.
        try:
            preparado = AtencionReservaService.preparar_venta(
                db,
                usuario,
                int(datos.reserva_id),
                PrepararVentaRequest(
                    items=[
                        PrepararVentaItemRequest(
                            inventario_id=item.inventario_id,
                            cantidad_compra=item.cantidad,
                        )
                        for item in datos.items
                    ]
                ),
            )
        except RolAtencionReservaNoAutorizadoError as exc:
            raise VentaPresencialScopeError() from exc
        except AtencionReservaScopeError as exc:
            raise VentaPresencialScopeError() from exc
        except _ReservaNoEncontradaCU18 as exc:
            raise ReservaNoEncontradaError() from exc
        except _ReservaEstadoInvalidoCU18 as exc:
            raise ReservaEstadoInvalidoError() from exc
        except SeleccionVentaInvalidaError as exc:
            raise SeleccionReservaInvalidaError() from exc

        # Proteccion de idempotencia a nivel de dominio. El UNIQUE
        # uq_venta_reserva sigue siendo la proteccion final ante concurrencia.
        if VentaRepository.obtener_por_reserva(db, preparado.reserva_id) is not None:
            raise VentaReservaDuplicadaError()

        reserva = ReservaRepository.obtener_por_id(db, preparado.reserva_id)
        if reserva is None:
            raise ReservaNoEncontradaError()

        lineas: list[tuple[int, int, Decimal]] = []
        for item in preparado.items:
            if int(item.cantidad_compra) <= 0:
                continue
            inventario = InventarioRepository.obtener_por_id(
                db, int(item.inventario_id)
            )
            if inventario is None:
                raise InventarioNoEncontradoError()
            # Las unidades ya estan en stock_reservado: NO se exige stock libre.
            precio = Decimal(inventario.variante_producto.producto.precio)
            if precio < 0:
                raise RegistroVentaInvalidoError()
            lineas.append(
                (int(item.inventario_id), int(item.cantidad_compra), precio)
            )

        if not lineas:
            raise VentaSinItemsError()

        rol = _validar_rol(usuario)
        return _persistir(
            db,
            usuario,
            cliente_id=int(reserva.cliente_id),
            empleado_id=_empleado_id(usuario, rol),
            sucursal_id=int(reserva.sucursal_id),
            reserva_id=int(reserva.id),
            lineas=lineas,
        )
