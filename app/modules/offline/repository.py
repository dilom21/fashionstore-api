"""Acceso al ledger durable CU19/CU20.

La API no recibe DML directo sobre el ledger. Solo ejecuta funciones
SECURITY DEFINER con verificacion de actor dentro de PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.offline.schemas import OperacionOfflineRequest


def hash_payload(datos: OperacionOfflineRequest) -> str:
    """Huella canonica estable: mismo contenido -> mismo hash."""
    lineas = sorted(
        (
            {"inventario_id": int(linea.inventario_id),
             "cantidad": int(linea.cantidad)}
            for linea in datos.lineas
        ),
        key=lambda item: item["inventario_id"],
    )
    canonico: dict = {
        "tipo": datos.tipo,
        "sucursal_id": int(datos.sucursal_id),
        "lineas": lineas,
    }
    if datos.fecha_atencion is not None:
        canonico["fecha_atencion"] = datos.fecha_atencion.astimezone(
            timezone.utc
        ).isoformat()
    if datos.observacion is not None:
        canonico["observacion"] = datos.observacion.strip()
    if datos.efectivo_recibido is not None:
        canonico["efectivo_recibido"] = format(
            Decimal(datos.efectivo_recibido), ".2f"
        )
    if datos.cliente_id is not None:
        canonico["cliente_id"] = int(datos.cliente_id)
    crudo = json.dumps(
        canonico, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


class OfflineLedgerRepository:
    @staticmethod
    def begin(
        db: Session, *, empresa_id: int, usuario_id: int, operacion_uuid: str,
        tipo: str, payload_hash: str, sucursal_id: int,
        efectivo_recibido: Decimal | None,
    ) -> dict:
        return dict(db.execute(text(
            "SELECT * FROM fashionstore_private.fn_offline_begin("
            ":empresa_id,:usuario_id,CAST(:operacion_uuid AS uuid),:tipo,"
            ":payload_hash,:sucursal_id,:efectivo_recibido)"
        ), {
            "empresa_id": empresa_id, "usuario_id": usuario_id,
            "operacion_uuid": operacion_uuid, "tipo": tipo,
            "payload_hash": payload_hash, "sucursal_id": sucursal_id,
            "efectivo_recibido": efectivo_recibido,
        }).mappings().one())

    @staticmethod
    def finish(
        db: Session, *, empresa_id: int, usuario_id: int, operacion_uuid: str,
        estado: str, id_definitivo: int | None, resultado: dict | None,
        error_codigo: str | None = None, error_mensaje: str | None = None,
    ) -> None:
        db.execute(text(
            "SELECT fashionstore_private.fn_offline_finish("
            ":empresa_id,:usuario_id,CAST(:operacion_uuid AS uuid),:estado,"
            ":id_definitivo,CAST(:resultado AS jsonb),:error_codigo,"
            ":error_mensaje)"
        ), {
            "empresa_id": empresa_id, "usuario_id": usuario_id,
            "operacion_uuid": operacion_uuid, "estado": estado,
            "id_definitivo": id_definitivo,
            "resultado": json.dumps(resultado, ensure_ascii=False)
            if resultado is not None else None,
            "error_codigo": error_codigo, "error_mensaje": error_mensaje,
        })

    @staticmethod
    def rechazar(
        db: Session, *, empresa_id: int, usuario_id: int, operacion_uuid: str,
        tipo: str, payload_hash: str, sucursal_id: int,
        efectivo_recibido: Decimal | None, estado: str,
        error_codigo: str, error_mensaje: str,
    ) -> dict:
        return dict(db.execute(text(
            "SELECT * FROM fashionstore_private.fn_offline_rechazar("
            ":empresa_id,:usuario_id,CAST(:operacion_uuid AS uuid),:tipo,"
            ":payload_hash,:sucursal_id,:efectivo_recibido,:estado,"
            ":error_codigo,:error_mensaje)"
        ), {
            "empresa_id": empresa_id, "usuario_id": usuario_id,
            "operacion_uuid": operacion_uuid, "tipo": tipo,
            "payload_hash": payload_hash, "sucursal_id": sucursal_id,
            "efectivo_recibido": efectivo_recibido, "estado": estado,
            "error_codigo": error_codigo, "error_mensaje": error_mensaje,
        }).mappings().one())

    @staticmethod
    def obtener(
        db: Session, *, empresa_id: int, usuario_id: int, operacion_uuid: str,
    ) -> dict | None:
        row = db.execute(text(
            "SELECT * FROM fashionstore_private.fn_offline_get("
            ":empresa_id,:usuario_id,CAST(:operacion_uuid AS uuid))"
        ), {
            "empresa_id": empresa_id, "usuario_id": usuario_id,
            "operacion_uuid": operacion_uuid,
        }).mappings().one_or_none()
        return dict(row) if row is not None else None
