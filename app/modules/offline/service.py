"""CU19/CU20: orquestacion offline con ledger unico por UUID.

La operacion comercial y la fila del ledger se confirman en la MISMA
transaccion PostgreSQL. Si la operacion falla, se revierte todo y luego se
registra el rechazo/conflicto en una transaccion nueva para conservar evidencia.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Iterable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext
from app.modules.autenticacion_seguridad.models.models import Cliente
from app.modules.autenticacion_seguridad.repositories.tenant_repository import (
    TenantAuthRepository,
)
from app.modules.catalogo.models.models import (
    Categoria, Producto, Temporada, VarianteProducto,
)
from app.modules.inventario.models.models import Inventario
from app.modules.offline.repository import OfflineLedgerRepository, hash_payload
from app.modules.offline.schemas import (
    EstadoTransporte, OperacionOfflineRequest, OperacionOfflineResponse,
)
from app.modules.reservas.models.models import DetalleReserva, Reserva
from app.modules.reservas.services.tenant_reserva_transaccional_5al import (
    ReservaTenantDeshabilitada, ReservaTenantIntegridadError,
    preparar_creacion_reserva, verificar_principal_reserva,
)
from app.modules.sucursales.models.models import Sucursal
from app.modules.ventas.models.models import DetalleVenta, Venta
from app.modules.ventas.services.tenant_presencial_service import (
    VentaTenantError, VentaTenantRolNoAutorizadoError, VentaTenantScopeError,
    VentaTenantStockError, VentaTenantSeleccionError, VentaTenantInventarioError,
    VentaTenantDuplicadaError,
)
from app.modules.ventas.services.tenant_transaccional import (
    VentaTenantDeshabilitada, VentaTenantIntegridadError,
    crear_venta_transaccional_tenant, registrar_pago_transaccional_tenant,
)

ROLES_VENTA_OFFLINE = frozenset({"CAJERO", "ADMINISTRADOR"})


class OfflineOperationError(Exception):
    estado: EstadoTransporte = "RECHAZADA"
    codigo = "OPERACION_RECHAZADA"
    http_status = 422

    def __init__(self, codigo: str, mensaje: str, http_status: int | None = None):
        self.codigo = codigo
        self.mensaje = mensaje
        if http_status is not None:
            self.http_status = http_status
        super().__init__(mensaje)


class OfflineNoAutorizado(OfflineOperationError):
    estado = "RECHAZADA"
    http_status = 403


class OfflineConflicto(OfflineOperationError):
    estado = "CONFLICTO"
    http_status = 409


class OfflineRechazada(OfflineOperationError):
    estado = "RECHAZADA"
    http_status = 422


class OfflineErrorReintentable(OfflineOperationError):
    estado = "ERROR_REINTENTABLE"
    http_status = 503


class OfflineNoEncontrada(OfflineOperationError):
    estado = "RECHAZADA"
    http_status = 404


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _respuesta(
    datos: OperacionOfflineRequest, estado: EstadoTransporte, *,
    id_definitivo: int | None = None, resultado: dict | None = None,
    error_codigo: str | None = None, error_mensaje: str | None = None,
) -> OperacionOfflineResponse:
    return OperacionOfflineResponse(
        operacion_uuid=datos.operacion_uuid, tipo=datos.tipo, estado=estado,
        id_definitivo=id_definitivo, resultado=resultado,
        error_codigo=error_codigo, error_mensaje=error_mensaje,
        reintentable=estado in {"PENDIENTE", "ENVIANDO", "ERROR_REINTENTABLE"},
    )


def _cliente_id(db: Session, tenant: TenantContext) -> int:
    c = Cliente.__table__
    cliente_id = db.scalar(select(c.c.id).where(
        c.c.empresa_id == tenant.empresa_id,
        c.c.usuario_id == tenant.usuario_id,
        c.c.estado.is_(True),
    ))
    if cliente_id is None:
        raise OfflineNoAutorizado(
            "CLIENTE_NO_ACTIVO", "No existe un cliente activo en esta empresa"
        )
    return int(cliente_id)


def _sucursal(db: Session, empresa_id: int, sucursal_id: int) -> None:
    s = Sucursal.__table__
    existe = db.scalar(select(s.c.id).where(
        s.c.id == sucursal_id, s.c.empresa_id == empresa_id,
        s.c.estado.is_(True),
    ))
    if existe is None:
        raise OfflineConflicto("SUCURSAL_NO_DISPONIBLE", "Sucursal no disponible")


def _validar_lineas(
    db: Session, *, empresa_id: int, sucursal_id: int,
    lineas: Iterable,
) -> None:
    """Valida TODAS las lineas contra inventario/producto/variante/stock real."""
    condiciones_base = (
        Inventario.empresa_id == empresa_id,
        Inventario.sucursal_id == sucursal_id,
        VarianteProducto.empresa_id == empresa_id,
        VarianteProducto.estado.is_(True),
        Producto.empresa_id == empresa_id,
        Producto.estado.is_(True),
        Categoria.empresa_id == empresa_id,
        Categoria.estado.is_(True),
        Temporada.empresa_id == empresa_id,
        Temporada.estado.is_(True),
    )
    for linea in lineas:
        encontrado = db.scalar(
            select(Inventario.id)
            .select_from(Inventario)
            .join(VarianteProducto, VarianteProducto.id == Inventario.variante_producto_id)
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .join(Categoria, Categoria.id == Producto.categoria_id)
            .join(Temporada, Temporada.id == Inventario.temporada_id)
            .where(
                *condiciones_base,
                Inventario.id == linea.inventario_id,
                (Inventario.stock_actual - Inventario.stock_reservado)
                >= linea.cantidad,
            )
        )
        if encontrado is None:
            raise OfflineConflicto(
                "STOCK_O_INVENTARIO",
                "Una linea no existe, no pertenece a la sucursal o no tiene stock",
            )


def _resolver_variantes_reserva(db: Session, *, empresa_id: int, sucursal_id: int,
                                lineas: Iterable) -> list:
    """Resuelve variante+sucursal a inventario bajo lock, sin exponer stock."""
    resueltas = []
    for linea in lineas:
        if linea.variante_id is None:
            resueltas.append(linea)
            continue
        fila = db.execute(
            select(Inventario.id).select_from(Inventario)
            .join(VarianteProducto, VarianteProducto.id == Inventario.variante_producto_id)
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .join(Categoria, Categoria.id == Producto.categoria_id)
            .join(Temporada, Temporada.id == Inventario.temporada_id)
            .where(
                Inventario.empresa_id == empresa_id,
                Inventario.sucursal_id == sucursal_id,
                Inventario.variante_producto_id == linea.variante_id,
                VarianteProducto.empresa_id == empresa_id, VarianteProducto.estado.is_(True),
                Producto.empresa_id == empresa_id, Producto.estado.is_(True),
                Categoria.empresa_id == empresa_id, Categoria.estado.is_(True),
                Temporada.empresa_id == empresa_id, Temporada.estado.is_(True),
                (Inventario.stock_actual - Inventario.stock_reservado) >= linea.cantidad,
            ).with_for_update()
        ).first()
        if fila is None:
            raise OfflineConflicto("STOCK_O_INVENTARIO", "La variante no esta disponible en la sucursal")
        resueltas.append(SimpleNamespace(inventario_id=int(fila[0]), cantidad=linea.cantidad))
    return resueltas


def _validar_actor(tenant: TenantContext, datos: OperacionOfflineRequest, db: Session):
    if datos.tipo == "RESERVA":
        if tenant.contexto != "cliente" or str(tenant.rol_nombre).upper() != "CLIENTE":
            raise OfflineNoAutorizado(
                "ROL_NO_AUTORIZADO", "RESERVA requiere contexto CLIENTE"
            )
        return _cliente_id(db, tenant)
    if tenant.contexto != "personal":
        raise OfflineNoAutorizado(
            "ROL_NO_AUTORIZADO", "VENTA_CONTADO requiere contexto personal"
        )
    rol = str(tenant.rol_nombre).strip().upper()
    if rol not in ROLES_VENTA_OFFLINE:
        raise OfflineNoAutorizado(
            "ROL_NO_AUTORIZADO", "VENTA_CONTADO solo permite CAJERO o ADMINISTRADOR"
        )
    if rol == "CAJERO":
        perfil = TenantAuthRepository.obtener_perfil_personal(
            db, usuario_id=tenant.usuario_id, empresa_id=tenant.empresa_id
        )
        if perfil is None or int(perfil.sucursal_id) != datos.sucursal_id:
            raise OfflineNoAutorizado(
                "SUCURSAL_NO_AUTORIZADA", "El cajero no esta asignado a esa sucursal"
            )
    return None


def _crear_o_reutilizar_carrito_offline(
    db: Session, *, empresa_id: int, cliente_id: int, sucursal_id: int,
    lineas,
) -> int:
    """Staging OFFLINE aislado del carrito online de CU15.

    Se usa SQL explicito porque la columna ``origen`` solo existe cuando la
    migracion 106 esta instalada. El modulo HTTP permanece cerrado con
    SAAS_OFFLINE_ENABLED=false hasta ese momento.
    """
    actual = db.scalar(text("""
        SELECT id FROM public.carrito
        WHERE empresa_id=:empresa_id AND cliente_id=:cliente_id
          AND sucursal_id=:sucursal_id AND origen='OFFLINE' AND estado='ACTIVO'
        FOR UPDATE
    """), {
        "empresa_id": empresa_id, "cliente_id": cliente_id,
        "sucursal_id": sucursal_id,
    })
    ahora = datetime.now(timezone.utc)
    if actual is None:
        actual = db.scalar(text("""
            INSERT INTO public.carrito(
              empresa_id,cliente_id,sucursal_id,fecha_creacion,
              fecha_actualizacion,estado,origen)
            VALUES(:empresa_id,:cliente_id,:sucursal_id,:ahora,:ahora,
                   'ACTIVO','OFFLINE')
            RETURNING id
        """), {
            "empresa_id": empresa_id, "cliente_id": cliente_id,
            "sucursal_id": sucursal_id, "ahora": ahora,
        })
    else:
        db.execute(text("""
            DELETE FROM public.detalle_carrito
            WHERE empresa_id=:empresa_id AND carrito_id=:carrito_id
        """), {"empresa_id": empresa_id, "carrito_id": actual})
    for linea in lineas:
        db.execute(text("""
            INSERT INTO public.detalle_carrito(
              empresa_id,carrito_id,inventario_id,cantidad)
            VALUES(:empresa_id,:carrito_id,:inventario_id,:cantidad)
        """), {
            "empresa_id": empresa_id, "carrito_id": actual,
            "inventario_id": linea.inventario_id, "cantidad": linea.cantidad,
        })
    db.execute(text("""
        UPDATE public.carrito SET fecha_actualizacion=:ahora
        WHERE id=:carrito_id AND empresa_id=:empresa_id AND estado='ACTIVO'
    """), {
        "empresa_id": empresa_id, "carrito_id": actual, "ahora": ahora,
    })
    return int(actual)


def _reservar(db: Session, tenant: TenantContext, datos: OperacionOfflineRequest, cliente_id: int) -> dict:
    _sucursal(db, tenant.empresa_id, datos.sucursal_id)
    # Resolver variante→inventario con joins tenant y FOR UPDATE antes de
    # construir el carrito; la validacion posterior conserva el contrato comun.
    lineas = _resolver_variantes_reserva(
        db, empresa_id=tenant.empresa_id, sucursal_id=datos.sucursal_id,
        lineas=datos.lineas,
    )
    _validar_lineas(
        db, empresa_id=tenant.empresa_id, sucursal_id=datos.sucursal_id,
        lineas=lineas,
    )
    carrito_id = _crear_o_reutilizar_carrito_offline(
        db, empresa_id=tenant.empresa_id, cliente_id=cliente_id,
        sucursal_id=datos.sucursal_id, lineas=lineas,
    )
    llamada = preparar_creacion_reserva(
        tenant=tenant, carrito_id=carrito_id,
        fecha_atencion=datos.fecha_atencion,
        observacion=datos.observacion,
    )
    verificar_principal_reserva(db, tenant)
    resultado = db.execute(
        llamada.sentencia, dict(llamada.parametros)
    ).one_or_none()
    if resultado is None or type(resultado[0]) is not int or resultado[0] <= 0:
        raise ReservaTenantIntegridadError("SP de reserva no devolvio ID valido")
    reserva_id = int(resultado[0])
    coincide = db.scalar(text("""
        SELECT r.id FROM public.reserva r
        JOIN public.cliente c ON c.id=r.cliente_id
          AND c.empresa_id=r.empresa_id
          AND c.usuario_id=:usuario_id AND c.estado IS TRUE
        JOIN public.carrito ca ON ca.id=r.carrito_id
          AND ca.empresa_id=r.empresa_id AND ca.cliente_id=c.id
        WHERE r.id=:reserva_id AND r.empresa_id=:empresa_id
          AND r.carrito_id=:carrito_id
    """), {
        "reserva_id": reserva_id, "empresa_id": tenant.empresa_id,
        "usuario_id": tenant.usuario_id, "carrito_id": carrito_id,
    })
    if coincide != reserva_id:
        raise ReservaTenantIntegridadError("Reserva devuelta no pertenece al tenant")
    reserva = db.scalar(select(Reserva).where(
        Reserva.id == reserva_id, Reserva.empresa_id == tenant.empresa_id,
    ))
    detalles = db.scalars(select(DetalleReserva).where(
        DetalleReserva.reserva_id == reserva_id,
        DetalleReserva.empresa_id == tenant.empresa_id,
    ).order_by(DetalleReserva.inventario_id)).all()
    return {
        "reserva_id": reserva_id, "carrito_id": carrito_id,
        "estado": reserva.estado if reserva else "PENDIENTE",
        "sucursal_id": int(reserva.sucursal_id) if reserva else datos.sucursal_id,
        "fecha_atencion": datos.fecha_atencion.astimezone(timezone.utc).isoformat(),
        "lineas": [
            {"inventario_id": int(d.inventario_id), "cantidad": int(d.cantidad)}
            for d in detalles
        ],
    }


def _resultado_venta(db: Session, tenant: TenantContext, venta_id: int, pago_id: int,
                     efectivo_recibido: Decimal | None) -> dict:
    venta = db.scalar(select(Venta).where(
        Venta.id == venta_id, Venta.empresa_id == tenant.empresa_id,
    ))
    if venta is None:
        raise VentaTenantIntegridadError("Venta no visible para el tenant")
    if venta.estado != "COMPLETADA":
        raise VentaTenantIntegridadError("Venta no quedo COMPLETADA")
    detalles = db.scalars(select(DetalleVenta).where(
        DetalleVenta.venta_id == venta_id,
        DetalleVenta.empresa_id == tenant.empresa_id,
    ).order_by(DetalleVenta.inventario_id)).all()
    total = Decimal(venta.total)
    lineas = [
        {
            "inventario_id": int(item.inventario_id),
            "cantidad": int(item.cantidad),
            "precio_unitario": str(item.precio_unitario),
            "subtotal_linea": str(item.precio_unitario * item.cantidad),
        }
        for item in detalles
    ]
    return {
        "venta_id": int(venta.id), "pago_id": int(pago_id),
        "estado": venta.estado, "total": str(total),
        "sucursal_id": int(venta.sucursal_id), "canal": venta.canal,
        "metodo": "EFECTIVO", "lineas": lineas,
        "efectivo_recibido": str(efectivo_recibido)
        if efectivo_recibido is not None else None,
        "vuelto": str(efectivo_recibido - total)
        if efectivo_recibido is not None else None,
    }


def _vender(db: Session, tenant: TenantContext, datos: OperacionOfflineRequest) -> tuple[int, dict]:
    _sucursal(db, tenant.empresa_id, datos.sucursal_id)
    _validar_lineas(
        db, empresa_id=tenant.empresa_id, sucursal_id=datos.sucursal_id,
        lineas=datos.lineas,
    )
    venta_id = crear_venta_transaccional_tenant(
        db, tenant=tenant, reserva_id=None,
        cliente_id=datos.cliente_id,
        items=[(linea.inventario_id, linea.cantidad) for linea in datos.lineas],
    )
    total = db.scalar(select(Venta.total).where(
        Venta.id == venta_id, Venta.empresa_id == tenant.empresa_id,
    ))
    if total is None or Decimal(total) <= 0:
        raise VentaTenantIntegridadError("Venta sin total valido")
    total = Decimal(total)
    if datos.efectivo_recibido is not None and datos.efectivo_recibido < total:
        raise OfflineRechazada(
            "EFECTIVO_INSUFICIENTE",
            "El efectivo recibido no cubre el total oficial recalculado",
        )
    pago_id = registrar_pago_transaccional_tenant(
        db, tenant=tenant, venta_id=venta_id, metodo="EFECTIVO",
    )
    return venta_id, _resultado_venta(
        db, tenant, venta_id, pago_id, datos.efectivo_recibido
    )


def _registrar_fallo(
    db: Session, tenant: TenantContext, datos: OperacionOfflineRequest,
    payload_hash: str, estado: EstadoTransporte, codigo: str, mensaje: str,
) -> dict | None:
    """Persiste evidencia en una transaccion nueva; nunca pisa un ACEPTADA."""
    try:
        db.rollback()
        fila = OfflineLedgerRepository.rechazar(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            operacion_uuid=str(datos.operacion_uuid), tipo=datos.tipo,
            payload_hash=payload_hash, sucursal_id=datos.sucursal_id,
            efectivo_recibido=datos.efectivo_recibido, estado=estado,
            error_codigo=codigo, error_mensaje=mensaje,
        )
        db.commit()
        return fila
    except Exception:
        db.rollback()
        return None


class OfflineService:
    @staticmethod
    def ejecutar(
        db: Session, tenant: TenantContext, datos: OperacionOfflineRequest,
        idempotency_key: str | None = None,
    ) -> tuple[OperacionOfflineResponse, int]:
        if idempotency_key is not None and idempotency_key != str(datos.operacion_uuid):
            return _respuesta(
                datos, "RECHAZADA", error_codigo="IDEMPOTENCY_KEY_INVALIDA",
                error_mensaje="Idempotency-Key debe coincidir con operacion_uuid",
            ), 422

        try:
            cliente_id = _validar_actor(tenant, datos, db)
        except OfflineOperationError as exc:
            return _respuesta(
                datos, exc.estado, error_codigo=exc.codigo,
                error_mensaje=exc.mensaje,
            ), exc.http_status
        except DBAPIError:
            db.rollback()
            return _respuesta(
                datos, "ERROR_REINTENTABLE",
                error_codigo="IDENTIDAD_NO_DISPONIBLE",
                error_mensaje="No fue posible validar la identidad; reintente",
            ), 503
        except Exception:
            db.rollback()
            return _respuesta(
                datos, "ERROR_REINTENTABLE",
                error_codigo="IDENTIDAD_NO_DISPONIBLE",
                error_mensaje="No fue posible validar la identidad; reintente",
            ), 503

        payload_hash = hash_payload(datos)
        try:
            inicio = OfflineLedgerRepository.begin(
                db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
                operacion_uuid=str(datos.operacion_uuid), tipo=datos.tipo,
                payload_hash=payload_hash, sucursal_id=datos.sucursal_id,
                efectivo_recibido=datos.efectivo_recibido,
            )
        except DBAPIError as exc:
            db.rollback()
            estado = _sqlstate(exc)
            if estado == "42501":
                return _respuesta(
                    datos, "RECHAZADA", error_codigo="NO_AUTORIZADO",
                    error_mensaje="El actor no coincide con la operacion",
                ), 403
            return _respuesta(
                datos, "ERROR_REINTENTABLE", error_codigo="LEDGER_NO_DISPONIBLE",
                error_mensaje="No fue posible registrar la operacion; reintente",
            ), 503
        except Exception:
            db.rollback()
            return _respuesta(
                datos, "ERROR_REINTENTABLE", error_codigo="LEDGER_NO_DISPONIBLE",
                error_mensaje="No fue posible registrar la operacion; reintente",
            ), 503

        if inicio.get("procesar") is not True:
            db.rollback()
            estado = inicio.get("estado") or "PENDIENTE"
            if estado == "ACEPTADA":
                return _respuesta(
                    datos, "ACEPTADA",
                    id_definitivo=inicio.get("id_definitivo"),
                    resultado=inicio.get("resultado"),
                ), 200
            if estado in {"CONFLICTO", "RECHAZADA"}:
                return _respuesta(
                    datos, estado, id_definitivo=inicio.get("id_definitivo"),
                    resultado=inicio.get("resultado"),
                    error_codigo=inicio.get("error_codigo"),
                    error_mensaje=inicio.get("error_mensaje"),
                ), 409 if estado == "CONFLICTO" else 422
            if estado == "ERROR_REINTENTABLE":
                return _respuesta(
                    datos, estado, error_codigo=inicio.get("error_codigo"),
                    error_mensaje=inicio.get("error_mensaje"),
                ), 503
            return _respuesta(
                datos, "PENDIENTE", error_codigo="OPERACION_EN_CURSO",
                error_mensaje="La operacion esta en curso; consulte por UUID",
            ), 202

        try:
            if datos.tipo == "RESERVA":
                resultado = _reservar(db, tenant, datos, cliente_id)
                id_definitivo = int(resultado["reserva_id"])
            else:
                id_definitivo, resultado = _vender(db, tenant, datos)
            OfflineLedgerRepository.finish(
                db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
                operacion_uuid=str(datos.operacion_uuid), estado="ACEPTADA",
                id_definitivo=id_definitivo, resultado=resultado,
            )
            db.commit()
            return _respuesta(
                datos, "ACEPTADA", id_definitivo=id_definitivo,
                resultado=resultado,
            ), 200
        except OfflineNoAutorizado as exc:
            fila = _registrar_fallo(
                db, tenant, datos, payload_hash, exc.estado, exc.codigo, exc.mensaje
            )
            if fila and fila.get("estado") == "ACEPTADA":
                return _respuesta(
                    datos, "ACEPTADA", id_definitivo=fila.get("id_definitivo"),
                    resultado=fila.get("resultado"),
                ), 200
            return _respuesta(
                datos, exc.estado, error_codigo=exc.codigo,
                error_mensaje=exc.mensaje,
            ), exc.http_status
        except (OfflineConflicto, OfflineRechazada) as exc:
            fila = _registrar_fallo(
                db, tenant, datos, payload_hash, exc.estado, exc.codigo, exc.mensaje
            )
            if fila and fila.get("estado") == "ACEPTADA":
                return _respuesta(
                    datos, "ACEPTADA", id_definitivo=fila.get("id_definitivo"),
                    resultado=fila.get("resultado"),
                ), 200
            return _respuesta(
                datos, exc.estado, error_codigo=exc.codigo,
                error_mensaje=exc.mensaje,
            ), exc.http_status
        except OfflineErrorReintentable as exc:
            _registrar_fallo(
                db, tenant, datos, payload_hash, exc.estado, exc.codigo, exc.mensaje
            )
            return _respuesta(
                datos, exc.estado, error_codigo=exc.codigo,
                error_mensaje=exc.mensaje,
            ), exc.http_status
        except VentaTenantDeshabilitada as exc:
            mensaje = "Procedimiento de venta no disponible"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "ERROR_REINTENTABLE",
                "SP_NO_DISPONIBLE", mensaje,
            )
            return _respuesta(
                datos, "ERROR_REINTENTABLE", error_codigo="SP_NO_DISPONIBLE",
                error_mensaje=mensaje,
            ), 503
        except ReservaTenantDeshabilitada as exc:
            mensaje = "Procedimiento de reserva no disponible"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "ERROR_REINTENTABLE",
                "SP_NO_DISPONIBLE", mensaje,
            )
            return _respuesta(
                datos, "ERROR_REINTENTABLE", error_codigo="SP_NO_DISPONIBLE",
                error_mensaje=mensaje,
            ), 503
        except (ReservaTenantIntegridadError, VentaTenantIntegridadError) as exc:
            mensaje = "Resultado comercial inconsistente"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "CONFLICTO",
                "INTEGRIDAD_COMERCIAL", mensaje,
            )
            return _respuesta(
                datos, "CONFLICTO", error_codigo="INTEGRIDAD_COMERCIAL",
                error_mensaje=mensaje,
            ), 409
        except VentaTenantScopeError:
            mensaje = "Sucursal no autorizada para la venta"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "RECHAZADA",
                "SUCURSAL_NO_AUTORIZADA", mensaje,
            )
            return _respuesta(
                datos, "RECHAZADA", error_codigo="SUCURSAL_NO_AUTORIZADA",
                error_mensaje=mensaje,
            ), 403
        except VentaTenantRolNoAutorizadoError:
            mensaje = "Rol no autorizado para la venta"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "RECHAZADA",
                "ROL_NO_AUTORIZADO", mensaje,
            )
            return _respuesta(
                datos, "RECHAZADA", error_codigo="ROL_NO_AUTORIZADO",
                error_mensaje=mensaje,
            ), 403
        except (VentaTenantStockError, VentaTenantSeleccionError,
                VentaTenantInventarioError, VentaTenantDuplicadaError):
            mensaje = "Stock, inventario o seleccion incompatible"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "CONFLICTO",
                "CONFLICTO_COMERCIAL", mensaje,
            )
            return _respuesta(
                datos, "CONFLICTO", error_codigo="CONFLICTO_COMERCIAL",
                error_mensaje=mensaje,
            ), 409
        except VentaTenantError:
            mensaje = "La operacion comercial fue rechazada"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "RECHAZADA",
                "OPERACION_RECHAZADA", mensaje,
            )
            return _respuesta(
                datos, "RECHAZADA", error_codigo="OPERACION_RECHAZADA",
                error_mensaje=mensaje,
            ), 422
        except (PermissionError, ValueError) as exc:
            mensaje = "La operacion no cumple las reglas comerciales"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "RECHAZADA",
                "VALIDACION", mensaje,
            )
            return _respuesta(
                datos, "RECHAZADA", error_codigo="VALIDACION",
                error_mensaje=mensaje,
            ), 422
        except DBAPIError as exc:
            db.rollback()
            estado = _sqlstate(exc)
            if estado == "42501":
                categoria, codigo, status = "RECHAZADA", "NO_AUTORIZADO", 403
            elif estado in {"23505", "23514", "P0001", "P0002"}:
                categoria, codigo, status = "CONFLICTO", "CONFLICTO_COMERCIAL", 409
            elif estado in {"22023", "22P02"}:
                categoria, codigo, status = "RECHAZADA", "VALIDACION", 422
            else:
                categoria, codigo, status = "ERROR_REINTENTABLE", "ERROR_TECNICO", 503
            mensaje = "No fue posible completar la operacion"
            _registrar_fallo(
                db, tenant, datos, payload_hash, categoria, codigo, mensaje
            )
            return _respuesta(
                datos, categoria, error_codigo=codigo, error_mensaje=mensaje,
            ), status
        except Exception:
            db.rollback()
            mensaje = "No fue posible completar la operacion; reintente"
            _registrar_fallo(
                db, tenant, datos, payload_hash, "ERROR_REINTENTABLE",
                "ERROR_TECNICO", mensaje,
            )
            return _respuesta(
                datos, "ERROR_REINTENTABLE", error_codigo="ERROR_TECNICO",
                error_mensaje=mensaje,
            ), 503

    @staticmethod
    def consultar(
        db: Session, tenant: TenantContext, operacion_uuid: UUID,
    ) -> OperacionOfflineResponse:
        if str(tenant.contexto) not in {"cliente", "personal"}:
            raise OfflineNoAutorizado("NO_AUTORIZADO", "Contexto no autorizado")
        fila = OfflineLedgerRepository.obtener(
            db, empresa_id=tenant.empresa_id, usuario_id=tenant.usuario_id,
            operacion_uuid=str(operacion_uuid),
        )
        if fila is None:
            raise OfflineNoEncontrada(
                "OPERACION_NO_ENCONTRADA", "Operacion offline no encontrada"
            )
        return OperacionOfflineResponse(
            operacion_uuid=operacion_uuid, tipo=fila["tipo"],
            estado=fila["estado"], id_definitivo=fila.get("id_definitivo"),
            resultado=fila.get("resultado"),
            error_codigo=fila.get("error_codigo"),
            error_mensaje=fila.get("error_mensaje"),
            reintentable=fila["estado"] in {
                "PENDIENTE", "ENVIANDO", "ERROR_REINTENTABLE"
            },
        )
