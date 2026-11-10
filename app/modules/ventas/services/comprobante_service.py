"""Reglas de negocio de CU23 - Emitir comprobante de venta.

Solo lectura: NO persiste nada, NO modifica venta/pago/reserva/inventario, NO
crea movimientos, NO ejecuta sp_confirmar_venta, NO cobra y NO crea otra venta.
El comprobante es un DTO construido a partir de la transaccion ya cerrada.

Funciona para cualquier origen (WEB/MOVIL via CU19+CU22, PRESENCIAL directa via
CU20+CU21 y PRESENCIAL desde reserva via CU18+CU20+CU21) porque la unica
entrada es ``venta_id``: lo que habilita el comprobante es que la venta este
COMPLETADA y que exista un pago APROBADO.

Autorizacion:

- contexto "cliente": el Cliente activo del usuario autenticado y solo su
  propia venta (``venta.cliente_id``). El frontend nunca envia cliente_id.
- contexto "personal": roles ADMINISTRADOR (cualquier sucursal),
  ENCARGADO_SUCURSAL y CAJERO (solo la sucursal de su empleado). Otros roles
  quedan fuera.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import (
    Cliente,
    Empleado,
    Usuario,
)
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.pagos.repositories.repository import PagoRepository
from app.modules.pagos.repositories.tenant_repository import PagoTenantRepository
from app.modules.ventas.repositories.repository import VentaRepository
from app.modules.ventas.repositories.tenant_repository import VentaTenantRepository
from app.modules.ventas.schemas.comprobante import (
    ComprobanteClienteResponse,
    ComprobanteEmpleadoResponse,
    ComprobantePagoResponse,
    ComprobanteSucursalResponse,
    ComprobanteVentaItemResponse,
    ComprobanteVentaResponse,
)

CONTEXTO_CLIENTE = "cliente"
CONTEXTO_PERSONAL = "personal"

ESTADO_VENTA_COMPLETADA = "COMPLETADA"

ROL_ADMINISTRADOR = "ADMINISTRADOR"
ROL_ENCARGADO_SUCURSAL = "ENCARGADO_SUCURSAL"
ROL_CAJERO = "CAJERO"
# Allowlist explicita coherente con CU20/CU21; CLIENTE nunca entra por aqui.
ROLES_COMPROBANTE_PERSONAL = (
    ROL_ADMINISTRADOR,
    ROL_ENCARGADO_SUCURSAL,
    ROL_CAJERO,
)

_CENTIMOS = Decimal("0.01")


# ---------------------------------------------------------------------------
# Errores de dominio de CU23
# ---------------------------------------------------------------------------


class ComprobanteVentaError(Exception):
    """Base de errores de negocio de CU23 (mapped a HTTP en el router)."""


class VentaComprobanteNoEncontradaError(ComprobanteVentaError):
    """La venta no existe."""


class ComprobanteNoAutorizadoError(ComprobanteVentaError):
    """La venta es de otro cliente, de otra sucursal o el rol no esta admitido."""


class VentaNoCompletadaError(ComprobanteVentaError):
    """La venta existe pero aun no esta COMPLETADA."""


class PagoAprobadoNoEncontradoError(ComprobanteVentaError):
    """La venta esta COMPLETADA pero no tiene ningun pago APROBADO."""


# ---------------------------------------------------------------------------
# Construccion del DTO (sin efectos secundarios)
# ---------------------------------------------------------------------------


def _cliente_response(
    db: Session, venta, *, empresa_id: int | None = None
) -> ComprobanteClienteResponse | None:
    """Cliente real de la venta; ``None`` en ventas anonimas (sin inventar)."""
    if venta.cliente_id is None:
        return None
    statement = select(Cliente).where(Cliente.id == int(venta.cliente_id))
    if empresa_id is not None:
        statement = statement.where(Cliente.empresa_id == empresa_id)
    cliente = db.scalars(statement).first()
    if cliente is None:
        return None
    return ComprobanteClienteResponse(
        id=int(cliente.id),
        nombre=cliente.nombre,
        apellido=cliente.apellido,
        ci=cliente.ci,
        telefono=cliente.telefono,
    )


def _empleado_response(
    db: Session, venta, *, empresa_id: int | None = None
) -> ComprobanteEmpleadoResponse | None:
    """Empleado que registro la venta; ``None`` en ventas WEB/MOVIL."""
    if venta.empleado_id is None:
        return None
    statement = select(Empleado).where(Empleado.id == int(venta.empleado_id))
    if empresa_id is not None:
        statement = statement.where(Empleado.empresa_id == empresa_id)
    empleado = db.scalars(statement).first()
    if empleado is None:
        return None
    return ComprobanteEmpleadoResponse(
        id=int(empleado.id),
        nombres=empleado.nombres,
        apellidos=empleado.apellidos,
    )


def _sucursal_response(venta) -> ComprobanteSucursalResponse:
    sucursal = venta.sucursal
    return ComprobanteSucursalResponse(
        id=int(sucursal.id),
        nombre=sucursal.nombre,
        direccion=sucursal.direccion,
        telefono=sucursal.telefono,
    )


def _pago_response(pago) -> ComprobantePagoResponse:
    return ComprobantePagoResponse(
        pago_id=int(pago.id),
        fecha_hora=pago.fecha_hora,
        monto=Decimal(pago.monto),
        metodo=pago.metodo,
        estado=pago.estado,
        referencia_transaccion=pago.referencia_transaccion,
        pasarela=pago.pasarela,
    )


def _construir_items(venta) -> list[ComprobanteVentaItemResponse]:
    """Una linea por ``detalle_venta`` con su producto/talla/color reales."""
    items: list[ComprobanteVentaItemResponse] = []
    for detalle in venta.detalles:
        inventario = detalle.inventario
        variante = inventario.variante_producto
        producto = variante.producto
        cantidad = int(detalle.cantidad)
        precio = Decimal(detalle.precio_unitario)
        items.append(
            ComprobanteVentaItemResponse(
                detalle_venta_id=int(detalle.id),
                inventario_id=int(detalle.inventario_id),
                producto_id=int(producto.id),
                producto_nombre=producto.nombre,
                variante_producto_id=int(variante.id),
                sku=variante.sku,
                talla_nombre=variante.talla.nombre,
                color_nombre=variante.color.nombre,
                cantidad=cantidad,
                precio_unitario=precio,
                subtotal_linea=(precio * cantidad).quantize(
                    _CENTIMOS, rounding=ROUND_HALF_UP
                ),
            )
        )
    return items


def _comprobante_response(
    db: Session, venta, pago, *, empresa_id: int | None = None
) -> ComprobanteVentaResponse:
    """Ensambla el DTO reutilizado por CU23 legacy y por el tenant SaaS."""
    items = _construir_items(venta)
    return ComprobanteVentaResponse(
        venta_id=int(venta.id),
        fecha_hora=venta.fecha_hora,
        canal=venta.canal,
        estado_venta=venta.estado,
        total=Decimal(venta.total),
        carrito_id=(
            int(venta.carrito_id) if venta.carrito_id is not None else None
        ),
        reserva_id=(
            int(venta.reserva_id) if venta.reserva_id is not None else None
        ),
        cliente=_cliente_response(db, venta, empresa_id=empresa_id),
        empleado=_empleado_response(db, venta, empresa_id=empresa_id),
        sucursal=_sucursal_response(venta),
        pago=_pago_response(pago),
        items=items,
        cantidad_total_unidades=sum(item.cantidad for item in items),
    )


class ComprobanteVentaService:
    """CU23 - Emitir el comprobante de una venta COMPLETADA y pagada."""

    # ------------------------------------------------------------------
    # Autorizacion
    # ------------------------------------------------------------------

    @staticmethod
    def _resolver_cliente(db: Session, usuario: Usuario) -> Cliente:
        """Cliente activo del usuario autenticado (contexto cliente)."""
        cliente = db.scalars(
            select(Cliente).where(
                Cliente.usuario_id == usuario.id,
                Cliente.estado.is_(True),
            )
        ).first()
        if cliente is None:
            raise ComprobanteNoAutorizadoError()
        return cliente

    @staticmethod
    def _validar_personal(usuario: Usuario, venta) -> None:
        """ADMINISTRADOR global; ENCARGADO_SUCURSAL/CAJERO solo su sucursal."""
        rol = str(usuario.rol.nombre).strip().upper()
        if rol not in ROLES_COMPROBANTE_PERSONAL:
            raise ComprobanteNoAutorizadoError()
        if rol == ROL_ADMINISTRADOR:
            return

        empleado = usuario.empleado
        if empleado is None or not empleado.estado or empleado.sucursal_id is None:
            raise ComprobanteNoAutorizadoError()
        if int(venta.sucursal_id) != int(empleado.sucursal_id):
            raise ComprobanteNoAutorizadoError()

    @staticmethod
    def _validar_acceso(
        db: Session, usuario: Usuario, contexto: str, venta
    ) -> None:
        if contexto == CONTEXTO_CLIENTE:
            cliente = ComprobanteVentaService._resolver_cliente(db, usuario)
            if venta.cliente_id is None or int(venta.cliente_id) != int(cliente.id):
                raise ComprobanteNoAutorizadoError()
            return
        ComprobanteVentaService._validar_personal(usuario, venta)

    # ------------------------------------------------------------------
    # Caso de uso
    # ------------------------------------------------------------------

    @staticmethod
    def generar_comprobante(
        db: Session,
        usuario: Usuario,
        contexto: str,
        venta_id: int,
    ) -> ComprobanteVentaResponse:
        """Comprobante de ``venta_id`` (sin persistir ni modificar nada)."""
        if int(venta_id) <= 0:
            raise VentaComprobanteNoEncontradaError()

        venta = VentaRepository.obtener_por_id(db, int(venta_id))
        if venta is None:
            raise VentaComprobanteNoEncontradaError()

        ComprobanteVentaService._validar_acceso(db, usuario, contexto, venta)

        if venta.estado != ESTADO_VENTA_COMPLETADA:
            raise VentaNoCompletadaError()

        pago = PagoRepository.obtener_pago_aprobado(db, int(venta.id))
        if pago is None:
            raise PagoAprobadoNoEncontradoError()

        return _comprobante_response(db, venta, pago)

    # ------------------------------------------------------------------
    # CU23 SaaS - personal empresarial
    # ------------------------------------------------------------------

    @staticmethod
    def _validar_personal_tenant(
        db: Session, tenant: TenantContext, venta
    ) -> None:
        """ADMIN global; ENCARGADO/CAJERO solo la sucursal de su empleado."""
        if tenant.contexto != CONTEXTO_PERSONAL:
            raise ComprobanteNoAutorizadoError()
        rol = str(tenant.rol_nombre).strip().upper()
        if rol not in ROLES_COMPROBANTE_PERSONAL:
            raise ComprobanteNoAutorizadoError()
        if rol == ROL_ADMINISTRADOR:
            return

        perfil = TenantAuthRepository.obtener_perfil_personal(
            db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
        )
        if perfil is None:
            raise ComprobanteNoAutorizadoError()
        if int(venta.sucursal_id) != int(perfil.sucursal_id):
            raise ComprobanteNoAutorizadoError()

    @staticmethod
    def generar_comprobante_tenant(
        db: Session,
        tenant: TenantContext,
        venta_id: int,
    ) -> ComprobanteVentaResponse:
        """Comprobante de una venta del tenant, solo lectura y con RLS."""
        if int(venta_id) <= 0:
            raise VentaComprobanteNoEncontradaError()

        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=int(venta_id)
        )
        if venta is None:
            raise VentaComprobanteNoEncontradaError()

        ComprobanteVentaService._validar_personal_tenant(db, tenant, venta)

        if venta.estado != ESTADO_VENTA_COMPLETADA:
            raise VentaNoCompletadaError()

        pago = PagoTenantRepository.obtener_pago_aprobado(
            db, empresa_id=tenant.empresa_id, venta_id=int(venta.id)
        )
        if pago is None:
            raise PagoAprobadoNoEncontradoError()

        return _comprobante_response(
            db, venta, pago, empresa_id=tenant.empresa_id
        )

    @staticmethod
    def generar_comprobante_cliente_tenant(
        db: Session, tenant: TenantContext, venta_id: int
    ) -> ComprobanteVentaResponse:
        """CU23 SaaS cliente: solo su venta dentro de la empresa del JWT."""
        if tenant.contexto != CONTEXTO_CLIENTE or int(venta_id) <= 0:
            raise ComprobanteNoAutorizadoError()
        cliente = db.scalars(
            select(Cliente).where(
                Cliente.empresa_id == tenant.empresa_id,
                Cliente.usuario_id == tenant.usuario_id,
                Cliente.estado.is_(True),
            )
        ).first()
        if cliente is None:
            raise ComprobanteNoAutorizadoError()
        venta = VentaTenantRepository.obtener_por_id(
            db, empresa_id=tenant.empresa_id, venta_id=int(venta_id)
        )
        if venta is None:
            raise VentaComprobanteNoEncontradaError()
        if venta.cliente_id is None or int(venta.cliente_id) != int(cliente.id):
            raise ComprobanteNoAutorizadoError()
        if venta.estado != ESTADO_VENTA_COMPLETADA:
            raise VentaNoCompletadaError()
        pago = PagoTenantRepository.obtener_pago_aprobado(
            db, empresa_id=tenant.empresa_id, venta_id=int(venta.id)
        )
        if pago is None:
            raise PagoAprobadoNoEncontradoError()
        return _comprobante_response(db, venta, pago, empresa_id=tenant.empresa_id)
