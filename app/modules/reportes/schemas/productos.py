"""Schemas de CU28 - Reporte de productos (sobre ventas COMPLETADAS).

Criterio de colecciones: un producto puede pertenecer a varias colecciones
(``producto_coleccion`` es M:N). ``ventas_por_coleccion`` cuenta la linea en
CADA coleccion del producto, por lo que la suma de esa seccion puede superar
el total global. Se documenta aqui para no inducir a error.

Temporada: se obtiene de ``inventario.temporada_id`` (la Venta no guarda
temporada).
"""

from decimal import Decimal

from pydantic import BaseModel

from app.modules.reportes.schemas.common import MetadatosReporteResponse


class ProductoRankingResponse(BaseModel):
    producto_id: int
    producto_nombre: str
    categoria: str
    unidades: int
    monto: Decimal


class VarianteRankingResponse(BaseModel):
    variante_producto_id: int
    sku: str
    producto_nombre: str
    talla: str
    color: str
    unidades: int
    monto: Decimal


class EtiquetaRankingResponse(BaseModel):
    etiqueta: str
    unidades: int
    monto: Decimal


class ResumenCatalogoResponse(BaseModel):
    productos_activos: int
    productos_inactivos: int
    variantes_activas: int
    variantes_inactivas: int


class ReporteProductosResponse(BaseModel):
    metadatos: MetadatosReporteResponse
    top_productos_por_unidades: list[ProductoRankingResponse]
    top_productos_por_monto: list[ProductoRankingResponse]
    ventas_por_categoria: list[EtiquetaRankingResponse]
    variantes_mas_vendidas: list[VarianteRankingResponse]
    tallas_mas_vendidas: list[EtiquetaRankingResponse]
    colores_mas_vendidos: list[EtiquetaRankingResponse]
    ventas_por_temporada: list[EtiquetaRankingResponse]
    ventas_por_coleccion: list[EtiquetaRankingResponse]
    resumen_catalogo: ResumenCatalogoResponse
