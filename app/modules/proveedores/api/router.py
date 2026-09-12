from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.proveedores.schemas.schemas import (
    ProveedorCreate,
    ProveedorDetalleResponse,
    ProveedorEstadoUpdate,
    ProveedorProductosResponse,
    ProveedorProductosUpdate,
    ProveedorResponse,
    ProveedorUpdate,
)
from app.modules.proveedores.services.service import (
    ProveedorCorreoInvalidoError,
    ProveedorDependenciaActivaError,
    ProveedorNitDuplicadoError,
    ProveedorNoEncontradoError,
    ProveedorProductoCostoInvalidoError,
    ProveedorProductoInactivoError,
    ProveedorProductoNoEncontradoError,
    ProveedorProductoRegistroInvalidoError,
    ProveedorRazonSocialInvalidaError,
    ProveedorRegistroInvalidoError,
    ProveedorService,
)

FUNCION = "GESTIONAR_PROVEEDORES"


def _perm(accion: str):
    return require_permission(FUNCION, accion)


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


router = APIRouter(prefix="/proveedores", tags=["Proveedores"])


@router.get("", response_model=list[ProveedorResponse])
def listar_proveedores(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    return ProveedorService.listar(db, buscar=buscar, estado=estado)


@router.post(
    "",
    response_model=ProveedorResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_proveedor(
    datos: ProveedorCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("CREAR")),
):
    try:
        return ProveedorService.crear(db, datos, usuario)
    except ProveedorRazonSocialInvalidaError:
        raise _error("La razon social del proveedor no es valida")
    except ProveedorCorreoInvalidoError:
        raise _error("El correo del proveedor no es valido")
    except ProveedorNitDuplicadoError:
        raise _error(
            "Ya existe un proveedor con ese NIT", status.HTTP_409_CONFLICT
        )


@router.get("/{proveedor_id}", response_model=ProveedorDetalleResponse)
def obtener_proveedor(
    proveedor_id: int,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    detalle = ProveedorService.obtener_detalle(db, proveedor_id)
    if detalle is None:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    return detalle


@router.patch("/{proveedor_id}", response_model=ProveedorResponse)
def actualizar_proveedor(
    proveedor_id: int,
    datos: ProveedorUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return ProveedorService.actualizar(db, proveedor_id, datos, usuario)
    except ProveedorNoEncontradoError:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    except ProveedorRazonSocialInvalidaError:
        raise _error("La razon social del proveedor no es valida")
    except ProveedorCorreoInvalidoError:
        raise _error("El correo del proveedor no es valido")
    except ProveedorNitDuplicadoError:
        raise _error(
            "Ya existe un proveedor con ese NIT", status.HTTP_409_CONFLICT
        )
    except ProveedorRegistroInvalidoError:
        raise _error(
            "No fue posible actualizar el proveedor", status.HTTP_409_CONFLICT
        )


@router.patch("/{proveedor_id}/estado", response_model=ProveedorResponse)
def cambiar_estado_proveedor(
    proveedor_id: int,
    datos: ProveedorEstadoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return ProveedorService.cambiar_estado(db, proveedor_id, datos, usuario)
    except ProveedorNoEncontradoError:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    except ProveedorDependenciaActivaError:
        raise _error(
            "El proveedor tiene ordenes de compra activas y no puede "
            "deshabilitarse",
            status.HTTP_409_CONFLICT,
        )
    except ProveedorRegistroInvalidoError:
        raise _error(
            "No fue posible actualizar el estado del proveedor",
            status.HTTP_409_CONFLICT,
        )


@router.get(
    "/{proveedor_id}/productos", response_model=ProveedorProductosResponse
)
def listar_productos_proveedor(
    proveedor_id: int,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(_perm("CONSULTAR")),
):
    productos = ProveedorService.listar_productos(db, proveedor_id)
    if productos is None:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    return productos


@router.put(
    "/{proveedor_id}/productos", response_model=ProveedorProductosResponse
)
def reemplazar_productos_proveedor(
    proveedor_id: int,
    datos: ProveedorProductosUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return ProveedorService.reemplazar_productos(
            db, proveedor_id, datos, usuario
        )
    except ProveedorNoEncontradoError:
        raise _error("Proveedor no encontrado", status.HTTP_404_NOT_FOUND)
    except ProveedorProductoNoEncontradoError:
        raise _error(
            "Uno o mas productos no existen", status.HTTP_404_NOT_FOUND
        )
    except ProveedorProductoInactivoError:
        raise _error(
            "Solo se pueden asociar productos activos a nuevas relaciones",
            status.HTTP_409_CONFLICT,
        )
    except ProveedorProductoCostoInvalidoError:
        raise _error("El costo de referencia no puede ser negativo")
    except ProveedorProductoRegistroInvalidoError:
        raise _error(
            "No fue posible actualizar los productos del proveedor",
            status.HTTP_409_CONFLICT,
        )
