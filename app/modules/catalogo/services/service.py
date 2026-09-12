from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.catalogo.models.models import (
    Categoria,
    Color,
    Producto,
    RecursoProducto,
    Talla,
    VarianteProducto,
)
from app.modules.catalogo.repositories.repository import (
    CategoriaRepository,
    ColorRepository,
    ProductoRepository,
    RecursoProductoRepository,
    TallaRepository,
    VarianteProductoRepository,
    registrar_evento_bitacora,
)
from app.modules.catalogo.schemas.schemas import (
    CatalogoFiltrosResponse,
    CategoriaCreate,
    CategoriaEstadoUpdate,
    CategoriaUpdate,
    ColeccionFiltroResponse,
    ColorCreate,
    ColorEstadoUpdate,
    ColorUpdate,
    DisponibilidadProductoResponse,
    DisponibilidadSucursalResponse,
    DisponibilidadVarianteResponse,
    FiltroOpcionResponse,
    ProductoCreate,
    ProductoEstadoUpdate,
    ProductoUpdate,
    RecursoProductoCreate,
    RecursoProductoEstadoUpdate,
    RecursoProductoUpdate,
    SucursalFiltroResponse,
    TallaCreate,
    TallaEstadoUpdate,
    TallaUpdate,
    VarianteCreate,
    VarianteEstadoUpdate,
    VarianteUpdate,
)
from app.modules.inventario.services.service import InventarioService
from app.modules.sucursales.repositories.repository import SucursalRepository
from app.modules.temporadas_colecciones.repositories.repository import (
    ColeccionRepository,
    TemporadaRepository,
)

ACCION_CREAR = "CREAR"
ACCION_MODIFICAR = "MODIFICAR"

ENTIDAD_CATEGORIA = "categoria"
ENTIDAD_TALLA = "talla"
ENTIDAD_COLOR = "color"
ENTIDAD_RECURSO_PRODUCTO = "recurso_producto"


# ---------------------------------------------------------------------------
# Errores tipados de CU07
# ---------------------------------------------------------------------------


class CategoriaNoEncontradaError(Exception):
    pass


class CategoriaNombreInvalidoError(Exception):
    pass


class CategoriaNombreDuplicadoError(Exception):
    pass


class CategoriaEnUsoError(Exception):
    """La categoria tiene productos activos y no puede deshabilitarse."""


class ProductoNoEncontradoError(Exception):
    pass


class ProductoNombreInvalidoError(Exception):
    pass


class ProductoPrecioInvalidoError(Exception):
    pass


class ProductoCategoriaInexistenteError(Exception):
    pass


class ProductoCategoriaInactivaError(Exception):
    pass


class ProductoRegistroInvalidoError(Exception):
    pass


class TallaNoEncontradaError(Exception):
    pass


class TallaNombreInvalidoError(Exception):
    pass


class TallaNombreDuplicadoError(Exception):
    pass


class TallaEnUsoError(Exception):
    """La talla tiene variantes activas y no puede deshabilitarse."""


class ColorNoEncontradoError(Exception):
    pass


class ColorNombreInvalidoError(Exception):
    pass


class ColorNombreDuplicadoError(Exception):
    pass


class ColorEnUsoError(Exception):
    """El color tiene variantes activas y no puede deshabilitarse."""


class VarianteNoEncontradaError(Exception):
    pass


class VarianteProductoNoEncontradoError(Exception):
    pass


class VarianteProductoInactivoError(Exception):
    pass


class VarianteTallaNoEncontradaError(Exception):
    pass


class VarianteTallaInactivaError(Exception):
    pass


class VarianteColorNoEncontradoError(Exception):
    pass


class VarianteColorInactivoError(Exception):
    pass


class VarianteSkuInvalidoError(Exception):
    pass


class VarianteSkuDuplicadoError(Exception):
    pass


class VarianteCombinacionDuplicadaError(Exception):
    pass


class VarianteRegistroInvalidoError(Exception):
    pass


class RecursoNoEncontradoError(Exception):
    pass


class RecursoProductoNoEncontradoError(Exception):
    pass


class RecursoColorNoEncontradoError(Exception):
    pass


class RecursoColorInactivoError(Exception):
    pass


class RecursoTipoInvalidoError(Exception):
    pass


class RecursoNoActivoError(Exception):
    pass


class RecursoPrincipalConflictoError(Exception):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _limpiar_texto(valor: str | None) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split())


def _normalizar_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio or None


def _normalizar_sku(valor: str | None) -> str:
    if valor is None:
        return ""
    return str(valor).strip().upper()


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------


class CategoriaService:
    @staticmethod
    def listar_categorias(db: Session) -> list[Categoria]:
        """Contrato publico de catalogo: solo categorias activas."""
        return CategoriaRepository.listar_activas(db)

    @staticmethod
    def obtener_categoria(db: Session, categoria_id: int) -> Categoria | None:
        """Contrato publico de catalogo: solo categoria activa."""
        return CategoriaRepository.obtener_activa_por_id(db, categoria_id)

    @staticmethod
    def listar_admin(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Categoria]:
        return CategoriaRepository.listar(db, buscar=buscar, estado=estado)

    @staticmethod
    def obtener_admin(db: Session, categoria_id: int) -> Categoria | None:
        return CategoriaRepository.obtener_por_id(db, categoria_id)

    @staticmethod
    def crear(db: Session, datos: CategoriaCreate, admin: Usuario) -> Categoria:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise CategoriaNombreInvalidoError()

        if CategoriaRepository.buscar_por_nombre_normalizado(db, nombre) is not None:
            raise CategoriaNombreDuplicadoError()

        descripcion = _normalizar_opcional(datos.descripcion)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            categoria = CategoriaRepository.crear(
                db, nombre=nombre, descripcion=descripcion
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_CATEGORIA,
                descripcion=f"Categoria creada: {categoria.nombre} (id {categoria.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CategoriaNombreDuplicadoError() from exc

        categoria = CategoriaRepository.obtener_por_id(db, categoria.id)
        if categoria is None:
            raise CategoriaNoEncontradaError()
        return categoria

    @staticmethod
    def actualizar(
        db: Session,
        categoria_id: int,
        datos: CategoriaUpdate,
        admin: Usuario,
    ) -> Categoria:
        categoria = CategoriaRepository.obtener_por_id(db, categoria_id)
        if categoria is None:
            raise CategoriaNoEncontradaError()

        hay_cambios = False

        if datos.nombre is not None:
            nuevo_nombre = _limpiar_texto(datos.nombre)
            if not nuevo_nombre:
                raise CategoriaNombreInvalidoError()
            if nuevo_nombre != categoria.nombre:
                existente = CategoriaRepository.buscar_por_nombre_normalizado(
                    db, nuevo_nombre
                )
                if existente is not None and existente.id != categoria.id:
                    raise CategoriaNombreDuplicadoError()
                categoria.nombre = nuevo_nombre
                hay_cambios = True

        if "descripcion" in datos.model_fields_set:
            descripcion = _normalizar_opcional(datos.descripcion)
            if descripcion != categoria.descripcion:
                categoria.descripcion = descripcion
                hay_cambios = True

        if not hay_cambios:
            return categoria

        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_CATEGORIA,
                descripcion=f"Categoria actualizada: {categoria.nombre} (id {categoria.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CategoriaNombreDuplicadoError() from exc

        categoria = CategoriaRepository.obtener_por_id(db, categoria.id)
        if categoria is None:
            raise CategoriaNoEncontradaError()
        return categoria

    @staticmethod
    def cambiar_estado(
        db: Session,
        categoria_id: int,
        datos: CategoriaEstadoUpdate,
        admin: Usuario,
    ) -> Categoria:
        categoria = CategoriaRepository.obtener_por_id(db, categoria_id)
        if categoria is None:
            raise CategoriaNoEncontradaError()

        estado = datos.estado
        if categoria.estado == estado:
            return categoria

        if not estado:
            if CategoriaRepository.contar_productos_activos(db, categoria.id) > 0:
                raise CategoriaEnUsoError()

        categoria.estado = estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_CATEGORIA,
                descripcion=(
                    f"Categoria {'habilitada' if estado else 'deshabilitada'}: "
                    f"{categoria.nombre} (id {categoria.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CategoriaEnUsoError() from exc

        categoria = CategoriaRepository.obtener_por_id(db, categoria.id)
        if categoria is None:
            raise CategoriaNoEncontradaError()
        return categoria


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------


class ProductoService:
    @staticmethod
    def listar_productos(
        db: Session,
        buscar: str | None = None,
        categoria_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
        coleccion_id: int | None = None,
        sucursal_id: int | None = None,
        con_stock: bool | None = None,
    ) -> list[Producto]:
        """Contrato publico de catalogo CU09: productos activos filtrables."""
        return ProductoRepository.listar(
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

    @staticmethod
    def obtener_producto(db: Session, producto_id: int) -> Producto | None:
        return ProductoRepository.obtener_por_id(db, producto_id)

    @staticmethod
    def listar_admin(
        db: Session,
        buscar: str | None = None,
        categoria_id: int | None = None,
        estado: bool | None = None,
    ) -> list[Producto]:
        return ProductoRepository.listar_admin(
            db,
            buscar=buscar,
            categoria_id=categoria_id,
            estado=estado,
        )

    @staticmethod
    def obtener_admin(db: Session, producto_id: int) -> Producto | None:
        return ProductoRepository.obtener_admin_por_id(db, producto_id)

    @staticmethod
    def _validar_categoria_activa(db: Session, categoria_id: int) -> Categoria:
        categoria = CategoriaRepository.obtener_por_id(db, categoria_id)
        if categoria is None:
            raise ProductoCategoriaInexistenteError()
        if not categoria.estado:
            raise ProductoCategoriaInactivaError()
        return categoria

    @staticmethod
    def crear(db: Session, datos: ProductoCreate, admin: Usuario) -> Producto:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise ProductoNombreInvalidoError()

        if datos.precio is None or Decimal(datos.precio) <= 0:
            raise ProductoPrecioInvalidoError()

        ProductoService._validar_categoria_activa(db, datos.categoria_id)
        descripcion = _normalizar_opcional(datos.descripcion)

        try:
            _establecer_contexto_bitacora(db, admin.id)
            producto = ProductoRepository.crear(
                db,
                categoria_id=datos.categoria_id,
                nombre=nombre,
                descripcion=descripcion,
                precio=Decimal(datos.precio),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProductoRegistroInvalidoError() from exc

        producto = ProductoRepository.obtener_admin_por_id(db, producto.id)
        if producto is None:
            raise ProductoNoEncontradoError()
        return producto

    @staticmethod
    def actualizar(
        db: Session,
        producto_id: int,
        datos: ProductoUpdate,
        admin: Usuario,
    ) -> Producto:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError()

        hay_cambios = False

        if datos.categoria_id is not None and datos.categoria_id != producto.categoria_id:
            ProductoService._validar_categoria_activa(db, datos.categoria_id)
            producto.categoria_id = datos.categoria_id
            hay_cambios = True

        if datos.nombre is not None:
            nombre = _limpiar_texto(datos.nombre)
            if not nombre:
                raise ProductoNombreInvalidoError()
            if nombre != producto.nombre:
                producto.nombre = nombre
                hay_cambios = True

        if "descripcion" in datos.model_fields_set:
            descripcion = _normalizar_opcional(datos.descripcion)
            if descripcion != producto.descripcion:
                producto.descripcion = descripcion
                hay_cambios = True

        if datos.precio is not None:
            if Decimal(datos.precio) <= 0:
                raise ProductoPrecioInvalidoError()
            if Decimal(datos.precio) != producto.precio:
                producto.precio = Decimal(datos.precio)
                hay_cambios = True

        if not hay_cambios:
            return producto

        try:
            _establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProductoRegistroInvalidoError() from exc

        producto = ProductoRepository.obtener_admin_por_id(db, producto.id)
        if producto is None:
            raise ProductoNoEncontradoError()
        return producto

    @staticmethod
    def cambiar_estado(
        db: Session,
        producto_id: int,
        datos: ProductoEstadoUpdate,
        admin: Usuario,
    ) -> Producto:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError()

        if producto.estado == datos.estado:
            return producto

        producto.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProductoRegistroInvalidoError() from exc

        producto = ProductoRepository.obtener_admin_por_id(db, producto.id)
        if producto is None:
            raise ProductoNoEncontradoError()
        return producto

    @staticmethod
    def obtener_disponibilidad(
        db: Session,
        producto_id: int,
        sucursal_id: int | None = None,
        talla_id: int | None = None,
        color_id: int | None = None,
        temporada_id: int | None = None,
    ) -> DisponibilidadProductoResponse | None:
        producto = ProductoRepository.obtener_por_id(db, producto_id)
        if producto is None:
            return None

        sucursales: dict[int, DisponibilidadSucursalResponse] = {}
        inventarios = InventarioService.listar_disponibles_por_producto(
            db,
            producto_id,
            sucursal_id=sucursal_id,
            talla_id=talla_id,
            color_id=color_id,
            temporada_id=temporada_id,
        )
        for inventario in inventarios:
            sucursal_id_actual = inventario.sucursal.id
            if sucursal_id_actual not in sucursales:
                sucursales[sucursal_id_actual] = DisponibilidadSucursalResponse(
                    sucursal_id=sucursal_id_actual,
                    sucursal=inventario.sucursal.nombre,
                    variantes=[],
                )

            sucursales[sucursal_id_actual].variantes.append(
                DisponibilidadVarianteResponse(
                    variante_id=inventario.variante_producto.id,
                    sku=inventario.variante_producto.sku,
                    talla=inventario.variante_producto.talla.nombre,
                    color=inventario.variante_producto.color.nombre,
                    temporada=inventario.temporada.nombre,
                    stock_disponible=max(
                        0,
                        inventario.stock_actual - inventario.stock_reservado,
                    ),
                )
            )

        return DisponibilidadProductoResponse(
            producto_id=producto.id,
            producto=producto.nombre,
            sucursales=list(sucursales.values()),
        )


# ---------------------------------------------------------------------------
# Catalogo publico (CU09)
# ---------------------------------------------------------------------------


class CatalogoService:
    @staticmethod
    def obtener_filtros(db: Session) -> CatalogoFiltrosResponse:
        """Opciones activas para construir los filtros publicos del catalogo.

        Solo lectura. Integra CU08 (temporadas/colecciones) y CU06 (sucursales)
        sin exponer campos administrativos.
        """
        categorias = CategoriaRepository.listar_activas(db)
        tallas = TallaRepository.listar(db, estado=True)
        colores = ColorRepository.listar(db, estado=True)
        temporadas = TemporadaRepository.listar(db, estado=True)
        colecciones = ColeccionRepository.listar(db, estado=True)
        sucursales = SucursalRepository.listar_activas(db)

        return CatalogoFiltrosResponse(
            categorias=[
                FiltroOpcionResponse(id=item.id, nombre=item.nombre)
                for item in categorias
            ],
            tallas=[
                FiltroOpcionResponse(id=item.id, nombre=item.nombre)
                for item in tallas
            ],
            colores=[
                FiltroOpcionResponse(id=item.id, nombre=item.nombre)
                for item in colores
            ],
            temporadas=[
                FiltroOpcionResponse(id=item.id, nombre=item.nombre)
                for item in temporadas
            ],
            colecciones=[
                ColeccionFiltroResponse(
                    id=item.id,
                    nombre=item.nombre,
                    temporada_id=item.temporada_id,
                )
                for item in colecciones
            ],
            sucursales=[
                SucursalFiltroResponse(
                    id=item.id,
                    nombre=item.nombre,
                    ciudad=item.ciudad.nombre if item.ciudad else None,
                )
                for item in sucursales
            ],
        )


# ---------------------------------------------------------------------------
# Tallas
# ---------------------------------------------------------------------------


class TallaService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Talla]:
        return TallaRepository.listar(db, buscar=buscar, estado=estado)

    @staticmethod
    def obtener(db: Session, talla_id: int) -> Talla | None:
        return TallaRepository.obtener_por_id(db, talla_id)

    @staticmethod
    def crear(db: Session, datos: TallaCreate, admin: Usuario) -> Talla:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise TallaNombreInvalidoError()

        if TallaRepository.buscar_por_nombre_normalizado(db, nombre) is not None:
            raise TallaNombreDuplicadoError()

        try:
            _establecer_contexto_bitacora(db, admin.id)
            talla = TallaRepository.crear(db, nombre=nombre)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_TALLA,
                descripcion=f"Talla creada: {talla.nombre} (id {talla.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TallaNombreDuplicadoError() from exc

        talla = TallaRepository.obtener_por_id(db, talla.id)
        if talla is None:
            raise TallaNoEncontradaError()
        return talla

    @staticmethod
    def actualizar(
        db: Session,
        talla_id: int,
        datos: TallaUpdate,
        admin: Usuario,
    ) -> Talla:
        talla = TallaRepository.obtener_por_id(db, talla_id)
        if talla is None:
            raise TallaNoEncontradaError()

        if datos.nombre is None:
            return talla

        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise TallaNombreInvalidoError()
        if nombre == talla.nombre:
            return talla

        existente = TallaRepository.buscar_por_nombre_normalizado(db, nombre)
        if existente is not None and existente.id != talla.id:
            raise TallaNombreDuplicadoError()

        talla.nombre = nombre
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_TALLA,
                descripcion=f"Talla actualizada: {talla.nombre} (id {talla.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TallaNombreDuplicadoError() from exc

        talla = TallaRepository.obtener_por_id(db, talla.id)
        if talla is None:
            raise TallaNoEncontradaError()
        return talla

    @staticmethod
    def cambiar_estado(
        db: Session,
        talla_id: int,
        datos: TallaEstadoUpdate,
        admin: Usuario,
    ) -> Talla:
        talla = TallaRepository.obtener_por_id(db, talla_id)
        if talla is None:
            raise TallaNoEncontradaError()

        if talla.estado == datos.estado:
            return talla

        if not datos.estado:
            if TallaRepository.contar_variantes_activas(db, talla.id) > 0:
                raise TallaEnUsoError()

        talla.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_TALLA,
                descripcion=(
                    f"Talla {'habilitada' if datos.estado else 'deshabilitada'}: "
                    f"{talla.nombre} (id {talla.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TallaEnUsoError() from exc

        talla = TallaRepository.obtener_por_id(db, talla.id)
        if talla is None:
            raise TallaNoEncontradaError()
        return talla


# ---------------------------------------------------------------------------
# Colores
# ---------------------------------------------------------------------------


class ColorService:
    @staticmethod
    def listar(
        db: Session,
        buscar: str | None = None,
        estado: bool | None = None,
    ) -> list[Color]:
        return ColorRepository.listar(db, buscar=buscar, estado=estado)

    @staticmethod
    def obtener(db: Session, color_id: int) -> Color | None:
        return ColorRepository.obtener_por_id(db, color_id)

    @staticmethod
    def crear(db: Session, datos: ColorCreate, admin: Usuario) -> Color:
        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise ColorNombreInvalidoError()

        if ColorRepository.buscar_por_nombre_normalizado(db, nombre) is not None:
            raise ColorNombreDuplicadoError()

        try:
            _establecer_contexto_bitacora(db, admin.id)
            color = ColorRepository.crear(db, nombre=nombre)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_COLOR,
                descripcion=f"Color creado: {color.nombre} (id {color.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColorNombreDuplicadoError() from exc

        color = ColorRepository.obtener_por_id(db, color.id)
        if color is None:
            raise ColorNoEncontradoError()
        return color

    @staticmethod
    def actualizar(
        db: Session,
        color_id: int,
        datos: ColorUpdate,
        admin: Usuario,
    ) -> Color:
        color = ColorRepository.obtener_por_id(db, color_id)
        if color is None:
            raise ColorNoEncontradoError()

        if datos.nombre is None:
            return color

        nombre = _limpiar_texto(datos.nombre)
        if not nombre:
            raise ColorNombreInvalidoError()
        if nombre == color.nombre:
            return color

        existente = ColorRepository.buscar_por_nombre_normalizado(db, nombre)
        if existente is not None and existente.id != color.id:
            raise ColorNombreDuplicadoError()

        color.nombre = nombre
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_COLOR,
                descripcion=f"Color actualizado: {color.nombre} (id {color.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColorNombreDuplicadoError() from exc

        color = ColorRepository.obtener_por_id(db, color.id)
        if color is None:
            raise ColorNoEncontradoError()
        return color

    @staticmethod
    def cambiar_estado(
        db: Session,
        color_id: int,
        datos: ColorEstadoUpdate,
        admin: Usuario,
    ) -> Color:
        color = ColorRepository.obtener_por_id(db, color_id)
        if color is None:
            raise ColorNoEncontradoError()

        if color.estado == datos.estado:
            return color

        if not datos.estado:
            if ColorRepository.contar_variantes_activas(db, color.id) > 0:
                raise ColorEnUsoError()

        color.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_COLOR,
                descripcion=(
                    f"Color {'habilitado' if datos.estado else 'deshabilitado'}: "
                    f"{color.nombre} (id {color.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ColorEnUsoError() from exc

        color = ColorRepository.obtener_por_id(db, color.id)
        if color is None:
            raise ColorNoEncontradoError()
        return color


# ---------------------------------------------------------------------------
# Variantes de producto
# ---------------------------------------------------------------------------


class VarianteProductoService:
    @staticmethod
    def listar_por_producto(
        db: Session,
        producto_id: int,
        estado: bool | None = None,
    ) -> list[VarianteProducto] | None:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            return None
        return VarianteProductoRepository.listar_por_producto(
            db, producto_id, estado=estado
        )

    @staticmethod
    def _validar_talla_activa(db: Session, talla_id: int) -> Talla:
        talla = TallaRepository.obtener_por_id(db, talla_id)
        if talla is None:
            raise VarianteTallaNoEncontradaError()
        if not talla.estado:
            raise VarianteTallaInactivaError()
        return talla

    @staticmethod
    def _validar_color_activo(db: Session, color_id: int) -> Color:
        color = ColorRepository.obtener_por_id(db, color_id)
        if color is None:
            raise VarianteColorNoEncontradoError()
        if not color.estado:
            raise VarianteColorInactivoError()
        return color

    @staticmethod
    def crear(
        db: Session,
        producto_id: int,
        datos: VarianteCreate,
        admin: Usuario,
    ) -> VarianteProducto:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            raise VarianteProductoNoEncontradoError()
        if not producto.estado:
            raise VarianteProductoInactivoError()

        VarianteProductoService._validar_talla_activa(db, datos.talla_id)
        VarianteProductoService._validar_color_activo(db, datos.color_id)

        sku = _normalizar_sku(datos.sku)
        if not sku:
            raise VarianteSkuInvalidoError()

        if (
            VarianteProductoRepository.buscar_combinacion(
                db,
                producto_id=producto_id,
                talla_id=datos.talla_id,
                color_id=datos.color_id,
            )
            is not None
        ):
            raise VarianteCombinacionDuplicadaError()

        if (
            VarianteProductoRepository.buscar_por_sku_normalizado(db, sku)
            is not None
        ):
            raise VarianteSkuDuplicadoError()

        try:
            _establecer_contexto_bitacora(db, admin.id)
            variante = VarianteProductoRepository.crear(
                db,
                producto_id=producto_id,
                talla_id=datos.talla_id,
                color_id=datos.color_id,
                sku=sku,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise VarianteRegistroInvalidoError() from exc

        variante = VarianteProductoRepository.obtener_por_id(db, variante.id)
        if variante is None:
            raise VarianteNoEncontradaError()
        return variante

    @staticmethod
    def actualizar(
        db: Session,
        variante_id: int,
        datos: VarianteUpdate,
        admin: Usuario,
    ) -> VarianteProducto:
        variante = VarianteProductoRepository.obtener_por_id(db, variante_id)
        if variante is None:
            raise VarianteNoEncontradaError()

        talla_id = variante.talla_id
        if datos.talla_id is not None and datos.talla_id != variante.talla_id:
            VarianteProductoService._validar_talla_activa(db, datos.talla_id)
            talla_id = datos.talla_id

        color_id = variante.color_id
        if datos.color_id is not None and datos.color_id != variante.color_id:
            VarianteProductoService._validar_color_activo(db, datos.color_id)
            color_id = datos.color_id

        sku = variante.sku
        if datos.sku is not None:
            sku = _normalizar_sku(datos.sku)
            if not sku:
                raise VarianteSkuInvalidoError()
            existente = VarianteProductoRepository.buscar_por_sku_normalizado(
                db, sku, excluir_id=variante.id
            )
            if existente is not None:
                raise VarianteSkuDuplicadoError()

        if (
            VarianteProductoRepository.buscar_combinacion(
                db,
                producto_id=variante.producto_id,
                talla_id=talla_id,
                color_id=color_id,
                excluir_id=variante.id,
            )
            is not None
        ):
            raise VarianteCombinacionDuplicadaError()

        hay_cambios = (
            talla_id != variante.talla_id
            or color_id != variante.color_id
            or sku != variante.sku
        )
        if not hay_cambios:
            return variante

        variante.talla_id = talla_id
        variante.color_id = color_id
        variante.sku = sku

        try:
            _establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise VarianteRegistroInvalidoError() from exc

        variante = VarianteProductoRepository.obtener_por_id(db, variante.id)
        if variante is None:
            raise VarianteNoEncontradaError()
        return variante

    @staticmethod
    def cambiar_estado(
        db: Session,
        variante_id: int,
        datos: VarianteEstadoUpdate,
        admin: Usuario,
    ) -> VarianteProducto:
        variante = VarianteProductoRepository.obtener_por_id(db, variante_id)
        if variante is None:
            raise VarianteNoEncontradaError()

        if variante.estado == datos.estado:
            return variante

        if datos.estado and not variante.producto.estado:
            raise VarianteProductoInactivoError()

        variante.estado = datos.estado
        try:
            _establecer_contexto_bitacora(db, admin.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise VarianteRegistroInvalidoError() from exc

        variante = VarianteProductoRepository.obtener_por_id(db, variante.id)
        if variante is None:
            raise VarianteNoEncontradaError()
        return variante


# ---------------------------------------------------------------------------
# Recursos de producto
# ---------------------------------------------------------------------------


class RecursoProductoService:
    @staticmethod
    def listar_por_producto(
        db: Session,
        producto_id: int,
        estado: bool | None = None,
    ) -> list[RecursoProducto] | None:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            return None
        return RecursoProductoRepository.listar_por_producto(
            db, producto_id, estado=estado
        )

    @staticmethod
    def _validar_color(db: Session, color_id: int) -> Color:
        color = ColorRepository.obtener_por_id(db, color_id)
        if color is None:
            raise RecursoColorNoEncontradoError()
        if not color.estado:
            raise RecursoColorInactivoError()
        return color

    @staticmethod
    def crear(
        db: Session,
        producto_id: int,
        datos: RecursoProductoCreate,
        admin: Usuario,
    ) -> RecursoProducto:
        producto = ProductoRepository.obtener_admin_por_id(db, producto_id)
        if producto is None:
            raise RecursoProductoNoEncontradoError()

        if datos.color_id is not None:
            RecursoProductoService._validar_color(db, datos.color_id)

        tipo = _limpiar_texto(datos.tipo)
        if not tipo:
            raise RecursoTipoInvalidoError()
        url = str(datos.url).strip()

        try:
            _establecer_contexto_bitacora(db, admin.id)
            if datos.es_principal:
                RecursoProductoRepository.desmarcar_principales(
                    db,
                    producto_id=producto_id,
                    color_id=datos.color_id,
                )
            recurso = RecursoProductoRepository.crear(
                db,
                producto_id=producto_id,
                color_id=datos.color_id,
                tipo=tipo,
                url=url,
                es_principal=datos.es_principal,
            )
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_CREAR,
                entidad_afectada=ENTIDAD_RECURSO_PRODUCTO,
                descripcion=(
                    f"Recurso creado para producto {producto_id} "
                    f"(id {recurso.id}, principal={datos.es_principal})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RecursoPrincipalConflictoError() from exc

        recurso = RecursoProductoRepository.obtener_por_id(db, recurso.id)
        if recurso is None:
            raise RecursoNoEncontradoError()
        return recurso

    @staticmethod
    def actualizar(
        db: Session,
        recurso_id: int,
        datos: RecursoProductoUpdate,
        admin: Usuario,
    ) -> RecursoProducto:
        recurso = RecursoProductoRepository.obtener_por_id(db, recurso_id)
        if recurso is None:
            raise RecursoNoEncontradoError()

        color_efectivo = recurso.color_id
        if "color_id" in datos.model_fields_set:
            if datos.color_id is not None:
                RecursoProductoService._validar_color(db, datos.color_id)
            color_efectivo = datos.color_id

        tipo_efectivo = recurso.tipo
        if datos.tipo is not None:
            tipo_efectivo = _limpiar_texto(datos.tipo)
            if not tipo_efectivo:
                raise RecursoTipoInvalidoError()

        url_efectiva = recurso.url
        if datos.url is not None:
            url_efectiva = str(datos.url).strip()

        principal_efectivo = recurso.es_principal
        if datos.es_principal is not None:
            principal_efectivo = datos.es_principal

        color_cambio = color_efectivo != recurso.color_id
        hay_cambios = (
            color_cambio
            or tipo_efectivo != recurso.tipo
            or url_efectiva != recurso.url
            or principal_efectivo != recurso.es_principal
        )
        if not hay_cambios:
            return recurso

        try:
            _establecer_contexto_bitacora(db, admin.id)
            if principal_efectivo and (not recurso.es_principal or color_cambio):
                RecursoProductoRepository.desmarcar_principales(
                    db,
                    producto_id=recurso.producto_id,
                    color_id=color_efectivo,
                    excluir_id=recurso.id,
                )
            recurso.color_id = color_efectivo
            recurso.tipo = tipo_efectivo
            recurso.url = url_efectiva
            recurso.es_principal = principal_efectivo
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_RECURSO_PRODUCTO,
                descripcion=f"Recurso actualizado (id {recurso.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RecursoPrincipalConflictoError() from exc

        recurso = RecursoProductoRepository.obtener_por_id(db, recurso.id)
        if recurso is None:
            raise RecursoNoEncontradoError()
        return recurso

    @staticmethod
    def cambiar_estado(
        db: Session,
        recurso_id: int,
        datos: RecursoProductoEstadoUpdate,
        admin: Usuario,
    ) -> RecursoProducto:
        recurso = RecursoProductoRepository.obtener_por_id(db, recurso_id)
        if recurso is None:
            raise RecursoNoEncontradoError()

        if recurso.estado == datos.estado:
            return recurso

        try:
            _establecer_contexto_bitacora(db, admin.id)
            if datos.estado and recurso.es_principal:
                RecursoProductoRepository.desmarcar_principales(
                    db,
                    producto_id=recurso.producto_id,
                    color_id=recurso.color_id,
                    excluir_id=recurso.id,
                )
            recurso.estado = datos.estado
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_RECURSO_PRODUCTO,
                descripcion=(
                    f"Recurso {'habilitado' if datos.estado else 'deshabilitado'} "
                    f"(id {recurso.id})"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RecursoPrincipalConflictoError() from exc

        recurso = RecursoProductoRepository.obtener_por_id(db, recurso.id)
        if recurso is None:
            raise RecursoNoEncontradoError()
        return recurso

    @staticmethod
    def marcar_principal(
        db: Session,
        recurso_id: int,
        admin: Usuario,
    ) -> RecursoProducto:
        recurso = RecursoProductoRepository.obtener_por_id(db, recurso_id)
        if recurso is None:
            raise RecursoNoEncontradoError()

        if not recurso.estado:
            raise RecursoNoActivoError()

        if recurso.es_principal:
            return recurso

        try:
            _establecer_contexto_bitacora(db, admin.id)
            RecursoProductoRepository.desmarcar_principales(
                db,
                producto_id=recurso.producto_id,
                color_id=recurso.color_id,
                excluir_id=recurso.id,
            )
            recurso.es_principal = True
            registrar_evento_bitacora(
                db,
                usuario_id=admin.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_RECURSO_PRODUCTO,
                descripcion=f"Recurso marcado como principal (id {recurso.id})",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise RecursoPrincipalConflictoError() from exc

        recurso = RecursoProductoRepository.obtener_por_id(db, recurso.id)
        if recurso is None:
            raise RecursoNoEncontradoError()
        return recurso
