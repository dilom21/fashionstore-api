from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_admin
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.promociones.schemas.schemas import (
    PromocionCreate,
    PromocionDetalleResponse,
    PromocionEstadoUpdate,
    PromocionProductosResponse,
    PromocionProductosUpdate,
    PromocionResponse,
    PromocionUpdate,
)
from app.modules.promociones.services.service import (
    AsignacionProductoInactivoError,
    AsignacionProductoNoEncontradoError,
    AsignacionRegistroInvalidoError,
    PromocionFechasInvalidasError,
    PromocionNoEncontradaError,
    PromocionNombreInvalidoError,
    PromocionRegistroInvalidoError,
    PromocionService,
    PromocionTipoDescuentoInvalidoError,
    PromocionValorInvalidoError,
)

# CU10 usa temporalmente get_current_admin porque el catalogo de permisos
# aun no incluye GESTIONAR_PROMOCIONES. Deuda tecnica: incorporar esa funcion
# al catalogo si el equipo decide ampliar la seguridad granular.


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


def _productos_response(
    promocion_id: int, productos
) -> PromocionProductosResponse:
    return PromocionProductosResponse(
        promocion_id=promocion_id,
        total=len(productos),
        productos=productos,
    )


router = APIRouter(prefix="/promociones", tags=["Promociones"])


@router.get("", response_model=list[PromocionResponse])
def listar_promociones(
    buscar: str | None = None,
    estado: bool | None = None,
    tipo_descuento: str | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    return PromocionService.listar(
        db,
        buscar=buscar,
        estado=estado,
        tipo_descuento=tipo_descuento,
    )


@router.post(
    "",
    response_model=PromocionResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_promocion(
    datos: PromocionCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return PromocionService.crear(db, datos, admin)
    except PromocionNombreInvalidoError:
        raise _error("El nombre de la promocion no es valido")
    except PromocionTipoDescuentoInvalidoError:
        raise _error("El tipo de descuento debe ser PORCENTAJE o MONTO")
    except PromocionValorInvalidoError:
        raise _error("El valor del descuento no es valido")
    except PromocionFechasInvalidasError:
        raise _error("La fecha de inicio no puede ser posterior a la fecha de fin")
    except PromocionRegistroInvalidoError:
        raise _error("No fue posible registrar la promocion")


@router.get("/{promocion_id}", response_model=PromocionDetalleResponse)
def obtener_promocion(
    promocion_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    detalle = PromocionService.obtener_detalle(db, promocion_id)
    if detalle is None:
        raise _error("Promocion no encontrada", status.HTTP_404_NOT_FOUND)
    return detalle


@router.patch("/{promocion_id}", response_model=PromocionResponse)
def actualizar_promocion(
    promocion_id: int,
    datos: PromocionUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return PromocionService.actualizar(db, promocion_id, datos, admin)
    except PromocionNoEncontradaError:
        raise _error("Promocion no encontrada", status.HTTP_404_NOT_FOUND)
    except PromocionNombreInvalidoError:
        raise _error("El nombre de la promocion no es valido")
    except PromocionTipoDescuentoInvalidoError:
        raise _error("El tipo de descuento debe ser PORCENTAJE o MONTO")
    except PromocionValorInvalidoError:
        raise _error("El valor del descuento no es valido")
    except PromocionFechasInvalidasError:
        raise _error("La fecha de inicio no puede ser posterior a la fecha de fin")
    except PromocionRegistroInvalidoError:
        raise _error("No fue posible actualizar la promocion")


@router.patch("/{promocion_id}/estado", response_model=PromocionResponse)
def cambiar_estado_promocion(
    promocion_id: int,
    datos: PromocionEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        return PromocionService.cambiar_estado(db, promocion_id, datos, admin)
    except PromocionNoEncontradaError:
        raise _error("Promocion no encontrada", status.HTTP_404_NOT_FOUND)
    except PromocionRegistroInvalidoError:
        raise _error("No fue posible actualizar el estado de la promocion")


@router.get(
    "/{promocion_id}/productos", response_model=PromocionProductosResponse
)
def listar_productos_promocion(
    promocion_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(get_current_admin),
):
    productos = PromocionService.listar_productos(db, promocion_id)
    if productos is None:
        raise _error("Promocion no encontrada", status.HTTP_404_NOT_FOUND)
    return _productos_response(promocion_id, productos)


@router.put(
    "/{promocion_id}/productos", response_model=PromocionProductosResponse
)
def reemplazar_productos_promocion(
    promocion_id: int,
    datos: PromocionProductosUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin),
):
    try:
        productos = PromocionService.reemplazar_productos(
            db, promocion_id, datos.producto_ids, admin
        )
    except PromocionNoEncontradaError:
        raise _error("Promocion no encontrada", status.HTTP_404_NOT_FOUND)
    except AsignacionProductoNoEncontradoError:
        raise _error("Uno o mas productos no existen", status.HTTP_404_NOT_FOUND)
    except AsignacionProductoInactivoError:
        raise _error(
            "Solo se pueden asignar productos activos",
            status.HTTP_409_CONFLICT,
        )
    except AsignacionRegistroInvalidoError:
        raise _error("No fue posible actualizar los productos de la promocion")
    return _productos_response(promocion_id, productos)
