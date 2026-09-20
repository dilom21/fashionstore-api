from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class CanalVenta(str, Enum):
    """Canales digitales permitidos para CU19 (CK venta_canal).

    PRESENCIAL pertenece a otros CU (venta en tienda) y no se admite aqui.
    """

    WEB = "WEB"
    MOVIL = "MOVIL"


class RealizarCompraDigitalRequest(BaseModel):
    """Unica fuente de verdad: el carrito ACTIVO del cliente autenticado.

    El cliente NO envia cliente_id, sucursal_id, precios, total ni estado:
    todo se obtiene o calcula desde carrito/detalle_carrito/inventario/producto.
    """

    carrito_id: int = Field(gt=0)
    canal: CanalVenta


class VentaItemResponse(BaseModel):
    """Linea de la venta digital (snapshot del checkout)."""

    detalle_id: int
    inventario_id: int
    producto_id: int
    producto_nombre: str
    imagen_principal: str | None
    variante_producto_id: int
    sku: str
    talla_id: int
    talla_nombre: str
    color_id: int
    color_nombre: str
    temporada_id: int
    temporada_nombre: str
    cantidad: int
    precio_unitario: Decimal
    subtotal_linea: Decimal


class VentaDigitalResponse(BaseModel):
    """Venta PENDIENTE lista para que CU22 procese el pago electronico."""

    venta_id: int
    carrito_id: int | None
    cliente_id: int
    sucursal_id: int
    sucursal_nombre: str
    canal: str
    estado: str
    fecha_hora: datetime
    total: Decimal
    items: list[VentaItemResponse]
    cantidad_total_unidades: int


# ---------------------------------------------------------------------------
# CU20 - Registrar venta presencial (ADMINISTRADOR / ENCARGADO_SUCURSAL / CAJERO)
# ---------------------------------------------------------------------------


class VentaPresencialItemRequest(BaseModel):
    """Prenda realmente vendida en tienda: fila de inventario y cantidad."""

    inventario_id: int = Field(gt=0)
    cantidad: int = Field(gt=0)


class RegistrarVentaPresencialRequest(BaseModel):
    """Origen discriminado por ``reserva_id``.

    - ``reserva_id`` nulo: venta presencial directa; ``cliente_id`` es opcional
      (el esquema permite venta anonima).
    - ``reserva_id`` informado: venta proveniente de CU18; el cliente y la
      sucursal se derivan de la reserva y no se admite otro cliente.

    El cliente NUNCA envia canal, estado, empleado_id, sucursal_id, precios ni
    total: todos se determinan en el backend.
    """

    reserva_id: int | None = Field(default=None, gt=0)
    cliente_id: int | None = Field(default=None, gt=0)
    items: list[VentaPresencialItemRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def _validar_origen(self) -> "RegistrarVentaPresencialRequest":
        if self.reserva_id is not None and self.cliente_id is not None:
            raise ValueError(
                "cliente_id no debe enviarse cuando la venta proviene "
                "de una reserva"
            )
        return self


class VentaPresencialResponse(BaseModel):
    """Venta PENDIENTE de tienda lista para que CU21 registre el pago.

    CU21 necesita especialmente ``venta_id``, ``total`` y ``estado``.
    """

    venta_id: int
    cliente_id: int | None
    empleado_id: int | None
    sucursal_id: int
    sucursal_nombre: str
    reserva_id: int | None
    canal: str
    estado: str
    fecha_hora: datetime
    total: Decimal
    items: list[VentaItemResponse]
    cantidad_total_unidades: int
