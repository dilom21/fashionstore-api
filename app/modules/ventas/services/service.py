"""Reglas de negocio de CU19 - Realizar compra digital (CLIENTE).

Un cliente autenticado convierte su carrito ACTIVO en una venta PENDIENTE con
sus detalle_venta (snapshot economico) y marca el carrito como CONVERTIDO.

CU19 NO procesa el pago, NO crea filas ``pago``, NO llama sp_confirmar_venta y
NO descuenta inventario: esa es la frontera con CU22. El total lo calcula la
autoridad de la base de datos (trigger/SP sobre detalle_venta).
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.carrito.repositories.repository import (
    CarritoRepository,
    RecursoProductoCarritoRepository,
)
from app.modules.carrito.services.service import (
    CarritoAjenoError,
    CarritoError,
    CarritoNoActivoError,
    CarritoNoEncontradoError,
    CarritoService,
)
from app.modules.ventas.repositories.repository import VentaRepository
from app.modules.ventas.schemas.schemas import (
    RealizarCompraDigitalRequest,
    VentaDigitalResponse,
    VentaItemResponse,
)

# SQLSTATE de PostgreSQL usados solo para clasificar el error. Nunca se
# devuelven al cliente: no se exponen SQL, constraints ni mensajes del motor.
SQLSTATE_UNIQUE_VIOLATION = "23505"
SQLSTATE_RAISE_EXCEPTION = "P0001"

CONSTRAINT_VENTA_CARRITO = "uq_venta_carrito"


class VentaError(Exception):
    """Base de errores de negocio de CU19 (mapped a HTTP en el router)."""


class CarritoVacioError(VentaError):
    """El carrito no contiene prendas para comprar."""


class VentaCarritoDuplicadaError(VentaError):
    """El carrito ya origino una venta (UNIQUE venta.carrito_id)."""


class StockInsuficienteVentaError(VentaError):
    pass


class InventarioNoDisponibleVentaError(VentaError):
    """Inventario/variante/producto/talla/color/sucursal inactivos."""


class InventarioSucursalVentaError(VentaError):
    """El inventario no pertenece a la sucursal de la venta."""


class VentaRegistroInvalidoError(VentaError):
    """Error de motor no clasificable en las categorias anteriores."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _stock_disponible(inventario) -> int:
    """Regla CU15/CU19: stock disponible = stock_actual - stock_reservado."""
    return inventario.stock_actual - inventario.stock_reservado


def _inventario_activo(inventario) -> bool:
    """Mismas reglas de 'activos' que la disponibilidad publica (CU09/CU15)."""
    variante = inventario.variante_producto
    return bool(
        variante.estado
        and variante.producto.estado
        and variante.talla.estado
        and variante.color.estado
        and inventario.sucursal.estado
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    valor = getattr(origen, "sqlstate", None)
    return str(valor) if valor else None


def _constraint_violada(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    diag = getattr(origen, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    return str(nombre) if nombre else None


def _mensaje_bd(exc: DBAPIError) -> str:
    """Texto del motor SOLO para clasificar el error interno (no se expone)."""
    origen = getattr(exc, "orig", None)
    return str(origen or exc).lower()


def _traducir_error_bd(exc: DBAPIError) -> VentaError:
    estado = _sqlstate(exc)
    constraint = _constraint_violada(exc)
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        if constraint == CONSTRAINT_VENTA_CARRITO or CONSTRAINT_VENTA_CARRITO in mensaje:
            return VentaCarritoDuplicadaError()
        return VentaRegistroInvalidoError()

    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "sucursal" in mensaje:
            return InventarioSucursalVentaError()
        return VentaRegistroInvalidoError()

    return VentaRegistroInvalidoError()


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    """Contexto de auditoria para los triggers de bitacora (como otros CU)."""
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _resolver_imagenes(db: Session, detalles) -> dict[tuple[int, int], str | None]:
    """Imagen principal por (producto, color) reutilizando recurso_producto."""
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


def _item_response(detalle, imagen: str | None) -> VentaItemResponse:
    inventario = detalle.inventario
    variante = inventario.variante_producto
    producto = variante.producto
    precio = Decimal(detalle.precio_unitario)
    return VentaItemResponse(
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
        precio_unitario=precio,
        subtotal_linea=precio * detalle.cantidad,
    )


def _detalle_response(db: Session, venta) -> VentaDigitalResponse:
    detalles = list(venta.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[VentaItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_response(detalle, imagen))

    return VentaDigitalResponse(
        venta_id=venta.id,
        carrito_id=venta.carrito_id,
        cliente_id=int(venta.cliente_id),
        sucursal_id=venta.sucursal_id,
        sucursal_nombre=venta.sucursal.nombre,
        canal=venta.canal,
        estado=venta.estado,
        fecha_hora=venta.fecha_hora,
        total=Decimal(venta.total),
        items=items,
        cantidad_total_unidades=sum(item.cantidad for item in items),
    )


class VentaService:
    """Reglas de negocio de CU19 - Realizar compra digital (CLIENTE)."""

    @staticmethod
    def realizar_compra_digital(
        db: Session, cliente, datos: RealizarCompraDigitalRequest
    ) -> VentaDigitalResponse:
        """Convierte el carrito ACTIVO del cliente en una venta PENDIENTE.

        Unidad atomica: validar carrito e inventario -> crear venta -> crear
        detalle_venta -> recalcular total -> carrito CONVERTIDO -> commit. Ante
        cualquier error de motor se hace rollback total: nunca queda un carrito
        CONVERTIDO sin venta ni una venta con detalles incompletos.
        """
        # Regla unica de CU15: propietario + vigencia de 2 horas + ACTIVO.
        # Un carrito vencido se marca EXPIRADO y se rechaza; ELIMINADO o
        # CONVERTIDO (por ejemplo tras una reserva) tambien se rechaza.
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, datos.carrito_id
        )
        if not carrito.detalles:
            raise CarritoVacioError()

        # Proteccion de idempotencia a nivel de dominio. El UNIQUE
        # uq_venta_carrito sigue siendo la proteccion final ante concurrencia.
        if VentaRepository.obtener_por_carrito(db, carrito.id) is not None:
            raise VentaCarritoDuplicadaError()

        lineas: list[tuple[int, int, Decimal]] = []
        for detalle in carrito.detalles:
            inventario = detalle.inventario
            if inventario.sucursal_id != carrito.sucursal_id:
                raise InventarioSucursalVentaError()
            if not _inventario_activo(inventario):
                raise InventarioNoDisponibleVentaError()
            if detalle.cantidad > _stock_disponible(inventario):
                raise StockInsuficienteVentaError()

            precio = Decimal(inventario.variante_producto.producto.precio)
            if precio < 0:
                raise VentaRegistroInvalidoError()
            lineas.append((detalle.inventario_id, detalle.cantidad, precio))

        if cliente.usuario_id is not None:
            _establecer_contexto_bitacora(db, int(cliente.usuario_id))

        venta_id: int | None = None
        try:
            venta = VentaRepository.crear(
                db,
                cliente_id=cliente.id,
                sucursal_id=carrito.sucursal_id,
                carrito_id=carrito.id,
                canal=datos.canal.value,
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

            CarritoRepository.marcar_convertido(db, carrito)
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        venta = VentaRepository.obtener_por_id(db, venta_id)
        if venta is None:
            raise VentaRegistroInvalidoError()
        return _detalle_response(db, venta)
