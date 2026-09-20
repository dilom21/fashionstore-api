"""Reglas de negocio de CU18 - Atender reserva de prendas.

Actores: ADMINISTRADOR (alcance global: ve, filtra y atiende cualquier
sucursal), ENCARGADO_SUCURSAL y CAJERO (solo su propia sucursal, sin poder
ampliar alcance con sucursal_id). CLIENTE queda fuera.

Solo se atienden reservas CONFIRMADA. Para ENCARGADO_SUCURSAL/CAJERO la
sucursal sale de usuario -> empleado -> sucursal_id y nunca del request.

Preparar venta es SOLO validacion/normalizacion de la seleccion: no crea venta,
no toca inventario, no cambia la reserva y no libera nada (eso ocurrira cuando
el modulo Venta/Pago complete la operacion).

Finalizar sin compra delega en el procedimiento existente
sp_finalizar_reserva_sin_compra, que es la autoridad transaccional.
"""

from datetime import date

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.reservas.repositories.repository import (
    ESTADO_CONFIRMADA,
    ReservaRepository,
)
from app.modules.reservas.schemas.schemas import (
    AtencionReservaDetalleResponse,
    AtencionReservaItemResponse,
    AtencionReservaListaResponse,
    AtencionReservaResumenResponse,
    FinalizarSinCompraRequest,
    PrepararVentaItemResponse,
    PrepararVentaRequest,
    PrepararVentaResponse,
    VentaAsociadaAtencionResponse,
)
from app.modules.reservas.services.gestion_service import (
    LIMITE_POR_DEFECTO,
    _rango_fechas,
)
from app.modules.reservas.services.service import (
    ReservaError,
    ReservaEstadoInvalidoError,
    ReservaNoEncontradaError,
    _resolver_imagenes,
    _traducir_error_bd,
)
from app.modules.ventas.repositories.repository import VentaRepository

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
# ADMINISTRADOR: alcance global (todas las sucursales).
ROLES_ATENCION_RESERVAS = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL, ROL_CAJERO)
# Roles que exigen Empleado activo y quedan atados a su sucursal.
ROLES_ATENCION_CON_SUCURSAL = (ROL_ENCARGADO_SUCURSAL, ROL_CAJERO)


class RolAtencionReservaNoAutorizadoError(ReservaError):
    """El rol autenticado no puede atender reservas (CU18)."""


class AtencionReservaScopeError(ReservaError):
    """La reserva esta fuera de la sucursal del empleado autenticado."""


class SeleccionVentaInvalidaError(ReservaError):
    """Seleccion de compra invalida para la reserva (422)."""


class ReservaConVentaAsociadaError(ReservaError):
    """La reserva ya tiene una venta asociada (CU20): no admite sin compra.

    Una vez que existe ``venta.reserva_id = reserva.id`` la atencion queda
    comprometida con Venta/Pago: finalizar sin compra liberaria la reserva y
    dejaria la venta PENDIENTE sin pago (estado invalido para CU21).
    """


def _item_atencion(
    detalle: DetalleReserva, imagen: str | None
) -> AtencionReservaItemResponse:
    inventario = detalle.inventario
    variante = inventario.variante_producto
    producto = variante.producto
    return AtencionReservaItemResponse(
        detalle_id=detalle.id,
        inventario_id=detalle.inventario_id,
        producto_id=producto.id,
        producto_nombre=producto.nombre,
        imagen_principal=imagen,
        variante_producto_id=variante.id,
        sku=variante.sku,
        talla_id=variante.talla.id,
        talla_nombre=variante.talla.nombre,
        color_id=variante.color.id,
        color_nombre=variante.color.nombre,
        temporada_id=inventario.temporada_id,
        temporada_nombre=inventario.temporada.nombre,
        cantidad_reservada=detalle.cantidad,
    )


def _resumen_atencion(reserva: Reserva) -> AtencionReservaResumenResponse:
    cliente = reserva.cliente
    return AtencionReservaResumenResponse(
        reserva_id=reserva.id,
        cliente_id=reserva.cliente_id,
        cliente_nombre=cliente.nombre,
        cliente_apellido=cliente.apellido,
        cliente_telefono=cliente.telefono,
        sucursal_id=reserva.sucursal_id,
        sucursal_nombre=reserva.sucursal.nombre,
        fecha_reserva=reserva.fecha_reserva,
        fecha_atencion=reserva.fecha_atencion,
        estado=reserva.estado,
        observacion=reserva.observacion,
        cantidad_lineas=len(reserva.detalles),
        cantidad_unidades=sum(
            detalle.cantidad for detalle in reserva.detalles
        ),
    )


def _detalle_atencion(
    db: Session, reserva: Reserva
) -> AtencionReservaDetalleResponse:
    detalles = list(reserva.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[AtencionReservaItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_atencion(detalle, imagen))

    cliente = reserva.cliente
    venta = VentaRepository.obtener_por_reserva(db, reserva.id)
    venta_asociada = (
        VentaAsociadaAtencionResponse(
            venta_id=int(venta.id),
            estado=venta.estado,
            total=venta.total,
            canal=venta.canal,
        )
        if venta is not None
        else None
    )
    return AtencionReservaDetalleResponse(
        reserva_id=reserva.id,
        cliente_id=reserva.cliente_id,
        cliente_nombre=cliente.nombre,
        cliente_apellido=cliente.apellido,
        cliente_telefono=cliente.telefono,
        sucursal_id=reserva.sucursal_id,
        sucursal_nombre=reserva.sucursal.nombre,
        fecha_reserva=reserva.fecha_reserva,
        fecha_atencion=reserva.fecha_atencion,
        estado=reserva.estado,
        observacion=reserva.observacion,
        items=items,
        cantidad_total_unidades=sum(item.cantidad_reservada for item in items),
        venta_asociada=venta_asociada,
    )


class AtencionReservaService:
    """Reglas de negocio de CU18 - Atender reserva de prendas."""

    @staticmethod
    def _validar_rol_atencion(usuario: Usuario) -> str:
        """ADMINISTRADOR, ENCARGADO_SUCURSAL o CAJERO (CLIENTE: 403).

        El RBAC no basta: otros roles pueden tener permisos genericos sobre
        reservas por CU16/CU17 y no deben entrar aqui.
        """
        rol = str(usuario.rol.nombre).strip().upper()
        if rol not in ROLES_ATENCION_RESERVAS:
            raise RolAtencionReservaNoAutorizadoError()
        return rol

    @staticmethod
    def _sucursal_empleado(usuario: Usuario) -> int:
        """Sucursal propia del empleado (ENCARGADO_SUCURSAL / CAJERO)."""
        empleado = usuario.empleado
        if empleado is None or not empleado.estado or empleado.sucursal_id is None:
            raise AtencionReservaScopeError()
        return int(empleado.sucursal_id)

    @staticmethod
    def _sucursal_alcance(usuario: Usuario) -> int | None:
        """Alcance efectivo de lectura/atencion.

        None = todas las sucursales (solo ADMINISTRADOR).
        ENCARGADO_SUCURSAL/CAJERO = la sucursal de su empleado.
        """
        rol = AtencionReservaService._validar_rol_atencion(usuario)
        if rol == ROL_ADMINISTRADOR:
            return None
        return AtencionReservaService._sucursal_empleado(usuario)

    @staticmethod
    def resolver_sucursal_listado(
        usuario: Usuario, sucursal_id: int | None
    ) -> int | None:
        """Sucursal efectiva del listado.

        ADMINISTRADOR: todas (None) o la que indique con sucursal_id.
        ENCARGADO_SUCURSAL/CAJERO: solo la suya; un sucursal_id distinto es 403
        (no pueden ampliar alcance desde el request).
        """
        rol = AtencionReservaService._validar_rol_atencion(usuario)
        if rol == ROL_ADMINISTRADOR:
            return int(sucursal_id) if sucursal_id is not None else None

        propia = AtencionReservaService._sucursal_empleado(usuario)
        if sucursal_id is not None and int(sucursal_id) != propia:
            raise AtencionReservaScopeError()
        return propia

    @staticmethod
    def _validar_alcance_reserva(usuario: Usuario, reserva: Reserva) -> None:
        """403 si la reserva esta fuera del alcance del usuario."""
        alcance = AtencionReservaService._sucursal_alcance(usuario)
        if alcance is not None and int(reserva.sucursal_id) != alcance:
            raise AtencionReservaScopeError()

    @staticmethod
    def _obtener_confirmada(
        db: Session, usuario: Usuario, reserva_id: int
    ) -> Reserva:
        """Rol/alcance -> 404 -> 403 -> 409 (no atendible)."""
        AtencionReservaService._validar_rol_atencion(usuario)
        reserva = ReservaRepository.obtener_por_id(db, reserva_id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        AtencionReservaService._validar_alcance_reserva(usuario, reserva)
        if reserva.estado != ESTADO_CONFIRMADA:
            raise ReservaEstadoInvalidoError()
        return reserva

    @staticmethod
    def listar(
        db: Session,
        usuario: Usuario,
        *,
        sucursal_id: int | None = None,
        buscar: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        limit: int = LIMITE_POR_DEFECTO,
        offset: int = 0,
    ) -> AtencionReservaListaResponse:
        """Reservas CONFIRMADA atendibles (proximas primero).

        ADMINISTRADOR: todas las sucursales, o solo la indicada en sucursal_id.
        ENCARGADO_SUCURSAL/CAJERO: unicamente la de su empleado.
        """
        alcance = AtencionReservaService.resolver_sucursal_listado(
            usuario, sucursal_id
        )
        desde, hasta = _rango_fechas(fecha_desde, fecha_hasta)

        reservas, total = ReservaRepository.listar_gestion(
            db,
            sucursal_id=alcance,
            estado=ESTADO_CONFIRMADA,
            buscar=buscar,
            fecha_desde=desde,
            fecha_hasta=hasta,
            limit=limit,
            offset=offset,
        )
        return AtencionReservaListaResponse(
            items=[_resumen_atencion(reserva) for reserva in reservas],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def obtener(
        db: Session, usuario: Usuario, reserva_id: int
    ) -> AtencionReservaDetalleResponse:
        reserva = AtencionReservaService._obtener_confirmada(
            db, usuario, reserva_id
        )
        return _detalle_atencion(db, reserva)

    @staticmethod
    def preparar_venta(
        db: Session,
        usuario: Usuario,
        reserva_id: int,
        datos: PrepararVentaRequest,
    ) -> PrepararVentaResponse:
        """Valida y normaliza la seleccion de compra. NO modifica nada.

        Devuelve el resultado completo de la reserva: por cada linea reservada
        indica cantidad_reservada, cantidad_compra y cantidad_no_compra. Las
        unidades no compradas NO se liberan aqui: la reserva sigue CONFIRMADA,
        el inventario y los movimientos quedan intactos y sera el modulo
        Venta/Pago quien complete la operacion (sp_confirmar_venta).
        """
        reserva = AtencionReservaService._obtener_confirmada(
            db, usuario, reserva_id
        )

        reservado = {
            int(detalle.inventario_id): int(detalle.cantidad)
            for detalle in reserva.detalles
        }
        if not reservado:
            raise ReservaEstadoInvalidoError()

        seleccion: dict[int, int] = {}
        for item in datos.items:
            inventario_id = int(item.inventario_id)
            if inventario_id in seleccion:
                raise SeleccionVentaInvalidaError()
            if inventario_id not in reservado:
                raise SeleccionVentaInvalidaError()
            if int(item.cantidad_compra) > reservado[inventario_id]:
                raise SeleccionVentaInvalidaError()
            seleccion[inventario_id] = int(item.cantidad_compra)

        items: list[PrepararVentaItemResponse] = []
        for inventario_id in sorted(reservado):
            cantidad_reservada = reservado[inventario_id]
            cantidad_compra = seleccion.get(inventario_id, 0)
            items.append(
                PrepararVentaItemResponse(
                    inventario_id=inventario_id,
                    cantidad_reservada=cantidad_reservada,
                    cantidad_compra=cantidad_compra,
                    cantidad_no_compra=cantidad_reservada - cantidad_compra,
                )
            )

        return PrepararVentaResponse(
            reserva_id=reserva.id,
            sucursal_id=reserva.sucursal_id,
            estado=reserva.estado,
            items=items,
            total_unidades_reservadas=sum(
                item.cantidad_reservada for item in items
            ),
            total_unidades_compra=sum(item.cantidad_compra for item in items),
            total_unidades_no_compra=sum(
                item.cantidad_no_compra for item in items
            ),
        )

    @staticmethod
    def finalizar_sin_compra(
        db: Session,
        usuario: Usuario,
        reserva_id: int,
        datos: FinalizarSinCompraRequest | None = None,
    ) -> AtencionReservaDetalleResponse:
        """CONFIRMADA -> ATENDIDA liberando todo lo reservado (via SP).

        El procedimiento sp_finalizar_reserva_sin_compra libera unicamente
        stock_reservado, registra LIBERACION_RESERVA por detalle y marca la
        reserva ATENDIDA. Si falla -> rollback completo (sin liberaciones
        parciales y sin tocar stock_actual).
        """
        reserva = AtencionReservaService._obtener_confirmada(
            db, usuario, reserva_id
        )

        # Una reserva con venta asociada (CU20) queda comprometida con
        # Venta/Pago: finalizar sin compra liberaria la reserva y dejaria la
        # venta PENDIENTE sin pago. No se ejecuta el SP ni se toca inventario.
        if VentaRepository.obtener_por_reserva(db, reserva.id) is not None:
            raise ReservaConVentaAsociadaError()

        observacion = ((datos.observacion if datos else None) or "").strip() or None
        try:
            ReservaRepository.finalizar_sin_compra(
                db,
                reserva_id=reserva.id,
                usuario_id=int(usuario.id),
                observacion=observacion,
            )
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        reserva = ReservaRepository.obtener_por_id(db, reserva.id)
        if reserva is None:
            raise ReservaNoEncontradaError()
        return _detalle_atencion(db, reserva)
