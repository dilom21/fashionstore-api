"""Schemas de CU24 - Historial de compras del cliente autenticado.

Solo lectura y solo del propio cliente: la identidad sale del JWT
(``get_current_cliente``), nunca de un ``cliente_id`` del request.

No existe una entidad historial persistida: el historial se construye desde
``venta`` + ``detalle_venta`` + ``pago`` + ``sucursal`` +
``inventario``/``variante_producto``/``producto``/``talla``/``color``.

CU24 NO genera comprobantes (eso es CU23), NO genera PDF y NO expone datos
sensibles de pago (sin client_secret, PAN, CVV ni secret keys).
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class EstadoHistorialFiltro(str, Enum):
    """Estados que forman parte del historial de compras (CK venta_estado).

    PENDIENTE y PAGADA quedan fuera: todavia no representan una compra
    historica finalizada.
    """

    COMPLETADA = "COMPLETADA"
    CANCELADA = "CANCELADA"
    REEMBOLSADA = "REEMBOLSADA"


class CanalHistorialFiltro(str, Enum):
    """Canales reales de ``venta.canal`` (CK venta_canal)."""

    WEB = "WEB"
    MOVIL = "MOVIL"
    PRESENCIAL = "PRESENCIAL"


class HistorialCompraResumenResponse(BaseModel):
    """Fila del listado: venta + sucursal + agregados de sus lineas."""

    venta_id: int
    fecha_hora: datetime
    canal: str
    estado: str
    total: Decimal
    sucursal_id: int
    sucursal_nombre: str
    cantidad_total_unidades: int
    cantidad_lineas: int


class HistorialComprasResponse(BaseModel):
    """Historial paginado (mismo patron que CU17/CU18 donde aplica)."""

    items: list[HistorialCompraResumenResponse]
    pagina: int
    tamano_pagina: int
    total_registros: int
    total_paginas: int


class HistorialCompraSucursalResponse(BaseModel):
    id: int
    nombre: str
    direccion: str
    telefono: str | None = None


class HistorialCompraPagoResponse(BaseModel):
    """Pago mas relevante de la venta (nunca datos de tarjeta)."""

    pago_id: int
    fecha_hora: datetime
    monto: Decimal
    metodo: str
    estado: str
    referencia_transaccion: str | None = None
    pasarela: str | None = None


class HistorialCompraItemResponse(BaseModel):
    """Linea del detalle construida desde ``detalle_venta``."""

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


class HistorialCompraDetalleResponse(BaseModel):
    """Detalle completo de una compra del cliente autenticado.

    No devuelve ``cliente``: el endpoint ya corresponde al cliente
    autenticado y su venta (misma razon que CU23).
    """

    venta_id: int
    fecha_hora: datetime
    canal: str
    estado: str
    total: Decimal
    carrito_id: int | None = None
    reserva_id: int | None = None
    sucursal: HistorialCompraSucursalResponse
    pago: HistorialCompraPagoResponse | None = None
    items: list[HistorialCompraItemResponse]
    cantidad_total_unidades: int
