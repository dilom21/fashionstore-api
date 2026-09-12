from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.schemas.schemas import (
    CatalogoFiltrosResponse,
    CategoriaCreate,
    CategoriaEstadoUpdate,
    CategoriaResponse,
    CategoriaUpdate,
    ColorCreate,
    ColorEstadoUpdate,
    ColorResponse,
    ColorUpdate,
    DisponibilidadProductoResponse,
    ProductoCreate,
    ProductoDetalleResponse,
    ProductoEstadoUpdate,
    ProductoResponse,
    ProductoUpdate,
    RecursoProductoAdminResponse,
    RecursoProductoCreate,
    RecursoProductoEstadoUpdate,
    RecursoProductoUpdate,
    TallaCreate,
    TallaEstadoUpdate,
    TallaResponse,
    TallaUpdate,
    VarianteAdminResponse,
    VarianteCreate,
    VarianteEstadoUpdate,
    VarianteUpdate,
)
from app.modules.catalogo.services.service import (
    CatalogoService,
    CategoriaEnUsoError,
    CategoriaNombreDuplicadoError,
    CategoriaNombreInvalidoError,
    CategoriaNoEncontradaError,
    CategoriaService,
    ColorEnUsoError,
    ColorNombreDuplicadoError,
    ColorNombreInvalidoError,
    ColorNoEncontradoError,
    ColorService,
    ProductoCategoriaInactivaError,
    ProductoCategoriaInexistenteError,
    ProductoNombreInvalidoError,
    ProductoNoEncontradoError,
    ProductoPrecioInvalidoError,
    ProductoRegistroInvalidoError,
    ProductoService,
    RecursoColorInactivoError,
    RecursoColorNoEncontradoError,
    RecursoNoActivoError,
    RecursoNoEncontradoError,
    RecursoPrincipalConflictoError,
    RecursoProductoNoEncontradoError,
    RecursoProductoService,
    RecursoTipoInvalidoError,
    TallaEnUsoError,
    TallaNombreDuplicadoError,
    TallaNombreInvalidoError,
    TallaNoEncontradaError,
    TallaService,
    VarianteColorInactivoError,
    VarianteColorNoEncontradoError,
    VarianteCombinacionDuplicadaError,
    VarianteNoEncontradaError,
    VarianteProductoInactivoError,
    VarianteProductoNoEncontradoError,
    VarianteProductoService,
    VarianteRegistroInvalidoError,
    VarianteSkuDuplicadoError,
    VarianteSkuInvalidoError,
    VarianteTallaInactivaError,
    VarianteTallaNoEncontradaError,
)

FUNCION = "GESTIONAR_PRODUCTOS"


def _perm(accion: str):
    return require_permission(FUNCION, accion)


def _error(detalle: str, codigo: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=codigo, detail=detalle)


# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------

categorias_router = APIRouter(prefix="/categorias", tags=["Categorias"])


@categorias_router.get("", response_model=list[CategoriaResponse])
def listar_categorias(db: Session = Depends(get_db)):
    """Contrato publico de catalogo: categorias activas."""
    return CategoriaService.listar_categorias(db)


@categorias_router.get("/admin", response_model=list[CategoriaResponse])
def listar_categorias_admin(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    return CategoriaService.listar_admin(db, buscar=buscar, estado=estado)


@categorias_router.get("/admin/{categoria_id}", response_model=CategoriaResponse)
def obtener_categoria_admin(
    categoria_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    categoria = CategoriaService.obtener_admin(db, categoria_id)
    if categoria is None:
        raise _error("Categoria no encontrada", status.HTTP_404_NOT_FOUND)
    return categoria


@categorias_router.post(
    "",
    response_model=CategoriaResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_categoria(
    datos: CategoriaCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return CategoriaService.crear(db, datos, admin)
    except CategoriaNombreInvalidoError:
        raise _error("El nombre de la categoria no es valido")
    except CategoriaNombreDuplicadoError:
        raise _error(
            "Ya existe una categoria con ese nombre", status.HTTP_409_CONFLICT
        )


@categorias_router.patch("/{categoria_id}", response_model=CategoriaResponse)
def actualizar_categoria(
    categoria_id: int,
    datos: CategoriaUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return CategoriaService.actualizar(db, categoria_id, datos, admin)
    except CategoriaNoEncontradaError:
        raise _error("Categoria no encontrada", status.HTTP_404_NOT_FOUND)
    except CategoriaNombreInvalidoError:
        raise _error("El nombre de la categoria no es valido")
    except CategoriaNombreDuplicadoError:
        raise _error(
            "Ya existe una categoria con ese nombre", status.HTTP_409_CONFLICT
        )


@categorias_router.patch("/{categoria_id}/estado", response_model=CategoriaResponse)
def cambiar_estado_categoria(
    categoria_id: int,
    datos: CategoriaEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return CategoriaService.cambiar_estado(db, categoria_id, datos, admin)
    except CategoriaNoEncontradaError:
        raise _error("Categoria no encontrada", status.HTTP_404_NOT_FOUND)
    except CategoriaEnUsoError:
        raise _error(
            "La categoria no puede deshabilitarse: tiene productos activos",
            status.HTTP_409_CONFLICT,
        )


@categorias_router.get("/{categoria_id}", response_model=CategoriaResponse)
def obtener_categoria(categoria_id: int, db: Session = Depends(get_db)):
    """Contrato publico de catalogo: detalle de categoria activa."""
    categoria = CategoriaService.obtener_categoria(db, categoria_id)
    if categoria is None:
        raise _error("Categoria no encontrada", status.HTTP_404_NOT_FOUND)
    return categoria


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

productos_router = APIRouter(prefix="/productos", tags=["Productos"])


@productos_router.get("", response_model=list[ProductoResponse])
def listar_productos(
    buscar: str | None = None,
    categoria_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    coleccion_id: int | None = Query(default=None, gt=0),
    sucursal_id: int | None = Query(default=None, gt=0),
    con_stock: bool | None = None,
    db: Session = Depends(get_db),
):
    """Contrato publico de catalogo CU09: productos activos filtrables."""
    return ProductoService.listar_productos(
        db,
        buscar=buscar,
        categoria_id=categoria_id,
        talla_id=talla_id,
        color_id=color_id,
        temporada_id=temporada_id,
        coleccion_id=coleccion_id,
        sucursal_id=sucursal_id,
        con_stock=con_stock,
    )


@productos_router.get("/admin", response_model=list[ProductoResponse])
def listar_productos_admin(
    buscar: str | None = None,
    categoria_id: int | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    return ProductoService.listar_admin(
        db,
        buscar=buscar,
        categoria_id=categoria_id,
        estado=estado,
    )


@productos_router.get("/admin/{producto_id}", response_model=ProductoResponse)
def obtener_producto_admin(
    producto_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    producto = ProductoService.obtener_admin(db, producto_id)
    if producto is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    return producto


@productos_router.post(
    "",
    response_model=ProductoResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_producto(
    datos: ProductoCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return ProductoService.crear(db, datos, admin)
    except ProductoNombreInvalidoError:
        raise _error("El nombre del producto no es valido")
    except ProductoPrecioInvalidoError:
        raise _error("El precio del producto debe ser mayor a 0")
    except ProductoCategoriaInexistenteError:
        raise _error("La categoria no existe", status.HTTP_404_NOT_FOUND)
    except ProductoCategoriaInactivaError:
        raise _error(
            "El producto solo puede asociarse a una categoria activa",
            status.HTTP_409_CONFLICT,
        )
    except ProductoRegistroInvalidoError:
        raise _error("No fue posible registrar el producto")


@productos_router.get(
    "/{producto_id}/disponibilidad",
    response_model=DisponibilidadProductoResponse,
)
def obtener_disponibilidad(
    producto_id: int,
    sucursal_id: int | None = Query(default=None, gt=0),
    talla_id: int | None = Query(default=None, gt=0),
    color_id: int | None = Query(default=None, gt=0),
    temporada_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    disponibilidad = ProductoService.obtener_disponibilidad(
        db,
        producto_id,
        sucursal_id=sucursal_id,
        talla_id=talla_id,
        color_id=color_id,
        temporada_id=temporada_id,
    )
    if disponibilidad is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    return disponibilidad


@productos_router.get(
    "/{producto_id}/variantes",
    response_model=list[VarianteAdminResponse],
)
def listar_variantes(
    producto_id: int,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    variantes = VarianteProductoService.listar_por_producto(
        db, producto_id, estado=estado
    )
    if variantes is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    return variantes


@productos_router.post(
    "/{producto_id}/variantes",
    response_model=VarianteAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_variante(
    producto_id: int,
    datos: VarianteCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return VarianteProductoService.crear(db, producto_id, datos, admin)
    except VarianteProductoNoEncontradoError:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    except VarianteProductoInactivoError:
        raise _error(
            "No se pueden crear variantes activas para un producto inactivo",
            status.HTTP_409_CONFLICT,
        )
    except VarianteTallaNoEncontradaError:
        raise _error("La talla no existe", status.HTTP_404_NOT_FOUND)
    except VarianteTallaInactivaError:
        raise _error("La talla no esta activa", status.HTTP_409_CONFLICT)
    except VarianteColorNoEncontradoError:
        raise _error("El color no existe", status.HTTP_404_NOT_FOUND)
    except VarianteColorInactivoError:
        raise _error("El color no esta activo", status.HTTP_409_CONFLICT)
    except VarianteSkuInvalidoError:
        raise _error("El SKU de la variante no es valido")
    except VarianteCombinacionDuplicadaError:
        raise _error(
            "Ya existe una variante para ese producto, talla y color",
            status.HTTP_409_CONFLICT,
        )
    except VarianteSkuDuplicadoError:
        raise _error(
            "Ya existe una variante con ese SKU", status.HTTP_409_CONFLICT
        )
    except VarianteRegistroInvalidoError:
        raise _error("No fue posible registrar la variante")


@productos_router.get(
    "/{producto_id}/recursos",
    response_model=list[RecursoProductoAdminResponse],
)
def listar_recursos(
    producto_id: int,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    recursos = RecursoProductoService.listar_por_producto(
        db, producto_id, estado=estado
    )
    if recursos is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    return recursos


@productos_router.post(
    "/{producto_id}/recursos",
    response_model=RecursoProductoAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_recurso(
    producto_id: int,
    datos: RecursoProductoCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return RecursoProductoService.crear(db, producto_id, datos, admin)
    except RecursoProductoNoEncontradoError:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    except RecursoColorNoEncontradoError:
        raise _error("El color no existe", status.HTTP_404_NOT_FOUND)
    except RecursoColorInactivoError:
        raise _error("El color no esta activo", status.HTTP_409_CONFLICT)
    except RecursoTipoInvalidoError:
        raise _error("El tipo de recurso no es valido")
    except RecursoPrincipalConflictoError:
        raise _error(
            "No fue posible registrar el recurso principal",
            status.HTTP_409_CONFLICT,
        )


@productos_router.patch("/{producto_id}/estado", response_model=ProductoResponse)
def cambiar_estado_producto(
    producto_id: int,
    datos: ProductoEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return ProductoService.cambiar_estado(db, producto_id, datos, admin)
    except ProductoNoEncontradoError:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    except ProductoRegistroInvalidoError:
        raise _error("No fue posible actualizar el estado del producto")


@productos_router.patch("/{producto_id}", response_model=ProductoResponse)
def actualizar_producto(
    producto_id: int,
    datos: ProductoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return ProductoService.actualizar(db, producto_id, datos, admin)
    except ProductoNoEncontradoError:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    except ProductoNombreInvalidoError:
        raise _error("El nombre del producto no es valido")
    except ProductoPrecioInvalidoError:
        raise _error("El precio del producto debe ser mayor a 0")
    except ProductoCategoriaInexistenteError:
        raise _error("La categoria no existe", status.HTTP_404_NOT_FOUND)
    except ProductoCategoriaInactivaError:
        raise _error(
            "No se puede mover el producto a una categoria inactiva",
            status.HTTP_409_CONFLICT,
        )
    except ProductoRegistroInvalidoError:
        raise _error("No fue posible actualizar el producto")


@productos_router.get("/{producto_id}", response_model=ProductoDetalleResponse)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    producto = ProductoService.obtener_producto(db, producto_id)
    if producto is None:
        raise _error("Producto no encontrado", status.HTTP_404_NOT_FOUND)
    return producto


# ---------------------------------------------------------------------------
# Variantes (por id)
# ---------------------------------------------------------------------------

variantes_router = APIRouter(prefix="/variantes", tags=["Variantes"])


@variantes_router.patch("/{variante_id}", response_model=VarianteAdminResponse)
def actualizar_variante(
    variante_id: int,
    datos: VarianteUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return VarianteProductoService.actualizar(db, variante_id, datos, admin)
    except VarianteNoEncontradaError:
        raise _error("Variante no encontrada", status.HTTP_404_NOT_FOUND)
    except VarianteProductoInactivoError:
        raise _error(
            "No se puede activar una variante de un producto inactivo",
            status.HTTP_409_CONFLICT,
        )
    except VarianteTallaNoEncontradaError:
        raise _error("La talla no existe", status.HTTP_404_NOT_FOUND)
    except VarianteTallaInactivaError:
        raise _error("La talla no esta activa", status.HTTP_409_CONFLICT)
    except VarianteColorNoEncontradoError:
        raise _error("El color no existe", status.HTTP_404_NOT_FOUND)
    except VarianteColorInactivoError:
        raise _error("El color no esta activo", status.HTTP_409_CONFLICT)
    except VarianteSkuInvalidoError:
        raise _error("El SKU de la variante no es valido")
    except VarianteCombinacionDuplicadaError:
        raise _error(
            "Ya existe una variante para ese producto, talla y color",
            status.HTTP_409_CONFLICT,
        )
    except VarianteSkuDuplicadoError:
        raise _error(
            "Ya existe una variante con ese SKU", status.HTTP_409_CONFLICT
        )
    except VarianteRegistroInvalidoError:
        raise _error("No fue posible actualizar la variante")


@variantes_router.patch(
    "/{variante_id}/estado", response_model=VarianteAdminResponse
)
def cambiar_estado_variante(
    variante_id: int,
    datos: VarianteEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return VarianteProductoService.cambiar_estado(
            db, variante_id, datos, admin
        )
    except VarianteNoEncontradaError:
        raise _error("Variante no encontrada", status.HTTP_404_NOT_FOUND)
    except VarianteProductoInactivoError:
        raise _error(
            "No se puede activar una variante de un producto inactivo",
            status.HTTP_409_CONFLICT,
        )
    except VarianteRegistroInvalidoError:
        raise _error("No fue posible actualizar el estado de la variante")


# ---------------------------------------------------------------------------
# Recursos (por id)
# ---------------------------------------------------------------------------

recursos_router = APIRouter(prefix="/recursos-producto", tags=["Recursos de producto"])


@recursos_router.patch(
    "/{recurso_id}", response_model=RecursoProductoAdminResponse
)
def actualizar_recurso(
    recurso_id: int,
    datos: RecursoProductoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return RecursoProductoService.actualizar(db, recurso_id, datos, admin)
    except RecursoNoEncontradoError:
        raise _error("Recurso no encontrado", status.HTTP_404_NOT_FOUND)
    except RecursoColorNoEncontradoError:
        raise _error("El color no existe", status.HTTP_404_NOT_FOUND)
    except RecursoColorInactivoError:
        raise _error("El color no esta activo", status.HTTP_409_CONFLICT)
    except RecursoTipoInvalidoError:
        raise _error("El tipo de recurso no es valido")
    except RecursoPrincipalConflictoError:
        raise _error(
            "No fue posible actualizar el recurso principal",
            status.HTTP_409_CONFLICT,
        )


@recursos_router.patch(
    "/{recurso_id}/estado", response_model=RecursoProductoAdminResponse
)
def cambiar_estado_recurso(
    recurso_id: int,
    datos: RecursoProductoEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return RecursoProductoService.cambiar_estado(
            db, recurso_id, datos, admin
        )
    except RecursoNoEncontradoError:
        raise _error("Recurso no encontrado", status.HTTP_404_NOT_FOUND)
    except RecursoPrincipalConflictoError:
        raise _error(
            "No fue posible actualizar el estado del recurso",
            status.HTTP_409_CONFLICT,
        )


@recursos_router.patch(
    "/{recurso_id}/principal", response_model=RecursoProductoAdminResponse
)
def marcar_recurso_principal(
    recurso_id: int,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return RecursoProductoService.marcar_principal(db, recurso_id, admin)
    except RecursoNoEncontradoError:
        raise _error("Recurso no encontrado", status.HTTP_404_NOT_FOUND)
    except RecursoNoActivoError:
        raise _error("El recurso debe estar activo para ser principal")
    except RecursoPrincipalConflictoError:
        raise _error(
            "No fue posible marcar el recurso como principal",
            status.HTTP_409_CONFLICT,
        )


# ---------------------------------------------------------------------------
# Tallas
# ---------------------------------------------------------------------------

tallas_router = APIRouter(prefix="/tallas", tags=["Tallas"])


@tallas_router.get("", response_model=list[TallaResponse])
def listar_tallas(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    return TallaService.listar(db, buscar=buscar, estado=estado)


@tallas_router.post(
    "",
    response_model=TallaResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_talla(
    datos: TallaCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return TallaService.crear(db, datos, admin)
    except TallaNombreInvalidoError:
        raise _error("El nombre de la talla no es valido")
    except TallaNombreDuplicadoError:
        raise _error(
            "Ya existe una talla con ese nombre", status.HTTP_409_CONFLICT
        )


@tallas_router.get("/{talla_id}", response_model=TallaResponse)
def obtener_talla(
    talla_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    talla = TallaService.obtener(db, talla_id)
    if talla is None:
        raise _error("Talla no encontrada", status.HTTP_404_NOT_FOUND)
    return talla


@tallas_router.patch("/{talla_id}", response_model=TallaResponse)
def actualizar_talla(
    talla_id: int,
    datos: TallaUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return TallaService.actualizar(db, talla_id, datos, admin)
    except TallaNoEncontradaError:
        raise _error("Talla no encontrada", status.HTTP_404_NOT_FOUND)
    except TallaNombreInvalidoError:
        raise _error("El nombre de la talla no es valido")
    except TallaNombreDuplicadoError:
        raise _error(
            "Ya existe una talla con ese nombre", status.HTTP_409_CONFLICT
        )


@tallas_router.patch("/{talla_id}/estado", response_model=TallaResponse)
def cambiar_estado_talla(
    talla_id: int,
    datos: TallaEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return TallaService.cambiar_estado(db, talla_id, datos, admin)
    except TallaNoEncontradaError:
        raise _error("Talla no encontrada", status.HTTP_404_NOT_FOUND)
    except TallaEnUsoError:
        raise _error(
            "La talla no puede deshabilitarse: tiene variantes activas",
            status.HTTP_409_CONFLICT,
        )


# ---------------------------------------------------------------------------
# Colores
# ---------------------------------------------------------------------------

colores_router = APIRouter(prefix="/colores", tags=["Colores"])


@colores_router.get("", response_model=list[ColorResponse])
def listar_colores(
    buscar: str | None = None,
    estado: bool | None = None,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    return ColorService.listar(db, buscar=buscar, estado=estado)


@colores_router.post(
    "",
    response_model=ColorResponse,
    status_code=status.HTTP_201_CREATED,
)
def crear_color(
    datos: ColorCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("CREAR")),
):
    try:
        return ColorService.crear(db, datos, admin)
    except ColorNombreInvalidoError:
        raise _error("El nombre del color no es valido")
    except ColorNombreDuplicadoError:
        raise _error(
            "Ya existe un color con ese nombre", status.HTTP_409_CONFLICT
        )


@colores_router.get("/{color_id}", response_model=ColorResponse)
def obtener_color(
    color_id: int,
    db: Session = Depends(get_db),
    _admin: Usuario = Depends(_perm("CONSULTAR")),
):
    color = ColorService.obtener(db, color_id)
    if color is None:
        raise _error("Color no encontrado", status.HTTP_404_NOT_FOUND)
    return color


@colores_router.patch("/{color_id}", response_model=ColorResponse)
def actualizar_color(
    color_id: int,
    datos: ColorUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("EDITAR")),
):
    try:
        return ColorService.actualizar(db, color_id, datos, admin)
    except ColorNoEncontradoError:
        raise _error("Color no encontrado", status.HTTP_404_NOT_FOUND)
    except ColorNombreInvalidoError:
        raise _error("El nombre del color no es valido")
    except ColorNombreDuplicadoError:
        raise _error(
            "Ya existe un color con ese nombre", status.HTTP_409_CONFLICT
        )


@colores_router.patch("/{color_id}/estado", response_model=ColorResponse)
def cambiar_estado_color(
    color_id: int,
    datos: ColorEstadoUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(_perm("ELIMINAR")),
):
    try:
        return ColorService.cambiar_estado(db, color_id, datos, admin)
    except ColorNoEncontradoError:
        raise _error("Color no encontrado", status.HTTP_404_NOT_FOUND)
    except ColorEnUsoError:
        raise _error(
            "El color no puede deshabilitarse: tiene variantes activas",
            status.HTTP_409_CONFLICT,
        )


# ---------------------------------------------------------------------------
# Catalogo publico (CU09)
# ---------------------------------------------------------------------------

catalogo_router = APIRouter(prefix="/catalogo", tags=["Catalogo"])


@catalogo_router.get("/filtros", response_model=CatalogoFiltrosResponse)
def obtener_filtros_catalogo(db: Session = Depends(get_db)):
    """Filtros publicos del catalogo: solo opciones activas."""
    return CatalogoService.obtener_filtros(db)


# ---------------------------------------------------------------------------
# Router del modulo catalogo (productos + categorias + tallas + colores)
# ---------------------------------------------------------------------------

router = APIRouter()
router.include_router(productos_router)
router.include_router(categorias_router)
router.include_router(variantes_router)
router.include_router(recursos_router)
router.include_router(tallas_router)
router.include_router(colores_router)
router.include_router(catalogo_router)
