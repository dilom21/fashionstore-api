from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class EstadoReservaFiltro(str, Enum):
    """Estados reales de la tabla reserva (CK reserva_estado).

    CU16 solo crea PENDIENTE y cancela a CANCELADA; el resto de estados los
    gestionaran CU17/CU18, por eso el listado los admite todos.
    """

    PENDIENTE = "PENDIENTE"
    CONFIRMADA = "CONFIRMADA"
    ATENDIDA = "ATENDIDA"
    CANCELADA = "CANCELADA"
    VENCIDA = "VENCIDA"


class CrearReservaRequest(BaseModel):
    """Unica fuente de verdad: el carrito ACTIVO del cliente autenticado.

    El cliente NO envia cliente_id, sucursal_id, inventarios ni cantidades:
    todo eso se toma de carrito/detalle_carrito en la base de datos.
    """

    carrito_id: int = Field(gt=0)
    fecha_atencion: datetime
    observacion: str | None = Field(default=None, max_length=500)


class CancelarReservaRequest(BaseModel):
    observacion: str | None = Field(default=None, max_length=500)


class ReservaResumenResponse(BaseModel):
    """Resumen de una reserva del cliente (panel 'Mis reservas')."""

    reserva_id: int
    carrito_id: int | None
    cliente_id: int
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    cantidad_lineas: int
    cantidad_unidades: int


class ReservaListaResponse(BaseModel):
    items: list[ReservaResumenResponse]
    total_reservas: int


class ReservaItemResponse(BaseModel):
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


class ReservaDetalleResponse(BaseModel):
    reserva_id: int
    carrito_id: int | None
    cliente_id: int
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    items: list[ReservaItemResponse]
    cantidad_total_unidades: int


# ---------------------------------------------------------------------------
# CU17 - Gestion de reservas por sucursal (ADMINISTRADOR / ENCARGADO_SUCURSAL)
# ---------------------------------------------------------------------------


class CancelarReservaSucursalRequest(BaseModel):
    observacion: str | None = Field(default=None, max_length=500)


class ReservaSucursalResumenResponse(BaseModel):
    """Reserva vista por el personal de sucursal (sin datos sensibles)."""

    reserva_id: int
    carrito_id: int | None
    cliente_id: int
    cliente_nombre: str
    cliente_apellido: str
    cliente_telefono: str | None
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    cantidad_lineas: int
    cantidad_unidades: int


class ReservaSucursalListaResponse(BaseModel):
    """Respuesta paginada de CU17 (patron items/total/limit/offset)."""

    items: list[ReservaSucursalResumenResponse]
    total: int
    limit: int
    offset: int


class ReservaSucursalDetalleResponse(BaseModel):
    reserva_id: int
    carrito_id: int | None
    cliente_id: int
    cliente_nombre: str
    cliente_apellido: str
    cliente_telefono: str | None
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    items: list[ReservaItemResponse]
    cantidad_total_unidades: int


# ---------------------------------------------------------------------------
# CU18 - Atencion de reservas en sucursal (ENCARGADO_SUCURSAL / CAJERO)
# ---------------------------------------------------------------------------


class AtencionReservaItemResponse(BaseModel):
    """Prenda reservada tal como la ve el empleado que atiende (CU18)."""

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
    cantidad_reservada: int


class AtencionReservaResumenResponse(BaseModel):
    reserva_id: int
    cliente_id: int
    cliente_nombre: str
    cliente_apellido: str
    cliente_telefono: str | None
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    cantidad_lineas: int
    cantidad_unidades: int


class AtencionReservaListaResponse(BaseModel):
    """Reservas CONFIRMADA de la sucursal (patron items/total/limit/offset)."""

    items: list[AtencionReservaResumenResponse]
    total: int
    limit: int
    offset: int


class VentaAsociadaAtencionResponse(BaseModel):
    """Venta ya asociada a la reserva (CU20), visible en el detalle CU18.

    Permite que el frontend recupere la venta tras recargar o reabrir la
    atencion y no vuelva a ofrecer FINALIZAR SIN COMPRA. Si no existe venta el
    campo ``venta_asociada`` es null (contrato backward-compatible).
    """

    venta_id: int
    estado: str
    total: Decimal
    canal: str


class AtencionReservaDetalleResponse(BaseModel):
    reserva_id: int
    cliente_id: int
    cliente_nombre: str
    cliente_apellido: str
    cliente_telefono: str | None
    sucursal_id: int
    sucursal_nombre: str
    fecha_reserva: datetime
    fecha_atencion: datetime
    estado: str
    observacion: str | None
    items: list[AtencionReservaItemResponse]
    cantidad_total_unidades: int
    venta_asociada: VentaAsociadaAtencionResponse | None = None


class PrepararVentaItemRequest(BaseModel):
    """Solo se envian prendas con cantidad_compra >= 1 (checkbox marcado)."""

    inventario_id: int = Field(gt=0)
    cantidad_compra: int = Field(ge=1)


class PrepararVentaRequest(BaseModel):
    items: list[PrepararVentaItemRequest] = Field(min_length=1)


class PrepararVentaItemResponse(BaseModel):
    inventario_id: int
    cantidad_reservada: int
    cantidad_compra: int
    cantidad_no_compra: int


class PrepararVentaResponse(BaseModel):
    """Resultado SOLO de validacion: la venta todavia NO ocurrio.

    No se crea venta/pago, no se libera stock_reservado y la reserva sigue
    CONFIRMADA: el modulo de Venta/Pago debera volver a validar y ejecutar
    sp_confirmar_venta cuando se complete el pago.
    """

    reserva_id: int
    sucursal_id: int
    estado: str
    items: list[PrepararVentaItemResponse]
    total_unidades_reservadas: int
    total_unidades_compra: int
    total_unidades_no_compra: int
    venta_registrada: bool = False
    reserva_modificada: bool = False


class FinalizarSinCompraRequest(BaseModel):
    observacion: str | None = Field(default=None, max_length=500)
