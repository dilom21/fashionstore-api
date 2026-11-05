"""E2/5BN: ordenes de compra tenantizadas con recepcion de inventario.

El cliente HTTP solo envia proveedor, sucursal, fecha estimada, observacion y
lineas (variante, temporada, cantidad, costo). El servidor deriva empresa,
empleado, estado, stock y bitacora; PostgreSQL revalida rol, sucursal,
suscripcion e idempotencia en una unica transaccion.

No reutiliza `/ordenes-compra` ni los SP legacy: solo llama a las rutinas
`public.sp_*_orden_compra_tenant` de la migracion 5BN.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

_CODIGOS_CONFLICTO = frozenset({"23514", "23505", "40001", "40P01"})
_CODIGOS_INVALIDOS = frozenset({"22023", "22P02"})

_DETALLE_403 = "Operacion no autorizada"
_DETALLE_409 = "Conflicto en la orden de compra"
_DETALLE_422 = "Datos de la orden de compra invalidos"
_DETALLE_FALLO = "La orden de compra no pudo procesarse"

_SQL_CABECERA = (
    "SELECT o.id, o.proveedor_id, p.razon_social AS proveedor_razon_social,\n"
    "       o.sucursal_id, s.nombre AS sucursal_nombre, o.estado, o.fecha_orden,\n"
    "       o.fecha_estimada, o.fecha_recepcion,\n"
    "       (SELECT count(*) FROM public.detalle_orden_compra d\n"
    "         WHERE d.orden_compra_id = o.id AND d.empresa_id = o.empresa_id) AS total_lineas\n"
    "  FROM public.orden_compra o\n"
    "  JOIN public.proveedor p ON p.id = o.proveedor_id AND p.empresa_id = o.empresa_id\n"
    "  JOIN public.sucursal s ON s.id = o.sucursal_id AND s.empresa_id = o.empresa_id"
)

_SQL_LINEAS = (
    "SELECT d.id, d.variante_producto_id, vp.sku, d.temporada_id,"
    " t.nombre AS temporada_nombre,\n"
    "       d.cantidad, d.costo_unitario,\n"
    "       COALESCE((SELECT sum(r.cantidad) FROM public.detalle_recepcion_compra r\n"
    "                  WHERE r.detalle_orden_compra_id = d.id\n"
    "                    AND r.empresa_id = d.empresa_id), 0) AS cantidad_recibida,\n"
    "       d.cantidad - COALESCE((SELECT sum(r.cantidad) FROM public.detalle_recepcion_compra r\n"
    "                  WHERE r.detalle_orden_compra_id = d.id\n"
    "                    AND r.empresa_id = d.empresa_id), 0) AS cantidad_pendiente\n"
    "  FROM public.detalle_orden_compra d\n"
    "  JOIN public.variante_producto vp"
    " ON vp.id = d.variante_producto_id AND vp.empresa_id = d.empresa_id\n"
    "  JOIN public.temporada t ON t.id = d.temporada_id AND t.empresa_id = d.empresa_id\n"
    " WHERE d.orden_compra_id = :orden AND d.empresa_id = :empresa\n"
    " ORDER BY d.id"
)


class OrdenCompraTenantError(Exception):
    """Fallo de negocio ya traducido a un codigo HTTP seguro."""

    def __init__(self, codigo: int, detalle: str):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _detalle_por_codigo(codigo: int) -> str:
    """Mensaje neutro por codigo: nunca se reenvia el texto SQL al cliente."""
    if codigo == 403:
        return _DETALLE_403
    if codigo == 409:
        return _DETALLE_409
    if codigo == 422:
        return _DETALLE_422
    return _DETALLE_FALLO


def _traducir(exc: DBAPIError) -> OrdenCompraTenantError:
    """Traduce SQLSTATE a error tipado; re-lanza lo desconocido.

    `IntegrityError` es subclase de `DBAPIError`, por eso el SQLSTATE se evalua
    antes de decidir: un 42501 jamas debe terminar como 500.
    """
    estado = _sqlstate(exc)
    if estado == "42501":
        return OrdenCompraTenantError(403, _DETALLE_403)
    if estado in _CODIGOS_CONFLICTO:
        return OrdenCompraTenantError(409, _DETALLE_409)
    if estado in _CODIGOS_INVALIDOS:
        return OrdenCompraTenantError(422, _DETALLE_422)
    raise exc


def _es_entero_positivo(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _costo_texto(valor: Any) -> str:
    """Costo canonico con dos decimales; el SP valida el formato textual."""
    try:
        numero = Decimal(str(valor))
        if not numero.is_finite() or numero < 0:
            raise InvalidOperation
        return format(numero.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrdenCompraTenantError(422, _DETALLE_422) from exc


def _cargar(valor: Any) -> dict:
    """Normaliza el JSONB devuelto por la rutina y valida `ok`."""
    if isinstance(valor, (str, bytes, bytearray)):
        try:
            valor = json.loads(valor)
        except (TypeError, ValueError) as exc:
            raise OrdenCompraTenantError(
                503, "Orden de compra sin respuesta valida"
            ) from exc
    if not isinstance(valor, dict):
        raise OrdenCompraTenantError(503, "Orden de compra sin respuesta valida")
    if valor.get("ok") is not True:
        try:
            codigo = int(valor.get("codigo", 409))
        except (TypeError, ValueError):
            codigo = 409
        raise OrdenCompraTenantError(codigo, _detalle_por_codigo(codigo))
    return valor


def _llamar(db: Session, consulta: str, parametros: dict) -> dict:
    try:
        valor = db.scalar(text(consulta), parametros)
    except DBAPIError as exc:
        raise _traducir(exc) from exc
    return _cargar(valor)


def huella_recepcion(
    *,
    empresa_id: int,
    orden_id: int,
    usuario_id: int,
    observacion: str | None,
    lineas: list[dict],
) -> str:
    """Huella canonica de la recepcion: mismo UUID + misma huella = replay."""
    normalizadas = sorted(
        (
            {
                "detalle_orden_compra_id": int(x["detalle_orden_compra_id"]),
                "cantidad": int(x["cantidad"]),
            }
            for x in lineas
        ),
        key=lambda x: x["detalle_orden_compra_id"],
    )
    payload = {
        "empresa_id": int(empresa_id),
        "orden_id": int(orden_id),
        "usuario_id": int(usuario_id),
        "observacion": observacion,
        "lineas": normalizadas,
    }
    canonico = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


class TenantOrdenCompraRepository:
    @staticmethod
    def crear(
        db: Session, *, empresa_id: int, usuario_id: int, proveedor_id: int,
        sucursal_id: int, fecha_estimada: datetime | None,
        observacion: str | None, lineas: list[dict],
    ) -> dict:
        if not all(
            _es_entero_positivo(v)
            for v in (empresa_id, usuario_id, proveedor_id, sucursal_id)
        ):
            raise OrdenCompraTenantError(403, "Identidad de compra invalida")
        if not lineas:
            raise OrdenCompraTenantError(
                422, "La orden requiere al menos una linea"
            )

        preparadas: list[dict] = []
        for linea in lineas:
            variante = linea.get("variante_producto_id")
            temporada = linea.get("temporada_id")
            cantidad = linea.get("cantidad")
            if not (
                _es_entero_positivo(variante)
                and _es_entero_positivo(temporada)
                and _es_entero_positivo(cantidad)
            ):
                raise OrdenCompraTenantError(422, _DETALLE_422)
            preparadas.append(
                {
                    "variante_producto_id": variante,
                    "temporada_id": temporada,
                    "cantidad": cantidad,
                    "costo_unitario": _costo_texto(linea.get("costo_unitario")),
                }
            )

        valor = _llamar(
            db,
            "SELECT public.sp_crear_orden_compra_tenant("
            "CAST(:empresa AS bigint), CAST(:usuario AS bigint), "
            "CAST(:proveedor AS bigint), CAST(:sucursal AS bigint), "
            "CAST(:fecha AS timestamptz), CAST(:observacion AS text), "
            "CAST(:lineas AS jsonb))",
            {
                "empresa": empresa_id,
                "usuario": usuario_id,
                "proveedor": proveedor_id,
                "sucursal": sucursal_id,
                "fecha": fecha_estimada,
                "observacion": observacion,
                "lineas": json.dumps(preparadas, separators=(",", ":")),
            },
        )
        return {
            "orden_compra_id": int(valor["orden_compra_id"]),
            "estado": str(valor["estado"]),
            "proveedor_id": int(valor.get("proveedor_id", proveedor_id)),
            "sucursal_id": int(valor.get("sucursal_id", sucursal_id)),
            "total_lineas": int(valor.get("total_lineas", len(preparadas))),
            "lineas": [
                {
                    "variante_producto_id": int(linea["variante_producto_id"]),
                    "temporada_id": int(linea["temporada_id"]),
                    "cantidad": int(linea["cantidad"]),
                    "costo_unitario": float(linea["costo_unitario"]),
                }
                for linea in valor.get("lineas") or []
            ],
            "stock_modificado": bool(valor.get("stock_modificado", False)),
        }

    @staticmethod
    def enviar(
        db: Session, *, empresa_id: int, usuario_id: int, orden_id: int
    ) -> dict:
        if not all(
            _es_entero_positivo(v) for v in (empresa_id, usuario_id, orden_id)
        ):
            raise OrdenCompraTenantError(403, "Identidad de compra invalida")
        valor = _llamar(
            db,
            "SELECT public.sp_enviar_orden_compra_tenant("
            "CAST(:empresa AS bigint), CAST(:orden AS bigint), "
            "CAST(:usuario AS bigint))",
            {"empresa": empresa_id, "orden": orden_id, "usuario": usuario_id},
        )
        return {
            "idempotente": bool(valor.get("idempotente")),
            "orden_compra_id": int(valor["orden_compra_id"]),
            "estado": str(valor["estado"]),
        }

    @staticmethod
    def cancelar(
        db: Session, *, empresa_id: int, usuario_id: int, orden_id: int,
        observacion: str | None,
    ) -> dict:
        if not all(
            _es_entero_positivo(v) for v in (empresa_id, usuario_id, orden_id)
        ):
            raise OrdenCompraTenantError(403, "Identidad de compra invalida")
        valor = _llamar(
            db,
            "SELECT public.sp_cancelar_orden_compra_tenant("
            "CAST(:empresa AS bigint), CAST(:orden AS bigint), "
            "CAST(:usuario AS bigint), CAST(:observacion AS text))",
            {
                "empresa": empresa_id,
                "orden": orden_id,
                "usuario": usuario_id,
                "observacion": observacion,
            },
        )
        return {
            "idempotente": bool(valor.get("idempotente")),
            "orden_compra_id": int(valor["orden_compra_id"]),
            "estado": str(valor["estado"]),
        }

    @staticmethod
    def recibir(
        db: Session, *, empresa_id: int, usuario_id: int, orden_id: int,
        operacion_id: str, observacion: str | None, lineas: list[dict],
    ) -> dict:
        if not all(
            _es_entero_positivo(v) for v in (empresa_id, usuario_id, orden_id)
        ):
            raise OrdenCompraTenantError(403, "Identidad de compra invalida")
        if not operacion_id:
            raise OrdenCompraTenantError(422, "operacion_id obligatorio")
        if not lineas:
            raise OrdenCompraTenantError(
                422, "La recepcion requiere al menos una linea"
            )

        preparadas: list[dict] = []
        for linea in lineas:
            detalle_id = linea.get("detalle_orden_compra_id")
            cantidad = linea.get("cantidad")
            if not (
                _es_entero_positivo(detalle_id) and _es_entero_positivo(cantidad)
            ):
                raise OrdenCompraTenantError(422, _DETALLE_422)
            preparadas.append(
                {"detalle_orden_compra_id": detalle_id, "cantidad": cantidad}
            )

        huella = huella_recepcion(
            empresa_id=empresa_id,
            orden_id=orden_id,
            usuario_id=usuario_id,
            observacion=observacion,
            lineas=preparadas,
        )
        valor = _llamar(
            db,
            "SELECT public.sp_recibir_orden_compra_tenant("
            "CAST(:empresa AS bigint), CAST(:orden AS bigint), "
            "CAST(:usuario AS bigint), CAST(:operacion AS uuid), "
            "CAST(:huella AS text), CAST(:lineas AS jsonb), "
            "CAST(:observacion AS text))",
            {
                "empresa": empresa_id,
                "orden": orden_id,
                "usuario": usuario_id,
                "operacion": operacion_id,
                "huella": huella,
                "lineas": json.dumps(preparadas, separators=(",", ":")),
                "observacion": observacion,
            },
        )
        recepcion = valor.get("recepcion") or {}
        if not isinstance(recepcion, dict):
            raise OrdenCompraTenantError(
                503, "Orden de compra sin respuesta valida"
            )
        # Se aplana `recepcion` igual que la venta de contado: la respuesta
        # expone los datos de la SP y agrega solo el flag de idempotencia.
        return {
            "idempotente": bool(valor.get("idempotente")),
            "recepcion_id": int(recepcion["recepcion_id"]),
            "orden_compra_id": int(recepcion["orden_compra_id"]),
            "estado_orden": str(recepcion["estado_orden"]),
            "unidades_recibidas": int(recepcion["unidades_recibidas"]),
            "stock": [
                {
                    "inventario_id": int(fila["inventario_id"]),
                    "stock_actual": int(fila["stock_actual"]),
                    "stock_reservado": int(fila["stock_reservado"]),
                }
                for fila in recepcion.get("stock") or []
            ],
            "operacion_id": str(recepcion["operacion_id"]),
        }

    @staticmethod
    def listar(
        db: Session, *, empresa_id: int, estado: str | None = None,
        proveedor_id: int | None = None, sucursal_id: int | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        condiciones = ["o.empresa_id = :empresa"]
        parametros: dict = {
            "empresa": empresa_id,
            "limit": int(limit),
            "offset": int(offset),
        }
        if estado is not None:
            condiciones.append("o.estado = :estado")
            parametros["estado"] = estado
        if proveedor_id is not None:
            condiciones.append("o.proveedor_id = :proveedor")
            parametros["proveedor"] = proveedor_id
        if sucursal_id is not None:
            condiciones.append("o.sucursal_id = :sucursal")
            parametros["sucursal"] = sucursal_id

        consulta = (
            _SQL_CABECERA
            + " WHERE "
            + " AND ".join(condiciones)
            + " ORDER BY o.id DESC LIMIT :limit OFFSET :offset"
        )
        try:
            filas = db.execute(text(consulta), parametros).mappings().all()
        except DBAPIError as exc:
            raise _traducir(exc) from exc
        return [dict(fila) for fila in filas]

    @staticmethod
    def obtener(db: Session, *, empresa_id: int, orden_id: int) -> dict | None:
        parametros = {"empresa": empresa_id, "orden": orden_id}
        try:
            cabecera = (
                db.execute(
                    text(
                        _SQL_CABECERA
                        + " WHERE o.id = :orden AND o.empresa_id = :empresa"
                    ),
                    parametros,
                )
                .mappings()
                .first()
            )
            if cabecera is None:
                return None
            filas = db.execute(text(_SQL_LINEAS), parametros).mappings().all()
        except DBAPIError as exc:
            raise _traducir(exc) from exc

        detalle = dict(cabecera)
        # El costo se expone como float solo en el borde HTTP.
        detalle["lineas"] = [
            {
                "id": int(fila["id"]),
                "variante_producto_id": int(fila["variante_producto_id"]),
                "sku": fila["sku"],
                "temporada_id": int(fila["temporada_id"]),
                "temporada_nombre": fila["temporada_nombre"],
                "cantidad": int(fila["cantidad"]),
                "costo_unitario": float(fila["costo_unitario"]),
                "cantidad_recibida": int(fila["cantidad_recibida"]),
                "cantidad_pendiente": int(fila["cantidad_pendiente"]),
            }
            for fila in filas
        ]
        return detalle
