"""Schemas de CU28 - Compras/proveedores y clientes/carritos.

Compras:

- ``valor_orden = SUM(DetalleOrdenCompra.cantidad * costo_unitario)`` (Decimal).
- ``valor_ordenes_recibidas`` solo suma ordenes ``RECIBIDA`` (el modelo no
  tiene ``cantidad_recibida`` por detalle, asi que ``PARCIAL`` no se cuenta
  como mercaderia ingresada).
- Sin rankings subjetivos de proveedores.

Clientes:

- Solo ventas COMPLETADAS. ``cliente_recurrente`` = cliente con mas de una
  venta COMPLETADA dentro del periodo consultado.
- ``Cliente`` no tiene ``fecha_creacion``: no se afirma "cliente nuevo".
- PII minimizada: id, nombre y apellido (sin CI ni telefono).

Carritos:

- Estados reales: ACTIVO, EXPIRADO, ELIMINADO, CONVERTIDO.
- ``tasa_conversion_carrito = CONVERTIDO / total_carritos * 100``.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import MetadatosReporteResponse


# --- Compras / proveedores ---------------------------------------------------


class OrdenEstadoResponse(BaseModel):
    estado: str
    cantidad: int
    valor: Decimal


class ProveedorOrdenResponse(BaseModel):
    proveedor_id: int
    razon_social: str
    cantidad_ordenes: int
    unidades_ordenadas: int
    valor_ordenes: Decimal
    ordenes_recibidas: int
    ordenes_canceladas: int
    cumplimiento_promedio_dias: Decimal | None = None


class CompraSucursalResponse(BaseModel):
    sucursal_id: int
    sucursal_nombre: str
    cantidad: int
    valor: Decimal


class CompraProductoResponse(BaseModel):
    variante_producto_id: int
    sku: str
    producto_nombre: str
    unidades: int
    valor: Decimal


class ComprasResumenResponse(BaseModel):
    total_ordenes: int
    unidades_ordenadas: int
    valor_total_ordenes: Decimal
    valor_ordenes_recibidas: Decimal


class ReporteComprasResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen: ComprasResumenResponse
    ordenes_por_estado: list[OrdenEstadoResponse]
    ordenes_por_proveedor: list[ProveedorOrdenResponse]
    ordenes_por_sucursal: list[CompraSucursalResponse]
    productos_abastecidos: list[CompraProductoResponse]


# --- Clientes / carritos ----------------------------------------------------


class ClienteCompraResponse(BaseModel):
    cliente_id: int
    nombre: str
    apellido: str
    compras: int
    unidades: int
    monto: Decimal


class ClientesResumenResponse(BaseModel):
    clientes_compradores: int
    clientes_recurrentes: int
    compras_totales: int
    unidades_totales: int
    monto_total: Decimal


class CarritoEstadoResponse(BaseModel):
    estado: str
    cantidad: int


class CarritosResumenResponse(BaseModel):
    total_carritos: int
    carritos_convertidos: int
    tasa_conversion_carrito: Decimal
    por_estado: list[CarritoEstadoResponse]


class ReporteClientesCarritosResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    resumen_clientes: ClientesResumenResponse
    compras_por_cliente: list[ClienteCompraResponse]
    resumen_carritos: CarritosResumenResponse


class CarritoDetalleFilaResponse(BaseModel):
    carrito_id: int
    fecha_creacion: datetime
    cliente: str
    estado: str
    sucursal: str
    lineas: int
    unidades: int
