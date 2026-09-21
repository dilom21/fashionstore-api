"""Reglas de CU28 - Dashboard y reportes (solo lectura).

Actores: ADMINISTRADOR (alcance global y/o sucursal concreta) y
ENCARGADO_SUCURSAL (solo la sucursal de su empleado activo). CAJERO y CLIENTE
no tienen ``CONSULTAR_REPORTES`` en la base.

CU28 NO escribe nada: no hay INSERT/UPDATE/DELETE sobre datos de negocio, no
se persiste ningun archivo y no se crea ninguna entidad Reporte.

Definiciones usadas (documentadas para frontend y exportaciones):

- Ventas monetarias: solo ``Venta.estado == COMPLETADA``.
- ``ticket_promedio = total_vendido / ventas_completadas`` (0 si no hay ventas).
- ``stock_disponible = stock_actual - stock_reservado`` (nunca negativo).
- "stock bajo" = ``0 < stock_disponible <= umbral_stock_bajo`` (criterio de
  reporte; la tabla no tiene ``stock_minimo``).
- Conversion Reserva->Venta: ``reservas_atendidas_con_venta_completada /
  reservas_atendidas * 100`` usando ``Venta.reserva_id``.
- Conversion Carrito->Venta: ``carritos CONVERTIDO / total carritos * 100``.
- ``valor_referencial`` de devoluciones: ``SUM(cantidad * precio_unitario)``
  solo de devoluciones COMPLETADA. NO es un reembolso financiero.
"""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reportes.repositories.auditoria_repository import (
    AuditoriaReporteRepository,
)
from app.modules.reportes.repositories.clientes_repository import (
    ClientesReporteRepository,
)
from app.modules.reportes.repositories.compras_repository import (
    ComprasReporteRepository,
)
from app.modules.reportes.repositories.devoluciones_repository import (
    DevolucionesReporteRepository,
)
from app.modules.reportes.repositories.inventario_repository import (
    TIPOS_MOVIMIENTO,
    InventarioReporteRepository,
)
from app.modules.reportes.repositories.pagos_repository import (
    PagosReporteRepository,
)
from app.modules.reportes.repositories.productos_repository import (
    ProductosReporteRepository,
)
from app.modules.reportes.repositories.reservas_repository import (
    ReservasReporteRepository,
)
from app.modules.reportes.repositories.ventas_repository import (
    VentasReporteRepository,
)
from app.modules.reportes.schemas.auditoria import (
    AuditoriaConteoResponse,
    AuditoriaFilaResponse,
    ReporteAuditoriaResponse,
)
from app.modules.reportes.schemas.common import (
    AlcanceReporteResponse,
    MetadatosReporteResponse,
    PaginacionResponse,
    PeriodoReporteResponse,
    SerieTemporalResponse,
)
from app.modules.reportes.schemas.compras_clientes import (
    CarritoEstadoResponse,
    CarritosResumenResponse,
    ClienteCompraResponse,
    ClientesResumenResponse,
    CompraProductoResponse,
    ComprasResumenResponse,
    CompraSucursalResponse,
    OrdenEstadoResponse,
    ProveedorOrdenResponse,
    ReporteClientesCarritosResponse,
    ReporteComprasResponse,
)
from app.modules.reportes.schemas.dashboard import (
    ConteoEstadoResponse,
    DashboardKpisResponse,
    DashboardResponse,
    ResumenDevolucionesResponse,
    ResumenInventarioResponse,
    ResumenReservasResponse,
    SucursalComparadaResponse,
    TopProductoResponse,
    VentasCanalResponse,
)
from app.modules.reportes.schemas.inventario import (
    InventarioMovimientoTipoResponse,
    InventarioResumenResponse,
    InventarioTablaItemResponse,
    ReporteInventarioResponse,
)
from app.modules.reportes.schemas.operaciones import (
    DevolucionEstadoResponse,
    DevolucionMotivoResponse,
    DevolucionProductoResponse,
    DevolucionResumenResponse,
    DevolucionSucursalResponse,
    PagoEstadoResponse,
    PagoMetodoResponse,
    PagoPasarelaResponse,
    PagoResumenResponse,
    ReporteDevolucionesResponse,
    ReportePagosResponse,
    ReporteReservasResponse,
    ReservaEstadoResponse,
    ReservaResumenResponse,
    ReservaSucursalResponse,
)
from app.modules.reportes.schemas.productos import (
    EtiquetaRankingResponse,
    ProductoRankingResponse,
    ReporteProductosResponse,
    ResumenCatalogoResponse,
    VarianteRankingResponse,
)
from app.modules.reportes.schemas.ventas import (
    ReporteVentasResponse,
    VentaCanalResponse,
    VentaEmpleadoResponse,
    VentaEstadoResponse,
    VentaResumenKpisResponse,
    VentaSucursalResponse,
    VentaTablaItemResponse,
)
from app.modules.sucursales.models.models import Sucursal

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROLES_REPORTES = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL)

PAGINA_POR_DEFECTO = 1
TAMANO_PAGINA_POR_DEFECTO = 20
TAMANO_PAGINA_MAXIMO = 50
TOP_POR_DEFECTO = 10
TOP_MAXIMO = 50
UMBRAL_STOCK_BAJO_POR_DEFECTO = 5
UMBRAL_STOCK_BAJO_MAXIMO = 1000
AGRUPACION_POR_DEFECTO = "DIA"

# Limite duro de exportacion: si el detalle lo supera se responde 422 en lugar
# de truncar silenciosamente.
MAX_FILAS_EXPORTACION = 10000

TIPOS_REPORTE = (
    "RESUMEN",
    "VENTAS",
    "PRODUCTOS",
    "INVENTARIO",
    "RESERVAS",
    "DEVOLUCIONES",
    "PAGOS",
    "COMPRAS_PROVEEDORES",
    "CLIENTES_CARRITOS",
    "AUDITORIA",
)
FORMATOS = ("PDF", "XLSX", "CSV")

_CENTIMOS = Decimal("0.01")


class ReportesError(Exception):
    """Base de errores de negocio de CU28 (mapped a HTTP en el router)."""


class ReportesNoAutorizadoError(ReportesError):
    """Rol/contexto no admitido (CAJERO, CLIENTE, o auditoria sin ADMIN)."""


class SucursalReporteNoEncontradaError(ReportesError):
    """El filtro ``sucursal_id`` no corresponde a una sucursal existente."""


class ReportesScopeError(ReportesError):
    """El usuario intenta consultar fuera de su sucursal."""


class RangoFechasInvalidoError(ReportesError):
    """``fecha_desde`` posterior a ``fecha_hasta`` (422)."""


class FormatoReporteNoSoportadoError(ReportesError):
    """``formato`` distinto de PDF/XLSX/CSV."""


class TipoReporteNoSoportadoError(ReportesError):
    """``tipo`` de exportacion fuera de TIPOS_REPORTE."""


class ReporteDemasiadoGrandeError(ReportesError):
    """El detalle supera MAX_FILAS_EXPORTACION."""


class GeneracionReporteError(ReportesError):
    """Fallo inesperado al construir el archivo."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rango_fechas(
    fecha_desde: date | None, fecha_hasta: date | None
) -> tuple[datetime | None, datetime | None]:
    """Intervalo semiabierto [desde, hasta+1 dia) para incluir dias completos."""
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_desde > fecha_hasta:
            raise RangoFechasInvalidoError()

    desde = (
        datetime.combine(fecha_desde, time.min)
        if fecha_desde is not None
        else None
    )
    hasta = (
        datetime.combine(fecha_hasta + timedelta(days=1), time.min)
        if fecha_hasta is not None
        else None
    )
    return desde, hasta


def _porcentaje(numerador: int, denominador: int) -> Decimal:
    """Porcentaje con 2 decimales; 0 si el denominador es 0 (sin dividir)."""
    if not denominador:
        return Decimal("0.00")
    valor = (Decimal(numerador) * Decimal(100)) / Decimal(denominador)
    return valor.quantize(_CENTIMOS, rounding=ROUND_HALF_UP)


def _promedio(total, cantidad: int) -> Decimal:
    """Promedio monetario con 2 decimales; 0 si no hay registros."""
    if not cantidad:
        return Decimal("0.00")
    return (Decimal(total) / Decimal(cantidad)).quantize(
        _CENTIMOS, rounding=ROUND_HALF_UP
    )


def _validar_rol(usuario: Usuario) -> str:
    """ADMINISTRADOR y ENCARGADO_SUCURSAL unicamente (RBAC no basta)."""
    rol = str(usuario.rol.nombre).strip().upper()
    if rol not in ROLES_REPORTES:
        raise ReportesNoAutorizadoError()
    return rol


def _validar_admin(usuario: Usuario) -> None:
    """La auditoria es SOLO ADMINISTRADOR (bitacora sensible y global)."""
    if _validar_rol(usuario) != ROL_ADMINISTRADOR:
        raise ReportesNoAutorizadoError()


def _alcance(
    db: Session, usuario: Usuario, sucursal_id: int | None
) -> tuple[int | None, str | None]:
    """(sucursal_id efectivo, nombre) aplicando el alcance real del usuario.

    - ADMINISTRADOR: ``sucursal_id`` opcional; si viene debe existir (404).
    - ENCARGADO_SUCURSAL: siempre su sucursal (otra -> 403); sin empleado
      activo -> 403.
    """
    rol = _validar_rol(usuario)
    if rol == ROL_ENCARGADO_SUCURSAL:
        empleado = usuario.empleado
        if (
            empleado is None
            or not empleado.estado
            or empleado.sucursal_id is None
        ):
            raise ReportesScopeError()
        propia = int(empleado.sucursal_id)
        if sucursal_id is not None and int(sucursal_id) != propia:
            raise ReportesScopeError()
        sucursal = db.get(Sucursal, propia)
        return propia, (sucursal.nombre if sucursal else None)

    if sucursal_id is None:
        return None, None
    sucursal = db.get(Sucursal, int(sucursal_id))
    if sucursal is None:
        raise SucursalReporteNoEncontradaError()
    return int(sucursal.id), sucursal.nombre


def _metadatos(
    tipo: str,
    *,
    fecha_desde: date | None,
    fecha_hasta: date | None,
    sucursal_id: int | None,
    sucursal_nombre: str | None,
) -> MetadatosReporteResponse:
    return MetadatosReporteResponse(
        tipo=tipo,
        periodo=PeriodoReporteResponse(
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta
        ),
        alcance=AlcanceReporteResponse(
            es_global=sucursal_id is None,
            sucursal_id=sucursal_id,
            sucursal_nombre=sucursal_nombre,
        ),
        generado_en=datetime.now(),
    )


def _paginacion(
    *, pagina: int, tamano_pagina: int, total_registros: int
) -> PaginacionResponse:
    total_paginas = (
        (total_registros + tamano_pagina - 1) // tamano_pagina
        if total_registros
        else 0
    )
    return PaginacionResponse(
        pagina=pagina,
        tamano_pagina=tamano_pagina,
        total_registros=total_registros,
        total_paginas=total_paginas,
    )


def _periodo_etiqueta(valor, agrupacion: str) -> str:
    """Etiqueta de la serie temporal segun la agrupacion real aplicada."""
    if valor is None:
        return "SIN_FECHA"
    if agrupacion.upper() == "MES":
        return valor.strftime("%Y-%m")
    return valor.strftime("%Y-%m-%d")


def _serie(fila, agrupacion: str) -> SerieTemporalResponse:
    return SerieTemporalResponse(
        periodo=_periodo_etiqueta(fila[0], agrupacion),
        cantidad=int(fila[1] or 0),
        monto=Decimal(fila[2]) if len(fila) > 2 and fila[2] is not None else None,
        unidades=int(fila[3]) if len(fila) > 3 and fila[3] is not None else None,
    )


class ReportesService:
    """CU28 - Dashboard y reportes (solo lectura, sin persistencia)."""

    @staticmethod
    def consultar_dashboard(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
        top: int = TOP_POR_DEFECTO,
    ) -> DashboardResponse:
        """Resumen ejecutivo: KPIs, evolucion, canales, top y comparativo."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        ventas = VentasReporteRepository.kpis(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        reservas = ReservasReporteRepository.kpis(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        devoluciones = DevolucionesReporteRepository.resumen(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        inventario = InventarioReporteRepository.resumen(
            db,
            sucursal_id=efectiva,
            umbral_stock_bajo=UMBRAL_STOCK_BAJO_POR_DEFECTO,
        )

        reservas_por_sucursal = {
            int(fila[0]): int(fila[2] or 0)
            for fila in ReservasReporteRepository.por_sucursal(
                db, desde=desde, hasta=hasta, sucursal_id=efectiva
            )
        }
        devoluciones_por_sucursal = {
            int(fila[0]): int(fila[2] or 0)
            for fila in DevolucionesReporteRepository.por_sucursal(
                db, desde=desde, hasta=hasta, sucursal_id=efectiva
            )
        }
        comparativo = [
            SucursalComparadaResponse(
                sucursal_id=int(fila[0]),
                sucursal_nombre=fila[1],
                ventas=int(fila[2] or 0),
                monto_vendido=Decimal(fila[3] or 0),
                unidades=int(fila[4] or 0),
                ticket_promedio=_promedio(fila[3], int(fila[2] or 0)),
                reservas=reservas_por_sucursal.get(int(fila[0]), 0),
                devoluciones=devoluciones_por_sucursal.get(int(fila[0]), 0),
            )
            for fila in VentasReporteRepository.por_sucursal(
                db, desde=desde, hasta=hasta, sucursal_id=efectiva
            )
        ]

        return DashboardResponse(
            metadatos=_metadatos(
                "DASHBOARD",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            kpis=DashboardKpisResponse(
                ventas_completadas=int(ventas["ventas_completadas"]),
                total_vendido=Decimal(ventas["total_vendido"] or 0),
                unidades_vendidas=int(ventas["unidades_vendidas"]),
                ticket_promedio=_promedio(
                    ventas["total_vendido"], int(ventas["ventas_completadas"])
                ),
                reservas_total=int(reservas["total_reservas"]),
                devoluciones_completadas=int(devoluciones["completadas"]),
                unidades_devueltas=int(devoluciones["unidades_devueltas"]),
                stock_disponible_total=int(
                    inventario["stock_disponible_total"]
                ),
            ),
            ventas_evolucion=[
                _serie(fila, agrupacion)
                for fila in VentasReporteRepository.evolucion(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    agrupacion=agrupacion,
                )
            ],
            ventas_por_canal=[
                VentasCanalResponse(
                    canal=fila[0],
                    cantidad_ventas=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                    unidades=int(fila[3] or 0),
                )
                for fila in VentasReporteRepository.por_canal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            top_productos=[
                TopProductoResponse(
                    producto_id=int(fila[0]),
                    producto_nombre=fila[1],
                    unidades=int(fila[2] or 0),
                    monto=Decimal(fila[3] or 0),
                )
                for fila in VentasReporteRepository.top_productos(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    top=top,
                )
            ],
            resumen_reservas=ResumenReservasResponse(
                total=int(reservas["total_reservas"]),
                por_estado=[
                    ConteoEstadoResponse(estado=fila[0], cantidad=int(fila[1]))
                    for fila in ReservasReporteRepository.por_estado(
                        db, desde=desde, hasta=hasta, sucursal_id=efectiva
                    )
                ],
            ),
            resumen_devoluciones=ResumenDevolucionesResponse(
                total=int(devoluciones["total_devoluciones"]),
                completadas=int(devoluciones["completadas"]),
                unidades_devueltas=int(devoluciones["unidades_devueltas"]),
                valor_referencial=Decimal(
                    devoluciones["valor_referencial"] or 0
                ),
                por_estado=[
                    ConteoEstadoResponse(estado=fila[0], cantidad=int(fila[1]))
                    for fila in DevolucionesReporteRepository.por_estado(
                        db, desde=desde, hasta=hasta, sucursal_id=efectiva
                    )
                ],
            ),
            resumen_inventario=ResumenInventarioResponse(
                stock_actual_total=int(inventario["stock_actual_total"]),
                stock_reservado_total=int(
                    inventario["stock_reservado_total"]
                ),
                stock_disponible_total=int(
                    inventario["stock_disponible_total"]
                ),
                variantes_agotadas=int(inventario["variantes_agotadas"]),
                variantes_stock_bajo=int(inventario["variantes_stock_bajo"]),
            ),
            comparativo_sucursales=comparativo,
        )

    @staticmethod
    def consultar_ventas(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
        pagina: int = PAGINA_POR_DEFECTO,
        tamano_pagina: int = TAMANO_PAGINA_POR_DEFECTO,
    ) -> ReporteVentasResponse:
        """Resumen por estado/canal/sucursal/empleado + tabla paginada."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        kpis = VentasReporteRepository.kpis(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        total_registros = VentasReporteRepository.contar(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        offset = (pagina - 1) * tamano_pagina
        filas = VentasReporteRepository.listar(
            db,
            desde=desde,
            hasta=hasta,
            sucursal_id=efectiva,
            limit=tamano_pagina,
            offset=offset,
        )

        return ReporteVentasResponse(
            metadatos=_metadatos(
                "VENTAS",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=VentaResumenKpisResponse(
                ventas_completadas=int(kpis["ventas_completadas"]),
                monto_total=Decimal(kpis["total_vendido"] or 0),
                unidades_vendidas=int(kpis["unidades_vendidas"]),
                ticket_promedio=_promedio(
                    kpis["total_vendido"], int(kpis["ventas_completadas"])
                ),
            ),
            ventas_por_estado=[
                VentaEstadoResponse(
                    estado=fila[0],
                    cantidad=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in VentasReporteRepository.por_estado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            ventas_por_canal=[
                VentaCanalResponse(
                    canal=fila[0],
                    cantidad=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                    unidades=int(fila[3] or 0),
                )
                for fila in VentasReporteRepository.por_canal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            ventas_por_sucursal=[
                VentaSucursalResponse(
                    sucursal_id=int(fila[0]),
                    sucursal_nombre=fila[1],
                    cantidad=int(fila[2] or 0),
                    monto=Decimal(fila[3] or 0),
                    unidades=int(fila[4] or 0),
                    ticket_promedio=_promedio(fila[3], int(fila[2] or 0)),
                )
                for fila in VentasReporteRepository.por_sucursal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            ventas_por_empleado=[
                VentaEmpleadoResponse(
                    empleado_id=int(fila[0]),
                    empleado_nombre=f"{fila[1]} {fila[2]}".strip(),
                    cantidad=int(fila[3] or 0),
                    monto=Decimal(fila[4] or 0),
                )
                for fila in VentasReporteRepository.por_empleado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            evolucion=[
                _serie(fila, agrupacion)
                for fila in VentasReporteRepository.evolucion(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    agrupacion=agrupacion,
                )
            ],
            paginacion=_paginacion(
                pagina=pagina,
                tamano_pagina=tamano_pagina,
                total_registros=total_registros,
            ),
            items=[
                VentaTablaItemResponse(
                    venta_id=int(fila[0]),
                    fecha_hora=fila[1],
                    sucursal=fila[2],
                    canal=fila[3],
                    estado=fila[4],
                    cliente_nombre=(
                        f"{fila[5]} {fila[6]}".strip()
                        if fila[5] or fila[6]
                        else None
                    ),
                    empleado_nombre=(
                        f"{fila[7]} {fila[8]}".strip()
                        if fila[7] or fila[8]
                        else None
                    ),
                    cantidad_unidades=int(fila[9] or 0),
                    total=Decimal(fila[10] or 0),
                )
                for fila in filas
            ],
        )

    @staticmethod
    def consultar_productos(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        top: int = TOP_POR_DEFECTO,
    ) -> ReporteProductosResponse:
        """Rankings de productos sobre ventas COMPLETADAS (LIMIT en SQL)."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)
        base = {
            "desde": desde,
            "hasta": hasta,
            "sucursal_id": efectiva,
            "top": top,
        }

        return ReporteProductosResponse(
            metadatos=_metadatos(
                "PRODUCTOS",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            top_productos_por_unidades=[
                ProductoRankingResponse(
                    producto_id=int(fila[0]),
                    producto_nombre=fila[1],
                    categoria=fila[2],
                    unidades=int(fila[3] or 0),
                    monto=Decimal(fila[4] or 0),
                )
                for fila in ProductosReporteRepository.top_productos(
                    db, **base
                )
            ],
            top_productos_por_monto=[
                ProductoRankingResponse(
                    producto_id=int(fila[0]),
                    producto_nombre=fila[1],
                    categoria=fila[2],
                    unidades=int(fila[3] or 0),
                    monto=Decimal(fila[4] or 0),
                )
                for fila in ProductosReporteRepository.top_productos(
                    db, por_monto=True, **base
                )
            ],
            ventas_por_categoria=[
                EtiquetaRankingResponse(
                    etiqueta=fila[0],
                    unidades=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in ProductosReporteRepository.por_categoria(db, **base)
            ],
            variantes_mas_vendidas=[
                VarianteRankingResponse(
                    variante_producto_id=int(fila[0]),
                    sku=fila[1],
                    producto_nombre=fila[2],
                    talla=fila[3],
                    color=fila[4],
                    unidades=int(fila[5] or 0),
                    monto=Decimal(fila[6] or 0),
                )
                for fila in ProductosReporteRepository.top_variantes(
                    db, **base
                )
            ],
            tallas_mas_vendidas=[
                EtiquetaRankingResponse(
                    etiqueta=fila[0],
                    unidades=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in ProductosReporteRepository.por_talla(db, **base)
            ],
            colores_mas_vendidos=[
                EtiquetaRankingResponse(
                    etiqueta=fila[0],
                    unidades=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in ProductosReporteRepository.por_color(db, **base)
            ],
            ventas_por_temporada=[
                EtiquetaRankingResponse(
                    etiqueta=fila[0],
                    unidades=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in ProductosReporteRepository.por_temporada(
                    db, **base
                )
            ],
            ventas_por_coleccion=[
                EtiquetaRankingResponse(
                    etiqueta=fila[0],
                    unidades=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in ProductosReporteRepository.por_coleccion(
                    db, **base
                )
            ],
            resumen_catalogo=ResumenCatalogoResponse(
                **ProductosReporteRepository.resumen_catalogo(db)
            ),
        )

    @staticmethod
    def consultar_inventario(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        umbral_stock_bajo: int = UMBRAL_STOCK_BAJO_POR_DEFECTO,
        pagina: int = PAGINA_POR_DEFECTO,
        tamano_pagina: int = TAMANO_PAGINA_POR_DEFECTO,
    ) -> ReporteInventarioResponse:
        """Existencias actuales + movimientos por tipo real del periodo."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        resumen = InventarioReporteRepository.resumen(
            db,
            sucursal_id=efectiva,
            umbral_stock_bajo=umbral_stock_bajo,
        )
        movimientos = {
            fila[0]: (int(fila[1] or 0), int(fila[2] or 0))
            for fila in InventarioReporteRepository.movimientos_por_tipo(
                db, desde=desde, hasta=hasta, sucursal_id=efectiva
            )
        }
        total_registros = InventarioReporteRepository.contar(
            db, sucursal_id=efectiva
        )
        filas = InventarioReporteRepository.listar(
            db,
            sucursal_id=efectiva,
            limit=tamano_pagina,
            offset=(pagina - 1) * tamano_pagina,
        )

        return ReporteInventarioResponse(
            metadatos=_metadatos(
                "INVENTARIO",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=InventarioResumenResponse(**resumen),
            movimientos_por_tipo=[
                InventarioMovimientoTipoResponse(
                    tipo=tipo,
                    cantidad_movimientos=movimientos.get(tipo, (0, 0))[0],
                    unidades=movimientos.get(tipo, (0, 0))[1],
                )
                for tipo in TIPOS_MOVIMIENTO
            ],
            paginacion=_paginacion(
                pagina=pagina,
                tamano_pagina=tamano_pagina,
                total_registros=total_registros,
            ),
            items=[
                InventarioTablaItemResponse(
                    inventario_id=int(fila[0]),
                    sucursal=fila[1],
                    producto=fila[2],
                    sku=fila[3],
                    talla=fila[4],
                    color=fila[5],
                    temporada=fila[6],
                    stock_actual=int(fila[7] or 0),
                    stock_reservado=int(fila[8] or 0),
                    stock_disponible=int(fila[9] or 0),
                    fecha_actualizacion=fila[10],
                )
                for fila in filas
            ],
        )

    @staticmethod
    def consultar_reservas(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
    ) -> ReporteReservasResponse:
        """Reservas por estado/sucursal, evolucion y conversion real a venta."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        kpis = ReservasReporteRepository.kpis(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        atendidas = int(kpis["reservas_atendidas"])
        convertidas = int(kpis["reservas_convertidas_en_venta"])

        return ReporteReservasResponse(
            metadatos=_metadatos(
                "RESERVAS",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=ReservaResumenResponse(
                total_reservas=int(kpis["total_reservas"]),
                cantidad_total_unidades=int(kpis["cantidad_total_unidades"]),
                reservas_atendidas=atendidas,
                reservas_convertidas_en_venta=convertidas,
                tasa_conversion=_porcentaje(convertidas, atendidas),
            ),
            reservas_por_estado=[
                ReservaEstadoResponse(
                    estado=fila[0],
                    cantidad=int(fila[1] or 0),
                    unidades=int(fila[2] or 0),
                )
                for fila in ReservasReporteRepository.por_estado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            reservas_por_sucursal=[
                ReservaSucursalResponse(
                    sucursal_id=int(fila[0]),
                    sucursal_nombre=fila[1],
                    cantidad=int(fila[2] or 0),
                    unidades=int(fila[3] or 0),
                )
                for fila in ReservasReporteRepository.por_sucursal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            evolucion=[
                _serie(fila, agrupacion)
                for fila in ReservasReporteRepository.evolucion(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    agrupacion=agrupacion,
                )
            ],
        )

    @staticmethod
    def consultar_devoluciones(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
        top: int = TOP_POR_DEFECTO,
    ) -> ReporteDevolucionesResponse:
        """Devoluciones por estado/sucursal, motivos y valor referencial."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        resumen = DevolucionesReporteRepository.resumen(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )

        return ReporteDevolucionesResponse(
            metadatos=_metadatos(
                "DEVOLUCIONES",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=DevolucionResumenResponse(
                total_devoluciones=int(resumen["total_devoluciones"]),
                completadas=int(resumen["completadas"]),
                unidades_devueltas=int(resumen["unidades_devueltas"]),
                valor_referencial=Decimal(resumen["valor_referencial"] or 0),
            ),
            devoluciones_por_estado=[
                DevolucionEstadoResponse(
                    estado=fila[0],
                    cantidad=int(fila[1] or 0),
                    unidades=int(fila[2] or 0),
                    valor_referencial=Decimal(fila[3] or 0),
                )
                for fila in DevolucionesReporteRepository.por_estado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            devoluciones_por_sucursal=[
                DevolucionSucursalResponse(
                    sucursal_id=int(fila[0]),
                    sucursal_nombre=fila[1],
                    cantidad=int(fila[2] or 0),
                    unidades=int(fila[3] or 0),
                )
                for fila in DevolucionesReporteRepository.por_sucursal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            evolucion=[
                _serie(fila, agrupacion)
                for fila in DevolucionesReporteRepository.evolucion(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    agrupacion=agrupacion,
                )
            ],
            productos_mas_devueltos=[
                DevolucionProductoResponse(
                    producto_id=int(fila[0]),
                    producto_nombre=fila[1],
                    unidades=int(fila[2] or 0),
                    valor_referencial=Decimal(fila[3] or 0),
                )
                for fila in DevolucionesReporteRepository.productos_mas_devueltos(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    top=top,
                )
            ],
            motivos_mas_frecuentes=[
                DevolucionMotivoResponse(
                    motivo=fila[0],
                    cantidad_devoluciones=int(fila[1] or 0),
                    unidades=int(fila[2] or 0),
                )
                for fila in DevolucionesReporteRepository.motivos(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    top=top,
                )
            ],
        )

    @staticmethod
    def consultar_pagos(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
    ) -> ReportePagosResponse:
        """Transacciones de pago (no ventas): estado, metodo y pasarela."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        resumen = PagosReporteRepository.resumen(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )

        return ReportePagosResponse(
            metadatos=_metadatos(
                "PAGOS",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=PagoResumenResponse(
                cantidad_pagos=int(resumen["cantidad_pagos"]),
                monto_total_procesado=Decimal(
                    resumen["monto_total_procesado"] or 0
                ),
                monto_aprobado=Decimal(resumen["monto_aprobado"] or 0),
                nota=resumen["nota"],
            ),
            pagos_por_estado=[
                PagoEstadoResponse(
                    estado=fila[0],
                    cantidad=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in PagosReporteRepository.por_estado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            pagos_por_metodo=[
                PagoMetodoResponse(
                    metodo=fila[0],
                    cantidad=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in PagosReporteRepository.por_metodo(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            pagos_por_pasarela=[
                PagoPasarelaResponse(
                    pasarela=fila[0],
                    cantidad=int(fila[1] or 0),
                    monto=Decimal(fila[2] or 0),
                )
                for fila in PagosReporteRepository.por_pasarela(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            evolucion=[
                _serie(fila, agrupacion)
                for fila in PagosReporteRepository.evolucion(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    agrupacion=agrupacion,
                )
            ],
        )

    @staticmethod
    def consultar_compras_proveedores(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        top: int = TOP_POR_DEFECTO,
    ) -> ReporteComprasResponse:
        """Ordenes de compra por estado/proveedor/sucursal y valor (Decimal)."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        resumen = ComprasReporteRepository.resumen(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )

        return ReporteComprasResponse(
            metadatos=_metadatos(
                "COMPRAS_PROVEEDORES",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen=ComprasResumenResponse(
                total_ordenes=int(resumen["total_ordenes"]),
                unidades_ordenadas=int(resumen["unidades_ordenadas"]),
                valor_total_ordenes=Decimal(
                    resumen["valor_total_ordenes"] or 0
                ),
                valor_ordenes_recibidas=Decimal(
                    resumen["valor_ordenes_recibidas"] or 0
                ),
            ),
            ordenes_por_estado=[
                OrdenEstadoResponse(
                    estado=fila[0],
                    cantidad=int(fila[1] or 0),
                    valor=Decimal(fila[2] or 0),
                )
                for fila in ComprasReporteRepository.por_estado(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            ordenes_por_proveedor=[
                ProveedorOrdenResponse(
                    proveedor_id=int(fila[0]),
                    razon_social=fila[1],
                    cantidad_ordenes=int(fila[2] or 0),
                    unidades_ordenadas=int(fila[3] or 0),
                    valor_ordenes=Decimal(fila[4] or 0),
                    ordenes_recibidas=int(fila[5] or 0),
                    ordenes_canceladas=int(fila[6] or 0),
                    cumplimiento_promedio_dias=(
                        Decimal(str(fila[7])).quantize(_CENTIMOS)
                        if fila[7] is not None
                        else None
                    ),
                )
                for fila in ComprasReporteRepository.por_proveedor(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            ordenes_por_sucursal=[
                CompraSucursalResponse(
                    sucursal_id=int(fila[0]),
                    sucursal_nombre=fila[1],
                    cantidad=int(fila[2] or 0),
                    valor=Decimal(fila[3] or 0),
                )
                for fila in ComprasReporteRepository.por_sucursal(
                    db, desde=desde, hasta=hasta, sucursal_id=efectiva
                )
            ],
            productos_abastecidos=[
                CompraProductoResponse(
                    variante_producto_id=int(fila[0]),
                    sku=fila[1],
                    producto_nombre=fila[2],
                    unidades=int(fila[3] or 0),
                    valor=Decimal(fila[4] or 0),
                )
                for fila in ComprasReporteRepository.productos_abastecidos(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    top=top,
                )
            ],
        )

    @staticmethod
    def consultar_clientes_carritos(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        top: int = TOP_POR_DEFECTO,
    ) -> ReporteClientesCarritosResponse:
        """Clientes compradores/recurrentes (sin PII) y estado de carritos."""
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        efectiva, nombre = _alcance(db, usuario, sucursal_id)

        clientes = ClientesReporteRepository.resumen(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )
        carritos = ClientesReporteRepository.carritos(
            db, desde=desde, hasta=hasta, sucursal_id=efectiva
        )

        return ReporteClientesCarritosResponse(
            metadatos=_metadatos(
                "CLIENTES_CARRITOS",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=efectiva,
                sucursal_nombre=nombre,
            ),
            resumen_clientes=ClientesResumenResponse(**clientes),
            compras_por_cliente=[
                ClienteCompraResponse(
                    cliente_id=int(fila[0]),
                    nombre=fila[1],
                    apellido=fila[2],
                    compras=int(fila[3] or 0),
                    unidades=int(fila[4] or 0),
                    monto=Decimal(fila[5] or 0),
                )
                for fila in ClientesReporteRepository.compras_por_cliente(
                    db,
                    desde=desde,
                    hasta=hasta,
                    sucursal_id=efectiva,
                    top=top,
                )
            ],
            resumen_carritos=CarritosResumenResponse(
                total_carritos=int(carritos["total_carritos"]),
                carritos_convertidos=int(carritos["carritos_convertidos"]),
                tasa_conversion_carrito=_porcentaje(
                    int(carritos["carritos_convertidos"]),
                    int(carritos["total_carritos"]),
                ),
                por_estado=[
                    CarritoEstadoResponse(
                        estado=fila[0], cantidad=int(fila[1] or 0)
                    )
                    for fila in carritos["por_estado"]
                ],
            ),
        )

    @staticmethod
    def consultar_auditoria(
        db: Session,
        usuario: Usuario,
        *,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
        pagina: int = PAGINA_POR_DEFECTO,
        tamano_pagina: int = TAMANO_PAGINA_POR_DEFECTO,
    ) -> ReporteAuditoriaResponse:
        """Bitacora agregada. SOLO ADMINISTRADOR (dato sensible/global)."""
        _validar_admin(usuario)
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)

        total = AuditoriaReporteRepository.contar(db, desde=desde, hasta=hasta)
        filas = AuditoriaReporteRepository.listar(
            db,
            desde=desde,
            hasta=hasta,
            limit=tamano_pagina,
            offset=(pagina - 1) * tamano_pagina,
        )

        return ReporteAuditoriaResponse(
            metadatos=_metadatos(
                "AUDITORIA",
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                sucursal_id=None,
                sucursal_nombre=None,
            ),
            total_eventos=total,
            eventos_por_accion=[
                AuditoriaConteoResponse(
                    etiqueta=fila[0], cantidad=int(fila[1] or 0)
                )
                for fila in AuditoriaReporteRepository.por_accion(
                    db, desde=desde, hasta=hasta
                )
            ],
            eventos_por_entidad=[
                AuditoriaConteoResponse(
                    etiqueta=fila[0], cantidad=int(fila[1] or 0)
                )
                for fila in AuditoriaReporteRepository.por_entidad(
                    db, desde=desde, hasta=hasta
                )
            ],
            eventos_por_usuario=[
                AuditoriaConteoResponse(
                    etiqueta=fila[0], cantidad=int(fila[1] or 0)
                )
                for fila in AuditoriaReporteRepository.por_usuario(
                    db, desde=desde, hasta=hasta
                )
            ],
            evolucion=[
                _serie(fila, agrupacion)
                for fila in AuditoriaReporteRepository.evolucion(
                    db, desde=desde, hasta=hasta, agrupacion=agrupacion
                )
            ],
            paginacion=_paginacion(
                pagina=pagina,
                tamano_pagina=tamano_pagina,
                total_registros=total,
            ),
            items=[
                AuditoriaFilaResponse(
                    bitacora_id=int(fila[0]),
                    fecha_hora=fila[1],
                    usuario=fila[2],
                    accion=fila[3],
                    entidad_afectada=fila[4],
                    descripcion=fila[5],
                )
                for fila in filas
            ],
        )

    @staticmethod
    def generar_reporte(
        db: Session,
        usuario: Usuario,
        *,
        tipo: str,
        formato: str,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        agrupacion: str = AGRUPACION_POR_DEFECTO,
        top: int = TOP_POR_DEFECTO,
        umbral_stock_bajo: int = UMBRAL_STOCK_BAJO_POR_DEFECTO,
        tamano_pagina: int | None = None,
    ) -> tuple[bytes, str, str]:
        """Construye el archivo en memoria (CSV/XLSX/PDF).

        Devuelve ``(contenido, nombre_archivo, media_type)``. NO persiste nada:
        reutiliza exactamente los mismos DTO que la vista JSON, de modo que los
        tres formatos muestran los mismos numeros.
        """
        tipo_normalizado = str(tipo).strip().upper()
        formato_normalizado = str(formato).strip().upper()
        if tipo_normalizado not in TIPOS_REPORTE:
            raise TipoReporteNoSoportadoError()
        if formato_normalizado not in FORMATOS:
            raise FormatoReporteNoSoportadoError()

        # Import local para evitar dependencia circular servicio <-> exportacion
        from app.modules.reportes.services.exportacion_service import (
            ExportacionService,
        )

        filas_detalle = int(tamano_pagina or MAX_FILAS_EXPORTACION)
        comun = {
            "sucursal_id": sucursal_id,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        }

        if tipo_normalizado == "RESUMEN":
            dto = ReportesService.consultar_dashboard(
                db, usuario, agrupacion=agrupacion, top=top, **comun
            )
        elif tipo_normalizado == "VENTAS":
            dto = ReportesService.consultar_ventas(
                db,
                usuario,
                agrupacion=agrupacion,
                pagina=1,
                tamano_pagina=filas_detalle,
                **comun,
            )
        elif tipo_normalizado == "PRODUCTOS":
            dto = ReportesService.consultar_productos(
                db, usuario, top=top, **comun
            )
        elif tipo_normalizado == "INVENTARIO":
            dto = ReportesService.consultar_inventario(
                db,
                usuario,
                umbral_stock_bajo=umbral_stock_bajo,
                pagina=1,
                tamano_pagina=filas_detalle,
                **comun,
            )
        elif tipo_normalizado == "RESERVAS":
            dto = ReportesService.consultar_reservas(
                db, usuario, agrupacion=agrupacion, **comun
            )
        elif tipo_normalizado == "DEVOLUCIONES":
            dto = ReportesService.consultar_devoluciones(
                db, usuario, agrupacion=agrupacion, top=top, **comun
            )
        elif tipo_normalizado == "PAGOS":
            dto = ReportesService.consultar_pagos(
                db, usuario, agrupacion=agrupacion, **comun
            )
        elif tipo_normalizado == "COMPRAS_PROVEEDORES":
            dto = ReportesService.consultar_compras_proveedores(
                db, usuario, top=top, **comun
            )
        elif tipo_normalizado == "CLIENTES_CARRITOS":
            dto = ReportesService.consultar_clientes_carritos(
                db, usuario, top=top, **comun
            )
        else:
            # AUDITORIA: solo ADMIN y sin filtro de sucursal (no aplica)
            dto = ReportesService.consultar_auditoria(
                db,
                usuario,
                agrupacion=agrupacion,
                pagina=1,
                tamano_pagina=filas_detalle,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )

        paginacion = getattr(dto, "paginacion", None)
        if (
            paginacion is not None
            and paginacion.total_registros > MAX_FILAS_EXPORTACION
        ):
            raise ReporteDemasiadoGrandeError()

        return ExportacionService.generar_archivo(
            dto, tipo=tipo_normalizado, formato=formato_normalizado
        )











