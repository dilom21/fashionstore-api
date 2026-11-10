"""CU13 SaaS: pago presencial y confirmacion atomica de la venta."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.pagos.repositories.tenant_repository import PagoTenantRepository
from app.modules.pagos.schemas.schemas import (
    PagoPresencialResponse,
    RegistrarPagoPresencialRequest,
)
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.services.service import (
    SQLSTATE_RAISE_EXCEPTION,
    _mensaje_bd,
    _sqlstate,
)
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada,
    VentaTenantIntegridadError,
    registrar_pago_transaccional_tenant,
)

CANAL_PRESENCIAL = "PRESENCIAL"
ESTADO_PENDIENTE = "PENDIENTE"

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
ROLES_PAGO = (ROL_ADMINISTRADOR, ROL_ENCARGADO_SUCURSAL, ROL_CAJERO)


class PagoTenantError(Exception):
    """Base de errores de pago presencial SaaS."""


class PagoTenantRolNoAutorizadoError(PagoTenantError):
    pass


class PagoTenantScopeError(PagoTenantError):
    pass


class PagoTenantVentaNoEncontradaError(PagoTenantError):
    pass


class PagoTenantVentaNoPresencialError(PagoTenantError):
    pass


class PagoTenantEstadoError(PagoTenantError):
    pass


class PagoTenantYaAprobadoError(PagoTenantError):
    pass


class PagoTenantMetodoError(PagoTenantError):
    pass


class PagoTenantStockError(PagoTenantError):
    pass


class PagoTenantReservaError(PagoTenantError):
    pass


class PagoTenantRegistroError(PagoTenantError):
    pass


class PagoTenantConfirmacionError(PagoTenantError):
    pass


def _rol(tenant: TenantContext) -> str:
    return str(tenant.rol_nombre).strip().upper()


def _validar_rol(tenant: TenantContext) -> str:
    if tenant.contexto != "personal":
        raise PagoTenantRolNoAutorizadoError()
    rol = _rol(tenant)
    if rol not in ROLES_PAGO:
        raise PagoTenantRolNoAutorizadoError()
    return rol


def _alcance(db: Session, tenant: TenantContext) -> int | None:
    rol = _validar_rol(tenant)
    if rol == ROL_ADMINISTRADOR:
        return None
    perfil = TenantAuthRepository.obtener_perfil_personal(
        db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
    )
    if perfil is None:
        raise PagoTenantScopeError()
    return int(perfil.sucursal_id)


def _traducir_error_bd(exc: DBAPIError) -> PagoTenantError:
    estado = _sqlstate(exc)
    mensaje = _mensaje_bd(exc)

    if estado in (SQLSTATE_RAISE_EXCEPTION, "23514", "23505", "P0002", "42501"):
        if estado == "42501":
            return PagoTenantScopeError()
        if "ya tiene un pago aprobado" in mensaje:
            return PagoTenantYaAprobadoError()
        if "no es presencial" in mensaje:
            return PagoTenantVentaNoPresencialError()
        if "no esta pendiente" in mensaje or "no puede" in mensaje:
            return PagoTenantEstadoError()
        if "stock" in mensaje:
            return PagoTenantStockError()
        if "reserva" in mensaje:
            return PagoTenantReservaError()
        if "no encontrada" in mensaje:
            return PagoTenantVentaNoEncontradaError()
        return PagoTenantConfirmacionError()

    if estado in ("22023", "22P02"):
        return PagoTenantMetodoError()

    return PagoTenantConfirmacionError()


class PagoTenantPresencialService:
    @staticmethod
    def registrar(
        db: Session,
        tenant: TenantContext,
        datos: RegistrarPagoPresencialRequest,
    ) -> PagoPresencialResponse:
        _validar_rol(tenant)
        alcance = _alcance(db, tenant)
        venta_id = int(datos.venta_id)

        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=venta_id
        )
        if venta is None:
            raise PagoTenantVentaNoEncontradaError()
        if alcance is not None and int(venta.sucursal_id) != alcance:
            raise PagoTenantScopeError()
        if venta.canal != CANAL_PRESENCIAL:
            raise PagoTenantVentaNoPresencialError()
        if venta.estado != ESTADO_PENDIENTE:
            raise PagoTenantEstadoError()
        if PagoTenantRepository.obtener_pago_aprobado(
            db, empresa_id=tenant.empresa_id, venta_id=venta_id
        ) is not None:
            raise PagoTenantYaAprobadoError()

        try:
            pago_id = registrar_pago_transaccional_tenant(
                db,
                tenant=tenant,
                venta_id=venta_id,
                metodo=datos.metodo.value,
            )
            pago = PagoTenantRepository.obtener_pago(
                db, empresa_id=tenant.empresa_id, pago_id=pago_id
            )
            venta_actual = VentaTenantRepository.obtener_por_id(
                db, empresa_id=tenant.empresa_id, venta_id=venta_id
            )
            if pago is None or venta_actual is None:
                raise VentaTenantIntegridadError(
                    "Pago o venta no pertenecen al tenant"
                )
            estado_reserva = None
            if venta_actual.reserva_id is not None:
                reserva = PagoTenantRepository.obtener_reserva(
                    db,
                    empresa_id=tenant.empresa_id,
                    reserva_id=int(venta_actual.reserva_id),
                )
                estado_reserva = reserva.estado if reserva is not None else None
            respuesta = PagoPresencialResponse(
                pago_id=pago.id,
                venta_id=venta_actual.id,
                metodo=pago.metodo,
                estado_pago=pago.estado,
                monto=Decimal(pago.monto),
                fecha=pago.fecha_hora,
                estado_venta=venta_actual.estado,
                reserva_id=venta_actual.reserva_id,
                estado_reserva=estado_reserva,
            )
            db.commit()
            return respuesta
        except PagoTenantError:
            db.rollback()
            raise
        except (VentaTenantDeshabilitada, VentaTenantIntegridadError):
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc
        except Exception:
            db.rollback()
            raise
