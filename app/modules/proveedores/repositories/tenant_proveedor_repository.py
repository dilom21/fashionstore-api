"""5C0: alta tenant de proveedores y asociacion de productos.

El cliente HTTP solo envia datos del proveedor y una lista de `producto_id`.
La empresa, el usuario y el estado se derivan del JWT/membresia y de
PostgreSQL. Las rutinas son SECURITY INVOKER: RLS/FORCE y la asercion HMAC
siguen aplicando; el rol y el plan se revalidan dentro de la transaccion.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

_CODIGOS_CONFLICTO = frozenset({"23505", "23503", "23514", "40001", "40P01"})
_CODIGOS_INVALIDOS = frozenset({"22023", "22P02"})

_DETALLE_403 = "Operacion no autorizada"
_DETALLE_409 = "Conflicto al guardar el proveedor"
_DETALLE_422 = "Datos del proveedor invalidos"
_DETALLE_FALLO = "El proveedor no pudo procesarse"


class ProveedorTenantError(Exception):
    """Fallo de negocio ya traducido a un codigo HTTP seguro."""

    def __init__(self, codigo: int, detalle: str):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _detalle_por_codigo(codigo: int) -> str:
    if codigo == 403:
        return _DETALLE_403
    if codigo == 409:
        return _DETALLE_409
    if codigo == 422:
        return _DETALLE_422
    return _DETALLE_FALLO


def _traducir(exc: DBAPIError) -> ProveedorTenantError:
    """Traduce SQLSTATE a error tipado; re-lanza lo desconocido.

    `IntegrityError` es subclase de `DBAPIError`: el SQLSTATE se evalua antes
    de decidir para que un 42501 nunca termine como 500.
    """
    estado = _sqlstate(exc)
    if estado == "42501":
        return ProveedorTenantError(403, _DETALLE_403)
    if estado in _CODIGOS_CONFLICTO:
        return ProveedorTenantError(409, _DETALLE_409)
    if estado in _CODIGOS_INVALIDOS:
        return ProveedorTenantError(422, _DETALLE_422)
    raise exc


def _cargar(valor: Any) -> dict:
    if isinstance(valor, (str, bytes, bytearray)):
        try:
            valor = json.loads(valor)
        except (TypeError, ValueError) as exc:
            raise ProveedorTenantError(
                503, "Proveedor sin respuesta valida"
            ) from exc
    if not isinstance(valor, dict):
        raise ProveedorTenantError(503, "Proveedor sin respuesta valida")
    if valor.get("ok") is not True:
        try:
            codigo = int(valor.get("codigo", 409))
        except (TypeError, ValueError):
            codigo = 409
        raise ProveedorTenantError(codigo, _detalle_por_codigo(codigo))
    return valor


def _llamar(db: Session, consulta: str, parametros: dict) -> dict:
    try:
        valor = db.scalar(text(consulta), parametros)
    except DBAPIError as exc:
        raise _traducir(exc) from exc
    return _cargar(valor)


class TenantProveedorRepository:
    @staticmethod
    def crear(
        db: Session,
        *,
        empresa_id: int,
        usuario_id: int,
        razon_social: str,
        nit: str | None,
        correo: str | None,
        telefono: str | None,
        direccion: str | None,
        producto_ids: list[int],
    ) -> dict:
        valor = _llamar(
            db,
            "SELECT public.sp_crear_proveedor_tenant("
            "CAST(:empresa AS bigint), CAST(:usuario AS bigint), "
            "CAST(:razon AS text), CAST(:nit AS text), CAST(:correo AS text), "
            "CAST(:telefono AS text), CAST(:direccion AS text), "
            "CAST(:productos AS jsonb))",
            {
                "empresa": empresa_id,
                "usuario": usuario_id,
                "razon": razon_social,
                "nit": nit,
                "correo": correo,
                "telefono": telefono,
                "direccion": direccion,
                "productos": json.dumps(
                    sorted(set(producto_ids)), separators=(",", ":")
                ),
            },
        )
        return {
            "id": int(valor["proveedor_id"]),
            "razon_social": str(valor["razon_social"]),
            "nit": valor.get("nit"),
            "correo": valor.get("correo"),
            "telefono": valor.get("telefono"),
            "direccion": valor.get("direccion"),
            "estado": bool(valor.get("estado", True)),
            "total_productos": int(valor.get("total_productos", 0)),
        }

    @staticmethod
    def reemplazar_productos(
        db: Session,
        *,
        empresa_id: int,
        proveedor_id: int,
        usuario_id: int,
        producto_ids: list[int],
    ) -> dict:
        valor = _llamar(
            db,
            "SELECT public.sp_reemplazar_productos_proveedor_tenant("
            "CAST(:empresa AS bigint), CAST(:proveedor AS bigint), "
            "CAST(:usuario AS bigint), CAST(:productos AS jsonb))",
            {
                "empresa": empresa_id,
                "proveedor": proveedor_id,
                "usuario": usuario_id,
                "productos": json.dumps(
                    sorted(set(producto_ids)), separators=(",", ":")
                ),
            },
        )
        return {
            "proveedor_id": int(valor["proveedor_id"]),
            "total_productos": int(valor.get("total_productos", 0)),
        }
