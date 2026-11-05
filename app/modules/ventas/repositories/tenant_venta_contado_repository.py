"""E1/5BM: venta presencial de contado tenantizada (idempotente).

El cliente HTTP solo envia sucursal, lineas (inventario_id + cantidad), metodo
de pago y un `operacion_id` UUID. El servidor deriva empresa, usuario, precios,
total, estado y pago; la base aplica stock, auditoria e idempotencia en una
unica transaccion.

No reutiliza `/ventas`, `/pagos` ni los SP legacy revocados.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

_METODOS = {"EFECTIVO", "TARJETA", "TRANSFERENCIA", "QR", "OTRO"}


class VentaContadoError(Exception):
    def __init__(self, codigo: int, detalle: str):
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def huella_operacion(*, empresa_id: int, usuario_id: int, sucursal_id: int,
                     metodo_pago: str, lineas: list[dict]) -> str:
    """Huella canonica de la carga: mismo UUID + misma huella = replay."""
    normalizadas = sorted(
        ({"inventario_id": int(x["inventario_id"]), "cantidad": int(x["cantidad"])}
         for x in lineas),
        key=lambda x: x["inventario_id"],
    )
    payload = {
        "empresa_id": int(empresa_id),
        "usuario_id": int(usuario_id),
        "sucursal_id": int(sucursal_id),
        "metodo_pago": metodo_pago,
        "lineas": normalizadas,
    }
    canonico = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


class TenantVentaContadoRepository:
    @staticmethod
    def comprobante(db: Session, *, empresa_id: int, usuario_id: int,
                    venta_id: int) -> dict:
        """Read-only receipt bridge for a tenant contado sale.

        Ownership is constrained by empresa_id and the caller must be a tenant
        personal member in the API layer. No receipt row is persisted.
        """
        row = db.execute(text("""
            SELECT v.id AS venta_id, v.carrito_id, v.sucursal_id,
                   s.nombre AS sucursal_nombre, v.canal, v.estado,
                   v.fecha_hora, v.total,
                   p.id AS pago_id, p.fecha_hora AS pago_fecha_hora,
                   p.monto AS pago_monto, p.metodo AS pago_metodo,
                   p.estado AS pago_estado, p.referencia_transaccion,
                   p.pasarela
            FROM venta v
            JOIN sucursal s ON s.id=v.sucursal_id AND s.empresa_id=:empresa
            JOIN pago p ON p.venta_id=v.id AND p.empresa_id=:empresa
                         AND p.estado='APROBADO'
            WHERE v.id=:venta AND v.empresa_id=:empresa
              AND v.canal='PRESENCIAL' AND v.estado='COMPLETADA'
            ORDER BY p.id DESC
            LIMIT 1
        """), {"empresa": empresa_id, "venta": venta_id}).mappings().first()
        if row is None:
            raise VentaContadoError(404, "Venta no encontrada")
        items = db.execute(text("""
            SELECT d.id AS detalle_venta_id, d.inventario_id,
                   d.cantidad, d.precio_unitario
            FROM detalle_venta d
            WHERE d.venta_id=:venta
            ORDER BY d.id
        """), {"venta": venta_id}).mappings().all()
        pago = {key.removeprefix("pago_"): value for key, value in row.items()
                 if key.startswith("pago_")}
        return {
            "venta_id": row["venta_id"], "carrito_id": row["carrito_id"],
            "sucursal_id": row["sucursal_id"],
            "sucursal_nombre": row["sucursal_nombre"], "canal": row["canal"],
            "estado": row["estado"], "fecha_hora": row["fecha_hora"],
            "total": row["total"], "pago": pago,
            "items": [dict(item) for item in items],
        }

    @staticmethod
    def registrar(
        db: Session, *, empresa_id: int, usuario_id: int, sucursal_id: int,
        operacion_id: str, metodo_pago: str, lineas: list[dict],
    ) -> dict:
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
               for v in (empresa_id, usuario_id, sucursal_id)):
            raise VentaContadoError(403, "Identidad de venta invalida")
        if metodo_pago not in _METODOS:
            raise VentaContadoError(422, "Metodo de pago no admitido")
        if not lineas:
            raise VentaContadoError(422, "La venta requiere al menos una linea")
        huella = huella_operacion(
            empresa_id=empresa_id, usuario_id=usuario_id,
            sucursal_id=sucursal_id, metodo_pago=metodo_pago, lineas=lineas,
        )
        try:
            valor = db.scalar(text(
                "SELECT public.sp_registrar_venta_contado_tenant("
                "CAST(:empresa AS bigint), CAST(:usuario AS bigint), "
                "CAST(:sucursal AS bigint), CAST(:operacion AS uuid), "
                "CAST(:huella AS text), CAST(:lineas AS jsonb), "
                "CAST(:metodo AS text))"),
                {"empresa": empresa_id, "usuario": usuario_id,
                 "sucursal": sucursal_id, "operacion": operacion_id,
                 "huella": huella,
                 "lineas": json.dumps(lineas, separators=(",", ":")),
                 "metodo": metodo_pago})
        except DBAPIError as exc:
            estado = _sqlstate(exc)
            if estado == "42501":
                raise VentaContadoError(403, "Operacion no autorizada") from exc
            if estado in {"23514", "23505", "40001", "40P01"}:
                raise VentaContadoError(409, "Conflicto en la venta") from exc
            if estado in {"22023", "22P02"}:
                raise VentaContadoError(422, "Datos de venta invalidos") from exc
            raise
        if isinstance(valor, (str, bytes)):
            valor = json.loads(valor)
        if not isinstance(valor, dict):
            raise VentaContadoError(503, "Venta sin respuesta valida")
        if valor.get("ok") is not True:
            raise VentaContadoError(
                int(valor.get("codigo", 409)),
                str(valor.get("mensaje", "Venta rechazada")),
            )
        venta = dict(valor.get("venta") or {})
        venta["idempotente"] = bool(valor.get("idempotente"))
        total = venta.get("total")
        if isinstance(total, (int, float)):
            venta["total"] = Decimal(str(total))
        return venta
