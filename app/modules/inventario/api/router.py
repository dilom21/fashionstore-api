from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.services.service import ProductoService
from app.modules.inventario.schemas.schemas import (
    DisponibilidadFiltro,
    InventarioConsultaResponse,
    InventarioResponse,
    MovimientoInventarioConsultaResponse,
    TipoMovimiento,
)
from app.modules.inventario.services.service import (
    DisponibilidadInvalidaError,
    InventarioService,
    RangoFechasInvalidoError,
    SucursalNoEncontradaError,
    SucursalScopeError,
    TipoMovimientoInvalidoError,
)

FUNCION_CONSULTAR_INVENTARIO = "CONSULTAR_INVENTARIO"


def _perm():
    """Permiso funcional del sistema RBAC existente (CU04)."""
    return require_permission(FUNCION_CONSULTAR_INVENTARIO, "CONSULTAR")


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


# ---------------------------------------------------------------------------
# CU13 - Consultar inventario por sucursal
# ---------------------------------------------------------------------------

inventario_router = APIRouter(prefix="/inventario", tags=["Inventario"])


@inventario_router.get("", response_model=InventarioConsultaResponse)
def consultar_inventario(
    sucursal_id: int | None = Query(
        default=None,
        gt=0,
        description="Solo el Administrador puede elegir la sucursal; el "
        "Encargado queda limitado a la suya.",
    ),
    producto: str | None = Query(
        default=None,
        min_length=1,
        description="Busqueda parcial por nombre de producto (case-insensitive).",
    ),
    producto_id: int | None = Query(default=None, gt=0),
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    disponibilidad: DisponibilidadFiltro | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm()),
):
    """CU13 - Consultar inventario por sucursal (solo lectura)."""
    try:
        return InventarioService.consultar_inventario(
            db,
            usuario,
            sucursal_id=sucursal_id,
            producto=producto,
            producto_id=producto_id,
            categoria_id=categoria_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
            disponibilidad=(
                disponibilidad.value if disponibilidad is not None else None
            ),
            limit=limit,
            offset=offset,
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la sucursal no corresponde a su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    except DisponibilidadInvalidaError:
        raise _error("Filtro de disponibilidad invalido")


@inventario_router.get("/sucursal/{sucursal_id}", response_model=list[InventarioResponse])
def inventario_por_sucursal(
    sucursal_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm()),
):
    try:
        sucursal_efectiva = InventarioService.resolver_sucursal_consulta(
            db, usuario, sucursal_id
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la sucursal no corresponde a su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    return InventarioService.listar_inventario(db, sucursal_id=sucursal_efectiva)


@inventario_router.get("/producto/{producto_id}", response_model=list[InventarioResponse])
def inventario_por_producto(
    producto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm()),
):
    if ProductoService.obtener_producto(db, producto_id) is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    try:
        alcance = InventarioService.resolver_alcance(usuario)
    except SucursalScopeError:
        raise _error(
            "No autorizado: el usuario no tiene sucursal asignada",
            status.HTTP_403_FORBIDDEN,
        )
    return InventarioService.listar_inventario(
        db, producto_id=producto_id, sucursal_id=alcance
    )


@inventario_router.get("/{inventario_id}", response_model=InventarioResponse)
def obtener_inventario(
    inventario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm()),
):
    inventario = InventarioService.obtener_inventario(db, inventario_id)
    if inventario is None:
        raise _error("Inventario no encontrado", status.HTTP_404_NOT_FOUND)
    try:
        InventarioService.validar_alcance_sucursal(usuario, inventario.sucursal_id)
    except SucursalScopeError:
        raise _error(
            "No autorizado: el inventario pertenece a otra sucursal",
            status.HTTP_403_FORBIDDEN,
        )
    return inventario


# ---------------------------------------------------------------------------
# CU14 - Consultar movimientos de inventario (Kardex)
# ---------------------------------------------------------------------------

movimientos_router = APIRouter(
    prefix="/movimientos-inventario", tags=["Inventario"]
)


@movimientos_router.get("", response_model=MovimientoInventarioConsultaResponse)
def consultar_movimientos_inventario(
    sucursal_id: int | None = Query(
        default=None,
        gt=0,
        description="Solo el Administrador puede elegir la sucursal; el "
        "Encargado queda limitado a la suya.",
    ),
    tipo: TipoMovimiento | None = Query(
        default=None,
        description="Tipo de movimiento del Kardex.",
    ),
    producto: str | None = Query(
        default=None,
        min_length=1,
        description="Busqueda parcial por nombre de producto (case-insensitive).",
    ),
    producto_id: int | None = Query(default=None, gt=0),
    variante_producto_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    usuario_id: int | None = Query(default=None, gt=0),
    referencia_tipo: str | None = Query(default=None, min_length=1),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm()),
):
    """CU14 - Consultar movimientos de inventario (Kardex, solo lectura)."""
    try:
        return InventarioService.consultar_movimientos_inventario(
            db,
            usuario,
            sucursal_id=sucursal_id,
            tipo=tipo.value if tipo is not None else None,
            producto=producto,
            producto_id=producto_id,
            variante_producto_id=variante_producto_id,
            temporada_id=temporada_id,
            usuario_id=usuario_id,
            referencia_tipo=referencia_tipo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
            offset=offset,
        )
    except SucursalScopeError:
        raise _error(
            "No autorizado: la sucursal no corresponde a su alcance",
            status.HTTP_403_FORBIDDEN,
        )
    except SucursalNoEncontradaError:
        raise _error("Sucursal no encontrada", status.HTTP_404_NOT_FOUND)
    except RangoFechasInvalidoError:
        raise _error(
            "fecha_desde no puede ser posterior a fecha_hasta",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except TipoMovimientoInvalidoError:
        raise _error(
            "Tipo de movimiento invalido",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


# ---------------------------------------------------------------------------
# Router compuesto del modulo inventario (CU13 + CU14)
# ---------------------------------------------------------------------------

router = APIRouter()
router.include_router(inventario_router)
router.include_router(movimientos_router)
