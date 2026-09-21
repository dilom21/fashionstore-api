"""Reglas de negocio de CU24 - Historial de compras del cliente.

Solo lectura y solo del propio cliente: la identidad sale del JWT
(``get_current_cliente``); jamas de un ``cliente_id`` enviado por el frontend.
Toda consulta se acota con ``Venta.cliente_id == cliente.id``.

El historial incluye unicamente compras historicas finalizadas:
COMPLETADA, CANCELADA y REEMBOLSADA. PENDIENTE y PAGADA quedan fuera (el
detalle de una venta asi responde 409, sin cambiar su estado).

No modifica nada: sin INSERT/UPDATE/DELETE, sin commit/rollback de negocio,
sin procedimientos almacenados y sin contexto de bitacora.

CU24 NO genera comprobantes: el frontend reutiliza CU23
(``GET /ventas/{venta_id}/comprobante``) con el ``venta_id`` devuelto.
"""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.ventas.repositories.repository import VentaRepository
from app.modules.ventas.schemas.historial import (
    HistorialCompraDetalleResponse,
    HistorialCompraItemResponse,
    HistorialCompraPagoResponse,
    HistorialCompraResumenResponse,
    HistorialCompraSucursalResponse,
    HistorialComprasResponse,
)

ESTADOS_HISTORIAL = ("COMPLETADA", "CANCELADA", "REEMBOLSADA")

PAGINA_POR_DEFECTO = 1
TAMANO_PAGINA_POR_DEFECTO = 20
TAMANO_PAGINA_MAXIMO = 50

_CENTIMOS = Decimal("0.01")


# ---------------------------------------------------------------------------
# Errores de dominio de CU24
# ---------------------------------------------------------------------------


class HistorialComprasError(Exception):
    """Base de errores de negocio de CU24 (mapped a HTTP en el router)."""


class CompraHistorialNoEncontradaError(HistorialComprasError):
    """La venta no existe."""


class CompraHistorialNoAutorizadaError(HistorialComprasError):
    """La compra pertenece a otro cliente."""


class CompraAunNoHistoricaError(HistorialComprasError):
    """La venta aun no forma parte del historial (PENDIENTE/PAGADA)."""


class RangoFechasHistorialError(HistorialComprasError):
    """fecha_desde posterior a fecha_hasta (422)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rango_fechas(
    fecha_desde: date | None, fecha_hasta: date | None
) -> tuple[datetime | None, datetime | None]:
    """Convierte fechas a un rango semiabierto [desde, hasta+1 dia).

    ``fecha_desde`` incluye todo el dia y ``fecha_hasta`` tambien, sin
    depender de 23:59:59. No se altera el tipo real de ``Venta.fecha_hora``.
    """
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_desde > fecha_hasta:
            raise RangoFechasHistorialError()

    desde = (
        datetime.combine(fecha_desde, time.min) if fecha_desde is not None else None
    )
    hasta = (
        datetime.combine(fecha_hasta + timedelta(days=1), time.min)
        if fecha_hasta is not None
        else None
    )
    return desde, hasta


def _sucursal_response(venta) -> HistorialCompraSucursalResponse:
    sucursal = venta.sucursal
    return HistorialCompraSucursalResponse(
        id=int(sucursal.id),
        nombre=sucursal.nombre,
        direccion=sucursal.direccion,
        telefono=sucursal.telefono,
    )


def _pago_response(pago) -> HistorialCompraPagoResponse | None:
    if pago is None:
        return None
    return HistorialCompraPagoResponse(
        pago_id=int(pago.id),
        fecha_hora=pago.fecha_hora,
        monto=Decimal(pago.monto),
        metodo=pago.metodo,
        estado=pago.estado,
        referencia_transaccion=pago.referencia_transaccion,
        pasarela=pago.pasarela,
    )


def _construir_items(venta) -> list[HistorialCompraItemResponse]:
    """Lineas del detalle con producto/talla/color reales y subtotal Decimal.

    Se construye con el schema propio de CU24 (``HistorialCompraItemResponse``)
    para no acoplar CU24 al DTO del comprobante de CU23.
    """
    items: list[HistorialCompraItemResponse] = []
    for detalle in venta.detalles:
        inventario = detalle.inventario
        variante = inventario.variante_producto
        producto = variante.producto
        cantidad = int(detalle.cantidad)
        precio = Decimal(detalle.precio_unitario)
        items.append(
            HistorialCompraItemResponse(
                detalle_venta_id=int(detalle.id),
                inventario_id=int(detalle.inventario_id),
                producto_id=int(producto.id),
                producto_nombre=producto.nombre,
                variante_producto_id=int(variante.id),
                sku=variante.sku,
                talla_nombre=variante.talla.nombre,
                color_nombre=variante.color.nombre,
                cantidad=cantidad,
                precio_unitario=precio,
                subtotal_linea=(precio * cantidad).quantize(
                    _CENTIMOS, rounding=ROUND_HALF_UP
                ),
            )
        )
    return items


class HistorialComprasService:
    """CU24 - Historial de compras del cliente autenticado (solo lectura)."""

    @staticmethod
    def consultar_historial(
        db: Session,
        cliente: Cliente,
        *,
        estado: str | None = None,
        canal: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        pagina: int = PAGINA_POR_DEFECTO,
        tamano_pagina: int = TAMANO_PAGINA_POR_DEFECTO,
    ) -> HistorialComprasResponse:
        """Historial paginado del cliente (mas reciente primero).

        La pagina/tamano ya vienen validados por FastAPI (>=1, <=50), igual que
        los enums estado/canal. Aqui solo se valida el rango de fechas.
        """
        estados = (estado,) if estado is not None else ESTADOS_HISTORIAL
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        offset = (int(pagina) - 1) * int(tamano_pagina)

        filas = VentaRepository.listar_historial_cliente(
            db,
            cliente_id=int(cliente.id),
            estados=estados,
            canal=canal,
            desde=desde,
            hasta=hasta,
            limit=int(tamano_pagina),
            offset=offset,
        )
        total_registros = VentaRepository.contar_historial_cliente(
            db,
            cliente_id=int(cliente.id),
            estados=estados,
            canal=canal,
            desde=desde,
            hasta=hasta,
        )

        items = [
            HistorialCompraResumenResponse(
                venta_id=int(fila.venta_id),
                fecha_hora=fila.fecha_hora,
                canal=fila.canal,
                estado=fila.estado,
                total=Decimal(fila.total),
                sucursal_id=int(fila.sucursal_id),
                sucursal_nombre=fila.sucursal_nombre,
                cantidad_total_unidades=int(fila.cantidad_total_unidades),
                cantidad_lineas=int(fila.cantidad_lineas),
            )
            for fila in filas
        ]
        total_paginas = (
            (total_registros + int(tamano_pagina) - 1) // int(tamano_pagina)
            if total_registros
            else 0
        )
        return HistorialComprasResponse(
            items=items,
            pagina=int(pagina),
            tamano_pagina=int(tamano_pagina),
            total_registros=total_registros,
            total_paginas=total_paginas,
        )

    @staticmethod
    def obtener_detalle_compra(
        db: Session,
        cliente: Cliente,
        venta_id: int,
    ) -> HistorialCompraDetalleResponse:
        """Detalle de una compra del cliente autenticado (sin modificar nada)."""
        if int(venta_id) <= 0:
            raise CompraHistorialNoEncontradaError()

        venta = VentaRepository.obtener_por_id(db, int(venta_id))
        if venta is None:
            raise CompraHistorialNoEncontradaError()

        # Propiedad: nunca se confia en cliente_id del request.
        if venta.cliente_id is None or int(venta.cliente_id) != int(cliente.id):
            raise CompraHistorialNoAutorizadaError()

        if venta.estado not in ESTADOS_HISTORIAL:
            raise CompraAunNoHistoricaError()

        pago = VentaRepository.obtener_ultimo_pago(db, int(venta.id))
        items = _construir_items(venta)
        return HistorialCompraDetalleResponse(
            venta_id=int(venta.id),
            fecha_hora=venta.fecha_hora,
            canal=venta.canal,
            estado=venta.estado,
            total=Decimal(venta.total),
            carrito_id=(
                int(venta.carrito_id) if venta.carrito_id is not None else None
            ),
            reserva_id=(
                int(venta.reserva_id) if venta.reserva_id is not None else None
            ),
            sucursal=_sucursal_response(venta),
            pago=_pago_response(pago),
            items=items,
            cantidad_total_unidades=sum(item.cantidad for item in items),
        )
