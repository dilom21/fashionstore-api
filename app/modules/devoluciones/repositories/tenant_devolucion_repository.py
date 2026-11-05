"""E2/5BN: devoluciones tenantizadas con reposicion fisica controlada.

El cliente HTTP solo envia venta, motivo, observacion, `operacion_id` e items
(detalle de venta, cantidad, reponer, motivo). Empresa, usuario, precios, monto,
estado y reembolso declarado se derivan en el servidor y en PostgreSQL.

El reembolso NO se ejecuta: sin pasarela integrada queda `PENDIENTE` con
`ejecutado = false`. No reutiliza `/devoluciones` ni los SP legacy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.catalogo.models.models import (
    Color, Producto, Talla, VarianteProducto,
)
from app.modules.devoluciones.models.models import Devolucion, DetalleDevolucion
from app.modules.inventario.models.models import Inventario
from app.modules.ventas.models.models import DetalleVenta, Venta

_CODIGOS_CONFLICTO = frozenset({"23514", "23505", "40001", "40P01"})
_CODIGOS_INVALIDOS = frozenset({"22023", "22P02"})

_DETALLE_403 = "Operacion no autorizada"
_DETALLE_409 = "Conflicto en la devolucion"
_DETALLE_422 = "Datos de la devolucion invalidos"
_DETALLE_FALLO = "La devolucion no pudo procesarse"

_SQL_CABECERA = (
    "SELECT d.id, d.venta_id, d.fecha_hora, d.estado, d.motivo, d.observacion,\n"
    "       v.sucursal_id, s.nombre AS sucursal_nombre, v.total AS venta_total,\n"
    "       COALESCE((SELECT sum(dd.cantidad) FROM public.detalle_devolucion dd\n"
    "                  WHERE dd.devolucion_id = d.id"
    " AND dd.empresa_id = d.empresa_id), 0) AS unidades,\n"
    "       COALESCE((SELECT sum(dd.cantidad * dv.precio_unitario)\n"
    "                   FROM public.detalle_devolucion dd\n"
    "                   JOIN public.detalle_venta dv ON dv.id = dd.detalle_venta_id\n"
    "                  WHERE dd.devolucion_id = d.id"
    " AND dd.empresa_id = d.empresa_id), 0) AS monto_estimado\n"
    "  FROM public.devolucion d\n"
    "  JOIN public.venta v ON v.id = d.venta_id AND v.empresa_id = d.empresa_id\n"
    "  JOIN public.sucursal s"
    " ON s.id = v.sucursal_id AND s.empresa_id = v.empresa_id"
)

_SQL_ITEMS = (
    "SELECT dd.id AS detalle_devolucion_id, dd.detalle_venta_id, dd.cantidad,\n"
    "       dd.motivo, dd.reponer, dv.inventario_id, dv.precio_unitario,\n"
    "       vp.sku, (dd.cantidad * dv.precio_unitario)::numeric(12,2) AS subtotal\n"
    "  FROM public.detalle_devolucion dd\n"
    "  JOIN public.detalle_venta dv"
    " ON dv.id = dd.detalle_venta_id AND dv.empresa_id = dd.empresa_id\n"
    "  JOIN public.inventario i"
    " ON i.id = dv.inventario_id AND i.empresa_id = dv.empresa_id\n"
    "  JOIN public.variante_producto vp"
    " ON vp.id = i.variante_producto_id AND vp.empresa_id = i.empresa_id\n"
    " WHERE dd.devolucion_id = :devolucion AND dd.empresa_id = :empresa\n"
    " ORDER BY dd.id"
)

_SQL_REEMBOLSO = (
    "SELECT r.id AS reembolso_id, r.estado, r.monto, r.moneda, r.medio, r.ejecutado,\n"
    "       r.referencia, r.fecha_creacion, r.fecha_ejecucion\n"
    "  FROM public.reembolso_devolucion r\n"
    " WHERE r.devolucion_id = :devolucion AND r.empresa_id = :empresa"
)


class DevolucionTenantError(Exception):
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


def _traducir(exc: DBAPIError) -> DevolucionTenantError:
    """Traduce SQLSTATE a error tipado; re-lanza lo desconocido.

    `IntegrityError` es subclase de `DBAPIError`, por eso el SQLSTATE se evalua
    antes de decidir: un 42501 jamas debe terminar como 500.
    """
    estado = _sqlstate(exc)
    if estado == "42501":
        return DevolucionTenantError(403, _DETALLE_403)
    if estado in _CODIGOS_CONFLICTO:
        return DevolucionTenantError(409, _DETALLE_409)
    if estado in _CODIGOS_INVALIDOS:
        return DevolucionTenantError(422, _DETALLE_422)
    raise exc


def _es_entero_positivo(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _cargar(valor: Any) -> dict:
    """Normaliza el JSONB devuelto por la rutina y valida `ok`."""
    if isinstance(valor, (str, bytes, bytearray)):
        try:
            valor = json.loads(valor)
        except (TypeError, ValueError) as exc:
            raise DevolucionTenantError(
                503, "Devolucion sin respuesta valida"
            ) from exc
    if not isinstance(valor, dict):
        raise DevolucionTenantError(503, "Devolucion sin respuesta valida")
    if valor.get("ok") is not True:
        try:
            codigo = int(valor.get("codigo", 409))
        except (TypeError, ValueError):
            codigo = 409
        raise DevolucionTenantError(codigo, _detalle_por_codigo(codigo))
    return valor


def _llamar(db: Session, consulta: str, parametros: dict) -> dict:
    try:
        valor = db.scalar(text(consulta), parametros)
    except DBAPIError as exc:
        raise _traducir(exc) from exc
    return _cargar(valor)


def _item_motivo(valor: Any) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise DevolucionTenantError(422, _DETALLE_422)
    return valor


def huella_creacion(
    *,
    empresa_id: int,
    venta_id: int,
    usuario_id: int,
    motivo: str,
    observacion: str | None,
    items: list[dict],
) -> str:
    """Huella canonica de la solicitud: mismo UUID + misma huella = replay."""
    normalizados = sorted(
        (
            {
                "detalle_venta_id": int(x["detalle_venta_id"]),
                "cantidad": int(x["cantidad"]),
                "reponer": bool(x.get("reponer", True)),
                "motivo": x.get("motivo"),
            }
            for x in items
        ),
        key=lambda x: x["detalle_venta_id"],
    )
    payload = {
        "empresa_id": int(empresa_id),
        "venta_id": int(venta_id),
        "usuario_id": int(usuario_id),
        "motivo": motivo,
        "observacion": observacion,
        "items": normalizados,
    }
    canonico = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def huella_proceso(
    *, empresa_id: int, devolucion_id: int, usuario_id: int
) -> str:
    """Huella canonica del procesamiento: mismo UUID + misma huella = replay."""
    payload = {
        "empresa_id": int(empresa_id),
        "devolucion_id": int(devolucion_id),
        "usuario_id": int(usuario_id),
    }
    canonico = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _fila_devolucion(fila: dict) -> dict:
    """Contrato de cabecera: montos como float solo en el borde HTTP."""
    return {
        "id": int(fila["id"]),
        "venta_id": int(fila["venta_id"]),
        "fecha_hora": fila["fecha_hora"],
        "estado": str(fila["estado"]),
        "motivo": fila["motivo"],
        "observacion": fila["observacion"],
        "sucursal_id": int(fila["sucursal_id"]),
        "sucursal_nombre": fila["sucursal_nombre"],
        "venta_total": float(fila["venta_total"]),
        "unidades": int(fila["unidades"]),
        "monto_estimado": float(fila["monto_estimado"]),
    }


class TenantDevolucionRepository:
    @staticmethod
    def crear(
        db: Session, *, empresa_id: int, usuario_id: int, venta_id: int,
        operacion_id: str, motivo: str, observacion: str | None,
        items: list[dict],
    ) -> dict:
        if not all(
            _es_entero_positivo(v) for v in (empresa_id, usuario_id, venta_id)
        ):
            raise DevolucionTenantError(403, "Identidad de devolucion invalida")
        if not operacion_id:
            raise DevolucionTenantError(422, "operacion_id obligatorio")
        if not isinstance(motivo, str) or not motivo.strip():
            raise DevolucionTenantError(422, "El motivo es obligatorio")
        if not items:
            raise DevolucionTenantError(
                422, "La devolucion requiere al menos un item"
            )

        preparados: list[dict] = []
        for item in items:
            detalle_id = item.get("detalle_venta_id")
            cantidad = item.get("cantidad")
            reponer = item.get("reponer", True)
            if not (
                _es_entero_positivo(detalle_id) and _es_entero_positivo(cantidad)
            ):
                raise DevolucionTenantError(422, _DETALLE_422)
            if not isinstance(reponer, bool):
                raise DevolucionTenantError(422, _DETALLE_422)
            preparados.append(
                {
                    "detalle_venta_id": detalle_id,
                    "cantidad": cantidad,
                    "reponer": reponer,
                    "motivo": _item_motivo(item.get("motivo")),
                }
            )

        huella = huella_creacion(
            empresa_id=empresa_id,
            venta_id=venta_id,
            usuario_id=usuario_id,
            motivo=motivo,
            observacion=observacion,
            items=preparados,
        )
        valor = _llamar(
            db,
            "SELECT public.sp_crear_devolucion_tenant("
            "CAST(:empresa AS bigint), CAST(:venta AS bigint), "
            "CAST(:usuario AS bigint), CAST(:operacion AS uuid), "
            "CAST(:huella AS text), CAST(:motivo AS text), "
            "CAST(:observacion AS text), CAST(:items AS jsonb))",
            {
                "empresa": empresa_id,
                "venta": venta_id,
                "usuario": usuario_id,
                "operacion": operacion_id,
                "huella": huella,
                "motivo": motivo,
                "observacion": observacion,
                "items": json.dumps(preparados, separators=(",", ":")),
            },
        )
        devolucion = valor.get("devolucion") or {}
        if not isinstance(devolucion, dict):
            raise DevolucionTenantError(503, "Devolucion sin respuesta valida")
        reembolso = devolucion.get("reembolso") or {}
        # Se aplana `devolucion` igual que la venta de contado: el cuerpo expone
        # los datos de la SP y agrega solo el flag de idempotencia.
        return {
            "idempotente": bool(valor.get("idempotente")),
            "devolucion_id": int(devolucion["devolucion_id"]),
            "venta_id": int(devolucion["venta_id"]),
            "estado": str(devolucion["estado"]),
            "motivo": str(devolucion["motivo"]),
            "unidades": int(devolucion["unidades"]),
            "monto_estimado": float(devolucion["monto_estimado"]),
            "moneda": str(devolucion.get("moneda", "BOB")),
            "items": [
                {
                    "detalle_venta_id": int(item["detalle_venta_id"]),
                    "inventario_id": int(item["inventario_id"]),
                    "cantidad": int(item["cantidad"]),
                    "reponer": bool(item["reponer"]),
                    "precio_unitario": float(item["precio_unitario"]),
                }
                for item in devolucion.get("items") or []
            ],
            "reembolso": {
                "estado": str(reembolso.get("estado", "PENDIENTE")),
                "ejecutado": bool(reembolso.get("ejecutado", False)),
                "medio": str(reembolso.get("medio", "NO_DISPONIBLE")),
                "monto": float(reembolso.get("monto", 0)),
            },
            "stock_modificado": bool(devolucion.get("stock_modificado", False)),
            "operacion_id": str(devolucion["operacion_id"]),
        }

    @staticmethod
    def _resolver(
        db: Session, *, empresa_id: int, usuario_id: int, devolucion_id: int,
        accion: str,
    ) -> dict:
        if not all(
            _es_entero_positivo(v)
            for v in (empresa_id, usuario_id, devolucion_id)
        ):
            raise DevolucionTenantError(403, "Identidad de devolucion invalida")
        valor = _llamar(
            db,
            "SELECT public.sp_resolver_devolucion_tenant("
            "CAST(:empresa AS bigint), CAST(:devolucion AS bigint), "
            "CAST(:usuario AS bigint), CAST(:accion AS text))",
            {
                "empresa": empresa_id,
                "devolucion": devolucion_id,
                "usuario": usuario_id,
                "accion": accion,
            },
        )
        return {
            "idempotente": bool(valor.get("idempotente")),
            "devolucion_id": int(valor["devolucion_id"]),
            "estado": str(valor["estado"]),
        }

    @staticmethod
    def aprobar(
        db: Session, *, empresa_id: int, usuario_id: int, devolucion_id: int
    ) -> dict:
        return TenantDevolucionRepository._resolver(
            db,
            empresa_id=empresa_id,
            usuario_id=usuario_id,
            devolucion_id=devolucion_id,
            accion="APROBAR",
        )

    @staticmethod
    def rechazar(
        db: Session, *, empresa_id: int, usuario_id: int, devolucion_id: int
    ) -> dict:
        return TenantDevolucionRepository._resolver(
            db,
            empresa_id=empresa_id,
            usuario_id=usuario_id,
            devolucion_id=devolucion_id,
            accion="RECHAZAR",
        )

    @staticmethod
    def procesar(
        db: Session, *, empresa_id: int, usuario_id: int, devolucion_id: int,
        operacion_id: str,
    ) -> dict:
        if not all(
            _es_entero_positivo(v)
            for v in (empresa_id, usuario_id, devolucion_id)
        ):
            raise DevolucionTenantError(403, "Identidad de devolucion invalida")
        if not operacion_id:
            raise DevolucionTenantError(422, "operacion_id obligatorio")
        huella = huella_proceso(
            empresa_id=empresa_id,
            devolucion_id=devolucion_id,
            usuario_id=usuario_id,
        )
        valor = _llamar(
            db,
            "SELECT public.sp_procesar_devolucion_tenant("
            "CAST(:empresa AS bigint), CAST(:devolucion AS bigint), "
            "CAST(:usuario AS bigint), CAST(:operacion AS uuid), "
            "CAST(:huella AS text))",
            {
                "empresa": empresa_id,
                "devolucion": devolucion_id,
                "usuario": usuario_id,
                "operacion": operacion_id,
                "huella": huella,
            },
        )
        devolucion = valor.get("devolucion") or {}
        if not isinstance(devolucion, dict):
            raise DevolucionTenantError(503, "Devolucion sin respuesta valida")
        reembolso = devolucion.get("reembolso") or {}
        return {
            "idempotente": bool(valor.get("idempotente")),
            "devolucion_id": int(devolucion["devolucion_id"]),
            "venta_id": int(devolucion["venta_id"]),
            "estado": str(devolucion["estado"]),
            "unidades": int(devolucion["unidades"]),
            "unidades_repuestas": int(devolucion.get("unidades_repuestas", 0)),
            "stock": [
                {
                    "inventario_id": int(fila["inventario_id"]),
                    "stock_actual": int(fila["stock_actual"]),
                    "stock_reservado": int(fila["stock_reservado"]),
                }
                for fila in devolucion.get("stock") or []
            ],
            "reembolso": {
                "reembolso_id": int(reembolso["reembolso_id"]),
                "estado": str(reembolso.get("estado", "PENDIENTE")),
                "ejecutado": bool(reembolso.get("ejecutado", False)),
                "medio": str(reembolso.get("medio", "NO_DISPONIBLE")),
                "monto": float(reembolso.get("monto", 0)),
                "moneda": str(reembolso.get("moneda", "BOB")),
            },
            "operacion_id": str(devolucion["operacion_id"]),
        }

    @staticmethod
    def listar(
        db: Session, *, empresa_id: int, estado: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        condiciones = ["d.empresa_id = :empresa"]
        parametros: dict = {
            "empresa": empresa_id,
            "limit": int(limit),
            "offset": int(offset),
        }
        if estado is not None:
            condiciones.append("d.estado = :estado")
            parametros["estado"] = estado

        consulta = (
            _SQL_CABECERA
            + " WHERE "
            + " AND ".join(condiciones)
            + " ORDER BY d.id DESC LIMIT :limit OFFSET :offset"
        )
        try:
            filas = db.execute(text(consulta), parametros).mappings().all()
        except DBAPIError as exc:
            raise _traducir(exc) from exc
        return [_fila_devolucion(dict(fila)) for fila in filas]

    @staticmethod
    def obtener(
        db: Session, *, empresa_id: int, devolucion_id: int
    ) -> dict | None:
        parametros = {"empresa": empresa_id, "devolucion": devolucion_id}
        try:
            cabecera = (
                db.execute(
                    text(
                        _SQL_CABECERA
                        + " WHERE d.id = :devolucion AND d.empresa_id = :empresa"
                    ),
                    parametros,
                )
                .mappings()
                .first()
            )
            if cabecera is None:
                return None
            filas = db.execute(text(_SQL_ITEMS), parametros).mappings().all()
            reembolso = (
                db.execute(text(_SQL_REEMBOLSO), parametros).mappings().first()
            )
        except DBAPIError as exc:
            raise _traducir(exc) from exc

        detalle = _fila_devolucion(dict(cabecera))
        detalle["items"] = [
            {
                "detalle_devolucion_id": int(fila["detalle_devolucion_id"]),
                "detalle_venta_id": int(fila["detalle_venta_id"]),
                "cantidad": int(fila["cantidad"]),
                "motivo": fila["motivo"],
                "reponer": bool(fila["reponer"]),
                "inventario_id": int(fila["inventario_id"]),
                "precio_unitario": float(fila["precio_unitario"]),
                "sku": fila["sku"],
                "subtotal": float(fila["subtotal"]),
            }
            for fila in filas
        ]
        # El reembolso solo existe tras `procesar`; no se inventa uno ejecutado.
        detalle["reembolso"] = (
            {
                "reembolso_id": int(reembolso["reembolso_id"]),
                "estado": str(reembolso["estado"]),
                "monto": float(reembolso["monto"]),
                "moneda": str(reembolso["moneda"]),
                "medio": str(reembolso["medio"]),
                "ejecutado": bool(reembolso["ejecutado"]),
                "referencia": reembolso["referencia"],
                "fecha_creacion": reembolso["fecha_creacion"],
                "fecha_ejecucion": reembolso["fecha_ejecucion"],
            }
            if reembolso is not None
            else None
        )
        return detalle

    @staticmethod
    def lineas_venta(
        db: Session, *, empresa_id: int, venta_id: int
    ) -> dict | None:
        """Lineas devolvibles de una venta de la empresa (5BZ).

        Devuelve `None` tanto si la venta no existe como si pertenece a otra
        empresa: el router traduce ambos casos al MISMO 404 para no filtrar
        existencia. `cantidad_devuelta` suma solo devoluciones no RECHAZADAS de
        ESA venta y `cantidad_disponible_devolver` nunca es negativo (equivalente
        portable de GREATEST(cantidad - devuelta, 0)).

        Es la consulta que permite construir `POST /tenant/devoluciones` con
        `detalle_venta_id` real en lugar de IDs adivinados.
        """
        if not all(_es_entero_positivo(v) for v in (empresa_id, venta_id)):
            raise DevolucionTenantError(403, "Identidad de devolucion invalida")

        v = Venta.__table__
        dv = DetalleVenta.__table__
        i = Inventario.__table__
        vp = VarianteProducto.__table__
        p = Producto.__table__
        talla = Talla.__table__
        color = Color.__table__
        dd = DetalleDevolucion.__table__
        d = Devolucion.__table__

        # Subconsulta correlacionada con la linea de venta del SELECT externo.
        # `venta_id` se enlaza como parametro (no como columna) para que el
        # planificador no introduzca un producto cartesiano con `venta`.
        devuelta = (
            select(func.coalesce(func.sum(dd.c.cantidad), 0))
            .select_from(
                dd.join(
                    d,
                    (d.c.id == dd.c.devolucion_id)
                    & (d.c.empresa_id == empresa_id),
                )
            )
            .where(
                dd.c.detalle_venta_id == dv.c.id,
                dd.c.empresa_id == empresa_id,
                d.c.venta_id == venta_id,
                d.c.estado != "RECHAZADA",
            )
            .scalar_subquery()
        )

        try:
            cabecera = (
                db.execute(
                    select(
                        v.c.id, v.c.sucursal_id, v.c.estado, v.c.total,
                    ).where(
                        v.c.id == venta_id,
                        v.c.empresa_id == empresa_id,
                    )
                )
                .mappings()
                .first()
            )
            if cabecera is None:
                return None
            filas = (
                db.execute(
                    select(
                        dv.c.id.label("detalle_venta_id"),
                        dv.c.inventario_id,
                        i.c.variante_producto_id,
                        vp.c.sku,
                        p.c.nombre.label("producto_nombre"),
                        talla.c.nombre.label("talla"),
                        color.c.nombre.label("color"),
                        dv.c.cantidad,
                        dv.c.precio_unitario,
                        devuelta.label("cantidad_devuelta"),
                        case(
                            (
                                dv.c.cantidad - devuelta > 0,
                                dv.c.cantidad - devuelta,
                            ),
                            else_=0,
                        ).label("cantidad_disponible_devolver"),
                    )
                    .select_from(
                        dv.join(
                            i,
                            (i.c.id == dv.c.inventario_id)
                            & (i.c.empresa_id == empresa_id),
                        )
                        .join(
                            vp,
                            (vp.c.id == i.c.variante_producto_id)
                            & (vp.c.empresa_id == empresa_id),
                        )
                        .join(
                            p,
                            (p.c.id == vp.c.producto_id)
                            & (p.c.empresa_id == empresa_id),
                        )
                        .join(talla, talla.c.id == vp.c.talla_id)
                        .join(color, color.c.id == vp.c.color_id)
                    )
                    .where(
                        dv.c.empresa_id == empresa_id,
                        dv.c.venta_id == venta_id,
                    )
                    .order_by(dv.c.id)
                )
                .mappings()
                .all()
            )
        except DBAPIError as exc:
            raise _traducir(exc) from exc

        return {
            "venta_id": int(cabecera["id"]),
            "sucursal_id": int(cabecera["sucursal_id"]),
            "estado": str(cabecera["estado"]),
            "total": float(cabecera["total"]),
            "lineas": [
                {
                    "detalle_venta_id": int(fila["detalle_venta_id"]),
                    "inventario_id": int(fila["inventario_id"]),
                    "variante_producto_id": int(fila["variante_producto_id"]),
                    "sku": str(fila["sku"]),
                    "producto_nombre": str(fila["producto_nombre"]),
                    "talla": str(fila["talla"]),
                    "color": str(fila["color"]),
                    "cantidad": int(fila["cantidad"]),
                    "precio_unitario": float(fila["precio_unitario"]),
                    "cantidad_devuelta": int(fila["cantidad_devuelta"]),
                    "cantidad_disponible_devolver": int(
                        fila["cantidad_disponible_devolver"]
                    ),
                }
                for fila in filas
            ],
        }
