"""CU28 - Exportacion (PDF/XLSX/CSV) de los DTO autoritativos.

AUTORIDAD UNICA DE CALCULOS: este servicio NO recalcula metricas. Recibe el DTO
ya construido por ``ReportesService`` (repositorio SQL -> service -> DTO) y solo
lo *presenta*. Asi JSON, PDF, XLSX y CSV muestran exactamente los mismos
numeros.

Los archivos se generan en memoria (BytesIO); no se escriben en disco ni se
suben a Supabase Storage.
"""

from datetime import date, datetime
from decimal import Decimal

from app.modules.reportes.exports.csv_exporter import exportar_csv
from app.modules.reportes.exports.pdf_exporter import exportar_pdf
from app.modules.reportes.exports.xlsx_exporter import exportar_xlsx

EXTENSIONES = {"CSV": "csv", "XLSX": "xlsx", "PDF": "pdf"}
MEDIA_TYPES = {
    "CSV": "text/csv; charset=utf-8",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "PDF": "application/pdf",
}
TITULOS = {
    "RESUMEN": "RESUMEN GENERAL",
    "VENTAS": "REPORTE DE VENTAS",
    "PRODUCTOS": "REPORTE DE PRODUCTOS",
    "INVENTARIO": "REPORTE DE INVENTARIO",
    "RESERVAS": "REPORTE DE RESERVAS",
    "DEVOLUCIONES": "REPORTE DE DEVOLUCIONES",
    "PAGOS": "REPORTE DE PAGOS",
    "COMPRAS_PROVEEDORES": "REPORTE DE COMPRAS Y PROVEEDORES",
    "CLIENTES_CARRITOS": "REPORTE DE CLIENTES Y CARRITOS",
    "AUDITORIA": "REPORTE DE AUDITORIA",
}


class Seccion:
    """Conjunto de datos ya presentable (titulo + encabezados + filas)."""

    def __init__(
        self,
        titulo: str,
        encabezados: list[str],
        filas: list[list[object]],
    ) -> None:
        self.titulo = titulo
        self.encabezados = encabezados
        self.filas = filas


def _fecha(valor: datetime | date | None) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    return valor.strftime("%d/%m/%Y")


def _moneda(valor) -> str:
    """Formato visual con Bs (solo presentacion; el JSON mantiene Decimal)."""
    if valor is None:
        return "Bs 0,00"
    texto = f"{Decimal(valor):,.2f}"
    return "Bs " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _contexto(dto, tipo: str) -> tuple[str, list[str]]:
    """Titulo + lineas de cabecera comunes (periodo, alcance, generacion)."""
    metadatos = dto.metadatos
    periodo = metadatos.periodo
    alcance = metadatos.alcance

    desde = _fecha(periodo.fecha_desde) if periodo.fecha_desde else "Inicio"
    hasta = _fecha(periodo.fecha_hasta) if periodo.fecha_hasta else "Hoy"
    if alcance.es_global:
        lugar = "Todas las sucursales"
    else:
        lugar = alcance.sucursal_nombre or f"Sucursal {alcance.sucursal_id}"

    lineas = [
        f"Periodo: {desde} - {hasta}",
        f"Alcance: {lugar}",
        f"Generado: {_fecha(metadatos.generado_en)}",
    ]
    return TITULOS.get(tipo, metadatos.tipo), lineas


def _nombre_archivo(tipo: str, dto, formato: str) -> str:
    periodo = dto.metadatos.periodo
    base = "reporte-" + tipo.lower().replace("_", "-")
    if periodo.fecha_desde or periodo.fecha_hasta:
        inicio = periodo.fecha_desde.isoformat() if periodo.fecha_desde else "inicio"
        fin = periodo.fecha_hasta.isoformat() if periodo.fecha_hasta else "hoy"
        base = f"{base}-{inicio}-a-{fin}"
    else:
        base = f"{base}-completo"
    return f"{base}.{EXTENSIONES[formato]}"


def _filas(items, mapeo) -> list[list[object]]:
    """Proyeccion uniforme: el mismo DTO alimenta los 3 formatos."""
    return [mapeo(item) for item in items]


class ExportacionService:
    """Adapta el DTO autoritativo a secciones y delega en los exportadores."""

    @staticmethod
    def _secciones(dto, tipo: str) -> list[Seccion]:
        if tipo == "RESUMEN":
            return _secciones_resumen(dto)
        if tipo == "VENTAS":
            return _secciones_ventas(dto)
        if tipo == "PRODUCTOS":
            return _secciones_productos(dto)
        if tipo == "INVENTARIO":
            return _secciones_inventario(dto)
        if tipo == "RESERVAS":
            return _secciones_reservas(dto)
        if tipo == "DEVOLUCIONES":
            return _secciones_devoluciones(dto)
        if tipo == "PAGOS":
            return _secciones_pagos(dto)
        if tipo == "COMPRAS_PROVEEDORES":
            return _secciones_compras(dto)
        if tipo == "CLIENTES_CARRITOS":
            return _secciones_clientes(dto)
        return _secciones_auditoria(dto)

    @staticmethod
    def generar_archivo(
        dto, *, tipo: str, formato: str
    ) -> tuple[bytes, str, str]:
        """Devuelve (contenido, nombre_archivo, media_type) sin tocar disco."""
        formato_normalizado = str(formato).strip().upper()
        titulo, meta = _contexto(dto, tipo)
        secciones = ExportacionService._secciones(dto, tipo)

        if formato_normalizado == "CSV":
            contenido = exportar_csv(secciones, titulo=titulo, meta=meta)
        elif formato_normalizado == "XLSX":
            contenido = exportar_xlsx(secciones, titulo=titulo, meta=meta)
        else:
            contenido = exportar_pdf(secciones, titulo=titulo, meta=meta)

        return (
            contenido,
            _nombre_archivo(tipo, dto, formato_normalizado),
            MEDIA_TYPES[formato_normalizado],
        )


def _secciones_resumen(dto) -> list[Seccion]:
    kpis = dto.kpis
    secciones = [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Ventas completadas", kpis.ventas_completadas],
                ["Total vendido (Bs)", _moneda(kpis.total_vendido)],
                ["Unidades vendidas", kpis.unidades_vendidas],
                ["Ticket promedio (Bs)", _moneda(kpis.ticket_promedio)],
                ["Reservas totales", kpis.reservas_total],
                ["Devoluciones completadas", kpis.devoluciones_completadas],
                ["Unidades devueltas", kpis.unidades_devueltas],
                ["Stock disponible total", kpis.stock_disponible_total],
            ],
        ),
        Seccion(
            "Evolucion de ventas",
            ["Periodo", "Ventas", "Monto", "Unidades"],
            _filas(
                dto.ventas_evolucion,
                lambda fila: [
                    fila.periodo,
                    fila.cantidad,
                    _moneda(fila.monto),
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Ventas por canal",
            ["Canal", "Ventas", "Monto", "Unidades"],
            _filas(
                dto.ventas_por_canal,
                lambda fila: [
                    fila.canal,
                    fila.cantidad_ventas,
                    _moneda(fila.monto),
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Productos mas vendidos",
            ["Producto", "Unidades", "Monto"],
            _filas(
                dto.top_productos,
                lambda fila: [
                    fila.producto_nombre,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Reservas por estado",
            ["Estado", "Cantidad"],
            _filas(
                dto.resumen_reservas.por_estado,
                lambda fila: [fila.estado, fila.cantidad],
            ),
        ),
        Seccion(
            "Devoluciones por estado",
            ["Estado", "Cantidad"],
            _filas(
                dto.resumen_devoluciones.por_estado,
                lambda fila: [fila.estado, fila.cantidad],
            ),
        ),
    ]
    if dto.comparativo_sucursales:
        secciones.append(
            Seccion(
                "Comparativo por sucursal",
                [
                    "Sucursal",
                    "Ventas",
                    "Monto",
                    "Unidades",
                    "Ticket promedio",
                    "Reservas",
                    "Devoluciones",
                ],
                _filas(
                    dto.comparativo_sucursales,
                    lambda fila: [
                        fila.sucursal_nombre,
                        fila.ventas,
                        _moneda(fila.monto_vendido),
                        fila.unidades,
                        _moneda(fila.ticket_promedio),
                        fila.reservas,
                        fila.devoluciones,
                    ],
                ),
            )
        )
    return secciones


def _secciones_ventas(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Ventas completadas", resumen.ventas_completadas],
                ["Monto total (Bs)", _moneda(resumen.monto_total)],
                ["Unidades vendidas", resumen.unidades_vendidas],
                ["Ticket promedio (Bs)", _moneda(resumen.ticket_promedio)],
            ],
        ),
        Seccion(
            "Por estado",
            ["Estado", "Cantidad", "Monto"],
            _filas(
                dto.ventas_por_estado,
                lambda fila: [
                    fila.estado,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Por canal",
            ["Canal", "Cantidad", "Monto", "Unidades"],
            _filas(
                dto.ventas_por_canal,
                lambda fila: [
                    fila.canal,
                    fila.cantidad,
                    _moneda(fila.monto),
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Por sucursal",
            ["Sucursal", "Cantidad", "Monto", "Unidades", "Ticket promedio"],
            _filas(
                dto.ventas_por_sucursal,
                lambda fila: [
                    fila.sucursal_nombre,
                    fila.cantidad,
                    _moneda(fila.monto),
                    fila.unidades,
                    _moneda(fila.ticket_promedio),
                ],
            ),
        ),
        Seccion(
            "Por empleado",
            ["Empleado", "Cantidad", "Monto"],
            _filas(
                dto.ventas_por_empleado,
                lambda fila: [
                    fila.empleado_nombre,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Evolucion",
            ["Periodo", "Cantidad", "Monto", "Unidades"],
            _filas(
                dto.evolucion,
                lambda fila: [
                    fila.periodo,
                    fila.cantidad,
                    _moneda(fila.monto),
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Detalle",
            [
                "Venta",
                "Fecha",
                "Sucursal",
                "Canal",
                "Estado",
                "Cliente",
                "Empleado",
                "Unidades",
                "Total",
            ],
            _filas(
                dto.items,
                lambda fila: [
                    f"VTA-{fila.venta_id:05d}",
                    _fecha(fila.fecha_hora),
                    fila.sucursal,
                    fila.canal,
                    fila.estado,
                    fila.cliente_nombre or "-",
                    fila.empleado_nombre or "-",
                    fila.cantidad_unidades,
                    _moneda(fila.total),
                ],
            ),
        ),
    ]


def _secciones_productos(dto) -> list[Seccion]:
    catalogo = dto.resumen_catalogo
    return [
        Seccion(
            "Resumen de catalogo",
            ["Indicador", "Valor"],
            [
                ["Productos activos", catalogo.productos_activos],
                ["Productos inactivos", catalogo.productos_inactivos],
                ["Variantes activas", catalogo.variantes_activas],
                ["Variantes inactivas", catalogo.variantes_inactivas],
            ],
        ),
        Seccion(
            "Top productos por unidades",
            ["Producto", "Categoria", "Unidades", "Monto"],
            _filas(
                dto.top_productos_por_unidades,
                lambda fila: [
                    fila.producto_nombre,
                    fila.categoria,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Top productos por monto",
            ["Producto", "Categoria", "Unidades", "Monto"],
            _filas(
                dto.top_productos_por_monto,
                lambda fila: [
                    fila.producto_nombre,
                    fila.categoria,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Ventas por categoria",
            ["Categoria", "Unidades", "Monto"],
            _filas(
                dto.ventas_por_categoria,
                lambda fila: [
                    fila.etiqueta,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Variantes mas vendidas",
            ["SKU", "Producto", "Talla", "Color", "Unidades", "Monto"],
            _filas(
                dto.variantes_mas_vendidas,
                lambda fila: [
                    fila.sku,
                    fila.producto_nombre,
                    fila.talla,
                    fila.color,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Tallas mas vendidas",
            ["Talla", "Unidades", "Monto"],
            _filas(
                dto.tallas_mas_vendidas,
                lambda fila: [
                    fila.etiqueta,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Colores mas vendidos",
            ["Color", "Unidades", "Monto"],
            _filas(
                dto.colores_mas_vendidos,
                lambda fila: [
                    fila.etiqueta,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Ventas por temporada",
            ["Temporada", "Unidades", "Monto"],
            _filas(
                dto.ventas_por_temporada,
                lambda fila: [
                    fila.etiqueta,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Ventas por coleccion",
            ["Coleccion", "Unidades", "Monto"],
            _filas(
                dto.ventas_por_coleccion,
                lambda fila: [
                    fila.etiqueta,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
    ]


def _secciones_inventario(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Stock actual total", resumen.stock_actual_total],
                ["Stock reservado total", resumen.stock_reservado_total],
                ["Stock disponible total", resumen.stock_disponible_total],
                ["Variantes agotadas", resumen.variantes_agotadas],
                [
                    f"Variantes con stock bajo (<= {resumen.umbral_stock_bajo})",
                    resumen.variantes_stock_bajo,
                ],
            ],
        ),
        Seccion(
            "Movimientos por tipo",
            ["Tipo", "Movimientos", "Unidades"],
            _filas(
                dto.movimientos_por_tipo,
                lambda fila: [
                    fila.tipo,
                    fila.cantidad_movimientos,
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Existencias",
            [
                "Sucursal",
                "Producto",
                "SKU",
                "Talla",
                "Color",
                "Temporada",
                "Stock actual",
                "Stock reservado",
                "Stock disponible",
                "Actualizado",
            ],
            _filas(
                dto.items,
                lambda fila: [
                    fila.sucursal,
                    fila.producto,
                    fila.sku,
                    fila.talla,
                    fila.color,
                    fila.temporada,
                    fila.stock_actual,
                    fila.stock_reservado,
                    fila.stock_disponible,
                    _fecha(fila.fecha_actualizacion),
                ],
            ),
        ),
    ]


def _secciones_reservas(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Total reservas", resumen.total_reservas],
                ["Unidades reservadas", resumen.cantidad_total_unidades],
                ["Reservas atendidas", resumen.reservas_atendidas],
                [
                    "Reservas con venta completada",
                    resumen.reservas_convertidas_en_venta,
                ],
                ["Tasa de conversion (%)", resumen.tasa_conversion],
            ],
        ),
        Seccion(
            "Por estado",
            ["Estado", "Cantidad", "Unidades"],
            _filas(
                dto.reservas_por_estado,
                lambda fila: [fila.estado, fila.cantidad, fila.unidades],
            ),
        ),
        Seccion(
            "Por sucursal",
            ["Sucursal", "Cantidad", "Unidades"],
            _filas(
                dto.reservas_por_sucursal,
                lambda fila: [
                    fila.sucursal_nombre,
                    fila.cantidad,
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Evolucion",
            ["Periodo", "Cantidad"],
            _filas(
                dto.evolucion, lambda fila: [fila.periodo, fila.cantidad]
            ),
        ),
    ]


def _secciones_devoluciones(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Total devoluciones", resumen.total_devoluciones],
                ["Devoluciones completadas", resumen.completadas],
                ["Unidades devueltas", resumen.unidades_devueltas],
                [
                    "Valor referencial (Bs)",
                    _moneda(resumen.valor_referencial),
                ],
            ],
        ),
        Seccion(
            "Por estado",
            ["Estado", "Cantidad", "Unidades", "Valor referencial"],
            _filas(
                dto.devoluciones_por_estado,
                lambda fila: [
                    fila.estado,
                    fila.cantidad,
                    fila.unidades,
                    _moneda(fila.valor_referencial),
                ],
            ),
        ),
        Seccion(
            "Por sucursal (completadas)",
            ["Sucursal", "Cantidad", "Unidades"],
            _filas(
                dto.devoluciones_por_sucursal,
                lambda fila: [
                    fila.sucursal_nombre,
                    fila.cantidad,
                    fila.unidades,
                ],
            ),
        ),
        Seccion(
            "Evolucion",
            ["Periodo", "Cantidad"],
            _filas(
                dto.evolucion, lambda fila: [fila.periodo, fila.cantidad]
            ),
        ),
        Seccion(
            "Productos mas devueltos",
            ["Producto", "Unidades", "Valor referencial"],
            _filas(
                dto.productos_mas_devueltos,
                lambda fila: [
                    fila.producto_nombre,
                    fila.unidades,
                    _moneda(fila.valor_referencial),
                ],
            ),
        ),
        Seccion(
            "Motivos mas frecuentes",
            ["Motivo", "Devoluciones", "Unidades"],
            _filas(
                dto.motivos_mas_frecuentes,
                lambda fila: [
                    fila.motivo,
                    fila.cantidad_devoluciones,
                    fila.unidades,
                ],
            ),
        ),
    ]


def _secciones_pagos(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Transacciones de pago", resumen.cantidad_pagos],
                [
                    "Monto total procesado (Bs)",
                    _moneda(resumen.monto_total_procesado),
                ],
                ["Monto aprobado (Bs)", _moneda(resumen.monto_aprobado)],
                ["Nota", resumen.nota],
            ],
        ),
        Seccion(
            "Por estado",
            ["Estado", "Cantidad", "Monto"],
            _filas(
                dto.pagos_por_estado,
                lambda fila: [
                    fila.estado,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Por metodo",
            ["Metodo", "Cantidad", "Monto"],
            _filas(
                dto.pagos_por_metodo,
                lambda fila: [
                    fila.metodo,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Por pasarela",
            ["Pasarela", "Cantidad", "Monto"],
            _filas(
                dto.pagos_por_pasarela,
                lambda fila: [
                    fila.pasarela,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Evolucion",
            ["Periodo", "Cantidad", "Monto"],
            _filas(
                dto.evolucion,
                lambda fila: [
                    fila.periodo,
                    fila.cantidad,
                    _moneda(fila.monto),
                ],
            ),
        ),
    ]


def _secciones_compras(dto) -> list[Seccion]:
    resumen = dto.resumen
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [
                ["Total ordenes", resumen.total_ordenes],
                ["Unidades ordenadas", resumen.unidades_ordenadas],
                [
                    "Valor total de ordenes (Bs)",
                    _moneda(resumen.valor_total_ordenes),
                ],
                [
                    "Valor de ordenes RECIBIDA (Bs)",
                    _moneda(resumen.valor_ordenes_recibidas),
                ],
            ],
        ),
        Seccion(
            "Por estado",
            ["Estado", "Cantidad", "Valor"],
            _filas(
                dto.ordenes_por_estado,
                lambda fila: [
                    fila.estado,
                    fila.cantidad,
                    _moneda(fila.valor),
                ],
            ),
        ),
        Seccion(
            "Por proveedor",
            [
                "Proveedor",
                "Ordenes",
                "Unidades",
                "Valor",
                "Recibidas",
                "Canceladas",
                "Dias vs. estimada (prom.)",
            ],
            _filas(
                dto.ordenes_por_proveedor,
                lambda fila: [
                    fila.razon_social,
                    fila.cantidad_ordenes,
                    fila.unidades_ordenadas,
                    _moneda(fila.valor_ordenes),
                    fila.ordenes_recibidas,
                    fila.ordenes_canceladas,
                    (
                        fila.cumplimiento_promedio_dias
                        if fila.cumplimiento_promedio_dias is not None
                        else "-"
                    ),
                ],
            ),
        ),
        Seccion(
            "Por sucursal",
            ["Sucursal", "Cantidad", "Valor"],
            _filas(
                dto.ordenes_por_sucursal,
                lambda fila: [
                    fila.sucursal_nombre,
                    fila.cantidad,
                    _moneda(fila.valor),
                ],
            ),
        ),
        Seccion(
            "Productos abastecidos",
            ["SKU", "Producto", "Unidades", "Valor"],
            _filas(
                dto.productos_abastecidos,
                lambda fila: [
                    fila.sku,
                    fila.producto_nombre,
                    fila.unidades,
                    _moneda(fila.valor),
                ],
            ),
        ),
    ]


def _secciones_clientes(dto) -> list[Seccion]:
    clientes = dto.resumen_clientes
    carritos = dto.resumen_carritos
    return [
        Seccion(
            "Resumen de clientes",
            ["Indicador", "Valor"],
            [
                ["Clientes compradores", clientes.clientes_compradores],
                ["Clientes recurrentes", clientes.clientes_recurrentes],
                ["Compras totales", clientes.compras_totales],
                ["Unidades compradas", clientes.unidades_totales],
                ["Monto total (Bs)", _moneda(clientes.monto_total)],
            ],
        ),
        Seccion(
            "Compras por cliente",
            ["Cliente", "Compras", "Unidades", "Monto"],
            _filas(
                dto.compras_por_cliente,
                lambda fila: [
                    f"{fila.nombre} {fila.apellido}".strip(),
                    fila.compras,
                    fila.unidades,
                    _moneda(fila.monto),
                ],
            ),
        ),
        Seccion(
            "Resumen de carritos",
            ["Indicador", "Valor"],
            [
                ["Total carritos", carritos.total_carritos],
                ["Carritos convertidos", carritos.carritos_convertidos],
                [
                    "Tasa de conversion carrito (%)",
                    carritos.tasa_conversion_carrito,
                ],
            ],
        ),
        Seccion(
            "Carritos por estado",
            ["Estado", "Cantidad"],
            _filas(
                carritos.por_estado,
                lambda fila: [fila.estado, fila.cantidad],
            ),
        ),
    ]


def _secciones_auditoria(dto) -> list[Seccion]:
    return [
        Seccion(
            "Resumen",
            ["Indicador", "Valor"],
            [["Total de eventos", dto.total_eventos]],
        ),
        Seccion(
            "Eventos por accion",
            ["Accion", "Cantidad"],
            _filas(
                dto.eventos_por_accion,
                lambda fila: [fila.etiqueta, fila.cantidad],
            ),
        ),
        Seccion(
            "Eventos por entidad",
            ["Entidad", "Cantidad"],
            _filas(
                dto.eventos_por_entidad,
                lambda fila: [fila.etiqueta, fila.cantidad],
            ),
        ),
        Seccion(
            "Eventos por usuario",
            ["Usuario", "Cantidad"],
            _filas(
                dto.eventos_por_usuario,
                lambda fila: [fila.etiqueta, fila.cantidad],
            ),
        ),
        Seccion(
            "Evolucion",
            ["Periodo", "Cantidad"],
            _filas(
                dto.evolucion, lambda fila: [fila.periodo, fila.cantidad]
            ),
        ),
        Seccion(
            "Detalle",
            [
                "Bitacora",
                "Fecha",
                "Usuario",
                "Accion",
                "Entidad",
                "Descripcion",
            ],
            _filas(
                dto.items,
                lambda fila: [
                    fila.bitacora_id,
                    _fecha(fila.fecha_hora),
                    fila.usuario or "-",
                    fila.accion,
                    fila.entidad_afectada or "-",
                    fila.descripcion or "-",
                ],
            ),
        ),
    ]






