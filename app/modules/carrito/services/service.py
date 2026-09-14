from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.carrito.models.models import Carrito, DetalleCarrito
from app.modules.carrito.repositories.repository import (
    ESTADO_ACTIVO,
    CarritoRepository,
    RecursoProductoCarritoRepository,
)
from app.modules.carrito.schemas.schemas import (
    AgregarItemCarritoRequest,
    CarritoDetalleResponse,
    CarritoItemResponse,
    CarritoListaResponse,
    CarritoResumenResponse,
)
from app.modules.inventario.repositories.repository import InventarioRepository

EXPIRACION_HORAS = 2

# Indices/constraints cuya violacion SI corresponde a una condicion de carrera.
CONSTRAINTS_CARRERA = frozenset(
    {
        "uq_carrito_activo_cliente_sucursal",
        "uq_detalle_carrito_inventario",
    }
)


class CarritoError(Exception):
    """Base de errores de negocio de CU15 (mapped a HTTP en el router)."""


class CarritoNoEncontradoError(CarritoError):
    pass


class CarritoAjenoError(CarritoError):
    """El carrito pertenece a otro cliente."""


class CarritoNoActivoError(CarritoError):
    """El carrito no esta ACTIVO (expirado, eliminado o convertido)."""


class DetalleNoEncontradoError(CarritoError):
    pass


class InventarioNoEncontradoError(CarritoError):
    pass


class InventarioSucursalInvalidaError(CarritoError):
    """El inventario no pertenece a la sucursal del carrito."""


class ProductoNoDisponibleError(CarritoError):
    """Inventario/variante/producto/talla/color/sucursal inactivos."""


class StockInsuficienteError(CarritoError):
    pass


class CarritoDuplicadoError(CarritoError):
    """Ya existe un carrito ACTIVO para cliente + sucursal."""


class CarritoRegistroInvalidoError(CarritoError):
    """IntegrityError no asociado a una carrera conocida (no reintentable)."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _limite_vigencia() -> datetime:
    """Un carrito vigente debe tener fecha_actualizacion > ahora - 2 horas."""
    return _ahora() - timedelta(hours=EXPIRACION_HORAS)


def _constraint_violada(exc: IntegrityError) -> str | None:
    """Nombre de la constraint violada (psycopg3), de forma segura.

    Usa ``exc.orig.diag.constraint_name`` (metadatos del driver) y, como
    respaldo, busca el nombre de una constraint esperada en el texto del error.
    El texto crudo nunca se expone al cliente: solo se usa para decidir.
    """
    origen = getattr(exc, "orig", None)
    diag = getattr(origen, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    if nombre:
        return str(nombre)

    texto = str(origen or exc)
    for esperada in CONSTRAINTS_CARRERA:
        if esperada in texto:
            return esperada
    return None


def _es_conflicto_de_carrera(exc: IntegrityError) -> bool:
    """True solo si la violacion corresponde a una constraint de carrera."""
    return _constraint_violada(exc) in CONSTRAINTS_CARRERA


def _stock_disponible(inventario) -> int:
    return inventario.stock_actual - inventario.stock_reservado


def _inventario_activo(inventario) -> bool:
    """Reglas de 'activos' equivalentes a la disponibilidad publica (CU09)."""
    variante = inventario.variante_producto
    return bool(
        variante.estado
        and variante.producto.estado
        and variante.talla.estado
        and variante.color.estado
        and inventario.sucursal.estado
    )


def _resolver_imagenes(db: Session, detalles: list[DetalleCarrito]):
    """Imagen principal por (producto, color) reutilizando recurso_producto."""
    pares = {
        (
            detalle.inventario.variante_producto.producto_id,
            detalle.inventario.variante_producto.color_id,
        )
        for detalle in detalles
    }
    if not pares:
        return {}

    producto_ids = {producto_id for producto_id, _ in pares}
    recursos = RecursoProductoCarritoRepository.listar_recursos_activos(
        db, producto_ids
    )
    por_producto: dict[int, list] = {}
    for recurso in recursos:
        por_producto.setdefault(recurso.producto_id, []).append(recurso)

    resultado: dict[tuple[int, int], str | None] = {}
    for producto_id, color_id in pares:
        lista = por_producto.get(producto_id, [])
        elegido = next(
            (r.url for r in lista if r.es_principal and r.color_id == color_id),
            None,
        )
        if elegido is None:
            elegido = next(
                (r.url for r in lista if r.es_principal and r.color_id is None),
                None,
            )
        if elegido is None and lista:
            elegido = lista[0].url
        resultado[(producto_id, color_id)] = elegido
    return resultado


def _item_response(detalle: DetalleCarrito, imagen: str | None) -> CarritoItemResponse:
    inventario = detalle.inventario
    variante = inventario.variante_producto
    producto = variante.producto
    precio = Decimal(producto.precio)
    return CarritoItemResponse(
        detalle_id=detalle.id,
        inventario_id=detalle.inventario_id,
        producto_id=producto.id,
        producto_nombre=producto.nombre,
        precio_unitario=precio,
        imagen_principal=imagen,
        variante_producto_id=variante.id,
        sku=variante.sku,
        talla_id=variante.talla.id,
        talla_nombre=variante.talla.nombre,
        color_id=variante.color.id,
        color_nombre=variante.color.nombre,
        temporada_id=inventario.temporada_id,
        temporada_nombre=inventario.temporada.nombre,
        cantidad=detalle.cantidad,
        stock_disponible=_stock_disponible(inventario),
        subtotal_linea=precio * detalle.cantidad,
    )


def _detalle_response(db: Session, carrito: Carrito) -> CarritoDetalleResponse:
    detalles = list(carrito.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[CarritoItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_response(detalle, imagen))

    subtotal = Decimal("0")
    unidades = 0
    for item in items:
        subtotal += item.subtotal_linea
        unidades += item.cantidad

    return CarritoDetalleResponse(
        carrito_id=carrito.id,
        sucursal_id=carrito.sucursal_id,
        sucursal_nombre=carrito.sucursal.nombre,
        estado=carrito.estado,
        fecha_creacion=carrito.fecha_creacion,
        fecha_actualizacion=carrito.fecha_actualizacion,
        items=items,
        cantidad_total_unidades=unidades,
        subtotal_carrito=subtotal,
    )


def _resumen_response(carrito: Carrito) -> CarritoResumenResponse:
    subtotal = Decimal("0")
    unidades = 0
    for detalle in carrito.detalles:
        precio = Decimal(detalle.inventario.variante_producto.producto.precio)
        subtotal += precio * detalle.cantidad
        unidades += detalle.cantidad
    return CarritoResumenResponse(
        carrito_id=carrito.id,
        sucursal_id=carrito.sucursal_id,
        sucursal_nombre=carrito.sucursal.nombre,
        cantidad_lineas=len(carrito.detalles),
        cantidad_unidades=unidades,
        subtotal=subtotal,
        fecha_actualizacion=carrito.fecha_actualizacion,
    )


class CarritoService:
    """Reglas de negocio de CU15 - Gestionar carrito de compras (CLIENTE)."""

    @staticmethod
    def _expirar(db: Session, cliente_id: int) -> None:
        """Marca EXPIRADO los carritos ACTIVO sin actividad por 2 horas."""
        cambiados = CarritoRepository.expirar_vencidos(
            db, cliente_id, _limite_vigencia()
        )
        if cambiados:
            db.commit()

    @staticmethod
    def _validar_carrito_activo_vigente(
        db: Session, cliente, carrito_id: int
    ) -> Carrito:
        """Regla unica de acceso directo por carrito_id.

        Si el carrito pertenece al cliente pero estaba ACTIVO y vencido, se
        marca EXPIRADO y se persiste antes de rechazar la operacion. Asi un
        PATCH/DELETE nunca llega a tocar detalle_carrito (el trigger
        trg_actualizar_actividad_carrito no puede revivir el carrito).

        Nunca reactiva: si el estado final no es ACTIVO -> CarritoNoActivoError.
        """
        CarritoService._expirar(db, cliente.id)

        carrito = CarritoRepository.obtener_por_id(db, carrito_id)
        if carrito is None:
            raise CarritoNoEncontradoError()
        if carrito.cliente_id != cliente.id:
            raise CarritoAjenoError()

        if (
            carrito.estado == ESTADO_ACTIVO
            and carrito.fecha_actualizacion <= _limite_vigencia()
        ):
            CarritoRepository.marcar_expirado(db, carrito)
            db.commit()
            carrito = CarritoRepository.obtener_por_id(db, carrito_id)
            if carrito is None:
                raise CarritoNoEncontradoError()

        if carrito.estado != ESTADO_ACTIVO:
            raise CarritoNoActivoError()
        return carrito

    @staticmethod
    def _obtener_inventario(db: Session, inventario_id: int):
        inventario = InventarioRepository.obtener_por_id(db, inventario_id)
        if inventario is None:
            raise InventarioNoEncontradoError()
        return inventario

    @staticmethod
    def _validar_inventario(inventario) -> None:
        if not _inventario_activo(inventario):
            raise ProductoNoDisponibleError()

    @staticmethod
    def agregar_item(
        db: Session, cliente, datos: AgregarItemCarritoRequest
    ) -> CarritoDetalleResponse:
        """Agrega una prenda: reutiliza/crea carrito y suma o crea el detalle.

        Condiciones de carrera: el indice unico parcial
        uq_carrito_activo_cliente_sucursal y UNIQUE(carrito_id, inventario_id)
        pueden fallar si dos peticiones concurrentes crean el primer carrito o
        la primera linea. SOLO esas violaciones (ver CONSTRAINTS_CARRERA) se
        revierten y se reintentan UNA vez, recuperando la fila ya persistida por
        la otra peticion (sin devolver un 409 innecesario). Si el reintento
        tambien falla -> CarritoDuplicadoError. Cualquier otro IntegrityError no
        se disfraza de carrera: se propaga como CarritoRegistroInvalidoError.
        """
        CarritoService._expirar(db, cliente.id)

        inventario = CarritoService._obtener_inventario(db, datos.inventario_id)
        CarritoService._validar_inventario(inventario)
        if inventario.sucursal_id != datos.sucursal_id:
            raise InventarioSucursalInvalidaError()

        limite = _limite_vigencia()
        disponible = _stock_disponible(inventario)
        carrito_id: int | None = None
        ultimo_error: IntegrityError | None = None

        for intento in range(2):
            try:
                carrito = CarritoRepository.obtener_activo(
                    db,
                    cliente.id,
                    datos.sucursal_id,
                    limite_vigencia=limite,
                )
                if carrito is None:
                    carrito = CarritoRepository.crear(
                        db, cliente.id, datos.sucursal_id
                    )

                detalle = CarritoRepository.obtener_detalle_por_inventario(
                    db, carrito.id, inventario.id
                )
                cantidad_final = (
                    detalle.cantidad if detalle is not None else 0
                ) + datos.cantidad
                if cantidad_final > disponible:
                    raise StockInsuficienteError()

                if detalle is not None:
                    detalle.cantidad = cantidad_final
                else:
                    CarritoRepository.crear_detalle(
                        db, carrito.id, inventario.id, cantidad_final
                    )
                db.commit()
                carrito_id = carrito.id
                break
            except IntegrityError as exc:
                # Solo las violaciones de carreras conocidas se reintentan.
                db.rollback()
                if not _es_conflicto_de_carrera(exc):
                    raise CarritoRegistroInvalidoError() from exc
                ultimo_error = exc
                if intento == 1:
                    raise CarritoDuplicadoError() from ultimo_error

        if carrito_id is None:
            raise CarritoDuplicadoError() from ultimo_error

        carrito = CarritoRepository.obtener_por_id(db, carrito_id)
        if carrito is None:
            raise CarritoNoEncontradoError()
        return _detalle_response(db, carrito)

    @staticmethod
    def listar_carritos(db: Session, cliente) -> CarritoListaResponse:
        """Carritos ACTIVO vigentes del cliente (contador = nro de carritos)."""
        CarritoService._expirar(db, cliente.id)
        carritos = CarritoRepository.listar_activos(
            db, cliente.id, limite_vigencia=_limite_vigencia()
        )
        items = [_resumen_response(carrito) for carrito in carritos]
        return CarritoListaResponse(
            items=items, total_carritos_activos=len(items)
        )

    @staticmethod
    def obtener_carrito(
        db: Session, cliente, carrito_id: int
    ) -> CarritoDetalleResponse:
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, carrito_id
        )
        return _detalle_response(db, carrito)

    @staticmethod
    def actualizar_cantidad(
        db: Session, cliente, carrito_id: int, detalle_id: int, cantidad: int
    ) -> CarritoDetalleResponse:
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, carrito_id
        )

        detalle = CarritoRepository.obtener_detalle(db, carrito.id, detalle_id)
        if detalle is None:
            raise DetalleNoEncontradoError()

        inventario = CarritoService._obtener_inventario(db, detalle.inventario_id)
        CarritoService._validar_inventario(inventario)
        if cantidad > _stock_disponible(inventario):
            raise StockInsuficienteError()

        detalle.cantidad = cantidad
        db.commit()

        carrito = CarritoRepository.obtener_por_id(db, carrito.id)
        if carrito is None:
            raise CarritoNoEncontradoError()
        return _detalle_response(db, carrito)

    @staticmethod
    def eliminar_detalle(
        db: Session, cliente, carrito_id: int, detalle_id: int
    ) -> CarritoDetalleResponse:
        """Elimina la fila fisica; si el carrito queda vacio se marca ELIMINADO."""
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, carrito_id
        )

        detalle = CarritoRepository.obtener_detalle(db, carrito.id, detalle_id)
        if detalle is None:
            raise DetalleNoEncontradoError()

        CarritoRepository.eliminar_detalle(db, detalle)
        if CarritoRepository.contar_detalles(db, carrito.id) == 0:
            # Regla obligatoria: no dejar carritos ACTIVO vacios.
            CarritoRepository.marcar_eliminado(db, carrito)
        db.commit()

        carrito = CarritoRepository.obtener_por_id(db, carrito.id)
        if carrito is None:
            raise CarritoNoEncontradoError()
        return _detalle_response(db, carrito)

    @staticmethod
    def eliminar_carrito(
        db: Session, cliente, carrito_id: int
    ) -> CarritoDetalleResponse:
        """Eliminacion logica: estado = ELIMINADO (nunca DELETE fisico)."""
        carrito = CarritoService._validar_carrito_activo_vigente(
            db, cliente, carrito_id
        )
        CarritoRepository.marcar_eliminado(db, carrito)
        db.commit()
        carrito = CarritoRepository.obtener_por_id(db, carrito.id)
        if carrito is None:
            raise CarritoNoEncontradoError()
        return _detalle_response(db, carrito)


