"""CU13 SaaS: registro de venta presencial (directa o desde reserva).

La venta se crea PENDIENTE mediante ``sp_crear_venta_presencial_tenant``.
No registra pago, no confirma la venta y no modifica inventario. El pago y la
confirmacion pertenecen al endpoint tenant de pagos.
"""
from __future__ import annotations

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.reservas.repositories.repository import ESTADO_CONFIRMADA
from app.modules.reservas.repositories.tenant_gestion_repository import (
    ReservaTenantGestionRepository,
)
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.schemas.schemas import (
    RegistrarVentaPresencialRequest,
    VentaPresencialResponse,
)
from app.modules.ventas.services.presencial_service import _venta_response
from app.modules.ventas.services.service import (
    SQLSTATE_RAISE_EXCEPTION,
    SQLSTATE_UNIQUE_VIOLATION,
    _constraint_violada,
    _mensaje_bd,
    _sqlstate,
)
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada,
    VentaTenantIntegridadError,
    crear_venta_transaccional_tenant,
)

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
ROLES_VENTA = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL, ROL_CAJERO)

CONSTRAINT_VENTA_RESERVA = "uq_venta_reserva"
CONSTRAINT_DETALLE_VENTA = "uq_detalle_venta"


class VentaTenantError(Exception):
    """Base de errores de CU13 SaaS."""


class VentaTenantRolNoAutorizadoError(VentaTenantError):
    pass


class VentaTenantScopeError(VentaTenantError):
    pass


class VentaTenantNoEncontradaError(VentaTenantError):
    pass


class VentaTenantClienteNoEncontradoError(VentaTenantError):
    pass


class VentaTenantInventarioError(VentaTenantError):
    pass


class VentaTenantStockError(VentaTenantError):
    pass


class VentaTenantReservaNoEncontradaError(VentaTenantError):
    pass


class VentaTenantReservaEstadoError(VentaTenantError):
    pass


class VentaTenantSeleccionError(VentaTenantError):
    pass


class VentaTenantDuplicadaError(VentaTenantError):
    pass


class VentaTenantRegistroError(VentaTenantError):
    pass


def _rol(tenant: TenantContext) -> str:
    return str(tenant.rol_nombre).strip().upper()


def _validar_rol(tenant: TenantContext) -> str:
    if tenant.contexto != "personal":
        raise VentaTenantRolNoAutorizadoError()
    rol = _rol(tenant)
    if rol not in ROLES_VENTA:
        raise VentaTenantRolNoAutorizadoError()
    return rol


def _perfil(db: Session, tenant: TenantContext):
    perfil = TenantAuthRepository.obtener_perfil_personal(
        db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
    )
    if perfil is None:
        raise VentaTenantScopeError()
    return perfil


def _alcance(db: Session, tenant: TenantContext) -> int | None:
    rol = _validar_rol(tenant)
    if rol == ROL_ADMINISTRADOR:
        return None
    return int(_perfil(db, tenant).sucursal_id)


def _traducir_error_bd(exc: DBAPIError) -> VentaTenantError:
    estado = _sqlstate(exc)
    constraint = _constraint_violada(exc) or ""
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        if CONSTRAINT_VENTA_RESERVA in constraint or "reserva" in mensaje:
            return VentaTenantDuplicadaError()
        if CONSTRAINT_DETALLE_VENTA in constraint:
            return VentaTenantSeleccionError()
        return VentaTenantRegistroError()

    if estado in (SQLSTATE_RAISE_EXCEPTION, "23514", "P0002", "42501"):
        if estado == "42501":
            return VentaTenantScopeError()
        if "stock" in mensaje:
            return VentaTenantStockError()
        if "ya tiene una venta" in mensaje:
            return VentaTenantDuplicadaError()
        if "cliente no encontrado" in mensaje:
            return VentaTenantClienteNoEncontradoError()
        if "reserva no encontrada" in mensaje:
            return VentaTenantReservaNoEncontradaError()
        if "reserva" in mensaje:
            return VentaTenantReservaEstadoError()
        if "inventario" in mensaje:
            return VentaTenantInventarioError()
        if "precio" in mensaje or "total" in mensaje or "productos" in mensaje:
            return VentaTenantRegistroError()
        return VentaTenantRegistroError()

    if estado in ("22023", "22P02"):
        return VentaTenantSeleccionError()

    return VentaTenantRegistroError()


def _items(datos: RegistrarVentaPresencialRequest) -> list[tuple[int, int]]:
    vistos: set[int] = set()
    resultado: list[tuple[int, int]] = []
    for item in datos.items:
        inventario_id = int(item.inventario_id)
        if inventario_id in vistos:
            raise VentaTenantSeleccionError()
        vistos.add(inventario_id)
        resultado.append((inventario_id, int(item.cantidad)))
    if not resultado:
        raise VentaTenantSeleccionError()
    return resultado


class VentaTenantPresencialService:
    @staticmethod
    def registrar(
        db: Session,
        tenant: TenantContext,
        datos: RegistrarVentaPresencialRequest,
    ) -> VentaPresencialResponse:
        _validar_rol(tenant)
        alcance = _alcance(db, tenant)
        items = _items(datos)

        if datos.reserva_id is not None:
            reserva = ReservaTenantGestionRepository.obtener_por_id(
                db,
                empresa_id=tenant.empresa_id,
                reserva_id=int(datos.reserva_id),
            )
            if reserva is None:
                raise VentaTenantReservaNoEncontradaError()
            if alcance is not None and int(reserva.sucursal_id) != alcance:
                raise VentaTenantScopeError()
            if reserva.estado != ESTADO_CONFIRMADA:
                raise VentaTenantReservaEstadoError()
            reserva_id = int(reserva.id)
            cliente_id = None
        else:
            reserva_id = None
            cliente_id = (
                int(datos.cliente_id) if datos.cliente_id is not None else None
            )

        try:
            venta_id = crear_venta_transaccional_tenant(
                db,
                tenant=tenant,
                reserva_id=reserva_id,
                cliente_id=cliente_id,
                items=items,
            )
            venta = VentaTenantRepository.obtener_por_id(
                db, empresa_id=tenant.empresa_id, venta_id=venta_id
            )
            if venta is None:
                raise VentaTenantIntegridadError(
                    "La venta creada no pertenece al tenant"
                )
            if alcance is not None and int(venta.sucursal_id) != alcance:
                raise VentaTenantScopeError()
            respuesta = _venta_response(db, venta)
            db.commit()
            return respuesta
        except VentaTenantError:
            db.rollback()
            raise
        except VentaTenantDeshabilitada:
            db.rollback()
            raise
        except VentaTenantIntegridadError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def obtener(
        db: Session, tenant: TenantContext, venta_id: int
    ) -> VentaPresencialResponse:
        _validar_rol(tenant)
        alcance = _alcance(db, tenant)
        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=int(venta_id)
        )
        if venta is None:
            raise VentaTenantNoEncontradaError()
        if alcance is not None and int(venta.sucursal_id) != alcance:
            raise VentaTenantScopeError()
        return _venta_response(db, venta)
