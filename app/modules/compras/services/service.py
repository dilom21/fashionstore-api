from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.compras.models.models import DetalleOrdenCompra, OrdenCompra
from app.modules.compras.repositories.repository import (
    OrdenCompraRepository,
    registrar_evento_bitacora,
)
from app.modules.compras.schemas.schemas import (
    DetalleOrdenCompraResponse,
    DetallesOrdenCompraResponse,
    DetallesOrdenCompraUpdate,
    OrdenCompraCreate,
    OrdenCompraResponse,
    OrdenCompraUpdate,
)

FUNCION = "GESTIONAR_ORDENES_COMPRA"

ESTADO_BORRADOR = "BORRADOR"
ESTADO_ENVIADA = "ENVIADA"
ESTADO_PARCIAL = "PARCIAL"
ESTADO_RECIBIDA = "RECIBIDA"
ESTADO_CANCELADA = "CANCELADA"

ESTADOS_VALIDOS = (
    ESTADO_BORRADOR,
    ESTADO_ENVIADA,
    ESTADO_PARCIAL,
    ESTADO_RECIBIDA,
    ESTADO_CANCELADA,
)

# Cancelacion conservadora: una orden RECIBIDA no puede cancelarse y una
# PARCIAL ya pudo haber afectado inventario, por lo que tampoco se cancela.
ESTADOS_CANCELABLES = (ESTADO_BORRADOR, ESTADO_ENVIADA)

# La cabecera solo puede editarse mientras la orden no este finalizada.
ESTADOS_EDITABLES_CABECERA = (ESTADO_BORRADOR, ESTADO_ENVIADA, ESTADO_PARCIAL)

ROL_ADMINISTRADOR = "ADMINISTRADOR"

ACCION_MODIFICAR = "MODIFICAR"
ENTIDAD_DETALLE_ORDEN_COMPRA = "detalle_orden_compra"

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Errores tipados de CU12
# ---------------------------------------------------------------------------


class OrdenCompraNoEncontradaError(Exception):
    pass


class ProveedorNoEncontradoError(Exception):
    pass


class ProveedorInactivoError(Exception):
    pass


class SucursalNoEncontradaError(Exception):
    pass


class SucursalInactivaError(Exception):
    pass


class SucursalScopeError(Exception):
    """El usuario no puede operar sobre la sucursal solicitada."""


class FechaEstimadaInvalidaError(Exception):
    pass


class EstadoOrdenInvalidoError(Exception):
    """La orden no admite edicion en su estado actual."""


class TransicionInvalidaError(Exception):
    pass


class SinDetallesError(Exception):
    pass


class DetalleEstadoInvalidoError(Exception):
    """Los detalles solo se editan mientras la orden esta en BORRADOR."""


class DetalleCantidadInvalidaError(Exception):
    pass


class DetalleCostoInvalidoError(Exception):
    pass


class DetalleVarianteNoEncontradaError(Exception):
    pass


class DetalleVarianteInactivaError(Exception):
    pass


class DetalleTemporadaNoEncontradaError(Exception):
    pass


class DetalleTemporadaInactivaError(Exception):
    pass


class DetalleProductoNoAsociadoError(Exception):
    pass


class DetalleRegistroInvalidoError(Exception):
    pass


class RecepcionInvalidaError(Exception):
    pass


class OrdenRegistroInvalidoError(Exception):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _asegurar_tz(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    if valor.tzinfo is None:
        return valor.replace(tzinfo=_UTC)
    return valor


def _limpiar_observacion(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio or None


def _es_administrador(usuario: Usuario) -> bool:
    nombre = str(usuario.rol.nombre).strip().upper()
    return nombre == ROL_ADMINISTRADOR


def _sucursal_scope(usuario: Usuario) -> int | None:
    """Sucursal permitida para el usuario.

    ADMINISTRADOR: None (cualquier sucursal activa).
    Otros roles (ENCARGADO_SUCURSAL): la sucursal de su empleado activo.
    """
    if _es_administrador(usuario):
        return None
    empleado = usuario.empleado
    if empleado is None or not empleado.estado:
        raise SucursalScopeError()
    return empleado.sucursal_id


def _validar_scope_sucursal(usuario: Usuario, sucursal_id: int) -> None:
    scope = _sucursal_scope(usuario)
    if scope is not None and scope != sucursal_id:
        raise SucursalScopeError()


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _construir_orden_response(
    orden: OrdenCompra, total_detalles: int = 0
) -> OrdenCompraResponse:
    return OrdenCompraResponse(
        id=orden.id,
        proveedor_id=orden.proveedor_id,
        proveedor_razon_social=(
            orden.proveedor.razon_social if orden.proveedor else None
        ),
        sucursal_id=orden.sucursal_id,
        sucursal_nombre=(
            orden.sucursal.nombre if orden.sucursal else None
        ),
        empleado_id=orden.empleado_id,
        fecha_orden=orden.fecha_orden,
        fecha_estimada=orden.fecha_estimada,
        fecha_recepcion=orden.fecha_recepcion,
        estado=orden.estado,
        observacion=orden.observacion,
        total_detalles=total_detalles,
    )


def _construir_detalle_response(
    detalle: DetalleOrdenCompra,
) -> DetalleOrdenCompraResponse:
    variante = detalle.variante
    producto = variante.producto if variante is not None else None
    return DetalleOrdenCompraResponse(
        id=detalle.id,
        variante_producto_id=detalle.variante_producto_id,
        temporada_id=detalle.temporada_id,
        cantidad=detalle.cantidad,
        costo_unitario=detalle.costo_unitario,
        sku=variante.sku if variante is not None else None,
        producto_id=variante.producto_id if variante is not None else None,
        producto_nombre=producto.nombre if producto is not None else None,
        temporada_nombre=(
            detalle.temporada.nombre
            if detalle.temporada is not None
            else None
        ),
    )


def _construir_detalles_response(
    orden_id: int, detalles: list[DetalleOrdenCompra]
) -> DetallesOrdenCompraResponse:
    items = [_construir_detalle_response(detalle) for detalle in detalles]
    return DetallesOrdenCompraResponse(
        orden_compra_id=orden_id,
        total=len(items),
        detalles=items,
    )


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------


class OrdenCompraService:
    @staticmethod
    def _obtener_orden_validada(
        db: Session, orden_id: int, usuario: Usuario
    ) -> OrdenCompra:
        orden = OrdenCompraRepository.obtener_por_id(db, orden_id)
        if orden is None:
            raise OrdenCompraNoEncontradaError()
        _validar_scope_sucursal(usuario, orden.sucursal_id)
        return orden

    @staticmethod
    def listar(
        db: Session,
        usuario: Usuario,
        *,
        buscar: str | None = None,
        proveedor_id: int | None = None,
        sucursal_id: int | None = None,
        estado: str | None = None,
        fecha_desde=None,
        fecha_hasta=None,
    ) -> list[OrdenCompraResponse]:
        scope = _sucursal_scope(usuario)
        if scope is not None:
            if sucursal_id is not None and sucursal_id != scope:
                raise SucursalScopeError()
            sucursal_id = scope

        ordenes = OrdenCompraRepository.listar(
            db,
            buscar=buscar,
            proveedor_id=proveedor_id,
            sucursal_id=sucursal_id,
            estado=estado,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        conteos = OrdenCompraRepository.contar_detalles_por_orden(
            db, [orden.id for orden in ordenes]
        )
        return [
            _construir_orden_response(orden, conteos.get(orden.id, 0))
            for orden in ordenes
        ]

    @staticmethod
    def obtener(
        db: Session, orden_id: int, usuario: Usuario
    ) -> OrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        total = OrdenCompraRepository.contar_detalles(db, orden.id)
        return _construir_orden_response(orden, total)

    @staticmethod
    def crear(
        db: Session, datos: OrdenCompraCreate, usuario: Usuario
    ) -> OrdenCompraResponse:
        proveedor = OrdenCompraRepository.obtener_proveedor(
            db, datos.proveedor_id
        )
        if proveedor is None:
            raise ProveedorNoEncontradoError()
        if not proveedor.estado:
            raise ProveedorInactivoError()

        sucursal = OrdenCompraRepository.obtener_sucursal(
            db, datos.sucursal_id
        )
        if sucursal is None:
            raise SucursalNoEncontradaError()
        if not sucursal.estado:
            raise SucursalInactivaError()

        _validar_scope_sucursal(usuario, datos.sucursal_id)

        # fecha_orden la asigna la base de datos (CURRENT_TIMESTAMP), por lo
        # que la validacion usa el instante actual como cota superior segura.
        ahora = datetime.now(_UTC)
        fecha_estimada = _asegurar_tz(datos.fecha_estimada)
        if fecha_estimada is not None and fecha_estimada < ahora:
            raise FechaEstimadaInvalidaError()

        observacion = _limpiar_observacion(datos.observacion)
        empleado_id = (
            usuario.empleado.id if usuario.empleado is not None else None
        )

        try:
            _establecer_contexto_bitacora(db, usuario.id)
            orden = OrdenCompraRepository.crear(
                db,
                proveedor_id=datos.proveedor_id,
                sucursal_id=datos.sucursal_id,
                empleado_id=empleado_id,
                fecha_estimada=fecha_estimada,
                observacion=observacion,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise OrdenRegistroInvalidoError() from exc

        orden = OrdenCompraRepository.obtener_por_id(db, orden.id)
        if orden is None:
            raise OrdenCompraNoEncontradaError()
        return _construir_orden_response(orden, 0)

    @staticmethod
    def actualizar(
        db: Session,
        orden_id: int,
        datos: OrdenCompraUpdate,
        usuario: Usuario,
    ) -> OrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        if orden.estado not in ESTADOS_EDITABLES_CABECERA:
            raise EstadoOrdenInvalidoError()

        campos = datos.model_fields_set
        hay_cambios = False

        if "fecha_estimada" in campos:
            nueva = _asegurar_tz(datos.fecha_estimada)
            if (
                nueva is not None
                and nueva < _asegurar_tz(orden.fecha_orden)
            ):
                raise FechaEstimadaInvalidaError()
            if nueva != orden.fecha_estimada:
                orden.fecha_estimada = nueva
                hay_cambios = True

        if "observacion" in campos:
            observacion = _limpiar_observacion(datos.observacion)
            if observacion != orden.observacion:
                orden.observacion = observacion
                hay_cambios = True

        if hay_cambios:
            try:
                _establecer_contexto_bitacora(db, usuario.id)
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise OrdenRegistroInvalidoError() from exc
            orden = OrdenCompraRepository.obtener_por_id(db, orden.id)
            if orden is None:
                raise OrdenCompraNoEncontradaError()

        total = OrdenCompraRepository.contar_detalles(db, orden.id)
        return _construir_orden_response(orden, total)

    # ------------------------------------------------------------------
    # Detalles
    # ------------------------------------------------------------------

    @staticmethod
    def listar_detalles(
        db: Session, orden_id: int, usuario: Usuario
    ) -> DetallesOrdenCompraResponse:
        OrdenCompraService._obtener_orden_validada(db, orden_id, usuario)
        detalles = OrdenCompraRepository.listar_detalles(db, orden_id)
        return _construir_detalles_response(orden_id, detalles)

    @staticmethod
    def reemplazar_detalles(
        db: Session,
        orden_id: int,
        datos: DetallesOrdenCompraUpdate,
        usuario: Usuario,
    ) -> DetallesOrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        if orden.estado != ESTADO_BORRADOR:
            raise DetalleEstadoInvalidoError()

        # Deduplicacion por (variante, temporada) conservando la ultima.
        normalizados: dict[tuple[int, int], dict] = {}
        for item in datos.detalles:
            if item.cantidad <= 0:
                raise DetalleCantidadInvalidaError()
            if item.costo_unitario < 0:
                raise DetalleCostoInvalidoError()
            clave = (item.variante_producto_id, item.temporada_id)
            normalizados[clave] = {
                "variante_producto_id": item.variante_producto_id,
                "temporada_id": item.temporada_id,
                "cantidad": item.cantidad,
                "costo_unitario": item.costo_unitario,
            }

        variante_ids = [clave[0] for clave in normalizados]
        temporada_ids = [clave[1] for clave in normalizados]

        variantes = OrdenCompraRepository.obtener_variantes(db, variante_ids)
        variantes_map = {variante.id: variante for variante in variantes}
        for variante_id in variante_ids:
            variante = variantes_map.get(variante_id)
            if variante is None:
                raise DetalleVarianteNoEncontradaError()
            if not variante.estado:
                raise DetalleVarianteInactivaError()

        temporadas = OrdenCompraRepository.obtener_temporadas(
            db, temporada_ids
        )
        temporadas_map = {temporada.id: temporada for temporada in temporadas}
        for temporada_id in temporada_ids:
            temporada = temporadas_map.get(temporada_id)
            if temporada is None:
                raise DetalleTemporadaNoEncontradaError()
            if not temporada.estado:
                raise DetalleTemporadaInactivaError()

        producto_ids = list(
            {variantes_map[clave[0]].producto_id for clave in normalizados}
        )
        asociados = OrdenCompraRepository.productos_asociados_activos(
            db, orden.proveedor_id, producto_ids
        )
        for clave in normalizados:
            if variantes_map[clave[0]].producto_id not in asociados:
                raise DetalleProductoNoAsociadoError()

        actuales = OrdenCompraRepository.listar_detalles(db, orden_id)
        actuales_map = {
            (detalle.variante_producto_id, detalle.temporada_id): detalle
            for detalle in actuales
        }
        sin_cambios = set(actuales_map) == set(normalizados) and all(
            actuales_map[clave].cantidad == normalizados[clave]["cantidad"]
            and Decimal(actuales_map[clave].costo_unitario)
            == Decimal(normalizados[clave]["costo_unitario"])
            for clave in normalizados
        )
        if sin_cambios:
            return _construir_detalles_response(orden_id, actuales)

        try:
            _establecer_contexto_bitacora(db, usuario.id)
            OrdenCompraRepository.reemplazar_detalles(
                db,
                orden_id=orden_id,
                items=list(normalizados.values()),
            )
            registrar_evento_bitacora(
                db,
                usuario_id=usuario.id,
                accion=ACCION_MODIFICAR,
                entidad_afectada=ENTIDAD_DETALLE_ORDEN_COMPRA,
                descripcion=(
                    f"Orden {orden_id}: detalles reemplazados "
                    f"({len(normalizados)} linea(s))"
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DetalleRegistroInvalidoError() from exc

        detalles = OrdenCompraRepository.listar_detalles(db, orden_id)
        return _construir_detalles_response(orden_id, detalles)

    # ------------------------------------------------------------------
    # Transiciones de estado
    # ------------------------------------------------------------------

    @staticmethod
    def enviar(
        db: Session, orden_id: int, usuario: Usuario
    ) -> OrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        if orden.estado != ESTADO_BORRADOR:
            raise TransicionInvalidaError()
        if OrdenCompraRepository.contar_detalles(db, orden.id) == 0:
            raise SinDetallesError()

        _establecer_contexto_bitacora(db, usuario.id)
        orden.estado = ESTADO_ENVIADA
        db.commit()

        orden = OrdenCompraRepository.obtener_por_id(db, orden.id)
        if orden is None:
            raise OrdenCompraNoEncontradaError()
        total = OrdenCompraRepository.contar_detalles(db, orden.id)
        return _construir_orden_response(orden, total)

    @staticmethod
    def cancelar(
        db: Session, orden_id: int, usuario: Usuario
    ) -> OrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        if orden.estado == ESTADO_CANCELADA:
            total = OrdenCompraRepository.contar_detalles(db, orden.id)
            return _construir_orden_response(orden, total)
        if orden.estado not in ESTADOS_CANCELABLES:
            raise TransicionInvalidaError()

        _establecer_contexto_bitacora(db, usuario.id)
        orden.estado = ESTADO_CANCELADA
        db.commit()

        orden = OrdenCompraRepository.obtener_por_id(db, orden.id)
        if orden is None:
            raise OrdenCompraNoEncontradaError()
        total = OrdenCompraRepository.contar_detalles(db, orden.id)
        return _construir_orden_response(orden, total)

    @staticmethod
    def recibir(
        db: Session, orden_id: int, usuario: Usuario
    ) -> OrdenCompraResponse:
        orden = OrdenCompraService._obtener_orden_validada(
            db, orden_id, usuario
        )
        if orden.estado not in (ESTADO_ENVIADA, ESTADO_PARCIAL):
            raise RecepcionInvalidaError()
        if OrdenCompraRepository.contar_detalles(db, orden.id) == 0:
            raise SinDetallesError()

        _establecer_contexto_bitacora(db, usuario.id)
        try:
            db.execute(
                text(
                    "CALL sp_recibir_orden_compra("
                    ":orden_id, :usuario_id)"
                ),
                {"orden_id": orden.id, "usuario_id": usuario.id},
            )
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise RecepcionInvalidaError() from exc

        orden = OrdenCompraRepository.obtener_por_id(db, orden.id)
        if orden is None:
            raise OrdenCompraNoEncontradaError()
        total = OrdenCompraRepository.contar_detalles(db, orden.id)
        return _construir_orden_response(orden, total)
