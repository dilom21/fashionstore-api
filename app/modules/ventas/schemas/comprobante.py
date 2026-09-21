"""Schemas de CU23 - Comprobante de venta.

El comprobante NO es una entidad persistida: es el DTO que resulta de consultar
``venta`` + ``detalle_venta`` + ``pago`` + ``cliente`` + ``empleado`` +
``sucursal`` + ``inventario``/``variante_producto``/``producto``/``talla``/
``color``. No existe tabla ``comprobante`` ni ``factura``.

No se inventan campos: no hay numero_factura, NIT de empresa, impuestos, IVA,
descuentos ni subtotal con descuento porque no existen en el modelo real.
El codigo visual (por ejemplo VTA-00535) es responsabilidad del frontend; el
backend devuelve unicamente ``venta_id``.

Tampoco se expone informacion sensible de tarjetas: Stripe no almacena PAN/CVC
en la base y CU23 no intenta recuperarlos.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ComprobanteClienteResponse(BaseModel):
    """Cliente de la venta. ``None`` cuando la venta es anonima (presencial)."""

    id: int
    nombre: str
    apellido: str
    ci: str | None = None
    telefono: str | None = None


class ComprobanteEmpleadoResponse(BaseModel):
    """Empleado que registro la venta. ``None`` en ventas WEB/MOVIL."""

    id: int
    nombres: str
    apellidos: str


class ComprobanteSucursalResponse(BaseModel):
    id: int
    nombre: str
    direccion: str
    telefono: str | None = None


class ComprobantePagoResponse(BaseModel):
    """Pago APROBADO que respalda el comprobante (nunca datos de tarjeta)."""

    pago_id: int
    fecha_hora: datetime
    monto: Decimal
    metodo: str
    estado: str
    referencia_transaccion: str | None = None
    pasarela: str | None = None


class ComprobanteVentaItemResponse(BaseModel):
    """Linea del comprobante construida desde ``detalle_venta``."""

    detalle_venta_id: int
    inventario_id: int
    producto_id: int
    producto_nombre: str
    variante_producto_id: int
    sku: str
    talla_nombre: str
    color_nombre: str
    cantidad: int
    precio_unitario: Decimal
    subtotal_linea: Decimal


class ComprobanteVentaResponse(BaseModel):
    """DTO completo para visualizar/descargar/imprimir el comprobante.

    Valido para los tres origenes (WEB/MOVIL, presencial directa y presencial
    desde reserva) porque solo depende de ``venta_id``.
    """

    venta_id: int
    fecha_hora: datetime
    canal: str
    estado_venta: str
    total: Decimal
    carrito_id: int | None = None
    reserva_id: int | None = None
    cliente: ComprobanteClienteResponse | None = None
    empleado: ComprobanteEmpleadoResponse | None = None
    sucursal: ComprobanteSucursalResponse
    pago: ComprobantePagoResponse
    items: list[ComprobanteVentaItemResponse]
    cantidad_total_unidades: int
