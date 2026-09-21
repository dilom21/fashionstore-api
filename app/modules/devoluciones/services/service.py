"""Reglas de negocio de CU25 - Registrar devolucion de productos.

Actores oficiales: ADMINISTRADOR (cualquier sucursal) y ENCARGADO_SUCURSAL
(solo su sucursal). CAJERO y CLIENTE no tienen el permiso real
``GESTIONAR_DEVOLUCIONES`` en la base y, ademas, el service aplica una
allowlist explicita de roles.

Ambito: DEVOLUCION FISICA de producto + reingreso a inventario. NO hay
reembolso financiero: no se llama a Stripe, no se crea/modifica ``pago`` y la
Venta original permanece COMPLETADA (puede haber devolucion parcial).

Reglas clave:

- Solo ventas COMPLETADAS admiten devolucion.
- Disponibilidad = cantidad_vendida - SUM(cantidad de devoluciones en
  SOLICITADA/APROBADA/COMPLETADA). RECHAZADA no compromete cantidad.
- Al registrar y al aprobar se bloquean la venta y sus lineas
  (``SELECT ... FOR UPDATE``) y se recalcula dentro de la transaccion.
- El procesamiento (reposicion de stock + movimiento DEVOLUCION) es autoridad
  de ``sp_registrar_devolucion``; no se reimplementa en Python.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.devoluciones.repositories.repository import (
    DevolucionRepository,
)
from app.modules.devoluciones.schemas.schemas import (
    DisponibilidadItemResponse,
    DisponibilidadVentaResponse,
    DevolucionClienteResponse,
    DevolucionDetalleResponse,
    DevolucionItemResponse,
    DevolucionResumenResponse,
    DevolucionSucursalResponse,
    DevolucionesListaResponse,
    DevolucionVentaResumenResponse,
    RegistrarDevolucionRequest,
)

ESTADO_VENTA_COMPLETADA = "COMPLETADA"

ESTADO_SOLICITADA = "SOLICITADA"
ESTADO_APROBADA = "APROBADA"
ESTADO_RECHAZADA = "RECHAZADA"
ESTADO_COMPLETADA = "COMPLETADA"

# Estados que comprometen cantidad de una linea. RECHAZADA no compromete.
ESTADOS_COMPROMETIDOS = (
    ESTADO_SOLICITADA,
    ESTADO_APROBADA,
    ESTADO_COMPLETADA,
)

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROLES_DEVOLUCION = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL)

PAGINA_POR_DEFECTO = 1
TAMANO_PAGINA_POR_DEFECTO = 20
TAMANO_PAGINA_MAXIMO = 50

SQLSTATE_RAISE_EXCEPTION = "P0001"
SQLSTATE_UNIQUE_VIOLATION = "23505"

_CENTIMOS = Decimal("0.01")
SQL_CONTEXTO_BITACORA = text(
    "SELECT set_config('app.usuario_id', :usuario_id, true)"
)


# ---------------------------------------------------------------------------
# Errores de dominio de CU25
# ---------------------------------------------------------------------------


class DevolucionError(Exception):
    """Base de errores de negocio de CU25 (mapped a HTTP en el router)."""


class VentaDevolucionNoEncontradaError(DevolucionError):
    """La venta original no existe."""


class DevolucionNoEncontradaError(DevolucionError):
    """La devolucion no existe."""


class DevolucionScopeError(DevolucionError):
    """La operacion esta fuera del alcance de sucursal del usuario."""


class DevolucionRolNoAutorizadoError(DevolucionError):
    """El rol autenticado no esta admitido en CU25."""


class VentaNoDevolvibleError(DevolucionError):
    """La venta no esta COMPLETADA o no tiene lineas que devolver."""


class DetalleVentaNoValidoError(DevolucionError):
    """El detalle de venta no existe o no pertenece a esa venta."""


class DetalleDevolucionDuplicadoError(DevolucionError):
    """La misma linea aparece dos veces en la solicitud."""


class CantidadDevolucionInvalidaError(DevolucionError):
    """La cantidad solicitada no es valida."""


class CantidadDevolucionNoDisponibleError(DevolucionError):
    """No hay cantidad disponible suficiente (sobredevolucion)."""


class DevolucionSinItemsError(DevolucionError):
    """La devolucion no tiene lineas."""


class EstadoDevolucionInvalidoError(DevolucionError):
    """Transicion de estado no permitida."""


class ProcesamientoDevolucionError(DevolucionError):
    """El procedimiento rechazo el procesamiento por regla de negocio."""


class RangoFechasDevolucionError(DevolucionError):
    """fecha_desde posterior a fecha_hasta (422)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rango_fechas(
    fecha_desde: date | None, fecha_hasta: date | None
) -> tuple[datetime | None, datetime | None]:
    """Rango semiabierto [desde, hasta+1 dia) para incluir dias completos."""
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_desde > fecha_hasta:
            raise RangoFechasDevolucionError()

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


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    """``app.usuario_id`` para trg_bitacora_devolucion (no escribe bitacora)."""
    db.execute(SQL_CONTEXTO_BITACORA, {"usuario_id": str(usuario_id)})


def _sqlstate(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    valor = getattr(origen, "sqlstate", None)
    return str(valor) if valor else None


def _mensaje_bd(exc: DBAPIError) -> str:
    origen = getattr(exc, "orig", None)
    return str(origen or exc).lower()


def _traducir_error_bd(exc: DBAPIError) -> DevolucionError:
    """Traduce errores de PostgreSQL a errores de negocio (sin SQL crudo)."""
    estado = _sqlstate(exc)
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        return DetalleDevolucionDuplicadoError()

    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "no existe" in mensaje:
            return DevolucionNoEncontradaError()
        if "aprobada" in mensaje:
            return EstadoDevolucionInvalidoError()
        if "no contiene" in mensaje or "no tiene detalle" in mensaje:
            return DevolucionSinItemsError()
        if (
            "devolver" in mensaje
            or "vendier" in mensaje
            or "excede" in mensaje
        ):
            return CantidadDevolucionNoDisponibleError()
        return ProcesamientoDevolucionError()

    return ProcesamientoDevolucionError()


# ---------------------------------------------------------------------------
# Builders de respuesta
# ---------------------------------------------------------------------------


def _sucursal_response(venta) -> DevolucionSucursalResponse:
    sucursal = venta.sucursal
    return DevolucionSucursalResponse(
        id=int(sucursal.id),
        nombre=sucursal.nombre,
        direccion=sucursal.direccion,
    )


def _cliente_response(db: Session, venta) -> DevolucionClienteResponse | None:
    """Cliente real de la venta; ``None`` si la venta fue anonima."""
    if venta.cliente_id is None:
        return None
    cliente = DevolucionRepository.obtener_cliente(db, int(venta.cliente_id))
    if cliente is None:
        return None
    return DevolucionClienteResponse(
        id=int(cliente.id),
        nombre=cliente.nombre,
        apellido=cliente.apellido,
    )


def _venta_resumen(venta) -> DevolucionVentaResumenResponse:
    return DevolucionVentaResumenResponse(
        venta_id=int(venta.id),
        fecha_hora=venta.fecha_hora,
        canal=venta.canal,
        total=Decimal(venta.total),
    )


def _filas_disponibilidad(venta, comprometidas: dict[int, int]) -> list[dict]:
    """Disponibilidad por linea: vendida - comprometida (nunca negativa)."""
    filas = []
    for detalle in venta.detalles:
        vendida = int(detalle.cantidad)
        comprometida = int(comprometidas.get(int(detalle.id), 0))
        disponible = vendida - comprometida
        filas.append(
            {
                "detalle": detalle,
                "cantidad_vendida": vendida,
                "cantidad_comprometida": comprometida,
                "cantidad_disponible": disponible if disponible > 0 else 0,
            }
        )
    return filas


def _item_disponibilidad(fila: dict) -> DisponibilidadItemResponse:
    detalle = fila["detalle"]
    variante = detalle.inventario.variante_producto
    producto = variante.producto
    return DisponibilidadItemResponse(
        detalle_venta_id=int(detalle.id),
        inventario_id=int(detalle.inventario_id),
        producto_id=int(producto.id),
        producto_nombre=producto.nombre,
        sku=variante.sku,
        talla_nombre=variante.talla.nombre,
        color_nombre=variante.color.nombre,
        cantidad_vendida=fila["cantidad_vendida"],
        cantidad_comprometida=fila["cantidad_comprometida"],
        cantidad_disponible=fila["cantidad_disponible"],
        precio_unitario=Decimal(detalle.precio_unitario),
    )


def _item_devolucion(detalle_dev) -> DevolucionItemResponse:
    detalle_venta = detalle_dev.detalle_venta
    variante = detalle_venta.inventario.variante_producto
    producto = variante.producto
    cantidad_solicitada = int(detalle_dev.cantidad)
    precio = Decimal(detalle_venta.precio_unitario)
    return DevolucionItemResponse(
        detalle_devolucion_id=int(detalle_dev.id),
        detalle_venta_id=int(detalle_dev.detalle_venta_id),
        producto_id=int(producto.id),
        producto_nombre=producto.nombre,
        sku=variante.sku,
        talla_nombre=variante.talla.nombre,
        color_nombre=variante.color.nombre,
        cantidad_vendida=int(detalle_venta.cantidad),
        cantidad_solicitada=cantidad_solicitada,
        precio_unitario=precio,
        subtotal_referencial=(precio * cantidad_solicitada).quantize(
            _CENTIMOS, rounding=ROUND_HALF_UP
        ),
        motivo=detalle_dev.motivo,
    )


def _detalle_response(
    db: Session, devolucion
) -> DevolucionDetalleResponse:
    venta = devolucion.venta
    items = [_item_devolucion(detalle) for detalle in devolucion.detalles]
    return DevolucionDetalleResponse(
        devolucion_id=int(devolucion.id),
        venta_id=int(devolucion.venta_id),
        fecha_hora=devolucion.fecha_hora,
        motivo=devolucion.motivo,
        observacion=devolucion.observacion,
        estado=devolucion.estado,
        venta=_venta_resumen(venta),
        sucursal=_sucursal_response(venta),
        cliente=_cliente_response(db, venta),
        items=items,
        cantidad_total_unidades=sum(
            item.cantidad_solicitada for item in items
        ),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _validar_rol(usuario: Usuario) -> str:
    """ADMINISTRADOR y ENCARGADO_SUCURSAL unicamente (RBAC no basta)."""
    rol = str(usuario.rol.nombre).strip().upper()
    if rol not in ROLES_DEVOLUCION:
        raise DevolucionRolNoAutorizadoError()
    return rol


def _sucursal_alcance(usuario: Usuario) -> int | None:
    """None = todas las sucursales (ADMINISTRADOR); int = su sucursal."""
    rol = _validar_rol(usuario)
    if rol == ROL_ADMINISTRADOR:
        return None
    empleado = usuario.empleado
    if empleado is None or not empleado.estado or empleado.sucursal_id is None:
        raise DevolucionScopeError()
    return int(empleado.sucursal_id)


def _validar_alcance(usuario: Usuario, sucursal_id: int) -> None:
    alcance = _sucursal_alcance(usuario)
    if alcance is not None and int(sucursal_id) != alcance:
        raise DevolucionScopeError()


class DevolucionService:
    """CU25 - Devolucion fisica de productos (sin reembolso financiero)."""

    # ------------------------------------------------------------------
    # Consulta previa de la venta
    # ------------------------------------------------------------------

    @staticmethod
    def consultar_disponibilidad_venta(
        db: Session, usuario: Usuario, venta_id: int
    ) -> DisponibilidadVentaResponse:
        """Venta COMPLETADA + disponibilidad real por linea (solo lectura)."""
        _validar_rol(usuario)
        if int(venta_id) <= 0:
            raise VentaDevolucionNoEncontradaError()

        venta = DevolucionRepository.obtener_venta_para_devolucion(
            db, int(venta_id)
        )
        if venta is None:
            raise VentaDevolucionNoEncontradaError()

        _validar_alcance(usuario, int(venta.sucursal_id))

        if venta.estado != ESTADO_VENTA_COMPLETADA:
            raise VentaNoDevolvibleError()
        if not venta.detalles:
            raise VentaNoDevolvibleError()

        comprometidas = DevolucionRepository.cantidades_comprometidas(
            db, int(venta.id), estados=ESTADOS_COMPROMETIDOS
        )
        items = [
            _item_disponibilidad(fila)
            for fila in _filas_disponibilidad(venta, comprometidas)
        ]
        return DisponibilidadVentaResponse(
            venta_id=int(venta.id),
            fecha_hora=venta.fecha_hora,
            estado=venta.estado,
            canal=venta.canal,
            total=Decimal(venta.total),
            sucursal=_sucursal_response(venta),
            cliente=_cliente_response(db, venta),
            items=items,
            cantidad_total_unidades=sum(
                item.cantidad_vendida for item in items
            ),
        )

    # ------------------------------------------------------------------
    # Registrar (SOLICITADA)
    # ------------------------------------------------------------------

    @staticmethod
    def registrar_devolucion(
        db: Session, usuario: Usuario, datos: RegistrarDevolucionRequest
    ) -> DevolucionDetalleResponse:
        """Crea la solicitud (SOLICITADA) en una sola transaccion atomica."""
        _validar_rol(usuario)

        venta_id = int(datos.venta_id)
        ids_solicitados = [int(item.detalle_venta_id) for item in datos.items]
        if len(set(ids_solicitados)) != len(ids_solicitados):
            raise DetalleDevolucionDuplicadoError()
        for item in datos.items:
            if int(item.cantidad) <= 0:
                raise CantidadDevolucionInvalidaError()

        try:
            venta = DevolucionRepository.obtener_venta_para_devolucion(
                db, venta_id
            )
            if venta is None:
                raise VentaDevolucionNoEncontradaError()
            _validar_alcance(usuario, int(venta.sucursal_id))
            if venta.estado != ESTADO_VENTA_COMPLETADA:
                raise VentaNoDevolvibleError()

            # Concurrencia: bloquear venta + lineas antes de recalcular.
            DevolucionRepository.bloquear_venta(db, venta_id)
            detalles_bloqueados = (
                DevolucionRepository.bloquear_detalles_venta(db, venta_id)
            )
            if not detalles_bloqueados:
                raise VentaNoDevolvibleError()

            vendidas = {
                int(detalle.id): int(detalle.cantidad)
                for detalle in detalles_bloqueados
            }
            comprometidas = DevolucionRepository.cantidades_comprometidas(
                db, venta_id, estados=ESTADOS_COMPROMETIDOS
            )

            for item in datos.items:
                detalle_id = int(item.detalle_venta_id)
                if detalle_id not in vendidas:
                    raise DetalleVentaNoValidoError()
                disponible = vendidas[detalle_id] - int(
                    comprometidas.get(detalle_id, 0)
                )
                if disponible < 0:
                    disponible = 0
                if int(item.cantidad) > disponible:
                    raise CantidadDevolucionNoDisponibleError()

            _establecer_contexto_bitacora(db, int(usuario.id))
            devolucion = DevolucionRepository.crear_devolucion(
                db,
                venta_id=venta_id,
                motivo=datos.motivo,
                observacion=datos.observacion,
                fecha_hora=datetime.now(timezone.utc),
            )
            for item in datos.items:
                motivo_linea = (item.motivo or "").strip() or None
                DevolucionRepository.crear_detalle(
                    db,
                    devolucion_id=int(devolucion.id),
                    detalle_venta_id=int(item.detalle_venta_id),
                    cantidad=int(item.cantidad),
                    motivo=motivo_linea,
                )

            # Atomicidad: la devolucion se recarga y el DTO se construye DENTRO
            # de la transaccion. Si cualquiera de estos pasos falla, el rollback
            # deja la base sin ninguna devolucion SOLICITADA persistida.
            creada = DevolucionRepository.obtener_por_id(
                db, int(devolucion.id)
            )
            if creada is None:
                raise DevolucionNoEncontradaError()
            respuesta = _detalle_response(db, creada)

            db.commit()
        except DevolucionError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return respuesta

    # ------------------------------------------------------------------
    # Listado y detalle (solo lectura)
    # ------------------------------------------------------------------

    @staticmethod
    def listar_devoluciones(
        db: Session,
        usuario: Usuario,
        *,
        estado: str | None = None,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        pagina: int = PAGINA_POR_DEFECTO,
        tamano_pagina: int = TAMANO_PAGINA_POR_DEFECTO,
    ) -> DevolucionesListaResponse:
        """Listado paginado en SQL, acotado al alcance del usuario."""
        alcance = _sucursal_alcance(usuario)
        if alcance is None:
            # ADMINISTRADOR: puede filtrar por sucursal o ver todas.
            efectiva = int(sucursal_id) if sucursal_id is not None else None
        else:
            # ENCARGADO_SUCURSAL: siempre su sucursal (no amplia alcance).
            if sucursal_id is not None and int(sucursal_id) != alcance:
                raise DevolucionScopeError()
            efectiva = alcance

        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)
        offset = (int(pagina) - 1) * int(tamano_pagina)

        filas = DevolucionRepository.listar_devoluciones(
            db,
            sucursal_id=efectiva,
            estado=estado,
            desde=desde,
            hasta=hasta,
            limit=int(tamano_pagina),
            offset=offset,
        )
        total_registros = DevolucionRepository.contar_devoluciones(
            db,
            sucursal_id=efectiva,
            estado=estado,
            desde=desde,
            hasta=hasta,
        )

        items = [
            DevolucionResumenResponse(
                devolucion_id=int(fila.devolucion_id),
                venta_id=int(fila.venta_id),
                fecha_hora=fila.fecha_hora,
                estado=fila.estado,
                motivo=fila.motivo,
                sucursal_id=int(fila.sucursal_id),
                sucursal_nombre=fila.sucursal_nombre,
                cantidad_lineas=int(fila.cantidad_lineas),
                cantidad_total_unidades=int(fila.cantidad_total_unidades),
            )
            for fila in filas
        ]
        total_paginas = (
            (total_registros + int(tamano_pagina) - 1) // int(tamano_pagina)
            if total_registros
            else 0
        )
        return DevolucionesListaResponse(
            items=items,
            pagina=int(pagina),
            tamano_pagina=int(tamano_pagina),
            total_registros=total_registros,
            total_paginas=total_paginas,
        )

    @staticmethod
    def obtener_detalle(
        db: Session, usuario: Usuario, devolucion_id: int
    ) -> DevolucionDetalleResponse:
        """Detalle completo de una devolucion dentro del alcance del usuario."""
        _validar_rol(usuario)
        if int(devolucion_id) <= 0:
            raise DevolucionNoEncontradaError()

        devolucion = DevolucionRepository.obtener_por_id(db, int(devolucion_id))
        if devolucion is None:
            raise DevolucionNoEncontradaError()

        _validar_alcance(usuario, int(devolucion.venta.sucursal_id))
        return _detalle_response(db, devolucion)

    # ------------------------------------------------------------------
    # Aprobar / rechazar (permiso EDITAR)
    # ------------------------------------------------------------------

    @staticmethod
    def aprobar(
        db: Session, usuario: Usuario, devolucion_id: int
    ) -> DevolucionDetalleResponse:
        """SOLICITADA -> APROBADA revalidando disponibilidad con bloqueo."""
        _validar_rol(usuario)
        if int(devolucion_id) <= 0:
            raise DevolucionNoEncontradaError()

        try:
            devolucion = DevolucionRepository.bloquear_devolucion(
                db, int(devolucion_id)
            )
            if devolucion is None:
                raise DevolucionNoEncontradaError()

            venta = DevolucionRepository.obtener_venta_para_devolucion(
                db, int(devolucion.venta_id)
            )
            if venta is None:
                raise VentaDevolucionNoEncontradaError()
            _validar_alcance(usuario, int(venta.sucursal_id))

            if devolucion.estado != ESTADO_SOLICITADA:
                raise EstadoDevolucionInvalidoError()

            detalles = DevolucionRepository.listar_detalles(
                db, int(devolucion.id)
            )
            if not detalles:
                raise DevolucionSinItemsError()

            # Concurrencia: bloquear lineas y recalcular excluyendo esta
            # devolucion (una APROBADA previa si compromete cantidad).
            DevolucionRepository.bloquear_detalles_venta(db, int(venta.id))
            vendidas = {
                int(detalle.id): int(detalle.cantidad)
                for detalle in venta.detalles
            }
            comprometidas = DevolucionRepository.cantidades_comprometidas(
                db,
                int(venta.id),
                estados=ESTADOS_COMPROMETIDOS,
                excluir_devolucion_id=int(devolucion.id),
            )
            for detalle in detalles:
                detalle_venta_id = int(detalle.detalle_venta_id)
                if detalle_venta_id not in vendidas:
                    raise DetalleVentaNoValidoError()
                disponible = vendidas[detalle_venta_id] - int(
                    comprometidas.get(detalle_venta_id, 0)
                )
                if disponible < 0:
                    disponible = 0
                if int(detalle.cantidad) > disponible:
                    raise CantidadDevolucionNoDisponibleError()

            _establecer_contexto_bitacora(db, int(usuario.id))
            DevolucionRepository.actualizar_estado(
                db, devolucion, ESTADO_APROBADA
            )

            # Atomicidad: el DTO se construye antes del unico commit.
            aprobada = DevolucionRepository.obtener_por_id(
                db, int(devolucion.id)
            )
            if aprobada is None:
                raise DevolucionNoEncontradaError()
            respuesta = _detalle_response(db, aprobada)

            db.commit()
        except DevolucionError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return respuesta

    @staticmethod
    def rechazar(
        db: Session, usuario: Usuario, devolucion_id: int
    ) -> DevolucionDetalleResponse:
        """SOLICITADA -> RECHAZADA. No toca inventario ni crea movimientos."""
        _validar_rol(usuario)
        if int(devolucion_id) <= 0:
            raise DevolucionNoEncontradaError()

        try:
            devolucion = DevolucionRepository.bloquear_devolucion(
                db, int(devolucion_id)
            )
            if devolucion is None:
                raise DevolucionNoEncontradaError()

            venta = DevolucionRepository.obtener_venta_para_devolucion(
                db, int(devolucion.venta_id)
            )
            if venta is None:
                raise VentaDevolucionNoEncontradaError()
            _validar_alcance(usuario, int(venta.sucursal_id))

            if devolucion.estado != ESTADO_SOLICITADA:
                raise EstadoDevolucionInvalidoError()

            _establecer_contexto_bitacora(db, int(usuario.id))
            DevolucionRepository.actualizar_estado(
                db, devolucion, ESTADO_RECHAZADA
            )

            # Atomicidad: el DTO se construye antes del unico commit.
            rechazada = DevolucionRepository.obtener_por_id(
                db, int(devolucion.id)
            )
            if rechazada is None:
                raise DevolucionNoEncontradaError()
            respuesta = _detalle_response(db, rechazada)

            db.commit()
        except DevolucionError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return respuesta

    # ------------------------------------------------------------------
    # Procesar (permiso EJECUTAR)
    # ------------------------------------------------------------------

    @staticmethod
    def procesar(
        db: Session, usuario: Usuario, devolucion_id: int
    ) -> DevolucionDetalleResponse:
        """APROBADA -> sp_registrar_devolucion -> COMPLETADA.

        El procedimiento es la autoridad: repone ``inventario.stock_actual``,
        registra el ``MovimientoInventario`` DEVOLUCION y marca COMPLETADA.
        Una segunda llamada encuentra la devolucion ya COMPLETADA y responde
        409 sin volver a sumar stock (no se reimplementa nada en Python).
        """
        _validar_rol(usuario)
        if int(devolucion_id) <= 0:
            raise DevolucionNoEncontradaError()

        try:
            devolucion = DevolucionRepository.bloquear_devolucion(
                db, int(devolucion_id)
            )
            if devolucion is None:
                raise DevolucionNoEncontradaError()

            venta = DevolucionRepository.obtener_venta_para_devolucion(
                db, int(devolucion.venta_id)
            )
            if venta is None:
                raise VentaDevolucionNoEncontradaError()
            _validar_alcance(usuario, int(venta.sucursal_id))

            # Solo APROBADA se procesa (evita doble reposicion de stock).
            if devolucion.estado != ESTADO_APROBADA:
                raise EstadoDevolucionInvalidoError()

            _establecer_contexto_bitacora(db, int(usuario.id))
            DevolucionRepository.procesar_devolucion(
                db,
                devolucion_id=int(devolucion.id),
                usuario_id=int(usuario.id),
            )

            # El SP modifica la fila directamente en PostgreSQL: la instancia
            # ORM de esta sesion sigue con estado APROBADA. Se fuerza la
            # sincronizacion con la BD (populate_existing) y se comprueba el
            # estado REAL antes de construir el DTO.
            completada = DevolucionRepository.obtener_por_id(
                db, int(devolucion.id), populate_existing=True
            )
            if completada is None:
                raise DevolucionNoEncontradaError()
            if completada.estado != ESTADO_COMPLETADA:
                raise ProcesamientoDevolucionError()
            respuesta = _detalle_response(db, completada)

            db.commit()
        except DevolucionError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        return respuesta






