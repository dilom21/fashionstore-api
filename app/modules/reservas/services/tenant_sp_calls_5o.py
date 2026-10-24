"""B3/P5O: preparación inerte de CALL SaaS basadas en TenantContext.

Se usa EXCLUSIVAMENTE desde un servicio HTTP que obtuvo el TenantContext
con get_current_tenant_context (JWT firmado + membresía vigente en BD).
ESTE MODULO NO EJECUTA SQL, NO ABRE CONEXIONES, NO INSTALA PROCEDIMIENTOS.
Una conexión SQL arbitraria puede suministrar IDs falsos: este constructor
NO constituye autenticación del principal PostgreSQL ni validación de RLS.
"""

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from app.core.tenant_context import TenantContext


@dataclass(frozen=True, slots=True)
class LlamadaSPTenant:
    """SQL parametrizado y snapshot inmutable de argumentos para ensayo FUTURO."""

    sentencia: TextClause
    parametros: Mapping[str, Any]


def _id_positivo(valor: int, etiqueta: str) -> int:
    if type(valor) is not int or valor <= 0:
        raise ValueError(f"{etiqueta} debe ser entero positivo")
    return valor


def _identidad_validada(tenant: TenantContext) -> tuple[int, int]:
    if not isinstance(tenant, TenantContext):
        raise TypeError("Se requiere TenantContext validado por dependencia SaaS")
    return (
        _id_positivo(tenant.empresa_id, "empresa_id"),
        _id_positivo(tenant.usuario_id, "usuario_id"),
    )


def _observacion(valor: str | None) -> str | None:
    if valor is not None and not isinstance(valor, str):
        raise TypeError("La observacion debe ser texto o None")
    return valor


def _preparar(sql: str, parametros: dict[str, Any]) -> LlamadaSPTenant:
    # MappingProxyType impide sustituir actor o tenant después de prepararla.
    # Los valores no son secretos y se parametrizan por SQLAlchemy.
    return LlamadaSPTenant(text(sql), MappingProxyType(parametros))


def preparar_creacion_reserva(
    *, tenant: TenantContext, carrito_id: int,
    fecha_atencion: datetime, observacion: str | None = None,
) -> LlamadaSPTenant:
    """Contrato CU16; no ejecuta la reserva ni valida inventario o membresía."""
    empresa_id, usuario_id = _identidad_validada(tenant)
    if tenant.contexto != "cliente" or tenant.rol_nombre != "CLIENTE":
        raise PermissionError("El contrato CU16 requiere perfil CLIENTE")
    _id_positivo(carrito_id, "carrito_id")
    if not isinstance(fecha_atencion, datetime) or (
        fecha_atencion.tzinfo is None or fecha_atencion.utcoffset() is None
    ):
        raise ValueError("fecha_atencion debe incluir zona horaria")
    return _preparar(
        "CALL public.sp_crear_reserva_tenant("
        ":empresa_id, :carrito_id, :usuario_id, :fecha_atencion, "
        ":observacion, :reserva_id)",
        {
            "empresa_id": empresa_id, "carrito_id": carrito_id,
            "usuario_id": usuario_id, "fecha_atencion": fecha_atencion,
            "observacion": _observacion(observacion),
            "reserva_id": None,  # INOUT; PostgreSQL debe devolverlo en futuro
        },
    )


def preparar_cancelacion_reserva(
    *, tenant: TenantContext, reserva_id: int, observacion: str | None = None,
) -> LlamadaSPTenant:
    """Contrato CU16/CU17; SP deberá comprobar propiedad/permiso efectivo."""
    empresa_id, usuario_id = _identidad_validada(tenant)
    cliente = tenant.contexto == "cliente" and tenant.rol_nombre == "CLIENTE"
    personal = tenant.contexto == "personal" and tenant.rol_nombre in (
        "ADMINISTRADOR", "ENCARGADO_SUCURSAL"
    )
    if not (cliente or personal):
        raise PermissionError("El contrato no permite cancelar con este contexto")
    _id_positivo(reserva_id, "reserva_id")
    return _preparar(
        "CALL public.sp_cancelar_reserva_tenant("
        ":empresa_id, :reserva_id, :usuario_id, :observacion)",
        {
            "empresa_id": empresa_id, "reserva_id": reserva_id,
            "usuario_id": usuario_id, "observacion": _observacion(observacion),
        },
    )


def preparar_finalizacion_sin_compra(
    *, tenant: TenantContext, reserva_id: int, observacion: str | None = None,
) -> LlamadaSPTenant:
    """Contrato CU18; requiere membresía/permisos/sucursal revisados en DB."""
    empresa_id, usuario_id = _identidad_validada(tenant)
    if tenant.contexto != "personal" or tenant.rol_nombre not in (
        "ADMINISTRADOR", "ENCARGADO_SUCURSAL", "CAJERO"
    ):
        raise PermissionError("El contrato CU18 requiere personal autorizado")
    _id_positivo(reserva_id, "reserva_id")
    return _preparar(
        "CALL public.sp_finalizar_reserva_sin_compra_tenant("
        ":empresa_id, :reserva_id, :usuario_id, :observacion)",
        {
            "empresa_id": empresa_id, "reserva_id": reserva_id,
            "usuario_id": usuario_id, "observacion": _observacion(observacion),
        },
    )
