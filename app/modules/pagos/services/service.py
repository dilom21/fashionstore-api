"""Reglas de negocio de CU21 - Registrar pago presencial.

Actor: CAJERO (tambien ENCARGADO_SUCURSAL y ADMINISTRADOR). Plataforma POS/Web.

Flujo:

    venta PRESENCIAL PENDIENTE
    -> registrar pago presencial APROBADO
    -> sp_confirmar_venta (autoridad transaccional de PostgreSQL)
    -> venta COMPLETADA (+ stock_actual, movimientos y reserva ATENDIDA)

La operacion es atomica: si sp_confirmar_venta falla, se hace rollback y el
pago APROBADO no queda persistido. CU21 NO reimplementa inventario, reservas ni
liberacion de sobrantes: todo eso pertenece al procedimiento.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.autenticacion_seguridad.models.models import Usuario
from app.modules.pagos.models.models import Pago
from app.modules.pagos.repositories.repository import PagoRepository
from app.modules.pagos.schemas.schemas import (
    PagoPresencialResponse,
    RegistrarPagoPresencialRequest,
)

CANAL_PRESENCIAL = "PRESENCIAL"
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_APROBADO = "APROBADO"

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
ROLES_PAGO_PRESENCIAL = (
    ROL_ADMINISTRADOR,
    ROL_ENCARGADO_SUCURSAL,
    ROL_CAJERO,
)

# SQLSTATE usados solo para clasificar el error internamente. Nunca se expone
# SQL, constraint ni mensaje del motor al cliente.
SQLSTATE_UNIQUE_VIOLATION = "23505"
SQLSTATE_FOREIGN_KEY_VIOLATION = "23503"
SQLSTATE_CHECK_VIOLATION = "23514"
SQLSTATE_RAISE_EXCEPTION = "P0001"


# ---------------------------------------------------------------------------
# Errores de dominio de CU21
# ---------------------------------------------------------------------------


class PagoError(Exception):
    """Base de errores de negocio de CU21 (mapped a HTTP en el router)."""


class VentaNoEncontradaError(PagoError):
    pass


class VentaNoPresencialError(PagoError):
    """La venta es digital (WEB/MOVIL): pertenece a CU22."""


class VentaEstadoInvalidoError(PagoError):
    """La venta no esta PENDIENTE (ya completada, cancelada, etc.)."""


class VentaFueraDeSucursalError(PagoError):
    """La venta esta fuera del alcance de sucursal del usuario."""


class MetodoPagoInvalidoError(PagoError):
    pass


class PagoYaAprobadoError(PagoError):
    """Ya existe un pago APROBADO para la venta."""


class PagoRegistroInvalidoError(PagoError):
    """La venta no permite registrar un pago valido (sin detalles/total)."""


class ConfirmacionVentaError(PagoError):
    """sp_confirmar_venta no pudo confirmar la venta."""


class StockConfirmacionInsuficienteError(ConfirmacionVentaError):
    """La confirmacion fallo por stock insuficiente/inconsistente."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _rol(usuario: Usuario) -> str:
    return str(usuario.rol.nombre).strip().upper()


def _validar_rol(usuario: Usuario) -> str:
    """Allowlist explicita: el RBAC no basta (CLIENTE queda excluido)."""
    rol = _rol(usuario)
    if rol not in ROLES_PAGO_PRESENCIAL:
        raise VentaFueraDeSucursalError()
    return rol


def _sucursal_alcance(usuario: Usuario) -> int | None:
    """None = global (ADMINISTRADOR); si no, la sucursal del empleado."""
    rol = _validar_rol(usuario)
    if rol == ROL_ADMINISTRADOR:
        return None
    empleado = usuario.empleado
    if empleado is None or not empleado.estado or empleado.sucursal_id is None:
        raise VentaFueraDeSucursalError()
    return int(empleado.sucursal_id)


def _validar_alcance(usuario: Usuario, venta) -> None:
    alcance = _sucursal_alcance(usuario)
    if alcance is not None and int(venta.sucursal_id) != alcance:
        raise VentaFueraDeSucursalError()


def _establecer_contexto_bitacora(db: Session, usuario_id: int) -> None:
    db.execute(
        text("SELECT set_config('app.usuario_id', :usuario_id, true)"),
        {"usuario_id": str(usuario_id)},
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    valor = getattr(origen, "sqlstate", None)
    return str(valor) if valor else None


def _constraint_violada(exc: DBAPIError) -> str | None:
    origen = getattr(exc, "orig", None)
    diag = getattr(origen, "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    return str(nombre) if nombre else None


def _mensaje_bd(exc: DBAPIError) -> str:
    """Texto del motor SOLO para clasificar (no se expone al cliente)."""
    origen = getattr(exc, "orig", None)
    return str(origen or exc).lower()


def _traducir_error_bd(exc: DBAPIError) -> PagoError:
    estado = _sqlstate(exc)
    constraint = _constraint_violada(exc) or ""
    mensaje = _mensaje_bd(exc)

    if estado == SQLSTATE_RAISE_EXCEPTION:
        if "stock" in mensaje or "disponible" in mensaje or "reservado" in mensaje:
            return StockConfirmacionInsuficienteError()
        if "no existe" in mensaje:
            return VentaNoEncontradaError()
        if "no puede confirmarse" in mensaje or "estado" in mensaje:
            return VentaEstadoInvalidoError()
        if "reserva" in mensaje:
            return ConfirmacionVentaError()
        return ConfirmacionVentaError()

    if estado == SQLSTATE_CHECK_VIOLATION:
        if "metodo" in constraint:
            return MetodoPagoInvalidoError()
        return PagoRegistroInvalidoError()

    if estado == SQLSTATE_UNIQUE_VIOLATION:
        return PagoRegistroInvalidoError()

    if estado == SQLSTATE_FOREIGN_KEY_VIOLATION:
        return VentaNoEncontradaError()

    return ConfirmacionVentaError()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PagoPresencialService:
    """Registra el pago APROBADO y confirma la venta de forma atomica."""

    @staticmethod
    def registrar_presencial(
        db: Session,
        usuario: Usuario,
        datos: RegistrarPagoPresencialRequest,
    ) -> PagoPresencialResponse:
        venta_id = int(datos.venta_id)
        try:
            venta = PagoRepository.bloquear_venta(db, venta_id)
            if venta is None:
                raise VentaNoEncontradaError()

            _validar_alcance(usuario, venta)

            if venta.canal != CANAL_PRESENCIAL:
                raise VentaNoPresencialError()
            if venta.estado != ESTADO_PENDIENTE:
                raise VentaEstadoInvalidoError()
            if PagoRepository.obtener_pago_aprobado(db, venta.id) is not None:
                raise PagoYaAprobadoError()
            if PagoRepository.contar_detalles(db, venta.id) == 0:
                raise PagoRegistroInvalidoError()

            monto = Decimal(venta.total)
            if monto <= 0:
                raise PagoRegistroInvalidoError()

            _establecer_contexto_bitacora(db, int(usuario.id))
            pago = PagoRepository.crear_pago(
                db,
                venta_id=int(venta.id),
                monto=monto,
                metodo=datos.metodo.value,
                estado=ESTADO_APROBADO,
                fecha_hora=_ahora(),
            )
            pago_id = int(pago.id)

            PagoRepository.confirmar_venta(db, int(venta.id), int(usuario.id))
            db.commit()
        except PagoError:
            db.rollback()
            raise
        except DBAPIError as exc:
            db.rollback()
            raise _traducir_error_bd(exc) from exc

        pago = db.get(Pago, pago_id)
        venta = PagoRepository.obtener_venta(db, venta_id)
        if pago is None or venta is None:
            raise ConfirmacionVentaError()

        estado_reserva = None
        if venta.reserva_id is not None:
            reserva = PagoRepository.obtener_reserva(db, int(venta.reserva_id))
            estado_reserva = reserva.estado if reserva is not None else None

        return PagoPresencialResponse(
            pago_id=pago.id,
            venta_id=venta.id,
            metodo=pago.metodo,
            estado_pago=pago.estado,
            monto=Decimal(pago.monto),
            fecha=pago.fecha_hora,
            estado_venta=venta.estado,
            reserva_id=venta.reserva_id,
            estado_reserva=estado_reserva,
        )
