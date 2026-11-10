"""CU12 SaaS: gestion y atencion de reservas dentro de una empresa.

Reutiliza las reglas de CU17/CU18 del primer parcial y sustituye la identidad
``Usuario`` legacy por el ``TenantContext`` firmado. La empresa nunca se toma
del body; la sucursal efectiva se deriva del empleado autenticado (salvo el
ADMINISTRADOR, que conserva alcance global dentro de su empresa).

Las escrituras delegan en los procedimientos tenant de PostgreSQL:
- confirmar: sp_confirmar_reserva_tenant
- cancelar: sp_cancelar_reserva_tenant (paquete CU11)
- finalizar sin compra: sp_finalizar_reserva_sin_compra_tenant (CU11)
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.reservas.models.models import Reserva
from app.modules.reservas.repositories.repository import (
    ESTADO_CONFIRMADA,
    ESTADO_PENDIENTE,
    ESTADOS_CANCELABLES,
)
from app.modules.reservas.repositories.tenant_gestion_repository import (
    ReservaTenantGestionRepository,
)
from app.modules.reservas.schemas.schemas import (
    AtencionReservaDetalleResponse,
    AtencionReservaItemResponse,
    AtencionReservaListaResponse,
    FinalizarSinCompraRequest,
    PrepararVentaItemResponse,
    PrepararVentaRequest,
    PrepararVentaResponse,
    ReservaSucursalDetalleResponse,
    ReservaSucursalListaResponse,
    VentaAsociadaAtencionResponse,
)
from app.modules.reservas.schemas.tenant_schemas import (
    ReservaTenantEstadisticasResponse,
)
from app.modules.reservas.services.atencion_service import (
    _item_atencion,
    _resumen_atencion,
)
from app.modules.reservas.services.gestion_service import (
    RangoFechasInvalidoError,
    _detalle_response as _detalle_gestion_response,
    _rango_fechas,
    _resumen_response,
)
from app.modules.reservas.services.service import _resolver_imagenes
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    cancelar_reserva_transaccional_tenant,
    confirmar_reserva_transaccional_tenant,
    finalizar_reserva_sin_compra_transaccional_tenant,
)
from app.modules.sucursales.models.models import Sucursal

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
ROLES_GESTION = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL)
ROLES_ATENCION = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL, ROL_CAJERO)

LIMITE_POR_DEFECTO = 20
LIMITE_MAXIMO = 100


class ReservaTenantError(Exception):
    """Base de errores de CU12 SaaS."""


class ReservaTenantRolNoAutorizadoError(ReservaTenantError):
    pass


class ReservaTenantScopeError(ReservaTenantError):
    pass


class ReservaTenantSucursalNoEncontradaError(ReservaTenantError):
    pass


class ReservaTenantNoEncontradaError(ReservaTenantError):
    pass


class ReservaTenantEstadoError(ReservaTenantError):
    pass


class ReservaTenantVentaAsociadaError(ReservaTenantError):
    pass


class ReservaTenantRangoError(ReservaTenantError):
    pass


class ReservaTenantSeleccionError(ReservaTenantError):
    pass


class ReservaTenantRegistroError(ReservaTenantError):
    pass


def _rol(tenant: TenantContext) -> str:
    return str(tenant.rol_nombre).strip().upper()


def _validar_roles(tenant: TenantContext, roles: tuple[str, ...]) -> str:
    if tenant.contexto != "personal":
        raise ReservaTenantRolNoAutorizadoError()
    rol = _rol(tenant)
    if rol not in roles:
        raise ReservaTenantRolNoAutorizadoError()
    return rol


def _perfil(db: Session, tenant: TenantContext):
    perfil = TenantAuthRepository.obtener_perfil_personal(
        db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
    )
    if perfil is None:
        raise ReservaTenantScopeError()
    return perfil


def _alcance_sucursal(
    db: Session, tenant: TenantContext, roles: tuple[str, ...]
) -> int | None:
    rol = _validar_roles(tenant, roles)
    if rol == ROL_ADMINISTRADOR:
        return None
    return int(_perfil(db, tenant).sucursal_id)


def _resolver_sucursal(
    db: Session,
    tenant: TenantContext,
    sucursal_id: int | None,
    roles: tuple[str, ...],
) -> int | None:
    alcance = _alcance_sucursal(db, tenant, roles)
    if alcance is not None:
        if sucursal_id is not None and int(sucursal_id) != alcance:
            raise ReservaTenantScopeError()
        return alcance
    if sucursal_id is not None:
        existe = db.scalar(
            select(Sucursal.id).where(
                Sucursal.id == int(sucursal_id),
                Sucursal.empresa_id == tenant.empresa_id,
                Sucursal.estado.is_(True),
            )
        )
        if existe is None:
            raise ReservaTenantSucursalNoEncontradaError()
    return sucursal_id


def _rango(
    fecha_desde: date | None, fecha_hasta: date | None
):
    try:
        return _rango_fechas(fecha_desde, fecha_hasta)
    except RangoFechasInvalidoError as exc:
        raise ReservaTenantRangoError() from exc


def _obtener(
    db: Session,
    tenant: TenantContext,
    reserva_id: int,
    roles: tuple[str, ...],
    *,
    estado_requerido: str | None = None,
) -> Reserva:
    _validar_roles(tenant, roles)
    reserva = ReservaTenantGestionRepository.obtener_por_id(
        db, empresa_id=tenant.empresa_id, reserva_id=int(reserva_id)
    )
    if reserva is None:
        raise ReservaTenantNoEncontradaError()
    alcance = _alcance_sucursal(db, tenant, roles)
    if alcance is not None and int(reserva.sucursal_id) != alcance:
        raise ReservaTenantScopeError()
    if estado_requerido is not None and reserva.estado != estado_requerido:
        raise ReservaTenantEstadoError()
    return reserva


def _detalle_atencion(
    db: Session, tenant: TenantContext, reserva: Reserva
) -> AtencionReservaDetalleResponse:
    detalles = list(reserva.detalles)
    imagenes = _resolver_imagenes(db, detalles)
    items: list[AtencionReservaItemResponse] = []
    for detalle in detalles:
        variante = detalle.inventario.variante_producto
        imagen = imagenes.get((variante.producto_id, variante.color_id))
        items.append(_item_atencion(detalle, imagen))

    venta = ReservaTenantGestionRepository.obtener_venta_por_reserva(
        db, empresa_id=tenant.empresa_id, reserva_id=int(reserva.id)
    )
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
    cliente = reserva.cliente
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


class ReservaTenantGestionService:
    # ------------------------------------------------------------------
    # Gestion (ADMINISTRADOR / ENCARGADO_SUCURSAL)
    # ------------------------------------------------------------------
    @staticmethod
    def listar_gestion(
        db: Session,
        tenant: TenantContext,
        *,
        sucursal_id: int | None = None,
        estado: str | None = None,
        cliente_id: int | None = None,
        buscar: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        limit: int = LIMITE_POR_DEFECTO,
        offset: int = 0,
    ) -> ReservaSucursalListaResponse:
        sucursal = _resolver_sucursal(db, tenant, sucursal_id, ROLES_GESTION)
        desde, hasta = _rango(fecha_desde, fecha_hasta)
        reservas, total = ReservaTenantGestionRepository.listar(
            db,
            empresa_id=tenant.empresa_id,
            sucursal_id=sucursal,
            estado=estado,
            cliente_id=cliente_id,
            buscar=buscar,
            desde=desde,
            hasta=hasta,
            limit=limit,
            offset=offset,
        )
        return ReservaSucursalListaResponse(
            items=[_resumen_response(reserva) for reserva in reservas],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def obtener_gestion(
        db: Session, tenant: TenantContext, reserva_id: int
    ) -> ReservaSucursalDetalleResponse:
        reserva = _obtener(db, tenant, reserva_id, ROLES_GESTION)
        return _detalle_gestion_response(db, reserva)

    @staticmethod
    def estadisticas(
        db: Session,
        tenant: TenantContext,
        *,
        sucursal_id: int | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
    ) -> ReservaTenantEstadisticasResponse:
        sucursal = _resolver_sucursal(db, tenant, sucursal_id, ROLES_GESTION)
        desde, hasta = _rango(fecha_desde, fecha_hasta)
        datos = ReservaTenantGestionRepository.estadisticas(
            db,
            empresa_id=tenant.empresa_id,
            sucursal_id=sucursal,
            desde=desde,
            hasta=hasta,
        )
        return ReservaTenantEstadisticasResponse(**datos)

    @staticmethod
    def confirmar(
        db: Session, tenant: TenantContext, reserva_id: int
    ) -> ReservaSucursalDetalleResponse:
        reserva = _obtener(db, tenant, reserva_id, ROLES_GESTION)
        if reserva.estado != ESTADO_PENDIENTE:
            raise ReservaTenantEstadoError()
        try:
            confirmar_reserva_transaccional_tenant(
                db, tenant=tenant, reserva_id=int(reserva.id)
            )
        except DBAPIError as exc:
            db.rollback()
            raise ReservaTenantRegistroError() from exc
        actual = ReservaTenantGestionRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, reserva_id=int(reserva.id)
        )
        if actual is None:
            raise ReservaTenantNoEncontradaError()
        return _detalle_gestion_response(db, actual)

    @staticmethod
    def cancelar(
        db: Session,
        tenant: TenantContext,
        reserva_id: int,
        datos=None,
    ) -> ReservaSucursalDetalleResponse:
        reserva = _obtener(db, tenant, reserva_id, ROLES_GESTION)
        if reserva.estado not in ESTADOS_CANCELABLES:
            raise ReservaTenantEstadoError()
        observacion = ((datos.observacion if datos else None) or "").strip() or None
        try:
            cancelar_reserva_transaccional_tenant(
                db,
                tenant=tenant,
                reserva_id=int(reserva.id),
                observacion=observacion,
            )
        except DBAPIError as exc:
            db.rollback()
            raise ReservaTenantRegistroError() from exc
        actual = ReservaTenantGestionRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, reserva_id=int(reserva.id)
        )
        if actual is None:
            raise ReservaTenantNoEncontradaError()
        return _detalle_gestion_response(db, actual)

    # ------------------------------------------------------------------
    # Atencion (ADMINISTRADOR / ENCARGADO_SUCURSAL / CAJERO)
    # ------------------------------------------------------------------
    @staticmethod
    def listar_atencion(
        db: Session,
        tenant: TenantContext,
        *,
        sucursal_id: int | None = None,
        buscar: str | None = None,
        fecha_desde: date | None = None,
        fecha_hasta: date | None = None,
        limit: int = LIMITE_POR_DEFECTO,
        offset: int = 0,
    ) -> AtencionReservaListaResponse:
        sucursal = _resolver_sucursal(db, tenant, sucursal_id, ROLES_ATENCION)
        desde, hasta = _rango(fecha_desde, fecha_hasta)
        reservas, total = ReservaTenantGestionRepository.listar(
            db,
            empresa_id=tenant.empresa_id,
            sucursal_id=sucursal,
            estado=ESTADO_CONFIRMADA,
            cliente_id=None,
            buscar=buscar,
            desde=desde,
            hasta=hasta,
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
    def obtener_atencion(
        db: Session, tenant: TenantContext, reserva_id: int
    ) -> AtencionReservaDetalleResponse:
        reserva = _obtener(
            db,
            tenant,
            reserva_id,
            ROLES_ATENCION,
            estado_requerido=ESTADO_CONFIRMADA,
        )
        return _detalle_atencion(db, tenant, reserva)

    @staticmethod
    def preparar_venta(
        db: Session,
        tenant: TenantContext,
        reserva_id: int,
        datos: PrepararVentaRequest,
    ) -> PrepararVentaResponse:
        reserva = _obtener(
            db,
            tenant,
            reserva_id,
            ROLES_ATENCION,
            estado_requerido=ESTADO_CONFIRMADA,
        )
        reservado = {
            int(detalle.inventario_id): int(detalle.cantidad)
            for detalle in reserva.detalles
        }
        if not reservado:
            raise ReservaTenantEstadoError()

        seleccion: dict[int, int] = {}
        for item in datos.items:
            inventario_id = int(item.inventario_id)
            if inventario_id in seleccion or inventario_id not in reservado:
                raise ReservaTenantSeleccionError()
            if int(item.cantidad_compra) > reservado[inventario_id]:
                raise ReservaTenantSeleccionError()
            seleccion[inventario_id] = int(item.cantidad_compra)

        items = [
            PrepararVentaItemResponse(
                inventario_id=inventario_id,
                cantidad_reservada=reservado[inventario_id],
                cantidad_compra=seleccion.get(inventario_id, 0),
                cantidad_no_compra=(
                    reservado[inventario_id] - seleccion.get(inventario_id, 0)
                ),
            )
            for inventario_id in sorted(reservado)
        ]
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
        tenant: TenantContext,
        reserva_id: int,
        datos: FinalizarSinCompraRequest | None = None,
    ) -> AtencionReservaDetalleResponse:
        reserva = _obtener(
            db,
            tenant,
            reserva_id,
            ROLES_ATENCION,
            estado_requerido=ESTADO_CONFIRMADA,
        )
        if ReservaTenantGestionRepository.obtener_venta_por_reserva(
            db, empresa_id=tenant.empresa_id, reserva_id=int(reserva.id)
        ) is not None:
            raise ReservaTenantVentaAsociadaError()

        observacion = ((datos.observacion if datos else None) or "").strip() or None
        try:
            finalizar_reserva_sin_compra_transaccional_tenant(
                db,
                tenant=tenant,
                reserva_id=int(reserva.id),
                observacion=observacion,
            )
        except DBAPIError as exc:
            db.rollback()
            raise ReservaTenantRegistroError() from exc
        actual = ReservaTenantGestionRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, reserva_id=int(reserva.id)
        )
        if actual is None:
            raise ReservaTenantNoEncontradaError()
        return _detalle_atencion(db, tenant, actual)
